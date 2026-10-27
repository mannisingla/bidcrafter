"""Job sync_draft: bring an existing bid draft in line with the results confirmed on the bid response screen,
without generating it again (which would lose a person's edits).

For each pre-qualification criterion a person decided (or reset), its "PQ x – supporting documents" section is set
to the documents chosen there: each document is still enclosed once, at the first criterion needing it, and later
criteria refer to it. Its separator page and the PQ compliance table are rendered again, unless a person edited
them since the draft was made. The CVs of key personnel follow the CVs chosen for each position. Nothing else in
the draft changes. A document taken out that another page of the bid still refers to stays enclosed, after the
first criterion referring to it (or in an annexure).
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

import psycopg

from ..db import audit
from ..ingest.classify import parse_date
from .drafter import _render, pq_compliance, pq_documents, standard_documents

CRITERION = re.compile(r"^((?:PQ|TEC) .+?) – ")
DOC_REF = re.compile(r'data-doc="([^"]+)"')
FORM_TITLES = {"blacklisting": "Undertaking on non-blacklisting", "local_presence": "Undertaking on local office",
               "manpower": "Certificate of IT manpower strength"}
CV_ANNEXURE = "Annexure: CVs of key personnel"
OTHER_ANNEXURE = "Annexure: Other documents"
GRACE = timedelta(minutes=2)       # sections saved within this of the draft being made are as drafted


def pq_labels(rows: list[dict]) -> list[str]:
    """The drafter's section labels for the PQ criteria: "PQ 6.1.3", and "PQ 4 (2)" for a repeated clause."""
    used, labels = set(), []
    for i, r in enumerate(rows):
        base = f"PQ {r.get('clause') or i + 1}"
        n, label = 2, base
        while label in used:
            label, n = f"{base} ({n})", n + 1
        used.add(label)
        labels.append(label)
    return labels


class Draft:
    """The draft's sections in order, and the documents of each documents section, changed in memory."""

    def __init__(self, sections: list[dict], members: dict[str, list[str]], drafted_at):
        self.sections = {s["id"]: s for s in sections}
        self.order = [s["id"] for s in sections]
        self.members = members
        self.drafted_at = drafted_at
        self.changed_members: set[str] = set()
        self.changed_html: set[str] = set()
        self.created: set[str] = set()
        self.deleted: set[str] = set()

    def find(self, kind: str, title: str | None = None, prefix: str | None = None) -> dict | None:
        for sid in self.order:
            s = self.sections[sid]
            if s["kind"] != kind:
                continue
            if title is not None and s["title"] == title:
                return s
            if prefix is not None and s["title"].startswith(prefix) and not s["title"].endswith("supporting documents"):
                return s
        return None

    def separator(self, label: str) -> dict | None:
        return self.find("text", prefix=f"{label} – ")

    def documents_section(self, label: str) -> dict | None:
        return self.find("docs", title=f"{label} – supporting documents")

    def enclosed_before(self, sid: str) -> set[str]:
        out: set[str] = set()
        for other in self.order[: self.order.index(sid)]:
            s = self.sections[other]
            if s["kind"] == "docs" and s["include"]:
                out.update(self.members.get(other, []))
        return out

    def enclosing(self) -> dict[str, str]:
        """document -> the (first) included documents section holding it"""
        out: dict[str, str] = {}
        for sid in self.order:
            s = self.sections[sid]
            if s["kind"] == "docs" and s["include"]:
                for d in self.members.get(sid, []):
                    out.setdefault(d, sid)
        return out

    def insert_after(self, anchor: str | None, title: str) -> str:
        sid = str(uuid.uuid4())
        self.sections[sid] = {"id": sid, "kind": "docs", "title": title, "html": "", "include": True, "updated_at": None}
        self.order.insert(self.order.index(anchor) + 1 if anchor else len(self.order), sid)
        self.members[sid] = []
        self.created.add(sid)
        return sid

    def set_members(self, sid: str, docs: list[str]) -> None:
        if self.members.get(sid, []) != docs:
            self.members[sid] = docs
            self.changed_members.add(sid)
        self.sections[sid]["include"] = True

    def remove(self, sid: str) -> None:
        self.order.remove(sid)
        if sid in self.created:
            self.created.discard(sid)
        else:
            self.deleted.add(sid)

    def as_drafted(self, s: dict) -> bool:
        """Not edited by a person since the draft was made (this job does not mark its own changes)."""
        return self.drafted_at is not None and s["updated_at"] is not None and s["updated_at"] <= self.drafted_at + GRACE

    def set_html(self, s: dict, html: str) -> bool:
        if not self.as_drafted(s):
            return False
        if s["html"] != html:
            s["html"] = html
            self.changed_html.add(s["id"])
        return True


