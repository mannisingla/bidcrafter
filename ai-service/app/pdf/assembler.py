"""Job: assemble the final bid PDF on this server.

1. Render drafted sections (cover, index, text) from HTML with WeasyPrint.
2. Take library documents exactly as uploaded (an image or Word file: its plain PDF conversion; never the OCR copy).
3. Number pages, then re-render the index and cross-references until numbers settle.
4. Merge everything with pikepdf and add bookmarks per section.
5. Stamp page number, signature and stamp on each page, and the header on the bid's own pages: nothing else is
   written on an original document. Annexures without a separator page get a divider page of their own instead.
6. Optionally apply a digital signature (PKCS#12) with pyHanko.
"""
from __future__ import annotations

import copy
import io
import json
import re
import shutil
import zipfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import pikepdf
import psycopg
from markupsafe import escape
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from weasyprint import HTML

from ..config import get_settings
from ..db import audit
from ..drafting.sanitize import clean_html
from ..files import relative, resolve

MM = 72 / 25.4

FONTS_DIR = Path(__file__).resolve().parents[2] / "fonts"
PX = 0.75   # 1 CSS px of the 794 x 1123 px prototype page = 0.75 pt

DEFAULT_SETTINGS = {
    "header": {"enabled": True, "left": "", "right": ""},
    "page_numbers": {"enabled": True, "format": "Page {n} of {N}", "position": "center", "start": 1,
                     "skip_cover": True},
    # image_id / stamp_id: saved signature and stamp images (sign_images), chosen on the Preview & sign screen
    "sign": {"enabled": True, "image_id": None, "caption": "Authorised signatory", "stamp": True, "stamp_id": None,
             "position": "right", "apply": "not_cover", "range": ""},
}


def _font_faces() -> str:
    faces = [("Source Sans 3", "SourceSans3-Regular.ttf", 400), ("Source Sans 3", "SourceSans3-SemiBold.ttf", 600),
             ("Source Sans 3", "SourceSans3-Bold.ttf", 700), ("Source Serif 4", "SourceSerif4-Regular.ttf", 400),
             ("Source Serif 4", "SourceSerif4-SemiBold.ttf", 600), ("Source Serif 4", "SourceSerif4-Bold.ttf", 700)]
    return "".join(f'@font-face{{font-family:"{fam}";src:url("{(FONTS_DIR / f).as_uri()}");font-weight:{w}}}'
                   for fam, f, w in faces if (FONTS_DIR / f).exists())


