"""Job: fill an Experience project's details from its documents.

Three kinds of document feed a project: its purchase order / work order (the project itself), a completion
certificate linked to it, and the RFP it was won on. Each is read and indexed locally like any library
document. Then the order or certificate (short, sent whole up to the policy limit) or the RFP's opening pages
and scope / background sections are sent, through the privacy gateway, for extraction.

Values are written into fields that are empty or were filled automatically before (listed in
metadata["_auto"]); a value a person entered is never replaced. Filled fields stay in "_auto" so a person
confirms them on the project page.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from ..db import audit
from ..privacy.gateway import Evidence, ask
from ..rfp.sectioner import split_sections

TAG_SUGGESTIONS = ["Manpower", "AMC", "Payment Gateway", "Software Development", "Mobile App", "Portal",
                   "ERP", "Data Centre", "Cloud", "Networking", "Cyber Security", "Helpdesk", "Facility Management",
                   "Digitisation", "GIS", "Analytics", "Training", "Hardware Supply", "O&M"]

PROJECT_KEYWORDS = ["scope of work", "scope of the work", "project scope", "background", "introduction", "objective",
                    "about the project", "project overview", "project description", "terms of reference",
                    "brief description", "purpose"]

SOURCE_LABEL = {"work_order": "purchase order / work order", "completion_certificate": "completion certificate",
                "project_rfp": "RFP / tender document"}
PURPOSE = {"work_order": "po_extraction", "completion_certificate": "po_extraction", "project_rfp": "project_extraction"}

SYSTEM = (
    "You read documents about a project our company (the vendor) carried out for a government or PSU client, and "
    "return the project's details. Use only the text provided; the text may come from OCR and contain noise. If a "
    "value is not stated, return an empty string (or null for the value). Tokens such as [[PERSON_1]] are "
    "placeholders; copy them unchanged."
)
INSTRUCTION = (
    "The extracts below are from the project's {source}. Return:\n"
    "- short_name: a short label of 2-5 words for the project, e.g. 'ACASH Website' or 'AIC MIS ATS'\n"
    "- project_title: the project or work name as stated\n"
    "- client: the organisation the work is done for. If an intermediary such as NICSI, NIC or a PSU placed the "
    "order for an end user, give the end user followed by the intermediary in brackets, e.g. 'ACASH (through NICSI)'\n"
    "- state_ministry: the state government or central ministry the client belongs to\n"
    "- description: 2-5 factual sentences on the scope of work\n"
    "- value_inr: the total order value excluding taxes, in rupees as a plain number exactly as printed "
    "(7,33,400.00 -> 733400), or null. When GST / CGST / SGST / IGST are added on separate lines, use the total "
    "before those lines, not the gross amount payable\n"
    "- po_number: the purchase order / work order number exactly as printed (for a proforma invoice, its invoice "
    "number); not a tender, project or empanelment number\n"
    "- po_date: the order / work order / proforma invoice date as YYYY-MM-DD\n"
    "- duration: the contract duration as stated, e.g. '3 man-months' or '12 months'\n"
    "- completion_date: the date the work was completed as YYYY-MM-DD (usually only on a completion certificate)\n"
    "- tags: 3-8 short tags for the kind of work. Prefer these where they fit: " + ", ".join(TAG_SUGGESTIONS) + "."
)
_STR = {"type": "string"}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["short_name", "project_title", "client", "state_ministry", "description", "value_inr", "po_number",
                 "po_date", "duration", "completion_date", "tags"],
    "properties": {"short_name": _STR, "project_title": _STR, "client": _STR, "state_ministry": _STR,
                   "description": _STR, "value_inr": {"type": ["number", "null"]}, "po_number": _STR,
                   "po_date": _STR, "duration": _STR,
                   "completion_date": _STR, "tags": {"type": "array", "items": _STR}},
}
# extraction field -> project metadata key (the keys the matcher and drafter already read)
FIELD_KEYS = {"short_name": "short_name", "project_title": "project", "client": "client", "state_ministry": "ministry",
              "description": "scope", "po_number": "po_number", "po_date": "po_date", "duration": "duration",
              "completion_date": "completion_date"}
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Payment instructions on orders and proforma invoices (the client's or intermediary's bank account) say nothing
# about the project, and an account number would make the gateway block the whole request.
_PAYMENT_LINE = re.compile(r"\b(?:a/?c|account|ifsc|bank|branch|rtgs|neft|utr|cheque|demand draft|swift|micr)\b", re.I)


def strip_payment_details(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _PAYMENT_LINE.search(line))


def extracts_from(category: str, doc_type: str, metadata: dict) -> bool:
    """Whether a document that has just been read should fill its Experience project's details."""
    if category != "experience":
        return False
    if doc_type == "work_order":
        return not metadata.get("work_order_id")
    return doc_type in ("completion_certificate", "project_rfp") and bool(metadata.get("work_order_id"))


def pick_sections(pages: list[str], limit: int = 5) -> list[dict]:
    """Opening pages plus the sections that best describe the project."""
    sections = split_sections(pages)
    if not sections:
        return []

    def score(s: dict) -> int:
        heading, body = s["heading"].lower(), s["text"][:6000].lower()
        return sum(6 for kw in PROJECT_KEYWORDS if kw in heading) + sum(min(body.count(kw), 3) for kw in PROJECT_KEYWORDS)

    ranked = sorted(range(1, len(sections)), key=lambda i: score(sections[i]), reverse=True)
    chosen = [0] + [i for i in ranked[: limit - 1] if score(sections[i]) >= 3]
    return [sections[i] for i in sorted(chosen)]