def _load(conn: psycopg.Connection, bid_id) -> Draft | None:
    rows = conn.execute("SELECT id, kind, title, html, include, updated_at FROM bid_sections WHERE bid_id = %s "
                        "ORDER BY position", (bid_id,)).fetchall()
    if not rows:
        return None
    sections = [{**r, "id": str(r["id"])} for r in rows]
    members: dict[str, list[str]] = {}
    for r in conn.execute("SELECT sd.section_id, sd.document_id FROM bid_section_documents sd "
                          "JOIN bid_sections s ON s.id = sd.section_id WHERE s.bid_id = %s ORDER BY sd.position", (bid_id,)):
        members.setdefault(str(r["section_id"]), []).append(str(r["document_id"]))
    drafted = conn.execute("SELECT max(at) AS at FROM audit_log WHERE action = 'bid.drafted' AND entity_id = %s",
                           (str(bid_id),)).fetchone()
    return Draft(sections, members, drafted["at"] if drafted else None)


def _save(conn: psycopg.Connection, bid_id, draft: Draft) -> None:
    for sid in draft.deleted:
        conn.execute("DELETE FROM bid_sections WHERE id = %s", (sid,))
    for sid in draft.created:
        s = draft.sections[sid]
        conn.execute("INSERT INTO bid_sections (id, bid_id, position, kind, title, html, sources, include) "
                     "VALUES (%s, %s, %s, %s, %s, '', '[]', true)", (sid, bid_id, 0, s["kind"], s["title"]))
    for sid in draft.changed_html - draft.created:
        conn.execute("UPDATE bid_sections SET html = %s WHERE id = %s", (draft.sections[sid]["html"], sid))
    for sid in (draft.changed_members | draft.created) - draft.deleted:
        conn.execute("UPDATE bid_sections SET include = true WHERE id = %s", (sid,))
        conn.execute("DELETE FROM bid_section_documents WHERE section_id = %s", (sid,))
        for i, d in enumerate(draft.members.get(sid, [])):
            conn.execute("INSERT INTO bid_section_documents (section_id, document_id, position) VALUES (%s, %s, %s)", (sid, d, i))
    for pos, sid in enumerate(draft.order):           # positions in order again (also closes gaps left by deletions)
        conn.execute("UPDATE bid_sections SET position = %s WHERE id = %s", (pos, sid))


