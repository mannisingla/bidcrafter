from datetime import date

from app.ingest.chunker import chunk_pages
from app.ingest.classify import parse_date
from app.ingest.pipeline import merge_suggestion
from app.privacy.redactor import Redactor, drop_account_numbers
from app.rfp.matcher import _to_crore


def test_iso_dates_are_not_read_day_first():
    assert parse_date("2027-01-05") == date(2027, 1, 5)
    assert parse_date("05/01/2027") == date(2027, 1, 5)          # Indian day-first stays


def test_crore_units_are_checked_before_rupees():
    assert _to_crore(50, "Rupees in crore") == 50
    assert _to_crore(50, "INR crore") == 50
    assert _to_crore(5000000, "INR") == 0.5
    assert _to_crore(250, "lakh") == 2.5


def test_read_again_keeps_details_filled_from_other_documents_and_what_a_person_entered():
    existing = {"client": "Typed by a person", "completion_date": "2021-12-31", "value_cr": 0.07,
                "work_order_id": None, "_auto": ["completion_date", "value_cr"]}
    fresh = {"value_cr": 0.0733, "po_date": "2016-07-11", "client": "From the PO", "_auto": ["value_cr", "po_date", "client"]}
    merged = merge_suggestion(existing, fresh)
    assert merged["client"] == "Typed by a person"                # a person's value wins
    assert merged["completion_date"] == "2021-12-31"              # from a completion certificate: kept
    assert merged["value_cr"] == 0.0733 and merged["po_date"] == "2016-07-11"
    assert set(merged["_auto"]) == {"completion_date", "value_cr", "po_date"}


def test_account_number_lines_are_dropped_but_the_emd_amount_stays():
    text = ("EMD: INR 10,00,000 by Bank Guarantee or RTGS\nBeneficiary: BSEDC\nAccount No.: 123456789012\n"
            "IFSC: SBIN0000152\nBid validity: 180 days")
    out = drop_account_numbers(text)
    assert "123456789012" not in out and "INR 10,00,000" in out and "Bid validity" in out
    r = Redactor(actions={"BANK_ACCOUNT": "block"})
    r.redact(out)
    assert not r.blocked


def test_a_chunk_keeps_the_heading_of_its_own_section():
    long = "word " * 260
    chunks = chunk_pages([f"1. SCOPE OF WORK\n\n{long}\n\n2. ELIGIBILITY CRITERIA\n\nThe bidder shall have turnover."])
    first = next(c for c in chunks if "word" in c["content"])
    assert first["heading"] == "1. SCOPE OF WORK"
