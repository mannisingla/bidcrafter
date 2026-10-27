import time

from app.ingest.extract import supplement


def test_ocr_adds_only_text_found_in_images():
    text = "This is to affirm that\nUneecops Technologies Limited\nHas been appraised at"
    ocr = "ot Q\nThis Is to affirm that\nUneecops Technologies Limited\nHas been appraised at\nAppraisal # 72038 | Exp. Aug 09, 2027"
    out = supplement(text, ocr)
    assert out.startswith(text)
    assert out.endswith("Appraisal # 72038 | Exp. Aug 09, 2027")
    assert out.count("Uneecops") == 1 and "ot Q" not in out


def test_nothing_added_when_ocr_finds_nothing_new():
    assert supplement("Line one\nLine two", "line one\nLINE TWO") == "Line one\nLine two"


def _scanned(monkeypatch, pages, read):
    from app.ingest import extract

    def ocr_page(pdf, number, languages, tmp):
        time.sleep(0.05)
        read.append(number)
        return f"OCR text of page {number}"
    monkeypatch.setattr(extract, "_read", lambda pdf: list(pages))
    monkeypatch.setattr(extract, "_ocr_page", ocr_page)
    return extract


def test_scanned_pages_are_ocr_d_and_a_page_with_a_text_layer_keeps_it(monkeypatch):
    read = []
    typed = "Covering letter typed in Word, signed and scanned with the order"
    extract = _scanned(monkeypatch, ["", typed, "", ""], read)
    _, pages = extract.extract_pages(extract.Path("scan.pdf"))
    assert pages == ["OCR text of page 1", typed, "OCR text of page 3", "OCR text of page 4"]
    assert sorted(read) == [1, 3, 4]


def test_reading_stops_when_the_document_is_deleted(monkeypatch):
    read = []
    extract = _scanned(monkeypatch, [""] * 8, read)
    seen = []
    def progress(done, total):
        seen.append((done, total))
        return done < 1                     # deleted after the first page
    try:
        extract.extract_pages(extract.Path("scan.pdf"), progress)
        raise AssertionError("expected Cancelled")
    except extract.Cancelled:
        pass
    assert seen[0] == (0, 8) and len(read) < 8


def test_nothing_is_read_when_the_document_is_already_gone(monkeypatch):
    read = []
    extract = _scanned(monkeypatch, ["", ""], read)
    try:
        extract.extract_pages(extract.Path("scan.pdf"), lambda done, total: False)
        raise AssertionError("expected Cancelled")
    except extract.Cancelled:
        pass
    assert read == []


def test_a_photo_on_a_white_page_is_cropped_to_the_paper_before_ocr(tmp_path):
    from PIL import Image
    from app.ingest.extract import _trim
    page = Image.new("RGB", (200, 300), "white")
    page.paste((200, 200, 200), (30, 50, 170, 250))          # grey paper of a phone photo
    page.paste((20, 20, 20), (60, 100, 140, 110))             # a line of text
    path = tmp_path / "page.png"
    page.save(path)
    _trim(path)
    with Image.open(path) as out:
        assert out.mode == "L" and out.size == (140, 200)
        assert out.getpixel((0, 0)) == 200 and out.getpixel((139, 199)) == 200