# The page box and typography are the prototype's own CSS (px on a 794 x 1123 px A4 page):
# body area top 72, sides 64, bottom 118 – the rest is reserved for header, page number and signature.
CSS = _font_faces() + """
@page { size: 794px 1123px; margin: 72px 64px 118px 64px; }
body { margin: 0; font-family: "Source Serif 4", Georgia, "Noto Serif", serif; font-size: 12.5px; line-height: 1.58; color: #141414; }
p { margin: 0 0 8px; }
h2.sec-title { font: 700 16px/1.3 "Source Sans 3", Arial, sans-serif; border-bottom: 2px solid #141414; padding-bottom: 6px; margin: 0 0 14px; }
h3 { font: 700 13.5px/1.3 "Source Sans 3", Arial, sans-serif; margin: 14px 0 6px; page-break-after: avoid; }
h4 { font: 700 12.5px/1.3 "Source Sans 3", Arial, sans-serif; margin: 10px 0 4px; page-break-after: avoid; }
ul, ol { margin: 0 0 8px; padding-left: 22px; } li { margin-bottom: 3px; }
table { width: 100%; border-collapse: collapse; margin: 0 0 10px; font-size: 11px; line-height: 1.4; }
th, td { border: 1px solid #8a8a8a; padding: 4px 6px; vertical-align: top; text-align: left; }
th { background: #ececec; font-family: "Source Sans 3", Arial, sans-serif; font-weight: 700; }
thead { display: table-header-group; } tr { page-break-inside: avoid; }
.xref { color: #0b4ea2; font-weight: 600; white-space: nowrap; }
mark.fill { background: #fff0a0; color: #4d3d00; padding: 0 2px; border-radius: 2px; }
.lh-own { border-bottom: 3px double #222; padding-bottom: 8px; margin-bottom: 14px; font: 700 17px "Source Sans 3", Arial, sans-serif; letter-spacing: .02em; }
.lh-own small { display: block; font-weight: 400; font-size: 10.5px; color: #444; letter-spacing: 0; }
.split { display: flex; justify-content: space-between; }
.ctr, .center { text-align: center; } .rt { text-align: right; } .sign-space { margin-top: 40px; }
.cover { position: relative; height: 933px; text-align: center; font-family: "Source Sans 3", Arial, sans-serif; }
.cover .env { margin-top: 30px; font-size: 12px; font-weight: 600; color: #555; }
.cover .kind { font: 700 34px "Source Serif 4", serif; margin: 46px 0 22px; letter-spacing: .06em; }
.cover .ttl { font: 600 19px/1.4 "Source Serif 4", serif; margin: 0 20px; }
.cover .ref { margin-top: 18px; font-weight: 700; font-size: 14px; }
.cover .box { position: absolute; left: 40px; right: 40px; bottom: 58px; border: 1.5px solid #141414; padding: 18px 20px; display: flex; gap: 20px; text-align: left; font-size: 12.5px; }
.cover .box > div { flex: 1; } .cover .box b { display: block; font-size: 13.5px; margin-top: 4px; }
.cover .due { position: absolute; left: 0; right: 0; bottom: 30px; font-size: 12px; color: #444; }
.empty { color: #666; font-style: italic; }
.separator { margin-top: 150px; border: 2px solid #141414; padding: 26px 28px; font-family: "Source Sans 3", Arial, sans-serif; }
.separator .sep-kind { font-size: 11px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; color: #555; }
.separator .sep-clause { font: 700 26px/1.25 "Source Serif 4", Georgia, serif; margin: 6px 0 12px; }
.separator .sep-text { font: 400 13.5px/1.55 "Source Serif 4", Georgia, serif; margin-bottom: 18px; }
.separator .sep-table { font-size: 11.5px; margin: 0; }
"""

_XREF = re.compile(r'<span\b[^>]*\bclass="xref"[^>]*>.*?</span>', re.S)
_XREF_TARGET = re.compile(r'data-(sec|doc)="([^"]+)"')


