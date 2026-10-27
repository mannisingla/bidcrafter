import json
import zipfile

import pikepdf

from app.pdf.assembler import export_folder


def test_bid_folder_has_the_complete_bid_one_file_per_part_and_a_zip(tmp_path):
    out = pikepdf.new()
    for _ in range(6):
        out.add_blank_page()
    complete = tmp_path / "technical-bid.pdf"
    out.save(complete)
    parts = [{"title": "Covering letter", "kind": "section", "pages": [0]},
             {"title": "Registration and tax documents - PAN_Card_UTL", "kind": "document", "pages": [1, 2]},
             {"title": "Quality / security: CMMI 5?", "kind": "document", "pages": [3, 4, 5]}]
    bid = {"title": "BGHS SI", "rfp_ref": "NIT/BSEDC/08/30/2026-27", "rfp_files": []}

    files = export_folder(tmp_path, out, parts, complete, bid)

    names = [f["name"] for f in files]
    assert names[0] == "00 Technical bid (complete) - NIT BSEDC 08 30 2026-27.pdf"
    assert names[1:] == ["01 Covering letter.pdf", "02 Registration and tax documents - PAN_Card_UTL.pdf",
                         "03 Quality security CMMI 5.pdf"]
    assert [f["pages"] for f in files] == [6, 1, 2, 3]
    for f in files:
        with pikepdf.open(tmp_path / "folder" / f["name"]) as p:
            assert len(p.pages) == f["pages"]
    index = json.loads((tmp_path / "folder.json").read_text())
    assert index["folder"] == "NIT BSEDC 08 30 2026-27 - Bid documents" and len(index["files"]) == 4
    with zipfile.ZipFile(tmp_path / "bid-folder.zip") as z:
        assert sorted(z.namelist()) == sorted(f"{index['folder']}/{n}" for n in names)


def test_a_second_build_replaces_the_folder(tmp_path):
    out = pikepdf.new()
    out.add_blank_page()
    complete = tmp_path / "technical-bid.pdf"
    out.save(complete)
    bid = {"title": "T", "rfp_ref": None, "rfp_files": []}
    export_folder(tmp_path, out, [{"title": "Old section", "kind": "section", "pages": [0]}], complete, bid)
    export_folder(tmp_path, out, [{"title": "New section", "kind": "section", "pages": [0]}], complete, bid)
    assert sorted(p.name for p in (tmp_path / "folder").glob("*.pdf")) == ["00 Technical bid (complete) - T.pdf", "01 New section.pdf"]


def test_a_criterion_and_its_documents_make_one_file(tmp_path):
    out = pikepdf.new()
    for _ in range(5):
        out.add_blank_page()
    complete = tmp_path / "technical-bid.pdf"
    out.save(complete)
    parts = [{"title": "PQ 6.1 (1) – Legal entity", "kind": "section", "group": "PQ 6.1 (1)", "pages": [0]},
             {"title": "PQ 6.1 (1) – supporting documents - UTL_ROC", "kind": "document", "group": "PQ 6.1 (1)", "pages": [1, 2]},
             {"title": "PQ 6.1 (2) – Statutory registrations", "kind": "section", "group": "PQ 6.1 (2)", "pages": [3]},
             {"title": "Undertaking", "kind": "section", "group": None, "pages": [4]}]
    files = export_folder(tmp_path, out, parts, complete, {"title": "T", "rfp_ref": "R1", "rfp_files": []})
    assert [(f["name"], f["pages"], f["kind"]) for f in files[1:]] == [
        ("01 PQ 6.1 (1) – Legal entity.pdf", 3, "criterion"), ("02 PQ 6.1 (2) – Statutory registrations.pdf", 1, "section"),
        ("03 Undertaking.pdf", 1, "section")]
