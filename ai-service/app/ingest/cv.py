"""Job: fill a CV's details in the HR library: name, proposed role, total experience, two highest qualifications,
skills and certifications.

The CV is read and indexed locally first. Lines with personal identifiers (Aadhaar, PAN, passport, date of
birth, family, address, bank) are dropped before anything leaves the server; the person's name, e-mail and
phone are replaced by tokens by the privacy gateway and restored locally in the answer.

Values are written into fields that are empty or were filled automatically before (metadata["_auto"]); a value
a person entered is never replaced. The matcher reads "role" and "total_years" for key personnel.
"""
from __future__ import annotations

import re

import psycopg
from psycopg.types.json import Jsonb

from ..db import audit
from ..privacy.gateway import Evidence, ask
from .project import extraction_status

SYSTEM = (
    "You read a CV and return the person's professional details. Use only the text provided; it may come from OCR "
    "and contain noise. If a value is not stated, return an empty string (or null for the experience). Tokens such "
    "as [[PERSON_1]] are placeholders for names; copy them unchanged."
)
INSTRUCTION = (
    "From the CV below return:\n"
    "- name: the person's full name\n"
    "- proposed_role: the position the CV proposes the person for (e.g. 'Proposed Position: Project Manager'); "
    "if none is stated, the current designation\n"
    "- total_years: total professional experience in years as a number (e.g. 19 or 7.5), as stated or from the "
    "employment history; null if it cannot be told\n"
    "- qualifications: the two highest degrees, highest first, each with its specialisation if stated, e.g. "
    "['MBA (Finance)', 'B.Tech (Computer Science)']; one if only one is stated\n"
    "- skills: up to 15 short technical or domain skills, e.g. 'Java', 'Oracle', 'Project Management'\n"
    "- certifications: professional certifications such as PMP, ITIL, CISA, CCNA or AWS (not degrees)"
)
_STR = {"type": "string"}
_LIST = {"type": "array", "items": _STR}
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "proposed_role", "total_years", "qualifications", "skills", "certifications"],
    "properties": {"name": _STR, "proposed_role": _STR, "total_years": {"type": ["number", "null"]},
                   "qualifications": _LIST, "skills": _LIST, "certifications": _LIST},
}
FIELD_KEYS = {"name": "name", "proposed_role": "role"}
LIST_KEYS = {"skills": "skills", "certifications": "certifications"}

# labelled personal identifiers only, so experience lines such as "Client: State Bank of India" stay
_PERSONAL_LINE = re.compile(
    r"aadhaa?r|\buid\b|passport|\bpan\s*(?:no\.?|number|card)?\s*[:\-]|date of birth|\bd\.?o\.?b\b|\bborn\b|"
    r"father|mother'?s name|husband|spouse|marital|religion|caste|nationality|gender|\bsex\s*[:\-]|blood group|"
    r"(?:permanent|present|residential|postal|correspondence|home)\s+address|\baddress\s*[:\-]|"
    r"\ba/?c\s*(?:no|number)|account\s*(?:no|number)|ifsc", re.I)
_NAME_LIKE = re.compile(r"[A-Za-z][A-Za-z.']+(?: [A-Za-z][A-Za-z.']+){1,3}")


def strip_personal_details(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not _PERSONAL_LINE.search(line))


def name_hints(title: str, metadata: dict) -> list[str]:
    """Names to redact for this CV: the one read locally and the file name if it looks like a name."""
    hints = [str(metadata.get("name") or "").strip()]
    words = re.sub(r"(?i)\b(?:cv|resume|curriculum vitae|bio ?data|profile)\b", " ", re.sub(r"[_\-\d]+", " ", title))
    candidate = " ".join(words.split())
    if _NAME_LIKE.fullmatch(candidate):
        hints.append(candidate)
    out = []
    for h in hints:
        if len(h) > 3:
            out += [h, h.upper(), h.title()]
    return list(dict.fromkeys(out))


def merge(meta_in: dict, answer: dict) -> tuple[dict, list[str]]:
    """Fill fields that are empty or were filled automatically; add new skills / certifications."""
    meta, filled = dict(meta_in), []
    auto = set(meta.get("_auto") or [])

    def free(key: str) -> bool:
        return key in auto or meta.get(key) in (None, "", [])

    # "qualification" holds the two highest degrees as text, highest first: "MBA (Finance), B.Tech"
    quals = [str(q).strip() for q in answer.get("qualifications") or [] if str(q).strip()][:2]
    values = {key: str(answer.get(field) or "").strip() for field, key in FIELD_KEYS.items()} | {"qualification": ", ".join(quals)}
    for key, value in values.items():
        if value and free(key) and meta.get(key) != value:
            meta[key] = value
            filled.append(key)
    years = answer.get("total_years")
    if isinstance(years, (int, float)) and 0 < years < 60 and free("total_years"):
        years = int(years) if float(years).is_integer() else round(float(years), 1)
        if meta.get("total_years") != years:
            meta["total_years"] = years
            filled.append("total_years")
    for field, key in LIST_KEYS.items():
        have = [str(x) for x in meta.get(key) or []]
        new = [t.strip() for t in answer.get(field) or [] if t.strip() and t.strip().lower() not in {x.lower() for x in have}]
        if new:
            meta[key] = have + new
            filled.append(key)
    if filled:
        meta["_auto"] = sorted(auto | set(filled))
    return meta, filled


def run(conn: psycopg.Connection, job: dict) -> dict:
    doc_id = job["payload"]["document_id"]
    doc = conn.execute("SELECT * FROM library_documents WHERE id = %s", (doc_id,)).fetchone()
    if doc is None:
        raise ValueError(f"Document {doc_id} no longer exists")
    if doc["category"] != "hr" or doc["doc_type"] != "cv":
        raise ValueError("Details are read only from a CV in the Human resources library")

    extraction_status(conn, doc_id, {"status": "running"})
    conn.commit()
    try:      # every failure is shown on the CV, so its "reading details" status never stays for good
        text = strip_personal_details((doc["extracted_text"] or "").replace("\f", "\n"))
        if not text.strip():
            raise ValueError("No text could be read from the CV")
        answer = ask(conn, purpose="cv_extraction", system=SYSTEM, instruction=INSTRUCTION,
                     evidence=[Evidence(text, {"kind": "cv", "id": str(doc_id), "label": "CV", "sensitivity": doc["sensitivity"]})],
                     schema=SCHEMA, schema_name="cv_details", job_id=job["id"],
                     people=name_hints(doc["title"], doc["metadata"] or {}))
        # merged into the CV's details as they are now (a person may be editing them), with the row locked
        current = conn.execute("SELECT metadata FROM library_documents WHERE id = %s FOR UPDATE", (doc_id,)).fetchone()
        if current is None:
            raise ValueError("The CV no longer exists")
        meta, filled = merge(current["metadata"] or {}, answer)
        meta["extraction"] = {**(meta.get("extraction") or {}), "status": "done", "filled": filled}
        conn.execute("UPDATE library_documents SET metadata = %s, updated_at = now() WHERE id = %s", (Jsonb(meta), doc_id))
    except Exception as exc:
        conn.rollback()
        extraction_status(conn, doc_id, {"status": "failed", "error": f"{type(exc).__name__}: {exc}"[:500]})
        conn.commit()
        raise
    audit(conn, "worker", "cv.extracted", "document", doc_id, {"filled": filled})
    conn.commit()
    return {"filled": filled}
