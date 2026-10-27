from app.ingest.classify import suggest
from app.ingest.project import extracts_from, merge, pick_sections, strip_payment_details
from app.privacy.redactor import Redactor

ANSWER = {"project_title": "Integrated Finance Management System", "client": "Finance Department",
          "state_ministry": "Government of Odisha", "description": "Design, develop and maintain IFMS.",
          "duration": "36 months", "tags": ["Software Development", "AMC", "payment gateway"]}


def test_merge_fills_only_empty_fields_and_adds_new_tags():
    meta, filled = merge({"client": "Odisha Finance", "domains": ["Payment Gateway"], "value_cr": 12.6}, ANSWER)
    assert meta["client"] == "Odisha Finance"                  # what a person entered wins
    assert meta["project"] == ANSWER["project_title"]
    assert meta["ministry"] == "Government of Odisha"
    assert meta["scope"].startswith("Design")
    assert meta["domains"] == ["Payment Gateway", "Software Development", "AMC"]
    assert meta["value_cr"] == 12.6
    assert "client" not in filled and {"project", "ministry", "scope", "duration", "domains"} <= set(meta["_auto"])


def test_merge_ignores_empty_answers():
    meta, filled = merge({}, {k: "" for k in ANSWER} | {"tags": []})
    assert filled == [] and meta == {}


def test_pick_sections_prefers_scope_and_background():
    pages = ["REQUEST FOR PROPOSAL\nSelection of agency\n",
             "1. INSTRUCTIONS TO BIDDERS\nBid security and submission rules.\n"
             "2. BACKGROUND\nThe department wants to modernise treasury operations.\n"
             "3. SCOPE OF WORK\nThe agency shall design, develop and maintain the system.\n"]
    headings = [s["heading"] for s in pick_sections(pages)]
    assert headings[0] == "Opening pages" or "REQUEST" in headings[0]
    assert any("SCOPE OF WORK" in h for h in headings) and any("BACKGROUND" in h for h in headings)
    assert not any("INSTRUCTIONS" in h for h in headings)


def test_po_date_from_work_order():
    s = suggest("experience", "po", "PURCHASE ORDER\nPO No. 45/IT/2023 Dated: 14.03.2023\nTotal Rs. 4.2 crore")
    assert s["doc_type"] == "work_order"
    assert s["metadata"]["po_date"] == "2023-03-14"


def test_merge_replaces_values_filled_automatically_but_not_by_a_person():
    meta, filled = merge({"value_cr": 0.07, "client": "NICSI", "_auto": ["value_cr"]},
                         {"value_inr": 733400.0, "client": "ACASH (through NICSI)", "po_date": "2016-04-12"})
    assert meta["value_cr"] == 0.0733 and "value_cr" in filled
    assert meta["client"] == "NICSI"
    assert meta["po_date"] == "2016-04-12" and meta["period"] == "Apr 2016 – ongoing"


def test_merge_derives_completion_and_ignores_bad_dates():
    meta, _ = merge({}, {"completion_date": "2017-01-31", "po_date": "31/01/2016"})
    assert meta["end_year"] == 2017 and meta["completed"] is True
    assert "po_date" not in meta


def test_which_documents_fill_a_project():
    assert extracts_from("experience", "work_order", {})
    assert not extracts_from("experience", "work_order", {"work_order_id": "p"})
    assert extracts_from("experience", "completion_certificate", {"work_order_id": "p"})
    assert extracts_from("experience", "project_rfp", {"work_order_id": "p"})
    assert not extracts_from("experience", "evidence", {"work_order_id": "p"})
    assert not extracts_from("statutory", "work_order", {})


def test_order_value_from_total_line_and_no_completion_guess():
    text = ("Work Order\nSubject: placement of order. Completion of user acceptance testing in 2 months.\n"
            "A Total of Above (Exclusive of Tax) a 5,66,400.00\nTotal Amount (A+B+C) (Exclusive of Tax) 7,33,400.00")
    s = suggest("experience", "po", text, "work_order")
    assert s["doc_type"] == "work_order"
    assert s["metadata"]["value_cr"] == 0.0733
    assert "completed" not in s["metadata"]


def test_payment_details_are_left_out_of_order_text():
    text = ("Work Name : Annual Technical Services for Web Based MIS\nTotal(Rs.) 1,43,333.99\n"
            "(i) Payment can be made through RTGS/NEFT\nIFSC Code: ICIC0000104\nA/C No.: 123456789012\n"
            "Branch: ICICI BANK CMS\nGross Amount Payable 2,19,638.00")
    out = strip_payment_details(text)
    assert "123456789012" not in out and "IFSC" not in out and "RTGS" not in out
    assert "Work Name" in out and "1,43,333.99" in out and "2,19,638.00" in out
    r = Redactor(actions={"BANK_ACCOUNT": "block"})
    r.redact(out)
    assert not r.blocked


def test_value_is_converted_from_rupees_and_kept_when_entered_by_a_person():
    meta, _ = merge({}, {"value_inr": 143333.99})
    assert meta["value_cr"] == 0.0143
    meta, filled = merge({"value_cr": 0.02}, {"value_inr": 143333.99})
    assert meta["value_cr"] == 0.02 and "value_cr" not in filled


def test_po_number_from_order_and_invoice():
    po = suggest("experience", "po", "PURCHASE ORDER\nPO No. 45/IT/2023 Dated: 14.03.2023\nTotal Rs. 4.2 crore", "work_order")
    assert po["metadata"]["po_number"] == "45/IT/2023"
    pi = suggest("experience", "pi", "PROFORMA INVOICE\nTender No.: NICSI/WEBSITE/2015/42\nInvoice No.: PWDDL210043 "
                 "Ref. No.: 121823(WD)\nDate: 24/06/2021", "work_order")
    assert pi["metadata"]["po_number"] == "PWDDL210043"


def test_merge_fills_po_number():
    meta, filled = merge({}, {"po_number": "NICSI/11/16600/2016"})
    assert meta["po_number"] == "NICSI/11/16600/2016" and "po_number" in filled
