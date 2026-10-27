"""Jobs: analyse an RFP (stage 1), then check eligibility for a bid response (stage 2).

Stage 1, analyze_rfp:
1. Read and index every RFP document locally (RFP volumes, annexures, corrigenda, addenda, clarifications).
2. Pick the relevant sections of each main document locally (sectioner + vector search fallback); amendments,
   being short, are taken whole.
3. Send only those, redacted, for structured extraction: the tender's facts, amendments, PQ and TEC criteria,
   key personnel and formats.

Stage 2, match_rfp: match every criterion against the organisation library locally (matcher.py).
"""
from __future__ import annotations

from datetime import datetime, timezone

import psycopg
from psycopg.types.json import Jsonb

from ..config import load_policy
from ..db import audit
from ..files import resolve
from ..ingest.chunker import chunk_pages
from ..ingest.classify import parse_date
from ..ingest.extract import extract_pages, to_pdf
from ..privacy.gateway import Evidence, ask
from ..privacy.redactor import drop_account_numbers
from ..rag import store
from . import matcher
from .sectioner import TARGET_QUERIES, TARGETS, select_sections, split_sections

_NULLABLE_STR = {"type": ["string", "null"]}
_NULLABLE_NUM = {"type": ["number", "null"]}
PQ_CATEGORIES = ["registration", "turnover", "net_worth", "project_experience", "domain_experience",
                 "certification", "blacklisting", "manpower", "tax_registration", "local_presence", "other"]


def _obj(props: dict) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


# Facts of the tender, as written in it (dates with times, amounts with currency). Drafting and the PDF read
# rfp_ref, title, department and bid_due; the rest is shown on the RFP analysis screen.
META_KEYS = [
    "rfp_ref", "title", "department", "publish_date", "prebid_meeting", "prebid_query_deadline", "bid_due",
    "tech_bid_opening", "presentation_date", "fin_bid_opening", "emd", "emd_exemption", "tender_fee",
    "estimated_budget", "performance_security", "selection_method", "min_technical_score", "bid_validity",
    "contract_period", "submission_mode", "scope_summary",
]
RFP_SCHEMA = _obj({
    "meta": _obj({k: _NULLABLE_STR for k in META_KEYS}),
    "amendments": {"type": "array", "items": _obj({
        "document": {"type": "string"}, "field": {"type": "string"}, "change": {"type": "string"}})},
    "pq_criteria": {"type": "array", "items": _obj({
        "clause": {"type": "string"},
        "text": {"type": "string"},
        "category": {"type": "string", "enum": PQ_CATEGORIES},
        "threshold_value": _NULLABLE_NUM,
        "threshold_unit": _NULLABLE_STR,
        "window_years": _NULLABLE_NUM,
        "alternatives": {"type": "array", "items": _obj({"count": {"type": "number"},
                                                         "min_value_cr": {"type": "number"}})},
        "evidence_expected": {"type": "string"},
    })},
    "tech_criteria": {"type": "array", "items": _obj({
        "clause": {"type": "string"}, "parameter": {"type": "string"}, "max_marks": _NULLABLE_NUM,
        "basis": {"type": "string"}})},
    "personnel": {"type": "array", "items": _obj({
        "role": {"type": "string"}, "qualification": {"type": "string"}, "min_years": _NULLABLE_NUM,
        "certifications": {"type": "array", "items": {"type": "string"}}})},
    "forms": {"type": "array", "items": _obj({"code": {"type": "string"}, "title": {"type": "string"}})},
})

