from app.drafting.drafter import pq_compliance, pq_documents
from app.rfp.matcher import apply_decisions, decision_key


def _match():
    return {
        "pq": [{"clause": "3.1", "text": "ISO 9001 certificate", "category": "certification", "status": "review",
                "evidence": ["a"], "note": "Possible evidence found by search; confirm"},
               {"clause": "3.2", "text": "Turnover", "category": "turnover", "status": "gap", "evidence": [], "note": "x"}],
        "personnel": [{"role": "Project Manager", "qualification": "MBA", "status": "partial", "document_id": "cv1",
                       "name": "A", "years": 5, "note": "5 years against 8 required"}],
    }


def test_a_confirmed_result_replaces_the_automatic_one_which_is_kept():
    match = _match()
    apply_decisions(match, {
        "pq": {"3.1|ISO 9001 certificate": {"status": "met", "evidence": ["b"], "note": "", "by": "bd@x.in", "at": "2026-10-25T10:00:00Z"}},
        "personnel": {"Project Manager|MBA": {"status": "met", "document_id": "cv2", "name": "B", "years": 9, "note": None,
                                              "by": "bd@x.in", "at": "2026-10-25T10:01:00Z"}},
    })
    row = match["pq"][0]
    assert (row["status"], row["evidence"], row["decided"]["by"]) == ("met", ["b"], "bd@x.in")
    assert row["auto"] == {"status": "review", "evidence": ["a"], "note": "Possible evidence found by search; confirm"}
    assert match["pq"][1]["status"] == "gap" and "decided" not in match["pq"][1]
    assert match["summary"] == {"met": 1, "gap": 1}
    person = match["personnel"][0]
    assert (person["document_id"], person["name"], person["years"], person["note"]) == ("cv2", "B", 9, "")
    assert person["auto"]["document_id"] == "cv1"


def test_decisions_are_found_by_clause_and_text():
    assert decision_key("pq", {"clause": None, "text": "Turnover"}) == "|Turnover"
    assert decision_key("personnel", {"role": "Team Lead", "qualification": "B.Tech"}) == "Team Lead|B.Tech"
    match = _match()
    apply_decisions(match, {"pq": {"3.1|ISO 9001 certificate (amended)": {"status": "met", "evidence": []}}})
    assert match["pq"][0]["status"] == "review"           # the criterion changed: confirm it again


def test_documents_chosen_by_a_person_are_enclosed_exactly():
    docs = {"gst": {"doc_type": "gst", "category": "statutory"}, "pan": {"doc_type": "pan", "category": "statutory"}}
    row = {"clause": "1", "text": "Valid GST and PAN registration", "category": "registration", "evidence": ["pan"],
           "status": "met", "decided": {"by": "bd@x.in", "at": "2026-10-25T10:00:00Z"}}
    assert pq_documents(row, {}, docs, ["gst", "pan"]) == ["pan"]
    assert pq_compliance(row, False) == "Complied"
    row.pop("decided")
    assert set(pq_documents(row, {}, docs, ["gst", "pan"])) == {"pan", "gst"}