def sync(conn: psycopg.Connection, bid_id, reset: list[int] = (), personnel: bool = False) -> dict:
    bid = conn.execute("SELECT analysis FROM bids WHERE id = %s FOR UPDATE", (bid_id,)).fetchone()
    analysis = (bid or {}).get("analysis") or {}
    match, extracted = analysis.get("match"), analysis.get("extracted")
    draft = _load(conn, bid_id)
    if not match or not extracted or draft is None:
        conn.commit()
        return {"skipped": "no eligibility check or no draft"}
    docs = {str(d["id"]): d for d in conn.execute(
        "SELECT id, doc_type, category, title, metadata, valid_until FROM library_documents "
        "WHERE status = 'ready' AND sensitivity <> 'restricted' "
        "AND (metadata->>'bid_id' IS NULL OR metadata->>'bid_id' = %s)", (str(bid_id),)).fetchall()}
    due = (parse_date(extracted["meta"]["bid_due"]) if extracted["meta"].get("bid_due") else None) or date.today()
    standard = standard_documents(docs, extracted, due)
    specs = extracted.get("pq_criteria") or []
    labels = pq_labels(match["pq"])

    synced, not_found, text_kept, removed = [], [], [], set()
    rows = [(i, r) for i, r in enumerate(match["pq"]) if r.get("decided") or i in reset]
    plans = []
    for i, r in rows:
        sep = draft.separator(labels[i])
        if sep is None:
            not_found.append(labels[i])
            continue
        plans.append((draft.order.index(sep["id"]), i, r, sep))
    for _, i, r, sep in sorted(plans, key=lambda p: p[0]):
        label = labels[i]
        wanted = pq_documents(r, specs[i] if i < len(specs) else {}, docs, standard)
        own = draft.documents_section(label)
        mine = [d for d in wanted if d not in draft.enclosed_before(sep["id"])]
        for sid in list(draft.order[draft.order.index(sep["id"]) + 1:]):      # enclosed later: moves up to here
            if draft.sections[sid]["kind"] == "docs" and (own is None or sid != own["id"]) and set(draft.members.get(sid, [])) & set(mine):
                draft.set_members(sid, [d for d in draft.members[sid] if d not in mine])
                if not draft.members[sid]:
                    draft.remove(sid)
        if own is not None:
            removed |= set(draft.members.get(own["id"], [])) - set(mine)
        if mine:
            sid = own["id"] if own is not None else draft.insert_after(sep["id"], f"{label} – supporting documents")
            draft.set_members(sid, mine)
        elif own is not None:
            draft.remove(own["id"])
        synced.append((i, r, sep, wanted))

    # Key personnel: the CVs chosen for each position
    people = match.get("personnel") or []
    if people and (personnel or any(p.get("decided") for p in people)):
        table = draft.find("text", title="Key personnel")
        annex = draft.find("docs", title=CV_ANNEXURE)
        anchor = annex or table
        if anchor is None:
            not_found.append("Key personnel")
        else:
            cvs = list(dict.fromkeys(p["document_id"] for p in people if p.get("document_id") in docs))
            mine = [d for d in cvs if d not in draft.enclosed_before(anchor["id"])]
            if annex is not None:
                removed |= set(draft.members.get(annex["id"], [])) - set(mine)
            if mine:
                draft.set_members(annex["id"] if annex is not None else draft.insert_after(table["id"], CV_ANNEXURE), mine)
            elif annex is not None:
                draft.remove(annex["id"])
            if table is not None and not draft.set_html(table, _render("personnel", people=people)):
                text_kept.append("Key personnel")

    # Separator pages and the PQ compliance table, now that the documents have their places
    titles = {draft.sections[sid]["title"]: sid for sid in draft.order}
    forms = {cat: titles[t] for cat, t in FORM_TITLES.items() if t in titles}
    at = draft.enclosing()
    for i, r, sep, wanted in synced:
        own = draft.documents_section(labels[i])
        form = forms.get(r["category"])
        html = _render("separator", kind_label="Pre-qualification criterion", clause=r["clause"], text=r["text"],
                       facts=[("Documents asked for by the RFP", (specs[i] if i < len(specs) else {}).get("evidence_expected"))],
                       compliance=pq_compliance(r, bool(form)),
                       documents=[{"id": d, "title": docs[d]["title"], "earlier": own is None or at.get(d) != own["id"]}
                                  for d in wanted],
                       sections=[{"id": form, "title": draft.sections[form]["title"]}] if form else [], remarks=r["note"])
        if not draft.set_html(sep, html):
            text_kept.append(labels[i])
    # The PQ table lists for each criterion the documents of its separator page, as the draft has them now (for a
    # criterion not decided, the eligibility check run since the draft may cite others: the draft is what counts)
    matrix = draft.find("text", title="Pre-qualification compliance")
    if matrix is not None and synced:
        chosen = {i: wanted for i, _, _, wanted in synced}
        rows_out = []
        for i, r in enumerate(match["pq"]):
            sep = draft.separator(labels[i])
            evidence = chosen[i] if i in chosen else DOC_REF.findall(sep["html"] or "") if sep else []
            rows_out.append({**r, "evidence": list(dict.fromkeys(evidence)), "form_section": forms.get(r["category"]),
                             "form_title": FORM_TITLES.get(r["category"]) if forms.get(r["category"]) else ""})
        if not draft.set_html(matrix, _render("pq_matrix", pq=rows_out, docs=docs)):
            text_kept.append("Pre-qualification compliance")

    # A document taken out that a page of the bid still refers to stays in the bid (the PQ table only repeats the
    # separator pages, so it does not count)
    kept = []
    at = draft.enclosing()
    for d in sorted(removed - set(at)):
        ref = next((sid for sid in draft.order if draft.sections[sid]["kind"] != "docs" and draft.sections[sid]["include"]
                    and (matrix is None or sid != matrix["id"]) and f'data-doc="{d}"' in (draft.sections[sid]["html"] or "")), None)
        if ref is None:
            continue
        m = CRITERION.match(draft.sections[ref]["title"])
        if m:           # after the criterion referring to it
            target = draft.documents_section(m.group(1))
            sid = target["id"] if target else draft.insert_after(ref, f"{m.group(1)} – supporting documents")
        else:
            target = draft.find("docs", title=OTHER_ANNEXURE)
            sid = target["id"] if target else draft.insert_after(None, OTHER_ANNEXURE)
        draft.set_members(sid, draft.members.get(sid, []) + [d])
        kept.append({"document": docs[d]["title"] if d in docs else d, "referred to by": draft.sections[ref]["title"]})

    _save(conn, bid_id, draft)
    result = {"criteria": [labels[i] for i, *_ in synced], "not_in_draft": not_found,
              "text_kept_as_edited": text_kept, "kept_because_referred_to": kept}
    if draft.changed_members or draft.changed_html or draft.created or draft.deleted:
        conn.execute("UPDATE bids SET updated_at = now() WHERE id = %s", (bid_id,))
        audit(conn, "worker", "bid.draft_synced", "bid", bid_id, result)
    conn.commit()
    return result


def run(conn: psycopg.Connection, job: dict) -> dict:
    p = job["payload"]
    return sync(conn, p["bid_id"], reset=[int(i) for i in p.get("pq") or []], personnel=bool(p.get("personnel")))
