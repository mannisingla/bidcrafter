"""Local, rule-based suggestions for document type and key details.

Nothing here calls the external model. Values filled automatically are listed in
metadata["_auto"] so the Library screen can ask a person to confirm them.
"""
from __future__ import annotations

import re
from datetime import date

from dateutil import parser as dateparser

TYPE_RULES: dict[str, list[tuple[str, str]]] = {
    "experience": [
        ("completion_certificate", r"completion certificate|successfully (?:completed|executed)|has completed"),
        ("performance_certificate", r"performance certificate|satisfactory performance|to whomso?ever it may concern"),
        ("work_order", r"work order|purchase order|letter of award|letter of intent|\bLOA\b|\bLOI\b|contract agreement"),
    ],
    "statutory": [
        # before incorporation: the memorandum and articles also print the CIN
        ("moa_aoa", r"memorandum (?:and|&) articles|memorandum of association|articles of association|\bMOA\b|\bAOA\b"),
        ("incorporation", r"certificate of incorporation|corporate identity number"),
        ("pan", r"permanent account number|income tax department"),
        ("gst", r"goods and services tax|\bGSTIN\b|REG-06"),
        ("certification", r"\bISO\b|\bCMMI\b|maturity level"),
        ("ca_certificate", r"chartered accountants?|\bUDIN\b"),
        ("financial_statement", r"balance sheet|profit and loss|statement of profit"),
        ("power_of_attorney", r"power of attorney|board resolution|resolved that"),
        ("manpower_certificate", r"on (?:its|our) (?:payroll|rolls)|manpower strength|headcount"),
        ("epf_esic", r"\bEPFO\b|\bESIC\b|provident fund"),
        # last: other statutory documents can carry a declaration
        ("undertaking", r"\bundertaking\b|\bdeclaration\b|\baffidavit\b|self[- ]?certif"),
    ],
    "hr": [
        ("manpower_certificate", r"on (?:its|our) (?:payroll|rolls)|headcount"),
        ("cv", r"curriculum vitae|\bresume\b|\bCV\b|education|experience"),
    ],
    "literature": [
        ("company_profile", r"company profile|about (?:us|the company)|incorporated in"),
        ("approach", r"approach|methodology"),
        ("security", r"information security|data privacy"),
        ("o_and_m", r"operations? (?:and|&) maintenance|helpdesk|service level"),
    ],
    "uploads": [       # documents uploaded for one bid
        ("emd", r"earnest money|\bEMD\b|bid security|bank guarantee"),
        ("tender_fee", r"tender (?:document )?fee|processing fee|cost of (?:the )?tender"),
        ("integrity_pact", r"integrity pact"),
        ("power_of_attorney", r"power of attorney|board resolution|resolved that"),
        ("undertaking", r"\bundertaking\b|\bdeclaration\b|\baffidavit\b|self[- ]?certif"),
    ],
}

_AMOUNT = re.compile(r"(?:₹|Rs\.?|INR)\s*([\d,]+(?:\.\d+)?)\s*(crores?|cr\b\.?|lakhs?|lacs?)?", re.I)
_DATE = (r"(\d{1,2}(?:st|nd|rd|th)?[\s./-][A-Za-z0-9]{1,9}[\s./-]\d{2,4}"
         r"|[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|\d{4}-\d{2}-\d{2})")
_VALID = re.compile(
    r"(?:valid\s*(?:until|till|upto|up to|through|thru)|expiry date|date of expiry|expiration date|expires on|"
    r"expiring on|valid to|validity\s*(?:date|up\s*to)?"
    r"|\bexp(?:\.|ires|iry)?)\s*[:\-]?\s*" + _DATE,     # "Exp. Aug 09, 2027" on a CMMI appraisal badge
    re.I,
)
# "Valid from 21 Aug 2024 to 20 Aug 2027": the second date is the expiry.
_VALID_RANGE = re.compile(
    r"valid(?:ity)?\s*(?:from|:)?\s*[:\-]?\s*" + _DATE + r"\s*(?:to|till|until|upto|-|–)\s*" + _DATE, re.I)
