from datetime import date

from app.ingest.classify import suggest


def test_iso_certificate_number_and_expiry():
    text = ("CERTIFICATE OF REGISTRATION\nThis is to certify that the Quality Management System complies with "
            "ISO 9001:2015\nCertificate No.: QMS/IN/2024/0123\nValid from 21-08-2024 to 20-08-2027")
    s = suggest("statutory", "iso", text)
    assert s["doc_type"] == "certification"
    assert s["metadata"]["standard"] == "ISO 9001:2015"
    assert s["metadata"]["number"] == "QMS/IN/2024/0123"
    assert s["valid_until"] == date(2027, 8, 20)
    assert "number" in s["metadata"]["_auto"]


def test_iso_style_expiry_date():
    s = suggest("statutory", "isms", "ISO/IEC 27001:2022\nCertificate Number: ISMS-765432\nExpiry Date: 2027-01-18")
    assert s["metadata"]["number"] == "ISMS-765432"
    assert s["valid_until"] == date(2027, 1, 18)


def test_statutory_ids_become_the_document_number():
    cin = suggest("statutory", "coi", "Certificate of Incorporation\nCorporate Identity Number: U74899DL1995PLC073288")
    assert cin["metadata"]["number"] == "U74899DL1995PLC073288"
    gst = suggest("statutory", "gst", "Goods and Services Tax\nRegistration Number: 07AAACU1234F1Z5")
    assert gst["metadata"]["number"] == "07AAACU1234F1Z5"


def test_ca_certificate_udin():
    s = suggest("statutory", "ca", "We, chartered accountants, certify the turnover.\nUDIN: 25512345ABCDEF1234")
    assert s["doc_type"] == "ca_certificate"
    assert s["metadata"]["number"] == "25512345ABCDEF1234"


def test_no_number_outside_statutory():
    s = suggest("experience", "wo", "Work Order\nReference No.: WO/2024/77 for Rs. 12.6 crore")
    assert "number" not in s["metadata"]


def test_cmmi_level_from_file_name_when_the_certificate_prints_it_as_an_image():
    s = suggest("statutory", "UTL CMMi 5_2024 to 2027",
                "This is to affirm that the organisation, Certified High Maturity Lead Appraiser, has been appraised at")
    assert s["doc_type"] == "certification"
    assert s["metadata"]["standard"] == "CMMI" and s["metadata"]["level"] == 5


def test_cmmi_without_level_and_not_a_version_number():
    s = suggest("statutory", "appraisal", "CMMI V2.0 appraisal. Certified High Maturity Lead Appraiser.")
    assert s["metadata"]["standard"] == "CMMI" and "level" not in s["metadata"]


def test_gst_state_from_gstin():
    s = suggest("statutory", "gst noida", "Goods and Services Tax REG-06\nRegistration Number :09AAACU1224H1ZM")
    assert s["metadata"]["gstin"] == "09AAACU1224H1ZM" and s["metadata"]["state"] == "Uttar Pradesh"


def test_memorandum_and_articles_are_not_the_certificate_of_incorporation():
    s = suggest("statutory", "UTL MOA & AOA", "THE COMPANIES ACT, 1956\nMEMORANDUM OF ASSOCIATION OF UNEECOPS TECHNOLOGIES "
                "LIMITED\nCorporate Identity Number: U74899DL1995PLC073288")
    assert s["doc_type"] == "moa_aoa" and s["metadata"]["number"] == "U74899DL1995PLC073288"
    coi = suggest("statutory", "UTL_ROC", "Certificate of Incorporation\nCorporate Identity Number: U74899DL1995PLC073288")
    assert coi["doc_type"] == "incorporation"


def test_cmmi_appraisal_number_and_expiry_from_badge_text():
    text = ("This is to affirm that Uneecops Technologies Limited ... Has been appraised at\n@ CMMI _\nAPPRAISED\n"
            "Appraisal # 72038 | Exp. Aug 09, 2027 _\nIssued on — 05th September 2024")
    s = suggest("statutory", "UTL CMMi 5_2024 to 2027", text)
    assert s["metadata"]["standard"] == "CMMI" and s["metadata"]["level"] == 5
    assert s["metadata"]["number"] == "72038"
    assert s["valid_until"] == date(2027, 8, 9)


def test_experience_is_not_an_expiry():
    assert suggest("statutory", "iso", "ISO 9001:2015 certificate. Experience 2015 onwards")["valid_until"] is None


def test_documents_uploaded_for_a_bid_get_their_type():
    from app.ingest.classify import suggest as guess
    assert guess("uploads", "BG", "Bank Guarantee in lieu of Earnest Money Deposit for the tender")["doc_type"] == "emd"
    assert guess("uploads", "Receipt", "Payment receipt: Tender Fee Rs 5,900 paid online")["doc_type"] == "tender_fee"
    assert guess("uploads", "IP", "PRE-CONTRACT INTEGRITY PACT between the purchaser and the bidder")["doc_type"] == "integrity_pact"
    assert guess("uploads", "POA", "POWER OF ATTORNEY in favour of the authorised signatory")["doc_type"] == "power_of_attorney"
