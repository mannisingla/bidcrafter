"""Signing the built bid with a DSC (Class 3 digital signature certificate) on a USB token.

The token's key never leaves the person's computer: the BidCraft Signer app there signs one SHA-256 hash with it
(the token's own driver asks for the PIN). This module does the rest, on the server, with pyHanko:

prepare()  The built PDF gets "Digitally signed by <name> on <date>" on every page that carries the signature
           (the Preview & sign "Apply to" setting) and one signature field, visible on the last of those pages. The
           CMS signed attributes of that signature are hashed; the hash goes to the browser, which has the signer
           app sign it.
finish()   The token's signature is checked against the certificate, put in the reserved place, the signed PDF is
           verified, and it replaces the bid's PDF and the complete file of the bid folder.

One signature covers every page: any change to the PDF after signing breaks it. Building the PDF again removes it.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
import os
import secrets
import shutil
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pikepdf
from asn1crypto import cms, x509 as asn1_x509
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader
from pyhanko.pdf_utils.text import TextBoxStyle
from pyhanko.sign import fields, signers
from pyhanko.sign.signers.pdf_byterange import PreparedByteRangeDigest
from pyhanko.sign.signers.pdf_signer import PdfTBSDocument
from pyhanko.sign.validation import validate_pdf_signature
from pyhanko.stamp import TextStampStyle
from pyhanko_certvalidator.registry import SimpleCertificateStore
from reportlab.lib.colors import HexColor
from reportlab.pdfgen import canvas

from .assembler import PX, sign_block_x, signed_pages

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_TTL = timedelta(minutes=15)
FIELD = "BidCraftDSC"
MARK_COLOR = "#16307a"


class SigningError(ValueError):
    """Shown to the person as it is."""


def _paths(target_dir: Path) -> dict[str, Path]:
    return {"pdf": target_dir / "technical-bid.pdf", "pages": target_dir / "pages.json",
            "pending": target_dir / "dsc-pending.pdf", "session": target_dir / "dsc-session.json"}


def _certificate(der: bytes) -> tuple[asn1_x509.Certificate, x509.Certificate]:
    try:
        return asn1_x509.Certificate.load(der), x509.load_der_x509_certificate(der)
    except ValueError as exc:
        raise SigningError("The certificate from the token could not be read") from exc


def _name(cert: asn1_x509.Certificate, field: str = "subject") -> str:
    names = getattr(cert, field).native
    return names.get("common_name") or names.get("organization_name") or "DSC holder"


def _marker_pdf(sizes: list[tuple[float, float]], marks: list[bool], text: str, sign: dict) -> bytes:
    """A page per page of the bid, with the marker line under the signature block where it is due."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for (w, h), mark in zip(sizes, marks):
        c.setPageSize((w, h))
        if mark:
            c.setFont("Helvetica", 6.5)
            c.setFillColor(HexColor(MARK_COLOR))
            x0 = sign_block_x(w, sign)
            if sign.get("position", "right") == "right":
                c.drawRightString(x0 + 204 * PX, 10 * PX, text)
            else:
                c.drawString(x0 + 6 * PX, 10 * PX, text)
        c.showPage()
    c.save()
    return buf.getvalue()


def _signer(cert: asn1_x509.Certificate, chain: list[asn1_x509.Certificate], value) -> signers.ExternalSigner:
    return signers.ExternalSigner(signing_cert=cert, cert_registry=SimpleCertificateStore.from_certs([cert, *chain]),
                                  signature_value=value)


