from app.privacy.redactor import Redactor, rehydrate, verhoeff_valid

ACTIONS = {"AADHAAR": "block", "BANK_ACCOUNT": "block"}


def test_pan_and_gstin_are_tokenised_consistently():
    r = Redactor(actions=ACTIONS)
    out = r.redact("PAN AAACU1234F, GSTIN 07AAACU1234F1Z5, again AAACU1234F.")
    assert "AAACU1234F" not in out
    assert out.count("[[PAN_1]]") == 2
    assert "[[GSTIN_1]]" in out
    assert rehydrate(out, r.mapping) == "PAN AAACU1234F, GSTIN 07AAACU1234F1Z5, again AAACU1234F."


def test_people_from_library_and_honorifics():
    r = Redactor(actions=ACTIONS, people=["Rajiv Mehta"])
    out = r.redact("Project Manager Rajiv Mehta will report to Shri Anil Kumar Sharma.")
    assert "Rajiv" not in out and "Anil" not in out
    assert r.counts["PERSON"] == 2


def test_contact_details():
    r = Redactor(actions=ACTIONS)
    out = r.redact("Call 98110 12345 or 011-23456789, mail bids@example.co.in")
    assert "98110" not in out and "23456789" not in out and "example" not in out


def test_aadhaar_blocks_only_when_checksum_valid():
    valid = "2341 2341 2346"
    assert verhoeff_valid(valid)
    r = Redactor(actions=ACTIONS)
    r.redact(f"Aadhaar {valid}")
    assert r.blocked == ["AADHAAR"]
    r2 = Redactor(actions=ACTIONS)
    r2.redact("Ref 2341 2341 2341")
    assert r2.blocked == []


def test_bank_account_blocks():
    r = Redactor(actions=ACTIONS)
    r.redact("A/c No. 123456789012, IFSC SBIN0001234")
    assert "BANK_ACCOUNT" in r.blocked
    assert r.counts["IFSC"] == 1


def test_allow_action_leaves_text():
    r = Redactor(actions={"EMAIL": "allow"})
    assert r.redact("x@y.com") == "x@y.com"


def test_rehydrate_nested():
    mapping = {"[[PERSON_1]]": "Priya Nair"}
    assert rehydrate({"a": ["Lead: [[PERSON_1]]"]}, mapping) == {"a": ["Lead: Priya Nair"]}