_TOTAL = re.compile(r"\b(?:total|gross)\b[^\n]{0,80}?(\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d{1,2})?|\d{5,}(?:\.\d{1,2})?)", re.I)
# "Work Order No.: 11/16600/2016", "PO No. 45/IT/2023", "Invoice No.: PWDDL210043" (NICSI proforma invoices)
_PO_NO = re.compile(r"\b(?:work\s*order|purchase\s*order|p\.?\s?o|order|invoice)\s*(?:no|number|#)\.?\s*[:\-–]*\s*"
                    r"([A-Z0-9][A-Z0-9/\-.()]{2,40}[A-Z0-9)])", re.I)
_PO_DATE = re.compile(r"(?:\bdated?|order date|po date|date of (?:issue|order|award))\s*[:\-]?\s*" + _DATE, re.I)
_UDIN = re.compile(r"\bUDIN\s*(?:No\.?|Number)?\s*[:\-]?\s*(\d{8}[A-Z0-9]{10})\b", re.I)
_EPF_CODE = re.compile(r"\b[A-Z]{5}\d{7}(?:\d{3})?\b")
_ESIC_CODE = re.compile(r"\b\d{17}\b")
# Labelled numbers: "Certificate No.: QMS/IN/2024/0012", "Registration Number - 12345", "Appraisal ID: 54321".
_LABELLED_NO = re.compile(
    r"\b(?:certificate|registration|reg\.?|licen[cs]e|appraisal|establishment|code|ref(?:erence)?|serial|document)\s*"
    r"(?:no\.?|number|id|code|#)\s*[:\-.]?\s*([A-Z0-9][A-Z0-9/\-.]{2,40}[A-Z0-9])",
    re.I,
)
_CIN = re.compile(r"\b[LU]\d{5}[A-Z]{2}(\d{4})[A-Z]{3}\d{6}\b")
_PAN = re.compile(r"\b[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z]\b")
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
_ISO = re.compile(r"ISO\s*/?\s*(?:IEC\s*)?(\d{4,5})(?:-\d)?(?::\s*(\d{4}))?", re.I)
# "CMMi 5_2024 to 2027", "CMMI ML3", "CMMI-L5" (often only in the file name); not "CMMI V2.0"
_CMMI_SHORT = re.compile(r"\bCMMI\W{0,3}(?:ML|level|L)?\s*([1-5])(?![\d.])", re.I)
_CMMI_HINT = re.compile(r"\bCMMI\b|maturity lead appraiser|appraisal disclosure statement", re.I)
# first two digits of a GSTIN
GST_STATES = {
    "01": "Jammu and Kashmir", "02": "Himachal Pradesh", "03": "Punjab", "04": "Chandigarh", "05": "Uttarakhand",
    "06": "Haryana", "07": "Delhi", "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram", "16": "Tripura", "17": "Meghalaya",
    "18": "Assam", "19": "West Bengal", "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "26": "Dadra and Nagar Haveli and Daman and Diu", "27": "Maharashtra", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu", "34": "Puducherry",
    "35": "Andaman and Nicobar Islands", "36": "Telangana", "37": "Andhra Pradesh", "38": "Ladakh",
}
_CMMI = re.compile(r"CMMI.{0,80}?(?:maturity\s+)?level\s*(\d)|\bML\s*(\d)\b", re.I | re.S)


def parse_inr_crore(text: str) -> float | None:
    """Largest rupee amount in the text, in crore (work orders usually state the total last)."""
    best = None
    for m in _AMOUNT.finditer(text):
        try:
            n = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        value = n if unit.startswith("cr") else n / 100 if unit.startswith(("la", "lac")) else n / 1e7
        if value >= 0.01 and (best is None or value > best):
            best = value
    return round(best, 4) if best else None