SYSTEM = (
    "You extract structured data from Indian government tender documents (RFPs). One tender can have several "
    "documents: the RFP volumes, annexures, and later corrigenda, addenda and pre-bid clarifications. Use only the "
    "extracts provided. When a value is not stated, return null rather than guessing. Tokens such as [[PERSON_1]] "
    "stand for redacted values; copy them unchanged if they are needed."
)
INSTRUCTION = (
    "From the tender extracts below, extract:\n"
    "- meta: the tender's facts. Give dates with the time where stated, exactly as written (e.g. '15/11/2026 "
    "15:00 hrs'); amounts with currency as written (e.g. 'INR 10,00,000'). publish_date is the RFP / NIT "
    "publication date; presentation_date the technical presentation; estimated_budget the estimated cost or "
    "project value if stated; emd_exemption who is exempt from EMD (e.g. MSEs, startups); selection_method with "
    "weightage (e.g. 'QCBS 70:30' or 'L1'); min_technical_score the qualifying technical score; submission_mode "
    "where bids are submitted (e.g. 'Online on CPPP'); scope_summary 3-5 factual sentences on the scope of work.\n"
    "- amendments: every change a corrigendum, addendum or clarification makes to these facts or to the criteria, "
    "naming the document. Where documents disagree, the values everywhere else must be the latest amended ones.\n"
    "- pq_criteria: every pre-qualification (eligibility) criterion with its clause number, in RFP order. Express "
    "turnover and net-worth thresholds in crore. For project experience criteria, list each alternative in "
    "'alternatives' (for example one project of 12 crore, or two of 8 crore each) and the look-back period in "
    "window_years. Leave 'alternatives' empty when it does not apply.\n"
    "- tech_criteria: the technical evaluation criteria (TEC) with clause, maximum marks and the basis of marking.\n"
    "- personnel: the key personnel required.\n"
    "- forms: the technical bid formats the bidder must submit."
)
# Bid stages past the analysis, kept when the RFP is analysed again (a constant list, safe in SQL).
ADVANCED = "'drafting', 'drafted', 'assembling', 'ready'"
# Document kinds; amendments are short and sent whole, the rest contribute their relevant sections.
AMENDMENT_KINDS = {"corrigendum", "addendum", "clarification"}


def _documents(bid: dict) -> list[dict]:
    """Each RFP document with its page texts, in upload order."""
    docs = []
    for f in bid["rfp_files"]:
        _, pages = extract_pages(to_pdf(resolve(f["path"])))
        docs.append({"name": f.get("name") or "RFP", "kind": f.get("kind") or "rfp", "path": f["path"], "pages": pages})
    return docs


def _evidence(conn: psycopg.Connection, bid_id, docs: list[dict]) -> list[Evidence]:
    evidence: list[Evidence] = []
    found: set[str] = set()
    for d in docs:
        if d["kind"] in AMENDMENT_KINDS:
            text = "\n".join(p for p in d["pages"] if p.strip())
            if text.strip():
                evidence.append(Evidence(drop_account_numbers(text), {"kind": "rfp", "target": "amendments", "sensitivity": "public",
                                                "label": f"{d['name']} ({d['kind']})"}))
            continue
        sections, missing = select_sections(split_sections(d["pages"]))
        found |= set(TARGETS) - set(missing)
        for s in sections:
            evidence.append(Evidence(drop_account_numbers(s["text"]), {"kind": "rfp", "target": s["target"], "sensitivity": "public",
                                                 "label": f"{d['name']}: {s['heading'][:70]} (pages {s['page_from']}-{s['page_to']})"}))
    for target in [t for t in TARGETS if t not in found]:     # local vector search over the RFP when no heading matched
        for hit in store.search(conn, TARGET_QUERIES[target], bid_id=bid_id, k=2):
            evidence.append(Evidence(drop_account_numbers(hit["content"]), {"kind": "rfp", "target": target, "sensitivity": "public",
                                                      "label": f"RFP pages {hit['page_from']}-{hit['page_to']}"}))
    evidence.sort(key=lambda e: TARGETS_ORDER.index(e.ref.get("target", "datasheet")))
    return evidence