def merged_settings(raw: dict | None) -> dict:
    out = copy.deepcopy(DEFAULT_SETTINGS)
    for key, value in (raw or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


@lru_cache(maxsize=1)
def _fonts() -> dict:
    """reportlab fonts matching the web preview (Source Sans 3)."""
    f = {"regular": "Helvetica", "semibold": "Helvetica-Bold", "bold": "Helvetica-Bold"}
    for key, name, file in (("regular", "BC-Sans", "SourceSans3-Regular.ttf"), ("semibold", "BC-Sans-Semi", "SourceSans3-SemiBold.ttf"),
                            ("bold", "BC-Sans-Bold", "SourceSans3-Bold.ttf")):
        if (FONTS_DIR / file).exists():
            pdfmetrics.registerFont(TTFont(name, str(FONTS_DIR / file)))
            f[key] = name
    return f


def _image(path) -> ImageReader | None:
    try:
        return ImageReader(str(path)) if path else None
    except (ValueError, OSError):
        return None


def sign_images(conn: psycopg.Connection, sign: dict) -> dict:
    """Files of the saved signature and stamp images chosen for a bid. A signature uploaded for the bid by an
    earlier version (sign.image_path) is used while no saved one is chosen."""
    chosen = {"signature": sign.get("image_id"), "stamp": sign.get("stamp_id")}
    ids = [str(v) for v in chosen.values() if v]
    rows = {str(r["id"]): r["file_path"] for r in conn.execute(
        "SELECT id, file_path FROM sign_images WHERE id::text = ANY(%s)", (ids,))} if ids else {}
    out = {kind: resolve(rows[str(v)]) for kind, v in chosen.items() if v and str(v) in rows}
    if "signature" not in out and sign.get("image_path"):
        out["signature"] = resolve(sign["image_path"])
    return out


def _fmt_range(r) -> str:
    if not r or r[0] is None:
        return "—"
    return str(r[0]) if r[0] == r[1] else f"{r[0]}–{r[1]}"


def fill_refs(html: str, refs: dict) -> str:
    def sub(m):
        t = _XREF_TARGET.search(m.group(0))
        if not t:
            return m.group(0)
        kind, target = t.groups()
        if not refs:
            label = "Pg. 000–000"            # first pass: reserve width
        else:
            label = "Pg. " + _fmt_range(refs.get(f"{kind}:{target}"))
        return f'<span class="xref" data-{kind}="{target}">{label}</span>'
    return _XREF.sub(sub, html)


def _render(body: str) -> tuple[bytes, int]:
    doc = HTML(string=f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head>"
                      f"<body>{body}</body></html>").render()
    return doc.write_pdf(), len(doc.pages)


def _cover_html(bid: dict, company: dict) -> str:
    meta = ((bid.get("analysis") or {}).get("extracted") or {}).get("meta") or {}
    title = meta.get("title") or bid["title"]
    due = meta.get("bid_due") or (bid["due_at"].strftime("%d %b %Y, %H:%M") if bid.get("due_at") else "")
    ref = bid.get("rfp_ref") or meta.get("rfp_ref")
    dept = bid.get("department") or meta.get("department") or ""
    return (
        f'<div class="cover"><div class="env">Envelope B: technical bid</div><div class="kind">TECHNICAL BID</div>'
        f'<div class="ttl">{escape(title)}</div><div class="ref">{"RFP No. " + str(escape(ref)) if ref else ""}</div>'
        f'<div class="box"><div>Submitted to<b>{escape(dept)}</b></div>'
        f'<div>Submitted by<b>{escape(company["name"].upper())}</b>{escape(company["city"])}'
        f'{"<br>CIN " + str(escape(company["cin"])) if company["cin"] else ""}</div></div>'
        f'<div class="due">{"Bid due date: " + str(escape(due)) if due else ""}</div></div>'
    )


def _index_html(sections: list[dict], refs: dict, rfp_ref: str | None) -> str:
    rows, n = [], 1
    for s in sections:
        if s["kind"] in ("cover", "index"):
            continue
        rows.append(f"<tr><td>{n}</td><td>{escape(s['title'])}</td><td>{_fmt_range(refs.get('sec:' + str(s['id'])))}</td></tr>")
        n += 1
    ref_line = f"<p>RFP No. {escape(rfp_ref)}</p>" if rfp_ref else ""
    return (f'<h2 class="sec-title">Index</h2>{ref_line}<table><thead><tr><th style="width:50px">S. No.</th>'
            f'<th>Particulars</th><th style="width:90px">Page no.</th></tr></thead><tbody>{"".join(rows)}</tbody></table>')


def _numbering(sections: list[dict], cfg: dict):
    skip = bool(cfg.get("skip_cover")) and bool(sections) and sections[0]["kind"] == "cover"
    start = int(cfg.get("start") or 1)

    def num(i: int):
        if skip and i == 0:
            return None
        return i - (1 if skip else 0) + start
    return num


def _compute_refs(sections, lengths, sec_docs, num) -> dict:
    refs, pos = {}, 0
    for sec, n in zip(sections, lengths):
        refs[f"sec:{sec['id']}"] = (num(pos), num(pos + n - 1))
        if sec["kind"] == "docs":
            docs = sec_docs.get(sec["id"], [])
            off = pos + n - sum(d["pages"] for d in docs)          # after the divider page, if there is one
            for d in docs:
                refs.setdefault(f"doc:{d['id']}", (num(off), num(off + d["pages"] - 1)))
                off += d["pages"]
        pos += n
    return refs


def signed_pages(settings: dict, kinds: list[str]) -> list[bool]:
    """Which pages carry the signature and stamp (the "Apply to" setting), by page kind (cover, docs…)."""
    sign = settings["sign"]
    apply = sign.get("apply") or "not_cover"
    chosen = _parse_range(sign.get("range", ""), len(kinds)) if apply in ("range", "current") else set()
    return [{"all": True, "not_cover": kind != "cover"}.get(apply, (i + 1) in chosen) for i, kind in enumerate(kinds)]


def sign_block_x(width: float, sign: dict) -> float:
    """Left edge of the 210 px signature block at the bottom of a page."""
    return width - (48 + 210) * PX if sign.get("position", "right") == "right" else 48 * PX


def _parse_range(text: str, total: int) -> set[int]:
    pages = set()
    for part in (text or "").split(","):
        m = re.match(r"^\s*(\d+)\s*(?:[-–]\s*(\d+))?\s*$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2) or m.group(1))
            pages.update(range(min(a, b), min(max(a, b), total) + 1))
    return pages


def _original_pdf(row: dict) -> Path:
    """A library document as uploaded: the PDF itself, or the plain conversion of an image or Word file. Never the
    OCR'd copy, which carries an added text layer."""
    original = resolve(row["file_path"])
    if original.suffix.lower() == ".pdf":
        return original
    converted = original.with_suffix(".converted.pdf")
    return converted if converted.exists() else resolve(row["pdf_path"] or row["file_path"])


def _needs_divider(sections: list[dict], i: int) -> bool:
    """Documents right after their own criterion's separator page are introduced by it; any other documents
    section (other statutory documents, CVs, power of attorney, one added in the builder) gets a divider page."""
    criterion = _criterion(sections[i]["title"])
    return not (criterion and i > 0 and _criterion(sections[i - 1]["title"]) == criterion)


def _divider_html(sec: dict, docs: list[dict], refs: dict) -> str:
    m = re.match(r"^annexure:\s*(.+)$", sec["title"], flags=re.I)
    kind, name = ("Annexure", m.group(1)) if m else ("Enclosures", sec["title"])
    rows = []
    for n, d in enumerate(docs, start=1):
        pages = _fmt_range(refs.get(f"doc:{d['id']}"))
        rows.append(f"<tr><td>{n}</td><td>{escape(d['title'])}</td><td>{pages}</td></tr>")
    return (f'<div class="separator"><div class="sep-kind">{kind}</div><div class="sep-clause">{escape(name)}</div>'
            f'<table class="sep-table"><thead><tr><th style="width:50px">S. No.</th><th>Document</th>'
            f'<th style="width:90px">Page no.</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')


def _overlay(sizes, info, settings, num, total_printed, images: dict | None = None) -> bytes:
    """Page number, signature and stamp per page, and the header on the bid's own pages (not on enclosed
    documents), at the prototype's positions. images: the signature and stamp image files (sign_images)."""
    f = _fonts()
    head, pn, sign = settings["header"], settings["page_numbers"], settings["sign"]
    signs = signed_pages(settings, [x["kind"] for x in info])
    signature, stamp = _image((images or {}).get("signature")), _image((images or {}).get("stamp"))

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for i, (w, h) in enumerate(sizes):
        c.setPageSize((w, h))
        kind = info[i]["kind"]
        if head.get("enabled") and kind not in ("cover", "docs"):
            c.setFont(f["regular"], 10 * PX)
            c.setFillColor(HexColor("#555555"))
            c.drawString(64 * PX, h - 40 * PX, head.get("left") or "")
            c.drawRightString(w - 64 * PX, h - 40 * PX, head.get("right") or "")
            c.setStrokeColor(HexColor("#c9c9c9"))
            c.setLineWidth(1 * PX)
            c.line(64 * PX, h - 48 * PX, w - 64 * PX, h - 48 * PX)
        n = num(i)
        if pn.get("enabled") and n is not None:
            label = str(pn.get("format") or "{n}").replace("{n}", str(n)).replace("{N}", str(total_printed))
            c.setFont(f["semibold"], 10.5 * PX)
            c.setFillColor(HexColor("#222222"))
            pos = pn.get("position", "center")
            if pos == "left":
                c.drawString(64 * PX, 41 * PX, label)
            elif pos == "right":
                c.drawRightString(w - 64 * PX, 41 * PX, label)
            elif pos == "top-right":
                c.drawRightString(w - 64 * PX, h - 23 * PX, label)
            else:
                c.drawCentredString(w / 2, 41 * PX, label)

        if signs[i] and (sign.get("enabled") or (sign.get("stamp") and stamp is not None)):
            x0 = sign_block_x(w, sign)
            y0 = 22 * PX
            if sign.get("stamp") and stamp is not None:
                c.drawImage(stamp, x0 + 4 * PX, y0 + 4 * PX, 86 * PX, 86 * PX, mask="auto", preserveAspectRatio=True, anchor="c")
            if sign.get("enabled"):
                sx, sy, sw, sh = x0 + 74 * PX, y0 + 24 * PX, 130 * PX, 52 * PX
                if signature is not None:
                    c.drawImage(signature, sx, sy, sw, sh, mask="auto", preserveAspectRatio=True, anchor="s")
                c.setStrokeColor(HexColor("#444444"))
                c.setLineWidth(1 * PX)
                c.line(sx, y0 + 20 * PX, sx + sw, y0 + 20 * PX)
                c.setFont(f["semibold"], 9.5 * PX)
                c.setFillColor(HexColor("#222222"))
                c.drawCentredString(sx + sw / 2, y0 + 9 * PX, sign.get("caption") or "")
        c.showPage()
    c.save()
    return buf.getvalue()


def _sign_pdf(path: Path) -> None:
    s = get_settings()
    if not s.sign_p12_path:
        return
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import signers

    signer = signers.SimpleSigner.load_pkcs12(pfx_file=s.sign_p12_path,
                                              passphrase=(s.sign_p12_password or "").encode() or None)
    signed = path.with_suffix(".signed.pdf")
    with open(path, "rb") as inf, open(signed, "wb") as outf:
        signers.sign_pdf(IncrementalPdfFileWriter(inf), signers.PdfSignatureMetadata(field_name="BidCraftSignature"),
                         signer=signer, output=outf)
    signed.replace(path)


def assemble(conn: psycopg.Connection, bid_id) -> tuple[Path, int]:
    s = get_settings()
    company = {"name": s.company_name, "city": s.company_city, "cin": s.company_cin}
    bid = conn.execute("SELECT * FROM bids WHERE id = %s", (bid_id,)).fetchone()
    settings = merged_settings(bid["settings"])
    if not settings["header"].get("left"):
        settings["header"]["left"] = bid.get("rfp_ref") or bid["title"][:60]
    if not settings["header"].get("right"):
        settings["header"]["right"] = f"Technical bid – {company['name']}"

    sections = conn.execute(
        "SELECT * FROM bid_sections WHERE bid_id = %s AND include ORDER BY position", (bid_id,)).fetchall()
    if not sections:
        raise ValueError("The draft has no sections to assemble")
    sec_docs: dict = {}
    for row in conn.execute(
        "SELECT sd.section_id, d.id, d.title, d.pdf_path, d.file_path FROM bid_section_documents sd "
        "JOIN library_documents d ON d.id = sd.document_id JOIN bid_sections s ON s.id = sd.section_id "
        # a document being read again still has its earlier PDF: keep it in the bid rather than drop it silently
        "WHERE s.bid_id = %s AND (d.status = 'ready' OR d.pdf_path IS NOT NULL) ORDER BY sd.section_id, sd.position",
        (bid_id,)
    ).fetchall():
        path = _original_pdf(row)
        try:
            with pikepdf.open(path) as p:
                pages = len(p.pages)
        except (pikepdf.PdfError, OSError) as exc:
            raise ValueError(f"The library document “{row['title']}” cannot be opened as a PDF ({exc}); "
                             "read it again or remove it from the draft") from exc
        sec_docs.setdefault(row["section_id"], []).append({"id": row["id"], "title": row["title"], "path": path, "pages": pages})

    num = _numbering(sections, settings["page_numbers"])
    dividers = {sec["id"] for i, sec in enumerate(sections) if sec["kind"] == "docs" and _needs_divider(sections, i)}
    refs: dict = {}
    rendered: dict = {}
    for _ in range(4):                     # re-render until page numbers settle
        rendered, lengths = {}, []
        for sec in sections:
            docs = sec_docs.get(sec["id"], [])
            if sec["kind"] == "docs" and docs:
                n = 0
                if sec["id"] in dividers:
                    rendered[sec["id"]], n = _render(_divider_html(sec, docs, refs))
                lengths.append(n + sum(d["pages"] for d in docs))
                continue
            if sec["kind"] == "cover":
                body = _cover_html(bid, company)
            elif sec["kind"] == "index":
                body = _index_html(sections, refs, bid.get("rfp_ref"))
            elif sec["kind"] == "docs":
                body = f'<h2 class="sec-title">{escape(sec["title"])}</h2><p class="empty">No documents attached.</p>'
            else:
                body = f'<h2 class="sec-title">{escape(sec["title"])}</h2>' + fill_refs(clean_html(sec["html"]), refs)
            rendered[sec["id"]], n = _render(body)
            lengths.append(n)
        new_refs = _compute_refs(sections, lengths, sec_docs, num)
        if new_refs == refs:
            break
        refs = new_refs

    total_pages = sum(lengths)
    total_printed = num(total_pages - 1) or total_pages
    out = pikepdf.new()
    opened: list[pikepdf.Pdf] = []
    info, starts = [], []
    parts: list[dict] = []          # the bid folder: one file per section, one per annexure document
    for sec in sections:
        starts.append((sec["title"], len(out.pages)))
        docs = sec_docs.get(sec["id"], [])
        if sec["kind"] == "docs" and docs:
            if sec["id"] in rendered:            # its divider page
                src = pikepdf.open(io.BytesIO(rendered[sec["id"]]))
                opened.append(src)
                for page in src.pages:
                    out.pages.append(page)
                    info.append({"kind": "divider"})
            for d in docs:
                src = pikepdf.open(d["path"])
                opened.append(src)
                first = len(out.pages)
                for page in src.pages:
                    out.pages.append(page)
                    info.append({"kind": "docs"})
                parts.append({"title": f"{_annex_group(sec['title'])} - {d['title']}", "kind": "document",
                              "group": _criterion(sec["title"]), "pages": list(range(first, len(out.pages)))})
        else:
            src = pikepdf.open(io.BytesIO(rendered[sec["id"]]))
            opened.append(src)
            first = len(out.pages)
            for page in src.pages:
                out.pages.append(page)
                info.append({"kind": sec["kind"]})
            parts.append({"title": sec["title"], "kind": "section", "group": _criterion(sec["title"]),
                          "pages": list(range(first, len(out.pages)))})

    target_dir = s.files_root / "bids" / str(bid_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    for leftover in ("dsc-pending.pdf", "dsc-session.json"):      # a DSC signing started on the previous build
        (target_dir / leftover).unlink(missing_ok=True)
    raw_path = target_dir / "technical-bid.raw.pdf"      # without stamps: the web preview overlays them live
    out.save(raw_path)
    pos, sec_meta = 0, []
    for sec, n in zip(sections, lengths):
        sec_meta.append({"id": str(sec["id"]), "title": sec["title"], "kind": sec["kind"], "start": pos, "count": n})
        pos += n
    (target_dir / "pages.json").write_text(json.dumps({
        "pages": [{"kind": p["kind"]} for p in info],
        "sections": sec_meta,
        "numbering": {"start": int(settings["page_numbers"].get("start") or 1),
                      "skip_cover": bool(settings["page_numbers"].get("skip_cover"))},
        "built_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")

    sizes = []
    for page in out.pages:
        box = [float(v) for v in page.mediabox]
        sizes.append((box[2] - box[0], box[3] - box[1]))
    overlay = pikepdf.open(io.BytesIO(_overlay(sizes, info, settings, num, total_printed, sign_images(conn, settings["sign"]))))
    opened.append(overlay)
    for i, page in enumerate(out.pages):
        page.add_overlay(overlay.pages[i])

    with out.open_outline() as outline:
        for title, start in starts:
            outline.root.append(pikepdf.OutlineItem(title, start))

    target = target_dir / "technical-bid.pdf"
    out.save(target)
    _sign_pdf(target)
    export_folder(target_dir, out, parts, target, bid)
    for p in opened:
        p.close()
    return target, len(sizes)


def _criterion(title: str) -> str | None:
    """'PQ 6.1 (1)' for a PQ / TEC separator or its documents (titles 'PQ 6.1 (1) – …'), else None."""
    m = re.match(r"^((?:PQ|TEC) .+?) – ", title)
    return m.group(1) if m else None


def _annex_group(title: str) -> str:
    return re.sub(r"^annexure:\s*", "", title, flags=re.I).strip() or "Annexure"


def _safe_name(text: str, limit: int = 110) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", text)
    return re.sub(r"\s+", " ", name).strip(" .")[:limit] or "document"


def export_folder(target_dir: Path, out: pikepdf.Pdf, parts: list[dict], complete: Path, bid: dict) -> list[dict]:
    """The bid folder: every part of the bid as its own numbered PDF (e-procurement portals usually ask for
    separate uploads), the complete signed bid, and the RFP's own documents; plus the whole folder as a ZIP.
    Parts are cut from the stamped pages, so their page numbers match the index of the complete bid."""
    folder = target_dir / "folder"
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "RFP documents").mkdir(parents=True)
    ref = _safe_name(bid.get("rfp_ref") or bid["title"], 60)
    files = [{"name": f"00 Technical bid (complete) - {ref}.pdf", "title": "Technical bid (complete, signed)",
              "kind": "complete", "pages": len(out.pages)}]
    shutil.copyfile(complete, folder / files[0]["name"])
    merged: list[dict] = []        # a criterion's separator and its documents make one file
    for part in parts:
        if merged and part.get("group") and merged[-1].get("group") == part["group"]:
            merged[-1]["pages"] = merged[-1]["pages"] + part["pages"]
            merged[-1]["kind"] = "criterion"
        else:
            merged.append(dict(part))
    for n, part in enumerate(merged, 1):
        if not part["pages"]:
            continue
        name = f"{n:02d} {_safe_name(part['title'])}.pdf"
        piece = pikepdf.new()
        for i in part["pages"]:
            piece.pages.append(out.pages[i])
        piece.save(folder / name)
        piece.close()
        files.append({"name": name, "title": part["title"], "kind": part["kind"], "pages": len(part["pages"])})
    for f in bid.get("rfp_files") or []:
        name = f"RFP documents/{_safe_name(f.get('name') or 'RFP.pdf', 140)}"
        source = resolve(f["path"])
        if source.exists() and not (folder / name).exists():
            shutil.copyfile(source, folder / name)
            files.append({"name": name, "title": f.get("name") or "RFP", "kind": "rfp", "pages": None})
    for f in files:
        f["size"] = (folder / f["name"]).stat().st_size
    zip_path = target_dir / "bid-folder.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(folder / f["name"], f"{ref} - Bid documents/{f['name']}")
    (target_dir / "folder.json").write_text(json.dumps({
        "folder": f"{ref} - Bid documents", "files": files, "zip_size": zip_path.stat().st_size,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }), encoding="utf-8")
    return files


def run(conn: psycopg.Connection, job: dict) -> dict:
    bid_id = job["payload"]["bid_id"]
    conn.execute("UPDATE bids SET status = 'assembling', error = NULL, updated_at = now() WHERE id = %s", (bid_id,))
    conn.commit()
    path, pages = assemble(conn, bid_id)
    conn.execute("UPDATE bids SET status = 'ready', output_pdf_path = %s, output_page_count = %s, updated_at = now() "
                 "WHERE id = %s", (relative(path), pages, bid_id))
    audit(conn, "worker", "bid.assembled", "bid", bid_id, {"pages": pages})
    conn.commit()
    return {"pages": pages, "path": relative(path)}