def prepare(target_dir: Path, settings: dict, certificate: bytes, chain: list[bytes]) -> dict:
    """The hash for the token to sign. settings: the bid's settings, merged (assembler.merged_settings)."""
    p = _paths(target_dir)
    if not p["pdf"].exists() or not p["pages"].exists():
        raise SigningError("Build the PDF first")
    pages = json.loads(p["pages"].read_text(encoding="utf-8"))
    if pages.get("dsc"):
        raise SigningError("This PDF is already digitally signed. Build it again to sign it afresh.")
    cert, crypto_cert = _certificate(certificate)
    now = datetime.now(timezone.utc)
    if not crypto_cert.not_valid_before_utc <= now <= crypto_cert.not_valid_after_utc:
        raise SigningError(f"The certificate is not valid today (valid till {crypto_cert.not_valid_after_utc:%d %b %Y})")
    key = crypto_cert.public_key()
    if not isinstance(key, rsa.RSAPublicKey):
        raise SigningError("Only RSA certificates are supported (the usual Class 3 DSC)")
    intermediates = [asn1_x509.Certificate.load(c) for c in chain]

    signer_name = _name(cert)
    when = datetime.now(IST)
    text = f"Digitally signed by {signer_name} on {when:%d %b %Y, %H:%M} IST"
    marks = signed_pages(settings, [x["kind"] for x in pages["pages"]])
    with pikepdf.open(p["pdf"]) as pdf:
        if len(pdf.pages) != len(marks):
            raise SigningError("The PDF does not match its page list. Build it again.")
        last = max((i for i, m in enumerate(marks) if m), default=len(pdf.pages) - 1)
        sizes = [(float(pg.mediabox[2]) - float(pg.mediabox[0]), float(pg.mediabox[3]) - float(pg.mediabox[1])) for pg in pdf.pages]
        already_signed = "/AcroForm" in pdf.Root and any(f.get("/FT") == "/Sig" for f in pdf.Root.AcroForm.get("/Fields", []))
        if not already_signed:          # a signature made on the server (PKCS#12) would break if the pages changed
            overlay = pikepdf.open(io.BytesIO(_marker_pdf(sizes, [m and i != last for i, m in enumerate(marks)], text,
                                                          settings["sign"])))
            for i, page in enumerate(pdf.pages):
                if marks[i] and i != last:
                    page.add_overlay(overlay.pages[i])
        marked = io.BytesIO()
        pdf.save(marked)

    # the visible signature: where the marker line goes, on the last signed page
    w = sizes[last][0]
    x0 = sign_block_x(w, settings["sign"])
    box = (x0, 2 * PX, x0 + 210 * PX, 20 * PX)
    writer = IncrementalPdfFileWriter(io.BytesIO(marked.getvalue()))
    fields.append_signature_field(writer, fields.SigFieldSpec(sig_field_name=FIELD, on_page=last, box=box))
    style = TextStampStyle(stamp_text=text.replace("%", "%%"), background=None, border_width=0,
                           text_box_style=TextBoxStyle(font_size=6))
    meta = signers.PdfSignatureMetadata(field_name=FIELD, md_algorithm="sha256", name=signer_name,
                                        reason="Technical bid", location="India")
    ext = _signer(cert, intermediates, bytes(key.key_size // 8))
    pdf_signer = signers.PdfSigner(meta, signer=ext, stamp_style=style)

    async def digest():
        prep, tbs, output = await pdf_signer.async_digest_doc_for_signing(writer)
        attrs = await ext.signed_attrs(prep.document_digest, tbs.md_algorithm)
        return prep, tbs.md_algorithm, attrs, output
    prep, md_algorithm, attrs, output = asyncio.run(digest())

    output.seek(0)
    p["pending"].write_bytes(output.read())
    session = secrets.token_urlsafe(24)
    p["session"].write_text(json.dumps({
        "session": session, "created": now.isoformat(), "built_at": pages.get("built_at"),
        "md_algorithm": md_algorithm, "signed_attrs": base64.b64encode(attrs.dump()).decode(),
        "certificate": base64.b64encode(certificate).decode(), "chain": [base64.b64encode(c).decode() for c in chain],
        "digest": {"document_digest": base64.b64encode(prep.document_digest).decode(),
                   "reserved_region_start": prep.reserved_region_start, "reserved_region_end": prep.reserved_region_end},
        "signer": signer_name, "issuer": _name(cert, "issuer"), "signed_at": when.isoformat(),
        "valid_to": crypto_cert.not_valid_after_utc.isoformat(), "serial": format(crypto_cert.serial_number, "x"),
    }), encoding="utf-8")
    return {"session": session, "hash": base64.b64encode(hashlib.sha256(attrs.dump()).digest()).decode(),
            "algorithm": "SHA256", "signer": signer_name}


def finish(target_dir: Path, session: str, signature: bytes) -> dict:
    p = _paths(target_dir)
    if not p["session"].exists():
        raise SigningError("Start the signing again")
    s = json.loads(p["session"].read_text(encoding="utf-8"))
    pages = json.loads(p["pages"].read_text(encoding="utf-8"))
    if not secrets.compare_digest(s["session"], session):
        raise SigningError("Another signing was started for this bid; start again")
    if datetime.now(timezone.utc) - datetime.fromisoformat(s["created"]) > SESSION_TTL or s["built_at"] != pages.get("built_at"):
        raise SigningError("The PDF changed or the signing took too long; start again")

    attrs_der = base64.b64decode(s["signed_attrs"])
    cert, crypto_cert = _certificate(base64.b64decode(s["certificate"]))
    try:        # the token signed what it was given, with the key of this certificate
        crypto_cert.public_key().verify(signature, attrs_der, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise SigningError("The token's signature does not match the certificate chosen") from exc

    ext = _signer(cert, [asn1_x509.Certificate.load(base64.b64decode(c)) for c in s["chain"]], signature)
    prep = PreparedByteRangeDigest(document_digest=base64.b64decode(s["digest"]["document_digest"]),
                                   reserved_region_start=s["digest"]["reserved_region_start"],
                                   reserved_region_end=s["digest"]["reserved_region_end"])

    async def embed():
        sig_cms = await ext.async_sign_prescribed_attributes(s["md_algorithm"], signed_attrs=cms.CMSAttributes.load(attrs_der))
        with open(p["pending"], "r+b") as out:
            await PdfTBSDocument.async_finish_signing(out, prepared_digest=prep, signature_cms=sig_cms)
    asyncio.run(embed())

    with open(p["pending"], "rb") as f:            # intact and cryptographically valid, as a reader will check it
        status = validate_pdf_signature(PdfFileReader(f).embedded_signatures[-1])
    if not (status.intact and status.valid):
        raise SigningError("The signed PDF did not verify; nothing was changed")

    os.replace(p["pending"], p["pdf"])
    p["session"].unlink(missing_ok=True)
    info = {k: s[k] for k in ("signer", "issuer", "signed_at", "valid_to", "serial")}
    pages["dsc"] = info
    p["pages"].write_text(json.dumps(pages), encoding="utf-8")
    _refresh_folder(target_dir, p["pdf"])
    return info


def _refresh_folder(target_dir: Path, signed: Path) -> None:
    """The complete file of the bid folder (and its ZIP) becomes the signed PDF."""
    index_path = target_dir / "folder.json"
    if not index_path.exists():
        return
    index = json.loads(index_path.read_text(encoding="utf-8"))
    complete = next((f for f in index["files"] if f.get("kind") == "complete"), None)
    if complete is None:
        return
    folder = target_dir / "folder"
    shutil.copyfile(signed, folder / complete["name"])
    complete["size"] = (folder / complete["name"]).stat().st_size
    complete["title"] = "Technical bid (complete, digitally signed)"
    zip_path = target_dir / "bid-folder.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in index["files"]:
            z.write(folder / f["name"], f"{index['folder']}/{f['name']}")
    index["zip_size"] = zip_path.stat().st_size
    index_path.write_text(json.dumps(index), encoding="utf-8")