def run(conn: psycopg.Connection, job: dict) -> dict:
    """Stage 1: read every RFP document and extract the tender's facts and criteria. No library matching."""
    bid_id = job["payload"]["bid_id"]
    bid = conn.execute("SELECT * FROM bids WHERE id = %s", (bid_id,)).fetchone()
    # a bid already drafted or built keeps its stage while its RFP is read again
    conn.execute(f"UPDATE bids SET status = CASE WHEN status IN ({ADVANCED}) THEN status ELSE 'analysing' END, "
                 "error = NULL, updated_at = now() WHERE id = %s", (bid_id,))
    conn.commit()

    docs = _documents(bid)
    pages = [p for d in docs for p in d["pages"]]
    if not pages:
        raise ValueError("No readable pages in the uploaded RFP documents")
    store.index_rfp(conn, bid_id, chunk_pages(pages))
    conn.commit()

    evidence = _evidence(conn, bid_id, docs)
    data = ask(conn, purpose="rfp_extraction", system=SYSTEM, instruction=INSTRUCTION, evidence=evidence,
               schema=RFP_SCHEMA, schema_name="rfp_extraction", bid_id=bid_id, job_id=job["id"])

    # corrections made on the RFP analysis screen win, including any saved while this analysis was running
    fresh = conn.execute("SELECT analysis FROM bids WHERE id = %s FOR UPDATE", (bid_id,)).fetchone()
    previous = (fresh["analysis"] if fresh else None) or {}
    edited = previous.get("edited") or {}
    data["meta"].update({k: v for k, v in edited.items() if k in META_KEYS})
    meta = data["meta"]
    due = parse_date(meta["bid_due"]) if meta.get("bid_due") else None
    analysis = {
        "extracted": data,
        "edited": edited,
        "decisions": previous.get("decisions") or {},      # results confirmed on the bid response screen
        "documents": [{"name": d["name"], "kind": d["kind"], "path": d["path"], "pages": len(d["pages"])} for d in docs],
        "rfp_pages": len(pages),
        "sections_sent": [e.ref["label"] for e in evidence],
        "analysed_at": datetime.now(timezone.utc).isoformat(),
        # the eligibility check (stage 2) is run again on request: the RFP may have changed
    }
    conn.execute(
        f"UPDATE bids SET analysis = %s, status = CASE WHEN status IN ({ADVANCED}) THEN status ELSE 'analysed' END, "
        "rfp_ref = COALESCE(%s, rfp_ref), "
        "department = COALESCE(%s, department), due_at = COALESCE(%s, due_at), "
        "title = COALESCE(NULLIF(%s, ''), title), updated_at = now() WHERE id = %s",
        (Jsonb(analysis), meta.get("rfp_ref"), meta.get("department"), due, meta.get("title") or "", bid_id),
    )
    audit(conn, "worker", "bid.analysed", "bid", bid_id,
          {"documents": len(docs), "criteria": len(data["pq_criteria"]), "amendments": len(data["amendments"]),
           "limits": load_policy()["limits"].get("rfp_extraction")})
    conn.commit()
    return {"documents": len(docs), "criteria": len(data["pq_criteria"])}


def eligibility(conn: psycopg.Connection, analysis: dict) -> dict:
    """Stage 2: match every criterion against the organisation library (local rules, nothing is sent)."""
    data = analysis.get("extracted")
    if not data:
        raise ValueError("Analyse the RFP first")
    meta = data["meta"]
    due = parse_date(meta["bid_due"]) if meta.get("bid_due") else None
    return matcher.match(conn, data, due, analysis.get("decisions"))


def run_eligibility(conn: psycopg.Connection, job: dict) -> dict:
    bid_id = job["payload"]["bid_id"]
    # locked while matching (local rules and searches, seconds): a result confirmed on the screen meanwhile waits,
    # then applies to the new check instead of being overwritten by it
    bid = conn.execute("SELECT analysis FROM bids WHERE id = %s FOR UPDATE", (bid_id,)).fetchone()
    match = eligibility(conn, bid["analysis"] or {})
    conn.execute("UPDATE bids SET analysis = analysis || %s, updated_at = now() WHERE id = %s",
                 (Jsonb({"match": match, "matched_at": datetime.now(timezone.utc).isoformat()}), bid_id))
    audit(conn, "worker", "bid.eligibility_checked", "bid", bid_id, {"summary": match["summary"]})
    conn.commit()
    return {"summary": match["summary"]}


TARGETS_ORDER = ("datasheet", "amendments", "scope", "eligibility", "evaluation", "personnel", "formats")