def _valid_date(value: str) -> bool:
    if not _ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def merge(project_meta: dict, answer: dict) -> tuple[dict, list[str]]:
    """Fill fields that are empty or were filled automatically; add new tags. Returns (metadata, keys filled)."""
    meta, filled = dict(project_meta), []
    auto = set(meta.get("_auto") or [])

    def free(key: str) -> bool:
        return key in auto or meta.get(key) in (None, "", [])

    for field, key in FIELD_KEYS.items():
        value = str(answer.get(field) or "").strip()
        if key.endswith("_date") and value and not _valid_date(value):
            value = ""
        if value and free(key) and meta.get(key) != value:
            meta[key] = value
            filled.append(key)
    # the model reports rupees as printed; converting to crore here avoids its unit slips
    value_inr = answer.get("value_inr")
    if isinstance(value_inr, (int, float)) and value_inr >= 1000 and free("value_cr"):
        value_cr = round(value_inr / 1e7, 4)
        if meta.get("value_cr") != value_cr:
            meta["value_cr"] = value_cr
            filled.append("value_cr")
    tags = [str(t) for t in meta.get("domains") or []]
    new = [t.strip() for t in answer.get("tags") or [] if t.strip() and t.strip().lower() not in {x.lower() for x in tags}]
    if new:
        meta["domains"] = tags + new
        filled.append("domains")

    # the matcher reads end_year and completed, the drafter period (the project page derives them the same way)
    if "completion_date" in filled:
        meta["end_year"] = int(meta["completion_date"][:4])
        meta["completed"] = meta["completion_date"] <= date.today().isoformat()
        filled += ["end_year", "completed"]
    if {"po_date", "completion_date"} & set(filled):
        fmt = _month_year
        meta["period"] = f"{fmt(meta.get('po_date') or '')} – {fmt(meta.get('completion_date') or '')}"
        filled.append("period")
    if filled:
        meta["_auto"] = sorted(auto | set(filled))
    return meta, filled


def _month_year(iso: str) -> str:
    """'Mar 2021' for an ISO date; a date typed otherwise (in the Details box) is shown as it is."""
    if not iso:
        return "ongoing"
    try:
        return date.fromisoformat(iso).strftime("%b %Y")
    except ValueError:
        return iso


def extraction_status(conn: psycopg.Connection, doc_id, value: dict) -> None:
    conn.execute("UPDATE library_documents SET metadata = metadata || %s, updated_at = now() WHERE id = %s",
                 (Jsonb({"extraction": {**value, "at": datetime.now(timezone.utc).isoformat()}}), doc_id))


def _evidence(src: dict) -> list[Evidence]:
    pages = (src["extracted_text"] or "").split("\f")
    ref = {"kind": src["doc_type"], "id": str(src["id"]), "sensitivity": src["sensitivity"]}
    if src["doc_type"] == "project_rfp":
        return [Evidence(s["text"], {**ref, "label": s["heading"][:120]}) for s in pick_sections(pages)]
    text = strip_payment_details("\n".join(p for p in pages if p.strip()))
    return [Evidence(text, {**ref, "label": SOURCE_LABEL[src["doc_type"]]})] if text.strip() else []


def run(conn: psycopg.Connection, job: dict) -> dict:
    doc_id = job["payload"]["document_id"]
    src = conn.execute("SELECT * FROM library_documents WHERE id = %s", (doc_id,)).fetchone()
    if src is None:
        raise ValueError(f"Document {doc_id} no longer exists")
    project_id = (src["metadata"] or {}).get("work_order_id") or (src["id"] if src["doc_type"] == "work_order" else None)

    extraction_status(conn, doc_id, {"status": "running"})
    conn.commit()
    try:      # every failure is shown on the document, so its "reading details" status never stays for good
        if not project_id or src["doc_type"] not in SOURCE_LABEL:
            raise ValueError("This document is not a project's purchase order, completion certificate or RFP")
        evidence = _evidence(src)
        if not evidence:
            raise ValueError("No text could be read from the document")
        answer = ask(conn, purpose=PURPOSE[src["doc_type"]], system=SYSTEM,
                     instruction=INSTRUCTION.format(source=SOURCE_LABEL[src["doc_type"]]), evidence=evidence,
                     schema=SCHEMA, schema_name="project_details", job_id=job["id"])
        # merged into the project as it is now (a person may be editing it), with the row locked
        project = conn.execute("SELECT metadata FROM library_documents WHERE id = %s FOR UPDATE", (project_id,)).fetchone()
        if project is None:
            raise ValueError("The project no longer exists")
        meta, filled = merge(project["metadata"] or {}, answer)
        conn.execute("UPDATE library_documents SET metadata = %s, updated_at = now() WHERE id = %s", (Jsonb(meta), project_id))
    except Exception as exc:
        conn.rollback()
        extraction_status(conn, doc_id, {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]})
        conn.commit()
        raise
    extraction_status(conn, doc_id, {"status": "done", "filled": [k for k in filled if k not in ("end_year", "completed", "period")]})
    audit(conn, "worker", "project.extracted", "document", project_id, {"source": str(doc_id), "filled": filled})
    conn.commit()
    return {"project": str(project_id), "filled": filled}