def parse_total_crore(text: str) -> float | None:
    """Largest amount on a 'Total ...' line, in crore: orders and invoices often print 'Total Amount 7,33,400.00'."""
    values = []
    for m in _TOTAL.finditer(text):
        try:
            values.append(float(m.group(1).replace(",", "")) / 1e7)
        except ValueError:
            continue
    best = max((v for v in values if v >= 0.001), default=None)
    return round(best, 4) if best else None


def parse_date(text: str) -> date | None:
    iso = re.match(r"\s*(\d{4}-\d{2}-\d{2})", text or "")
    if iso:                         # dayfirst would read 2027-01-05 as 1 May
        try:
            return date.fromisoformat(iso.group(1))
        except ValueError:
            pass
    try:
        return dateparser.parse(text, dayfirst=True, fuzzy=True).date()
    except (ValueError, OverflowError):
        return None


def document_number(doc_type: str, text: str) -> str | None:
    """Registration / certificate number printed on a statutory document, if one can be found."""
    if doc_type in ("ca_certificate", "financial_statement") and (m := _UDIN.search(text)):     # the auditor's UDIN
        return m.group(1).upper()
    if doc_type == "epf_esic" and (m := _EPF_CODE.search(text) or _ESIC_CODE.search(text)):
        return m.group(0)
    for m in _LABELLED_NO.finditer(text):
        if any(ch.isdigit() for ch in m.group(1)):
            return m.group(1).rstrip(".-/")
    return None


# Audited financial statements: a balance sheet "as at" a date and a profit and loss statement. Checked before the
# other statutory rules: the accounts also mention GST, the CIN and the auditors (chartered accountants, UDIN).
_BALANCE_SHEET = re.compile(r"balance\s+sheet\s+as\s+(?:at|on)", re.I)
_PROFIT_LOSS = re.compile(r"statement\s+of\s+profit\s+(?:and|&)\s+loss|profit\s+(?:and|&)\s+loss\s+(?:account|statement)", re.I)
_FY_TITLE = re.compile(r"\b(?:F\.?\s?Y\.?|financial\s+year)\s*[:\-]?\s*(20\d{2})\s*[-–/]\s*(?:20)?(\d{2})\b", re.I)
# "Balance Sheet as at 31st March, 2025", "for the year ended March 31, 2025", "as on 31.03.2025" (not a lease
# ending 31 March 2030 in the notes)
_YEAR_END = re.compile(r"(?:as\s+(?:at|on)|(?:year|period)\s+ended(?:\s+on)?)\s+"
                       r"(?:31(?:st)?\s*(?:of\s+)?march,?\s*|march\s+31(?:st)?,?\s*|31[./-]0?3[./-])(20\d{2})\b", re.I)
_SHORT_TITLE = re.compile(r"\b(?:abridged|short|summary)\b", re.I)
_FULL_TITLE = re.compile(r"\b(?:full|complete|annual\s+report)\b", re.I)
FULL_STATEMENT_PAGES = 15      # abridged accounts run to a few pages; with all notes and schedules, twenty and more


def is_financial_statement(sample: str) -> bool:
    return bool(_BALANCE_SHEET.search(sample) and _PROFIT_LOSS.search(sample))


def financial_year(title: str, text: str) -> str | None:
    """'2024-25' for accounts of the year ended 31 March 2025: from the title, else the latest year end printed."""
    if (m := _FY_TITLE.search(title)):
        return f"{m.group(1)}-{m.group(2)}"
    ends = [int(y) for y in _YEAR_END.findall(text)]
    if not ends:
        return None
    end = max(ends)
    return f"{end - 1}-{str(end)[2:]}"


def statement_version(title: str, pages: int | None) -> str | None:
    if _SHORT_TITLE.search(title):
        return "Short"
    if _FULL_TITLE.search(title):
        return "Full"
    return None if pages is None else "Full" if pages > FULL_STATEMENT_PAGES else "Short"


