from datetime import date

from app.ingest import classify
from app.rfp.matcher import _match_one, financial_statements

ACCOUNTS = (
    "Uneecops Technologies Limited  CIN: U72200DL2001PLC111222\n"
    "INDEPENDENT AUDITOR'S REPORT ... For ABC & Co., Chartered Accountants  UDIN: 25087564BMKXYZ1234\n"
    "Balance Sheet as at 31st March, 2025   As at 31st March 2024\n"
    "Goods and Services Tax receivable 12,40,000\n"
    "Statement of Profit and Loss for the year ended 31st March, 2025\n"
    "Lease liability payable till 31st March 2030"
)


def test_audited_accounts_are_recognised_before_gst_and_incorporation():
    s = classify.suggest("statutory", "UTL audited accounts", ACCOUNTS, None, 6)
    assert s["doc_type"] == "financial_statement"
    assert (s["metadata"]["financial_year"], s["metadata"]["version"]) == ("2024-25", "Short")
    assert s["metadata"]["number"] == "25087564BMKXYZ1234"          # the auditor's UDIN


def test_the_title_or_the_page_count_tells_the_year_and_version():
    meta = classify.suggest("statutory", "UTL Annual Report FY 2022-23", ACCOUNTS, None, 6)["metadata"]
    assert (meta["financial_year"], meta["version"]) == ("2022-23", "Full")
    assert classify.suggest("statutory", "UTL accounts", ACCOUNTS, None, 40)["metadata"]["version"] == "Full"
    assert classify.suggest("statutory", "UTL accounts (abridged)", ACCOUNTS, None, 40)["metadata"]["version"] == "Short"
    assert classify.suggest("statutory", "GST", "Goods and Services Tax  Form GST REG-06", None, 2)["doc_type"] == "gst"


def _fs(id_, fy, version):
    return {"id": id_, "doc_type": "financial_statement", "metadata": {"financial_year": fy, "version": version}}


LIBRARY = [_fs("s25", "2024-25", "Short"), _fs("f25", "2024-25", "Full"), _fs("s24", "2023-24", "Short"),
           _fs("f24", "2023-24", "Full"), _fs("f23", "2022-23", "Full"), _fs("s22", "2021-22", "Short")]
TURNOVER = {"clause": "6.1 (3)", "text": "Average annual turnover of Rs 50 crore in the last three financial years",
            "category": "turnover", "threshold_value": 50, "threshold_unit": "crore",
            "evidence_expected": "Audited balance sheets and CA certificate"}


def test_the_short_statements_of_the_last_three_years_by_default():
    ids, note = financial_statements(LIBRARY, TURNOVER)
    assert ids == ["s25", "s24", "f23"]
    assert note.startswith("Short audited financial statements for FY 2024-25, 2023-24, 2022-23")
    assert "only the full version is in the library for 2022-23" in note


def test_the_full_accounts_only_when_the_rfp_asks_for_them():
    ids, note = financial_statements(LIBRARY, {**TURNOVER, "window_years": 2,
                                               "evidence_expected": "Complete audited financial statements with notes to accounts"})
    assert ids == ["f25", "f24"] and note.startswith("Full audited")


def test_turnover_encloses_the_ca_certificate_and_the_short_statements():
    ca = {"id": "ca", "doc_type": "ca_certificate", "metadata": {"turnover_cr": {"2022-23": 40, "2023-24": 55, "2024-25": 70}}}
    r = _match_one(None, TURNOVER, [ca, *LIBRARY], date(2026, 11, 15))
    assert r["status"] == "met" and r["evidence"] == ["ca", "s25", "s24", "f23"]
    assert r["note"].startswith("Average ₹ 55.00 Cr against ₹ 50 Cr required. Short audited")


def test_a_criterion_asking_for_balance_sheets_gets_the_statements_to_confirm():
    r = _match_one(None, {"clause": "4", "text": "Copies of audited balance sheets for the last three years",
                          "category": "other"}, LIBRARY, date(2026, 11, 15))
    assert r["status"] == "review" and r["evidence"] == ["s25", "s24", "f23"]


def test_a_signed_undertaking_is_its_own_type_after_the_specific_ones():
    assert classify.suggest("statutory", "Non-blacklisting", "UNDERTAKING  We hereby declare that we are not blacklisted", None, 1)["doc_type"] == "undertaking"
    assert classify.suggest("statutory", "PAN", "Permanent Account Number ... declaration", None, 1)["doc_type"] == "pan"
