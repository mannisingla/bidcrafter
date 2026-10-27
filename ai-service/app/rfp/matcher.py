"""Match RFP criteria to the organisation library, on this server.

Where the library holds structured details (turnover, certificate validity, project
values, CV experience) the decision is a plain rule, so it can be checked. Anything
else falls back to local vector search and is marked "review" for a person to confirm.

Metadata conventions per doc_type are documented in README.md.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta

import psycopg

from ..rag import store

UNDERTAKING_CATEGORIES = {"blacklisting", "local_presence"}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _docs(conn: psycopg.Connection) -> list[dict]:
    return conn.execute(
        "SELECT id, category, doc_type, title, metadata, valid_until, sensitivity FROM library_documents "
        # restricted: never evidence; a bid's own documents (EMD, power of attorney…) are not the library's
        "WHERE status = 'ready' AND sensitivity <> 'restricted' AND metadata->>'bid_id' IS NULL ORDER BY created_at"
    ).fetchall()


def _of(docs, *types):
    return [d for d in docs if d["doc_type"] in types]


def _result(c, status, evidence=(), note=""):
    return {"clause": c.get("clause", ""), "text": c.get("text", ""), "category": c.get("category", "other"),
            "status": status, "evidence": [str(e) for e in evidence], "note": note}


def _to_crore(value, unit):
    if value is None:
        return None
    unit = (unit or "crore").lower()
    if "crore" in unit or re.search(r"\bcr\b", unit):      # before "rupee": "Rupees in crore" is crore
        return value
    if "lakh" in unit or "lac" in unit:
        return value / 100
    if "rupee" in unit or unit in ("inr", "rs", "₹"):
        return value / 1e7
    return value


def _year_series(meta: dict, key: str) -> list[float]:
    series = meta.get(key) or {}
    if isinstance(series, dict):
        return [float(v) for _, v in sorted(series.items()) if _num(v) is not None]
    return []


def _match_certification(c, docs, ref):
    text = c["text"]
    wanted = []
    for m in re.finditer(r"ISO\s*/?\s*(?:IEC\s*)?(\d{4,5})", text, re.I):
        wanted.append(("ISO", m.group(1)))
    if (m := re.search(r"CMMI[^.\n]{0,60}?(?:level|ML)\s*(\d)", text, re.I)) or re.search(r"\bCMMI\b", text, re.I):
        wanted.append(("CMMI", m.group(1) if m else "1"))
    if not wanted:
        return None
    certs = _of(docs, "certification")
    evidence, notes, worst = [], [], "met"
    rank = {"met": 0, "partial": 1, "gap": 2}
    for kind, code in wanted:
        if kind == "ISO":
            hits = [d for d in certs if code in str(d["metadata"].get("standard", "")) or code in d["title"]]
        else:
            hits = [d for d in certs if "cmmi" in (str(d["metadata"].get("standard", "")) + d["title"]).lower()
                    and (_num(d["metadata"].get("level")) or 0) >= int(code)]
        label = f"{kind} {code}" if kind == "ISO" else f"CMMI level {code}+"
        if not hits:
            notes.append(f"No {label} certificate in the library")
            worst = "gap"
            continue
        best = max(hits, key=lambda d: d["valid_until"] or date.min)
        evidence.append(best["id"])
        vu = best["valid_until"]
        if vu is None:
            status, note = "partial", f"{label}: no expiry date recorded"
        elif vu < ref:
            status, note = "gap", f"{label} expired on {vu:%d %b %Y}"
        elif vu < ref + timedelta(days=180):
            status, note = "partial", f"{label} expires {vu:%d %b %Y}, soon after the due date"
        else:
            status, note = "met", f"{label} valid till {vu:%d %b %Y}"
        notes.append(note)
        worst = max(worst, status, key=rank.get)
    return worst, evidence, "; ".join(notes)


def _match_projects(c, docs, ref):
    orders = [d for d in _of(docs, "work_order") if _num(d["metadata"].get("value_cr")) is not None]
    window = _num(c.get("window_years"))
    if window:
        cutoff = ref.year - int(window)
        orders = [d for d in orders if (_num(d["metadata"].get("end_year")) or ref.year) >= cutoff]
    completions = _of(docs, "completion_certificate", "performance_certificate")

    def completed(order):
        if order["metadata"].get("completed"):
            return True
        return any(str(cc["metadata"].get("work_order_id")) == str(order["id"]) for cc in completions)

    eligible = sorted((d for d in orders if completed(d)), key=lambda d: -_num(d["metadata"]["value_cr"]))
    alternatives = c.get("alternatives") or []
    if not alternatives and _num(c.get("threshold_value")):
        alternatives = [{"count": 1, "min_value_cr": _to_crore(_num(c["threshold_value"]), c.get("threshold_unit"))}]
    if not alternatives:
        return None
    for alt in alternatives:
        picks = [d for d in eligible if _num(d["metadata"]["value_cr"]) >= alt["min_value_cr"]][: int(alt["count"])]
        if len(picks) >= int(alt["count"]):
            ev = []
            for p in picks:
                ev.append(p["id"])
                ev += [cc["id"] for cc in completions if str(cc["metadata"].get("work_order_id")) == str(p["id"])]
            values = ", ".join(f"₹ {_num(p['metadata']['value_cr']):.2f} Cr" for p in picks)
            return "met", ev, f"Meets: {int(alt['count'])} project(s) of ₹ {alt['min_value_cr']} Cr or more ({values})"
    best = eligible[:3]
    return ("partial" if best else "gap"), [d["id"] for d in best], \
        "No combination of completed projects meets the thresholds; check work-order values and completion status"


# Audited financial statements come in two versions in the library: short (abridged: auditor's report, balance
# sheet, profit and loss, cash flow) and full (with all notes and schedules, or the annual report). The short one
# is enclosed unless the RFP asks for the full accounts.
FULL_ACCOUNTS = re.compile(
    r"\b(?:full|complete|entire)\b[^.;]{0,60}\b(?:audited|accounts|financial statements?|balance sheets?)"
    r"|annual reports?|notes (?:to|forming part of) (?:the )?(?:accounts|financial statements)"
    r"|schedules? (?:to|of|forming part of) (?:the )?(?:accounts|balance sheets?)|directors'? report", re.I)
ASKS_FOR_ACCOUNTS = re.compile(r"audited|balance sheets?|financial statements?|profit (?:and|&) loss|annual accounts", re.I)


def financial_statements(docs, c) -> tuple[list, str]:
    """The audited financial statements to enclose for a criterion, latest years first (as many years as the
    criterion covers, three unless it says), and a note on what was chosen."""
    fs = _of(docs, "financial_statement")
    if not fs:
        return [], ""
    want = "full" if FULL_ACCOUNTS.search(f"{c.get('text', '')} {c.get('evidence_expected') or ''}") else "short"
    years = int(_num(c.get("window_years")) or 3)
    years = years if 1 <= years <= 7 else 3

    def version(d) -> str:
        return str(d["metadata"].get("version") or "").strip().lower()

    by_year: dict[str, list] = {}
    for d in fs:
        by_year.setdefault(str(d["metadata"].get("financial_year") or "").strip(), []).append(d)
    # per year: the version wanted, else one without a version recorded, else the other one
    chosen = [min(by_year[fy], key=lambda d: 0 if version(d) == want else 1 if not version(d) else 2)
              for fy in sorted(by_year, reverse=True)[:years]]
    dated = [str(d["metadata"].get("financial_year")) for d in chosen if d["metadata"].get("financial_year")]
    note = f"{want.capitalize()} audited financial statements" + (f" for FY {', '.join(dated)}" if dated else "")
    other = [str(d["metadata"].get("financial_year") or "undated") for d in chosen if version(d) not in ("", want)]
    if other:
        note += f"; only the {'short' if want == 'full' else 'full'} version is in the library for {', '.join(other)}"
    if len(dated) < len(chosen):
        note += "; set the financial year in the details of the undated ones"
    return [d["id"] for d in chosen], note


def _search_review(conn, c, categories):
    hits = store.search(conn, c["text"], categories=categories, k=5, include_restricted=False)
    ids = list(dict.fromkeys(h["document_id"] for h in hits if h["similarity"] >= 0.45))[:3]
    return _result(c, "review" if ids else "gap", ids,
                   "Possible evidence found by search; confirm" if ids else "No matching evidence found in the library")


def _match_one(conn, c, docs, ref):
    cat = c.get("category", "other")
    threshold = _num(c.get("threshold_value"))

    if cat in UNDERTAKING_CATEGORIES:
        return _result(c, "undertaking", note="An undertaking will be drafted for signature")

    if cat == "registration":
        coi = _of(docs, "incorporation")
        if not coi:
            return _result(c, "gap", note="Certificate of incorporation is not in the library")
        year = _num(coi[0]["metadata"].get("incorporation_year"))
        if threshold and year and ref.year - year < threshold:
            return _result(c, "gap", [coi[0]["id"]], f"Incorporated {int(year)}; {int(threshold)} years required")
        return _result(c, "met", [coi[0]["id"]], f"Incorporated {int(year)}" if year else "Certificate on file")

    if cat in ("turnover", "net_worth"):
        ca = _of(docs, "ca_certificate")
        fin, fin_note = financial_statements(docs, c)
        key = "turnover_cr" if cat == "turnover" else "net_worth_cr"
        # the certificate covering the most recent years (a newer one supersedes an older one)
        with_series = sorted((d for d in ca if _year_series(d["metadata"], key)),
                             key=lambda d: max(str(y) for y in (d["metadata"].get(key) or {})), reverse=True)
        series = _year_series(with_series[0]["metadata"], key) if with_series else []
        evidence = [d["id"] for d in (with_series[:1] or ca[-1:])] + fin

        def result(status, note):
            return _result(c, status, evidence, f"{note}. {fin_note}" if fin_note else note)
        if not series:
            return result("review" if ca or fin else "gap", f"Add '{key}' to the CA certificate's details to check this automatically")
        last3 = series[-3:]
        if cat == "net_worth":
            ok = all(v > 0 for v in last3)
            return result("met" if ok else "gap", "Positive in each of the last three years" if ok else "Negative net worth in a recent year")
        avg = sum(last3) / len(last3)
        need = _to_crore(threshold, c.get("threshold_unit"))
        if need is None:
            return result("review", f"Average ₹ {avg:.2f} Cr; threshold not extracted")
        return result("met" if avg >= need else "gap", f"Average ₹ {avg:.2f} Cr against ₹ {need:g} Cr required")

    if cat == "certification":
        r = _match_certification(c, docs, ref)
        if r:
            return _result(c, *r)
        return _search_review(conn, c, ["statutory"])

    if cat == "project_experience":
        r = _match_projects(c, docs, ref)
        if r:
            return _result(c, *r)
        return _search_review(conn, c, ["experience"])

    if cat == "manpower":
        certs = _of(docs, "manpower_certificate")
        if not certs:
            return _result(c, "gap", note="No manpower certificate in the library")
        count = _num(certs[-1]["metadata"].get("headcount"))
        if threshold and count is not None and count < threshold:
            return _result(c, "gap", [certs[-1]["id"]], f"{int(count)} on payroll; {int(threshold)} required")
        return _result(c, "met" if count is not None else "review", [certs[-1]["id"]],
                       f"{int(count)} on payroll" if count is not None else "Confirm headcount")

    if cat == "tax_registration":
        pan, gst = _of(docs, "pan"), _of(docs, "gst")
        ev = [d["id"] for d in pan[:1] + gst[:1]]
        missing = [n for n, l in (("PAN", pan), ("GST", gst)) if not l]
        return _result(c, "gap" if missing else "met", ev,
                       ("Missing: " + ", ".join(missing)) if missing else "PAN and GST registration on file")

    if cat == "domain_experience":
        return _search_review(conn, c, ["experience"])

    # e.g. "copies of audited balance sheets for the last three financial years" under another heading
    if ASKS_FOR_ACCOUNTS.search(f"{c.get('text', '')} {c.get('evidence_expected') or ''}"):
        fin, fin_note = financial_statements(docs, c)
        if fin:
            return _result(c, "review", fin, f"{fin_note}; confirm they are the years asked for")

    return _search_review(conn, c, ["experience", "statutory", "hr"])


def _match_people(requirements: list[dict], docs: list[dict]) -> list[dict]:
    cvs = _of(docs, "cv")
    used: set = set()
    out = []
    stop = {"and", "of", "the", "for", "senior", "expert"}      # "manager"/"lead" tell roles apart
    for req in requirements:
        words = {w for w in re.findall(r"[a-z]+", req["role"].lower()) if w not in stop} or set(req["role"].lower().split())
        best, best_score = None, 0.0
        for cv in cvs:
            if cv["id"] in used:
                continue
            role = str(cv["metadata"].get("role", "")).lower()
            score = len(words & set(re.findall(r"[a-z]+", role))) / max(len(words), 1)
            if score > best_score:
                best, best_score = cv, score
        need = _num(req.get("min_years"))
        if not best or best_score == 0:
            out.append({**req, "status": "gap", "document_id": None, "name": None, "years": None,
                        "note": "No matching CV in the library"})
            continue
        used.add(best["id"])
        years = _num(best["metadata"].get("total_years"))
        ok = need is None or (years is not None and years >= need)
        out.append({**req, "status": "met" if ok else "partial", "document_id": str(best["id"]),
                    "name": best["metadata"].get("name"), "years": years,
                    "note": "" if ok else f"{years or '?'} years against {need:g} required"})
    return out


def match(conn: psycopg.Connection, extracted: dict, due: date | None, decisions: dict | None = None) -> dict:
    ref = due or date.today()
    docs = _docs(conn)
    pq = [_match_one(conn, c, docs, ref) for c in extracted.get("pq_criteria", [])]
    people = _match_people(extracted.get("personnel", []), docs)
    result = {"pq": pq, "personnel": people, "reference_date": ref.isoformat()}
    apply_decisions(result, decisions or {})
    return result


# A person's confirmation or correction of a result, saved on the bid response screen (the API writes the same
# keys: BidsController.Decide). Stored in analysis.decisions per criterion, so it outlives a new check or a new
# reading of the RFP; the automatic result is kept under "auto".
DECIDED_FIELDS = {"pq": ("status", "evidence", "note"), "personnel": ("status", "document_id", "name", "years", "note")}
IDENTITY = {"pq": ("clause", "text"), "personnel": ("role", "qualification")}


def decision_key(kind: str, row: dict) -> str:
    return "|".join("" if row.get(k) is None else str(row.get(k)) for k in IDENTITY[kind])


def apply_decisions(match: dict, decisions: dict) -> None:
    for kind, fields in DECIDED_FIELDS.items():
        chosen = decisions.get(kind) or {}
        for row in match.get(kind, []):
            d = chosen.get(decision_key(kind, row))
            if not d:
                continue
            row["auto"] = {k: row.get(k) for k in fields}
            row.update({k: d.get(k) for k in fields})
            row["note"] = row["note"] or ""
            row["decided"] = {"by": d.get("by"), "at": d.get("at")}
    match["summary"] = dict(Counter(r["status"] for r in match.get("pq", [])))
