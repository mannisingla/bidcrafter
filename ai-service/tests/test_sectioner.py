from app.rfp.sectioner import select_sections, split_sections

# Page text as pypdf reads a criteria table: every numbered row looks like a heading.
PAGES = [
    "REQUEST FOR PROPOSAL\nBihar Government Health Scheme",
    "6. CRITERIA FOR EVALUATION\nThe evaluation shall be conducted in four stages.\n"
    "6.1 Pre-Qualification (PQ) Criteria\nThe following criteria are mandatory.\nSl. No. Basic Requirement\n"
    "1 Legal Entity\nThe Bidder shall be a Company registered under the Companies Act for five years.\n"
    "2 Statutory Registrations\nThe Bidder shall possess valid PAN, GST, EPF and ESI registration.\n"
    "3 Average Annual Turnover\nAverage annual turnover of INR 50 crore in the last three financial years.\n"
    "6.1.1 General Conditions for PQ Evaluation\nConsortium is not allowed.\n"
    "6.2 Technical Qualification Criteria\nBidders shall score at least 70 marks.\n",
    "6.3 Scoring Model\nThe technical proposal is scored out of 100 marks.\n"
    "1 Relevant Project Experience\n30 Marks: 10 marks per project above INR 10 crore.\n"
    "2 Approach and Methodology\n20 Marks\n"
    "6.4 Financial Evaluation\nL1 bidder is selected.\n",
]


def test_a_chosen_clause_carries_its_table_rows_and_sub_clauses():
    chosen, _ = select_sections(split_sections(PAGES))
    pq = next(s for s in chosen if s["heading"].startswith("6.1 Pre-Qualification"))
    for row in ("Legal Entity", "Statutory Registrations", "INR 50 crore", "Consortium is not allowed"):
        assert row in pq["text"]
    assert "6.2 Technical" not in pq["text"]                      # stops at the next clause


def test_the_scoring_model_is_picked_with_its_marks():
    chosen, _ = select_sections(split_sections(PAGES))
    scoring = next(s for s in chosen if s["heading"].startswith("6.3 Scoring Model"))
    assert scoring["target"] == "evaluation"
    assert "30 Marks" in scoring["text"] and "Approach and Methodology" in scoring["text"]
    assert "L1 bidder" not in scoring["text"]
