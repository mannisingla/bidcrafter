from app.rfp import analyzer

RFP = ["REQUEST FOR PROPOSAL\nSelection of System Integrator\nDate of publish: 01/11/2026\nBid due date: 20/11/2026 15:00 hrs",
       "1. SCOPE OF WORK\nThe agency shall design, develop and maintain the portal for five years.\n"
       "2. PRE-QUALIFICATION CRITERIA\nThe bidder shall have an average turnover of Rs. 50 crore in the last three years.\n"
       "3. TECHNICAL EVALUATION CRITERIA\nProject experience: 30 marks. Approach and methodology: 20 marks."]
CORRIGENDUM = ["CORRIGENDUM 1\nThe bid due date is extended to 30/11/2026 15:00 hrs."]


def test_amendments_are_sent_whole_after_the_bid_data_and_every_label_names_its_document(monkeypatch):
    monkeypatch.setattr(analyzer.store, "search", lambda *a, **k: [])
    docs = [{"name": "RFP Vol-1.pdf", "kind": "rfp", "path": "a", "pages": RFP},
            {"name": "Corrigendum-1.pdf", "kind": "corrigendum", "path": "b", "pages": CORRIGENDUM}]
    evidence = analyzer._evidence(None, "bid", docs)
    labels = [e.ref["label"] for e in evidence]
    targets = [e.ref["target"] for e in evidence]
    assert "Corrigendum-1.pdf (corrigendum)" in labels
    amendment = evidence[labels.index("Corrigendum-1.pdf (corrigendum)")]
    assert "30/11/2026" in amendment.text
    assert targets.index("amendments") == targets.count("datasheet")          # right after the bid data
    assert all(l.startswith(("RFP Vol-1.pdf:", "Corrigendum-1.pdf")) for l in labels)
    assert targets == sorted(targets, key=analyzer.TARGETS_ORDER.index)


def test_meta_covers_the_tender_facts_shown_on_the_analysis_screen():
    for key in ("publish_date", "bid_due", "presentation_date", "emd", "estimated_budget", "selection_method"):
        assert key in analyzer.META_KEYS
    assert set(analyzer.RFP_SCHEMA["properties"]["meta"]["required"]) == set(analyzer.META_KEYS)