def suggest(category: str, title: str, text: str, doc_type: str | None = None, pages: int | None = None) -> dict:
    """doc_type: the type already chosen for the document (a person, or the upload screen); otherwise guessed.
    pages: the document's page count, where known."""
    sample = f"{title}\n{text[:20000]}"
    if not doc_type or doc_type == "other":
        doc_type = "other"
        if category == "statutory" and is_financial_statement(sample):
            doc_type = "financial_statement"
        else:
            for name, pattern in TYPE_RULES.get(category, []):
                if re.search(pattern, sample, re.I):
                    doc_type = name
                    break

    meta: dict = {}
    valid_until = None
    if (m := _VALID_RANGE.search(sample)):
        valid_until = parse_date(m.group(2))
    elif (m := _VALID.search(sample)):
        valid_until = parse_date(m.group(1))

    if doc_type in ("incorporation", "moa_aoa") and (m := _CIN.search(sample)):
        meta.update(cin=m.group(0), incorporation_year=int(m.group(1)))
    elif doc_type == "pan" and (m := _PAN.search(sample)):
        meta["pan"] = m.group(0)
    elif doc_type == "gst" and (m := _GSTIN.search(sample)):
        meta["gstin"] = m.group(0)
        if (state := GST_STATES.get(m.group(0)[:2])):
            meta["state"] = state
    elif doc_type == "certification":
        if (m := _CMMI.search(sample) or _CMMI_SHORT.search(sample)):
            meta.update(standard="CMMI", level=int(next(g for g in m.groups() if g)))
        elif _CMMI_HINT.search(sample):     # the level is often printed as an image; a person adds it
            meta["standard"] = "CMMI"
        elif (m := _ISO.search(sample)):
            meta["standard"] = f"ISO {m.group(1)}" + (f":{m.group(2)}" if m.group(2) else "")
    elif doc_type in ("work_order", "completion_certificate", "performance_certificate"):
        if (v := parse_inr_crore(sample) or parse_total_crore(sample)):
            meta["value_cr"] = v
        if doc_type == "completion_certificate":
            meta["completed"] = True
        if doc_type == "work_order" and (m := _PO_DATE.search(sample)) and (d := parse_date(m.group(1))):
            meta["po_date"] = d.isoformat()
        if doc_type == "work_order" and (n := next((m.group(1) for m in _PO_NO.finditer(sample)
                                                    if any(ch.isdigit() for ch in m.group(1))), None)):
            meta["po_number"] = n
    elif doc_type == "cv":
        if (m := re.search(r"Name(?:\s+of\s+(?:the\s+)?staff)?\s*[:\-]\s*([A-Z][A-Za-z.]+(?:\s+[A-Z][A-Za-z.]+){0,3})", sample)):
            meta["name"] = m.group(1).strip()
        if (m := re.search(r"(?:Proposed\s+)?(?:Position|Designation|Role)\s*[:\-]\s*([^\n]{3,60})", sample, re.I)):
            meta["role"] = m.group(1).strip()
        years = [int(y) for y in re.findall(r"(\d{1,2})\+?\s*(?:years|yrs)", sample, re.I) if 0 < int(y) < 45]
        if years:
            meta["total_years"] = max(years)
    elif doc_type == "financial_statement":
        if (fy := financial_year(title, sample)):
            meta["financial_year"] = fy
        if (version := statement_version(title, pages)):
            meta["version"] = version
    elif doc_type == "manpower_certificate":
        if (m := re.search(r"(\d{2,6})\s+(?:IT\s+)?(?:professionals|employees|personnel)", sample, re.I)):
            meta["headcount"] = int(m.group(1))

    if category == "statutory":
        number = meta.get("cin") or meta.get("pan") or meta.get("gstin") or document_number(doc_type, sample)
        if number:
            meta["number"] = number

    if meta:
        meta["_auto"] = [k for k in meta]
    return {"doc_type": doc_type, "metadata": meta, "valid_until": valid_until}
