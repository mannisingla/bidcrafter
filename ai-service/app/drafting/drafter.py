"""Job: draft the bid.

Most sections are produced locally from library data with templates. Only the narrative
section ("Understanding, approach and methodology") goes through the privacy gateway,
with a handful of RFP scope extracts and library write-ups as evidence.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date

import psycopg
from jinja2 import DictLoader, Environment, select_autoescape
from markupsafe import Markup, escape
from psycopg.types.json import Jsonb

from ..config import get_settings, load_policy
from ..ingest.classify import parse_date
from ..db import audit
from ..privacy.gateway import Evidence, ask
from ..privacy.redactor import drop_account_numbers
from ..rag import store
from ..rfp import analyzer
from .sanitize import clean_html
from .templates import TEMPLATES

_env = Environment(loader=DictLoader(TEMPLATES), autoescape=select_autoescape(default=True, default_for_string=True))

NARRATIVE_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["html"],
                    "properties": {"html": {"type": "string"}}}
NARRATIVE_SYSTEM = (
    "You write sections of technical bids for an Indian IT services company responding to government RFPs. "
    "Write formal Indian English. Use only facts present in the extracts; never invent numbers, client names, "
    "dates or certifications. Where a needed fact is missing, write [[FILL: short description]]. Keep tokens "
    "such as [[PERSON_1]] exactly as they appear. Return HTML using only h3, h4, p, ul, ol, li, table, thead, "
    "tbody, tr, th, td, b and i."
)
NARRATIVE_INSTRUCTION = (
    "Draft the bid section 'Understanding, approach and methodology' with these parts: "
    "1. Our understanding of the project, from the RFP extracts. "
    "2. Scope as understood: a table with columns Component and Our response. "
    "3. Approach and methodology. 4. Information security. 5. Operations and maintenance. "
    "Adapt the company's own write-ups (library extracts) to this RFP's scope."
)

# Statutory documents every technical bid carries, attached even when no criterion cites them
STANDARD_TYPES = ("incorporation", "pan", "gst")
# Words in a PQ clause (or the documents it asks for) naming a registration document
REGISTRATION_WORDS = [
    ("incorporation", r"incorporat|companies act|registered under|legal entity|\bllp\b"),
    ("moa_aoa", r"memorandum|articles of association|\bmoa\b|\baoa\b"),
    ("pan", r"\bpan\b|permanent account"),
    ("gst", r"\bgst"),
    ("epf_esic", r"\bepf|\besi"),
]
# What a technical evaluation criterion is about, from its parameter and basis of marking
TEC_TOPICS = [
    ("legal", r"legal entity|years in operation|incorporat"),
    ("finance", r"turnover|financial|net worth|revenue"),
    ("certification", r"certif|\biso\b|cmmi|quality"),
    ("people", r"resource|manpower|professional|personnel|team|staff|\bcvs?\b|expert"),
    ("projects", r"project|experience|similar|work order|citation|integration|mobile app|implementation"),
    ("approach", r"approach|methodology|solution|architecture|work plan|understanding|operations? (?:and|&) maintenance|"
                 r"\bo&m\b|support strategy|security plan|strategy|design"),   # not "years in operation"
    ("presentation", r"presentation|\bpoc\b|proof of concept|demonstrat"),
]


def _cert_keys(d: dict) -> list[str]:
    """What a certificate is called in RFP text: its standard number(s), or CMMI."""
    label = f"{d['metadata'].get('standard') or ''} {d['title']}".lower()
    numbers = [n for n in re.findall(r"\d{4,5}", label) if not re.fullmatch(r"(19|20)\d\d", n)]   # not "2018"
    return numbers + (["cmmi"] if "cmmi" in label else [])


def standard_documents(docs: dict, extracted: dict, due: date) -> list[str]:
    """Registration documents always; certifications the RFP's criteria mention (ISO 27001, CMMI…) even when the
    eligibility check did not cite them. Only documents valid on the bid due date."""
    criteria = " ".join(json.dumps(extracted.get(k) or []) for k in ("pq_criteria", "tech_criteria")).lower()
    return [k for k, d in docs.items()
            if d["category"] == "statutory" and (d["valid_until"] is None or d["valid_until"] >= due)
            and (d["doc_type"] in STANDARD_TYPES
                 or (d["doc_type"] == "certification" and any(key in criteria for key in _cert_keys(d))))]


def pq_documents(row: dict, spec: dict, docs: dict, standard: list[str]) -> list[str]:
    """Documents for one PQ criterion: what the eligibility check cited, plus the registrations and certificates
    the clause (or the documents it asks for) names."""
    ids = [d for d in row["evidence"] if d in docs]
    if row.get("decided"):
        return list(dict.fromkeys(ids))          # documents chosen by a person: exactly those
    text = f"{row['text']} {spec.get('evidence_expected') or ''}".lower()
    if row["category"] in ("registration", "tax_registration"):
        wanted = {t for t, rx in REGISTRATION_WORDS if re.search(rx, text)}
        ids += [k for k, d in docs.items() if d["doc_type"] in wanted and d["category"] == "statutory"
                and (k in standard or d["doc_type"] in ("moa_aoa", "epf_esic"))]
    if row["category"] == "certification":
        ids += [k for k in standard if docs[k]["doc_type"] == "certification"
                and any(key in text for key in _cert_keys(docs[k]))]
    return list(dict.fromkeys(ids))


def tec_topics(t: dict) -> list[str]:
    parameter = str(t.get("parameter") or "").lower()
    topics = [name for name, rx in TEC_TOPICS if re.search(rx, f"{parameter} {t.get('basis') or ''}".lower())]
    if "people" in topics and "projects" in topics and re.search(r"personnel|team|resource|staff", parameter):
        topics.remove("projects")          # "experience of key personnel" is about people, not projects
    return topics


def tec_documents(topics: list[str], match: dict, docs: dict, standard: list[str]) -> list[str]:
    def cited(categories: set[str], types: tuple = ()) -> list[str]:
        return [d for r in match["pq"] if r["category"] in categories for d in r["evidence"]
                if d in docs and (not types or docs[d]["doc_type"] in types)]

    ids: list[str] = []
    if "legal" in topics:
        ids += [k for k in standard if docs[k]["doc_type"] == "incorporation"] + cited({"registration"}, ("incorporation", "moa_aoa"))
    if "finance" in topics:
        ids += cited({"turnover", "net_worth"})
    if "certification" in topics:
        ids += [k for k in standard if docs[k]["doc_type"] == "certification"] + cited({"certification"})
    if "projects" in topics:
        ids += cited({"project_experience", "domain_experience"})
    if "people" in topics:
        ids += cited({"manpower"}) + [p["document_id"] for p in match["personnel"] if p.get("document_id")]
    return [d for d in dict.fromkeys(ids) if d in docs]


def pq_compliance(row: dict, has_form: bool):
    if row["status"] == "met":
        return "Complied"
    if row["status"] == "undertaking" or has_form:
        return "Complied – undertaking / certificate enclosed"
    return {"partial": _fill("partially complied – confirm"), "review": _fill("to be confirmed")}.get(
        row["status"], _fill("not complied – document to be provided"))


def _number(value) -> float | None:
    """A value typed in the Details box may be text ("12.6"): the templates format numbers."""
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _short(text: str, limit: int = 60) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def _fill(text: str) -> Markup:
    return Markup(f'<mark class="fill">[{escape(text)}]</mark>')


def _xref(kind: str, target) -> Markup:
    return Markup(f'<span class="xref" data-{kind}="{escape(str(target))}">Pg. 000–000</span>')


def _render(name: str, **ctx) -> str:
    return _env.get_template(name).render(fill=_fill, xref=_xref, **ctx)


def _narrative(conn, bid, job) -> tuple[str, list[str]]:
    scope = store.search(conn, "scope of work functional requirements modules deliverables", bid_id=bid["id"], k=6)
    library = store.search(conn, "approach methodology implementation security operations maintenance",
                           categories=["literature"], k=6, include_restricted=False)
    evidence = [Evidence(drop_account_numbers(h["content"]), {"kind": "rfp", "sensitivity": "public",
                                        "label": f"RFP pages {h['page_from']}-{h['page_to']}"}) for h in scope]
    evidence += [Evidence(drop_account_numbers(h["content"]), {"kind": "library", "id": str(h["document_id"]), "label": h["title"],
                                         "sensitivity": h["sensitivity"]}) for h in library]
    answer = ask(conn, purpose="section_drafting", system=NARRATIVE_SYSTEM, instruction=NARRATIVE_INSTRUCTION,
                 evidence=evidence, schema=NARRATIVE_SCHEMA, schema_name="narrative", bid_id=bid["id"],
                 job_id=job["id"])
    sources = [e.ref["label"] for e in evidence]
    return clean_html(answer["html"]), sources


def run(conn: psycopg.Connection, job: dict) -> dict:
    bid_id = job["payload"]["bid_id"]
    overwrite = bool(job["payload"].get("overwrite"))
    bid = conn.execute("SELECT * FROM bids WHERE id = %s", (bid_id,)).fetchone()
    analysis = bid["analysis"] or {}
    if not analysis.get("match"):
        # drafting without the eligibility check on the bid response screen: run it now (local rules)
        analysis["match"] = analyzer.eligibility(conn, analysis)
        conn.execute("UPDATE bids SET analysis = analysis || %s WHERE id = %s", (Jsonb({"match": analysis["match"]}), bid_id))
    if conn.execute("SELECT 1 FROM bid_sections WHERE bid_id = %s LIMIT 1", (bid_id,)).fetchone() and not overwrite:
        conn.commit()     # a second "Generate draft" (e.g. clicked twice): the draft is there, nothing to do
        return {"skipped": "a draft already exists; use Redraft to replace it"}
    conn.execute("UPDATE bids SET status = 'drafting', error = NULL, updated_at = now() WHERE id = %s", (bid_id,))
    conn.commit()

    narrative_html, narrative_sources = _narrative(conn, bid, job)

    s = get_settings()
    company = {"name": s.company_name, "city": s.company_city, "cin": s.company_cin}
    rfp = analysis["extracted"]["meta"]
    match = analysis["match"]
    docs = {str(d["id"]): d for d in conn.execute(
        "SELECT id, doc_type, category, title, metadata, extracted_text, valid_until FROM library_documents "
        "WHERE status = 'ready' AND sensitivity <> 'restricted' "      # restricted documents never go into a bid
        "AND (metadata->>'bid_id' IS NULL OR metadata->>'bid_id' = %s)", (str(bid_id),)   # nor another bid's own
    ).fetchall()}
    own = [k for k, d in docs.items() if (d["metadata"] or {}).get("bid_id") == str(bid_id)]   # uploaded for this bid
    first = lambda t: next((k for k, d in docs.items() if d["doc_type"] == t), None)  # noqa: E731

    sections: list[dict] = []

    def add(kind, title, html="", doc_ids=(), sources=()):
        sid = str(uuid.uuid4())
        sections.append({"id": sid, "kind": kind, "title": title, "html": html,
                         "docs": [d for d in doc_ids if d in docs], "sources": list(sources)})
        return sid

    add("cover", "Cover page")
    add("index", "Index")
    cert_line = ", ".join(sorted({str(d["metadata"].get("standard")) for d in docs.values()
                                  if d["doc_type"] == "certification" and d["metadata"].get("standard")}))
    add("text", "Covering letter", _render("covering_letter", company=company, rfp=rfp, certifications=cert_line),
        sources=["RFP bid data sheet", "Template: covering letter"])
    # the bid security uploaded for this bid goes right after the covering letter
    security = [k for k in own if docs[k]["doc_type"] in ("emd", "tender_fee")]
    if security:
        add("docs", "EMD / bid security and tender fee", doc_ids=security)

    profile = next((d for d in docs.values() if d["doc_type"] == "company_profile"), None)
    profile_html = clean_html("".join(f"<p>{escape(p.strip())}</p>"
                                      for p in (profile["extracted_text"] or "").split("\n\n")[:12] if p.strip())) if profile else ""
    certs = [{"id": k, "title": d["title"]} for k, d in docs.items() if d["doc_type"] == "certification"]
    details = {"pan": (docs.get(first("pan")) or {}).get("metadata", {}).get("pan"),
               "gstin": (docs.get(first("gst")) or {}).get("metadata", {}).get("gstin")}
    refs = {"incorporation": first("incorporation"), "pan": first("pan"), "gst": first("gst")}
    add("text", "Bidder’s profile", _render("bidder_profile", company=company, refs=refs, certs=certs,
                                             details=details, profile_html=profile_html),
        sources=["Library: statutory documents", "Library: company profile"])

    pq_section_index = len(sections)
    add("text", "Pre-qualification compliance", "", sources=["RFP eligibility criteria", "Local matching rules"])

    due = (parse_date(rfp["bid_due"]) if rfp.get("bid_due") else None) or date.today()
    extracted = analysis["extracted"]
    standard = standard_documents(docs, extracted, due)
    enclosed_at: dict[str, str] = {d: "EMD" for d in security}     # document -> where it is enclosed (once)
    separators: list[dict] = []            # rendered last: they refer to sections and pages further on

    used_labels: set[str] = set()

    def unique(label: str) -> str:
        """Two criteria with the same clause (or "PQ 1" for an unnumbered one next to a clause "1") keep apart."""
        n, out = 2, label
        while out in used_labels:
            out, n = f"{label} ({n})", n + 1
        used_labels.add(out)
        return out

    def enclose(label: str, doc_ids: list[str], title: str | None = None) -> None:
        new = [d for d in dict.fromkeys(doc_ids) if d in docs and d not in enclosed_at]
        for d in new:
            enclosed_at[d] = label
        if new:
            add("docs", title or f"{label} – supporting documents", doc_ids=new)

    # One separator page per PQ criterion (the clause and our compliance), followed by its documents
    pq_specs = extracted.get("pq_criteria") or []
    for i, r in enumerate(match["pq"]):
        spec = pq_specs[i] if i < len(pq_specs) else {}
        label = unique(f"PQ {r['clause'] or i + 1}")
        ids = pq_documents(r, spec, docs, standard)
        add("text", f"{label} – {_short(r['text'])}", sources=[f"RFP clause {r['clause']}".strip(), "Eligibility check"])
        separators.append({"index": len(sections) - 1, "kind": "pq", "label": label, "row": r, "spec": spec, "doc_ids": ids})
        enclose(label, ids)

    # The same for each technical evaluation criterion (TEC), after a summary table
    tec_rows = extracted.get("tech_criteria") or []
    tec_section_index = None
    if tec_rows:
        tec_section_index = len(sections)
        add("text", "Technical evaluation compliance", sources=["RFP technical evaluation criteria"])
        for i, t in enumerate(tec_rows):
            label = unique(f"TEC {t.get('clause') or i + 1}")
            topics = tec_topics(t)
            ids = tec_documents(topics, match, docs, standard)
            add("text", f"{label} – {_short(t.get('parameter'))}",
                sources=[f"RFP clause {t.get('clause') or ''}".strip(), "Technical evaluation criteria"])
            separators.append({"index": len(sections) - 1, "kind": "tec", "label": label, "row": t, "topics": topics, "doc_ids": ids})
            enclose(label, ids)

    leftover = [d for d in standard if d not in enclosed_at]
    if leftover:
        enclose("Annexure: Other statutory documents", leftover, title="Annexure: Other statutory documents")

    project_rows = [r for r in match["pq"] if r["category"] == "project_experience"]
    project_ids = [d for r in project_rows for d in r["evidence"] if d in docs and docs[d]["doc_type"] == "work_order"]
    projects = []
    for pid in dict.fromkeys(project_ids):
        m = docs[pid]["metadata"]
        projects.append({"id": pid, "project": m.get("project") or docs[pid]["title"], "client": m.get("client"),
                         "value_cr": _number(m.get("value_cr")), "period": m.get("period"), "scope": m.get("scope"),
                         "completed": m.get("completed", True),
                         "certificates": [k for k, d in docs.items() if str(d["metadata"].get("work_order_id")) == pid]})
    citations_id = add("text", "Project citations", _render("citations", projects=projects),
                       sources=["Library: work orders", "RFP project experience criteria"]) if projects else None

    narrative_id = add("text", "Understanding, approach and methodology", narrative_html, sources=narrative_sources)

    people = match["personnel"]
    personnel_id = None
    if people:
        personnel_id = add("text", "Key personnel", _render("personnel", people=people), sources=["RFP key personnel", "Library: CVs"])
        cv_ids = [p["document_id"] for p in people if p.get("document_id") and p["document_id"] not in enclosed_at]
        if cv_ids:
            enclose("Annexure: CVs of key personnel", cv_ids, title="Annexure: CVs of key personnel")

    form_sections = {}
    form_sections["blacklisting"] = add("text", "Undertaking on non-blacklisting",
                                        _render("undertaking_blacklisting", company=company, rfp=rfp))
    if any(r["category"] == "local_presence" for r in match["pq"]):
        form_sections["local_presence"] = add("text", "Undertaking on local office",
                                              _render("undertaking_local_office", company=company, rfp=rfp))
    if any(r["category"] == "manpower" and r["status"] == "gap" for r in match["pq"]):
        # same outcome as the prototype's "Draft certificate": a certificate with fields to fill, linked to the clause
        form_sections["manpower"] = add("text", "Certificate of IT manpower strength",
                                        _render("manpower_certificate", company=company, rfp=rfp),
                                        sources=["RFP manpower criterion", "Drafted by agent; HR register count needed"])
    # the power of attorney uploaded for this bid, else the library's
    poa = sorted((k for k, d in docs.items() if d["doc_type"] == "power_of_attorney" and k not in enclosed_at),
                 key=lambda k: k not in own)
    if poa:
        enclose("Annexure: Power of attorney", poa[:1], title="Annexure: Power of attorney / board resolution")

    titles = {x["id"]: x["title"] for x in sections}
    pq_rows = []
    for r in match["pq"]:
        sec = form_sections.get(r["category"])
        pq_rows.append({**r, "form_section": sec, "form_title": titles.get(sec, "") if sec else ""})
    sections[pq_section_index]["html"] = _render("pq_matrix", pq=pq_rows, docs=docs)

    # Separator pages and the TEC table, now that every section and document has its place
    topic_sections = {"projects": [citations_id], "approach": [narrative_id],
                      "people": [personnel_id, form_sections.get("manpower")]}
    tec_table = []
    for s in separators:
        r = s["row"]
        documents = [{"id": d, "title": docs[d]["title"], "earlier": enclosed_at.get(d) != s["label"]} for d in s["doc_ids"]]
        if s["kind"] == "pq":
            form = form_sections.get(r["category"])
            refs = [{"id": form, "title": titles[form]}] if form else []
            html = _render("separator", kind_label="Pre-qualification criterion", clause=r["clause"], text=r["text"],
                           facts=[("Documents asked for by the RFP", s["spec"].get("evidence_expected"))],
                           compliance=pq_compliance(r, bool(form)), documents=documents, sections=refs, remarks=r["note"])
        else:
            refs = [{"id": sid, "title": titles[sid]} for t in s["topics"] for sid in topic_sections.get(t, []) if sid]
            note = "To be demonstrated at the technical presentation." if "presentation" in s["topics"] else ""
            compliance = ("Enclosed – see the supporting documents and sections below" if documents or refs
                          else "At the technical presentation" if note else _fill("response to be added"))
            marks = r.get("max_marks")
            html = _render("separator", kind_label="Technical evaluation criterion", clause=r.get("clause"),
                           text=r.get("parameter"), compliance=compliance, documents=documents, sections=refs, remarks=note,
                           facts=[("Maximum marks", f"{marks:g}" if isinstance(marks, (int, float)) else ""),
                                  ("Basis of marking", r.get("basis"))])
            tec_table.append({"clause": r.get("clause") or "", "parameter": r.get("parameter"), "max_marks": marks,
                              "documents": documents, "sections": refs, "note": note})
        sections[s["index"]]["html"] = html
    if tec_section_index is not None:
        sections[tec_section_index]["html"] = _render("tec_matrix", tec=tec_table)

    conn.execute("DELETE FROM bid_sections WHERE bid_id = %s", (bid_id,))
    for pos, sec in enumerate(sections):
        conn.execute(
            "INSERT INTO bid_sections (id, bid_id, position, kind, title, html, sources) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (sec["id"], bid_id, pos, sec["kind"], sec["title"], sec["html"], Jsonb(sec["sources"])),
        )
        for i, doc_id in enumerate(sec["docs"]):
            conn.execute("INSERT INTO bid_section_documents (section_id, document_id, position) VALUES (%s, %s, %s)",
                         (sec["id"], doc_id, i))
    conn.execute("UPDATE bids SET status = 'drafted', updated_at = now() WHERE id = %s", (bid_id,))
    audit(conn, "worker", "bid.drafted", "bid", bid_id, {"sections": len(sections)})
    conn.commit()
    return {"sections": len(sections)}


REDRAFT_TONES = {"formal": "formal and complete", "concise": "concise, removing repetition", "detailed": "detailed, expanding on method and evidence"}
_MARK = __import__("re").compile(r'<mark class="fill">\[([^\]]*)\]</mark>')


def redraft(conn: psycopg.Connection, job: dict) -> dict:
    """Job: rewrite one section through the privacy gateway, keeping fill-in fields and page references."""
    p = job["payload"]
    sec = conn.execute("SELECT * FROM bid_sections WHERE id = %s AND bid_id = %s", (p["section_id"], p["bid_id"])).fetchone()
    if sec is None or sec["kind"] != "text":
        raise ValueError("Only text sections can be redrafted")
    tone = REDRAFT_TONES.get(p.get("tone") or "formal", REDRAFT_TONES["formal"])
    current = _MARK.sub(lambda m: f"[[FILL: {m.group(1)}]]", sec["html"])
    query = f"{sec['title']} {p.get('instruction') or ''}"
    limit = load_policy()["limits"].get("section_redraft", {}).get("max_chars_per_item", 12000)
    if len(current) > limit:      # the gateway would cut it, and the cut-off part would be lost from the section
        raise ValueError(f"This section is too long to redraft in one go ({len(current):,} characters; the limit is "
                         f"{limit:,}). Split it into smaller sections first.")
    scope = store.search(conn, query, bid_id=p["bid_id"], k=4)
    library = store.search(conn, query, categories=["literature", "experience"], k=4, include_restricted=False)
    # the draft is built locally from library documents (profile, citations…): confidential, sent redacted
    evidence = [Evidence(drop_account_numbers(current), {"kind": "draft", "label": f"Current draft of '{sec['title']}'",
                                                         "sensitivity": "confidential"})]
    evidence += [Evidence(drop_account_numbers(h["content"]), {"kind": "rfp", "sensitivity": "public", "label": f"RFP pages {h['page_from']}-{h['page_to']}"}) for h in scope]
    evidence += [Evidence(drop_account_numbers(h["content"]), {"kind": "library", "id": str(h["document_id"]), "label": h["title"],
                                                               "sensitivity": h["sensitivity"]}) for h in library]
    instruction = (
        f"Redraft the bid section '{sec['title']}' (the first extract is the current draft). Make it {tone}. "
        f"Reviewer's instruction: {p.get('instruction') or 'improve clarity and alignment with the RFP'}. "
        "Keep every [[FILL: ...]] marker and every <span class=\"xref\" ...> element exactly as they are."
    )
    answer = ask(conn, purpose="section_redraft", system=NARRATIVE_SYSTEM, instruction=instruction, evidence=evidence,
                 schema=NARRATIVE_SCHEMA, schema_name="narrative", bid_id=p["bid_id"], job_id=job["id"])
    html = clean_html(answer["html"])
    sources = [e.ref["label"] for e in evidence[1:]]
    conn.execute("UPDATE bid_sections SET html = %s, sources = %s, updated_at = now() WHERE id = %s",
                 (html, Jsonb(sources), sec["id"]))
    audit(conn, "worker", "section.redrafted", "section", sec["id"], {"tone": p.get("tone")})
    conn.commit()
    return {"section_id": str(sec["id"])}
