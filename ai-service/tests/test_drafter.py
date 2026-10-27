from datetime import date

from app.drafting.drafter import standard_documents


def _doc(doc_type, title, standard=None, valid=None, category="statutory"):
    return {"doc_type": doc_type, "title": title, "category": category, "valid_until": valid,
            "metadata": {"standard": standard} if standard else {}}


DOCS = {
    "roc": _doc("incorporation", "UTL_ROC"), "pan": _doc("pan", "PAN_Card_UTL"),
    "gst1": _doc("gst", "GST-UTL_Delhi Address"), "gst2": _doc("gst", "GST UTL NOIDA UP_3 Pages"),
    "moa": _doc("moa_aoa", "UTL MOA & AOA"),
    "cmmi": _doc("certification", "UTL CMMi 5_2024 to 2027", "CMMI", date(2027, 8, 9)),
    "iso27": _doc("certification", "UTL - ISO 27K", "ISO 27001:2022", date(2028, 11, 5)),
    "iso20": _doc("certification", "UTL - ISO 20K", "ISO 20000-1:2018", date(2026, 11, 27)),
    "iso14": _doc("certification", "UTL-ISO 14001", "ISO 14001"),
    "wo": _doc("work_order", "AXOM", category="experience"),
}
RFP = {"pq_criteria": [{"text": "The Bidder shall hold valid ISO 9001, ISO/IEC 27001 and CMMI level 3/5 certifications"}],
       "tech_criteria": [{"parameter": "Quality & Information-Security Certifications", "basis": "ISO 20000: 5 marks"}]}


def test_registration_documents_always_and_certifications_the_rfp_asks_for():
    got = standard_documents(DOCS, RFP, date(2026, 11, 11))
    assert {"roc", "pan", "gst1", "gst2"} <= set(got)            # every GST registration, not only one
    assert {"cmmi", "iso27", "iso20"} <= set(got)                # named in the PQ / TEC criteria
    assert "iso14" not in got and "moa" not in got and "wo" not in got


def test_certifications_expired_by_the_due_date_are_left_out():
    got = standard_documents(DOCS, RFP, date(2026, 12, 15))
    assert "iso20" not in got and "cmmi" in got


def test_a_year_in_the_standard_does_not_match_a_year_in_the_rfp():
    rfp = {"pq_criteria": [{"text": "Average turnover for 2018-19 to 2022-23"}], "tech_criteria": []}
    assert "iso20" not in standard_documents(DOCS, rfp, date(2026, 11, 11))


from app.drafting.drafter import _render, pq_compliance, pq_documents, tec_documents, tec_topics  # noqa: E402


def test_tec_parameters_map_to_their_topics():
    topics = {p: tec_topics({"parameter": p, "basis": ""}) for p in [
        "Legal Entity (years in operation)", "Average annual turnover (software development)",
        "Relevant project experience (Health/beneficiary system)", "Quality & Information-Security Certifications",
        "Resource Strength (technical/managerial professionals)", "POC and Presentation (demo & proposal)",
        "Qualifications and experience of key personnel", "Approach, methodology and work plan"]}
    assert topics["Legal Entity (years in operation)"] == ["legal"]
    assert topics["Average annual turnover (software development)"] == ["finance"]
    assert topics["Relevant project experience (Health/beneficiary system)"] == ["projects"]
    assert "certification" in topics["Quality & Information-Security Certifications"]
    assert topics["Resource Strength (technical/managerial professionals)"] == ["people"]
    assert "presentation" in topics["POC and Presentation (demo & proposal)"]
    assert topics["Qualifications and experience of key personnel"] == ["people"]
    assert topics["Approach, methodology and work plan"] == ["approach"]


MATCH = {"pq": [{"clause": "6.1 (1)", "text": "Company registered under the Companies Act for five years", "category": "registration",
                 "status": "met", "evidence": ["roc"], "note": "Incorporated 1995"},
                {"clause": "6.1 (2)", "text": "valid PAN, GST Registration, EPF and ESI", "category": "tax_registration",
                 "status": "met", "evidence": ["pan"], "note": ""},
                {"clause": "6.1 (5)", "text": "one project of 5 crore", "category": "project_experience",
                 "status": "met", "evidence": ["wo"], "note": ""}],
         "personnel": []}


def test_pq_documents_add_every_registration_the_clause_names():
    standard = standard_documents(DOCS, RFP, date(2026, 11, 11))
    assert pq_documents(MATCH["pq"][1], {}, DOCS, standard) == ["pan", "gst1", "gst2"]
    assert pq_documents(MATCH["pq"][0], {"evidence_expected": "Certificate of Incorporation, PAN and GST"}, DOCS, standard)[:2] == ["roc", "pan"]


def test_tec_documents_come_from_the_matching_pq_evidence():
    standard = standard_documents(DOCS, RFP, date(2026, 11, 11))
    assert tec_documents(["projects"], MATCH, DOCS, standard) == ["wo"]
    assert set(tec_documents(["certification"], MATCH, DOCS, standard)) == {"cmmi", "iso27", "iso20"}
    assert tec_documents(["legal"], MATCH, DOCS, standard) == ["roc"]


def test_compliance_wording():
    assert pq_compliance({"status": "met"}, False) == "Complied"
    assert pq_compliance({"status": "undertaking"}, True).startswith("Complied – undertaking")
    assert "to be confirmed" in str(pq_compliance({"status": "review"}, False))


def test_separator_page_names_the_clause_compliance_and_page_references():
    html = _render("separator", kind_label="Pre-qualification criterion", clause="6.1 (1)", text="Legal entity",
                   facts=[("Documents asked for by the RFP", "Certificate of Incorporation")], compliance="Complied",
                   documents=[{"id": "roc", "title": "UTL_ROC", "earlier": False}], sections=[], remarks="Incorporated 1995")
    assert 'class="separator"' in html and "Clause 6.1 (1)" in html and "Complied" in html
    assert 'data-doc="roc"' in html and "Incorporated 1995" in html
