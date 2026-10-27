import io

import pikepdf
from PIL import Image
from pypdf import PdfReader

from app.ingest.extract import image_to_pdf
from app.pdf import assembler
from app.pdf.assembler import _needs_divider, _numbering, _original_pdf, _overlay, merged_settings


def _images(pdf_path):
    with pikepdf.open(pdf_path) as pdf:
        return [(raw.read_raw_bytes(), pikepdf.PdfImage(raw).as_pil_image().copy())
                for page in pdf.pages for raw in page.images.values()]


def test_a_photo_goes_into_its_pdf_byte_for_byte(tmp_path):
    src = tmp_path / "po.jpeg"
    Image.new("RGB", (1200, 1600), (200, 190, 180)).save(src, "JPEG", quality=70)
    out = tmp_path / "po.converted.pdf"
    image_to_pdf(src, out)
    (raw, _), = _images(out)
    assert raw == src.read_bytes()


def test_other_images_go_in_losslessly_at_full_size(tmp_path):
    src = tmp_path / "scan.png"
    im = Image.new("RGB", (3000, 4000), "white")
    im.putpixel((10, 10), (12, 34, 56))
    im.save(src)
    out = tmp_path / "scan.converted.pdf"
    image_to_pdf(src, out)
    (_, embedded), = _images(out)
    assert embedded.size == (3000, 4000) and embedded.convert("RGB").getpixel((10, 10)) == (12, 34, 56)


def test_an_enclosed_document_gets_only_the_page_number_signature_and_seal():
    settings = merged_settings(None)
    settings["header"].update(left="RFP-REF-77", right="Technical bid")
    num = _numbering([{"kind": "text"}], settings["page_numbers"])
    pdf = _overlay([(595, 842)] * 2, [{"kind": "text"}, {"kind": "docs"}], settings, num, 2)
    own, enclosed = (p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "RFP-REF-77" in own and "Page 1 of 2" in own
    assert "RFP-REF-77" not in enclosed and "Technical bid" not in enclosed and "Page 2 of 2" in enclosed


def test_documents_without_their_own_separator_get_a_divider_page():
    sections = [{"title": "PQ 6.1 (1) – Legal entity"}, {"title": "PQ 6.1 (1) – supporting documents"},
                {"title": "TEC 2 – Approach and methodology"}, {"title": "Annexure: Other statutory documents"},
                {"title": "Annexure: CVs of key personnel"}, {"title": "Scanned undertakings"}]
    assert [_needs_divider(sections, i) for i in (1, 3, 4, 5)] == [False, True, True, True]


def test_the_bid_encloses_the_uploaded_file_not_the_ocr_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(assembler, "resolve", lambda p: tmp_path / p)
    assert _original_pdf({"file_path": "a.pdf", "pdf_path": "a.ocr.pdf"}) == tmp_path / "a.pdf"
    (tmp_path / "b.converted.pdf").write_bytes(b"%PDF")
    assert _original_pdf({"file_path": "b.jpeg", "pdf_path": "b.converted.ocr.pdf"}) == tmp_path / "b.converted.pdf"
    assert _original_pdf({"file_path": "c.docx", "pdf_path": "c.converted.pdf"}) == tmp_path / "c.converted.pdf"


def test_the_chosen_signature_and_stamp_images_go_on_each_page(tmp_path):
    sig, stamp = tmp_path / "sig.png", tmp_path / "stamp.png"
    Image.new("RGBA", (300, 120), (20, 40, 120, 255)).save(sig)
    Image.new("RGBA", (200, 200), (30, 60, 160, 255)).save(stamp)
    settings = merged_settings(None)
    num = _numbering([{"kind": "text"}], settings["page_numbers"])
    with pikepdf.open(io.BytesIO(_overlay([(595, 842)], [{"kind": "docs"}], settings, num, 1, {"signature": sig, "stamp": stamp}))) as pdf:
        assert len(pdf.pages[0].images) == 2
    with pikepdf.open(io.BytesIO(_overlay([(595, 842)], [{"kind": "docs"}], settings, num, 1))) as pdf:
        assert len(pdf.pages[0].images) == 0          # nothing drawn in place of a stamp image: no generated seal
