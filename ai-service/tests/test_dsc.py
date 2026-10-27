import base64
import io
import json
from datetime import datetime, timedelta, timezone

import pikepdf
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from cryptography.x509.oid import NameOID
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from app.pdf import dsc
from app.pdf.assembler import merged_settings


def _token():
    """A key and a certificate standing in for a Class 3 DSC on a USB token."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MANISH KUMAR"), x509.NameAttribute(NameOID.ORGANIZATION_NAME, "UTL")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365)).sign(key, hashes.SHA256()))
    return key, cert.public_bytes(serialization.Encoding.DER)


def _token_signs(key, hash_b64: str) -> bytes:
    """What the signer app does with the token: RSA PKCS#1 v1.5 over a SHA-256 hash."""
    return key.sign(base64.b64decode(hash_b64), padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))


def _built_bid(tmp_path):
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for text in ("Cover", "Covering letter", "PAN card"):
        c.drawString(72, 700, text)
        c.showPage()
    c.save()
    (tmp_path / "technical-bid.pdf").write_bytes(buf.getvalue())
    (tmp_path / "pages.json").write_text(json.dumps({"pages": [{"kind": "cover"}, {"kind": "text"}, {"kind": "docs"}],
                                                     "built_at": "2026-10-27T10:00:00+00:00"}))
    (tmp_path / "folder").mkdir()
    (tmp_path / "folder" / "00 Technical bid (complete) - R1.pdf").write_bytes(buf.getvalue())
    (tmp_path / "folder.json").write_text(json.dumps({"folder": "R1 - Bid documents", "files": [
        {"name": "00 Technical bid (complete) - R1.pdf", "title": "Technical bid", "kind": "complete", "pages": 3}]}))
    return tmp_path


def test_one_dsc_signature_covers_the_bid_and_shows_on_each_signed_page(tmp_path):
    key, cert = _token()
    bid = _built_bid(tmp_path)
    prep = dsc.prepare(bid, merged_settings(None), cert, [])
    assert prep["signer"] == "MANISH KUMAR" and prep["algorithm"] == "SHA256"
    info = dsc.finish(bid, prep["session"], _token_signs(key, prep["hash"]))
    assert info["signer"] == "MANISH KUMAR"

    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    with open(bid / "technical-bid.pdf", "rb") as f:
        sigs = PdfFileReader(f).embedded_signatures
        assert len(sigs) == 1
        status = validate_pdf_signature(sigs[0])
        assert status.intact and status.valid and status.coverage.name == "ENTIRE_FILE"
    texts = [p.extract_text() for p in PdfReader(bid / "technical-bid.pdf").pages]
    assert "Digitally signed by MANISH KUMAR" not in texts[0]          # cover: not signed by default
    assert "Digitally signed by MANISH KUMAR" in texts[1]              # marker line
    assert json.loads((bid / "pages.json").read_text())["dsc"]["signer"] == "MANISH KUMAR"
    assert (bid / "folder" / "00 Technical bid (complete) - R1.pdf").read_bytes() == (bid / "technical-bid.pdf").read_bytes()
    assert not (bid / "dsc-session.json").exists()


def test_a_signature_from_another_key_is_refused_and_nothing_changes(tmp_path):
    _, cert = _token()
    other, _ = _token()
    bid = _built_bid(tmp_path)
    before = (bid / "technical-bid.pdf").read_bytes()
    prep = dsc.prepare(bid, merged_settings(None), cert, [])
    with pytest.raises(dsc.SigningError, match="does not match"):
        dsc.finish(bid, prep["session"], _token_signs(other, prep["hash"]))
    assert (bid / "technical-bid.pdf").read_bytes() == before


def test_signing_needs_the_current_build_and_session(tmp_path):
    key, cert = _token()
    bid = _built_bid(tmp_path)
    prep = dsc.prepare(bid, merged_settings(None), cert, [])
    with pytest.raises(dsc.SigningError, match="Another signing"):
        dsc.finish(bid, "not-the-session", _token_signs(key, prep["hash"]))
    pages = json.loads((bid / "pages.json").read_text())
    (bid / "pages.json").write_text(json.dumps({**pages, "built_at": "2026-10-27T11:00:00+00:00"}))   # built again
    with pytest.raises(dsc.SigningError, match="changed"):
        dsc.finish(bid, prep["session"], _token_signs(key, prep["hash"]))
