"""Find the parts of an RFP worth sending for extraction, locally.

The whole RFP is indexed on this server. Only sections that score as eligibility,
evaluation, personnel, formats or bid-data-sheet content are selected as evidence.
"""
from __future__ import annotations

import re

from ..ingest.chunker import looks_like_heading

TARGETS: dict[str, list[str]] = {
    "eligibility": ["pre-qualification", "prequalification", "pre qualification", "eligibility criteria",
                    "minimum eligibility", "qualification criteria", "pq criteria", "eligibility conditions"],
    "evaluation": ["technical evaluation", "evaluation criteria", "evaluation methodology", "marking scheme",
                   "qcbs", "technical score", "evaluation of technical", "scoring model", "scoring", "marking"],
    "personnel": ["key personnel", "key professionals", "resource deployment", "manpower requirement",
                  "team composition", "minimum qualification of"],
    "formats": ["form tech", "technical bid format", "formats for", "proforma", "annexure", "check list",
                "checklist"],
    "datasheet": ["critical dates", "important dates", "bid data sheet", "fact sheet", "earnest money", "emd",
                  "tender fee", "bid validity", "last date", "bid submission end date", "publish date",
                  "presentation", "opening of", "estimated cost", "estimated value", "project cost", "schedule of"],
    "scope": ["scope of work", "scope of the work", "project scope", "background", "introduction", "objective",
              "about the project", "project overview", "terms of reference"],
}

# Fallback searches against the RFP's own chunks when no heading matched a target
TARGET_QUERIES = {
    "eligibility": "eligibility pre-qualification criteria bidder turnover experience certification",
    "evaluation": "technical evaluation criteria marks scoring",
    "personnel": "key personnel qualification experience years",
    "formats": "formats forms to be submitted technical bid",
    "datasheet": "bid due date EMD tender fee bid validity publish date presentation opening estimated cost",
    "scope": "scope of work background objective of the project",
}


def split_sections(pages: list[str]) -> list[dict]:
    sections: list[dict] = []
    current = {"heading": "Opening pages", "lines": [], "page_from": 1, "page_to": 1}
    for page_no, text in enumerate(pages, 1):
        for line in text.splitlines():
            if looks_like_heading(line) and current["lines"]:
                sections.append(current)
                current = {"heading": line.strip(), "lines": [], "page_from": page_no, "page_to": page_no}
            current["lines"].append(line)
            current["page_to"] = page_no
    sections.append(current)
    return [{"heading": s["heading"], "text": "\n".join(s["lines"]).strip(),
             "page_from": s["page_from"], "page_to": s["page_to"]} for s in sections if "".join(s["lines"]).strip()]


def _score(section: dict, keywords: list[str]) -> float:
    heading = section["heading"].lower()
    body = section["text"][:6000].lower()
    score = 0.0
    for kw in keywords:
        if kw in heading:
            score += 6
        score += min(body.count(kw), 4)
    # tables of criteria are dense with these words
    score += 0.3 * min(len(re.findall(r"\b(?:crore|turnover|years|certificate|marks)\b", body)), 10)
    return score


def select_sections(sections: list[dict], per_target: int = 2) -> tuple[list[dict], list[str]]:
    """Best sections per target, opening pages first. Returns (sections, targets with no match)."""
    chosen: dict[int, dict] = {}
    if sections:
        chosen[0] = {**sections[0], "target": "datasheet"}
    missing = []
    for target, keywords in TARGETS.items():
        ranked = sorted(range(len(sections)), key=lambda i: _score(sections[i], keywords), reverse=True)
        hits = [i for i in ranked[:per_target] if _score(sections[i], keywords) >= 6]
        if not hits:
            missing.append(target)
        for i in hits:
            chosen.setdefault(i, {**sections[i], "target": target})
    return [_extend(sections, i, chosen) for i in sorted(chosen)], missing


# Criteria tables split into many small "sections": each row ("1 Legal Entity", "2 Statutory") reads as a heading.
# A chosen section therefore runs on through its rows and sub-clauses until the next sibling or parent clause.
_DOTTED = re.compile(r"^\s*(\d+(?:\.\d+)+)\b")                     # 6.1, 6.1.1
_CHAPTER = re.compile(r"^\s*\d+\.\s+[A-Z][A-Z0-9 &/(),'-]{3,}$")   # "7. GENERAL CONDITIONS"
MAX_SECTION_CHARS = {"scope": 4000}
DEFAULT_MAX_SECTION_CHARS = 10000


def _extend(sections: list[dict], i: int, chosen: dict[int, dict]) -> dict:
    base = chosen[i]
    number = (m.group(1) if (m := _DOTTED.match(base["heading"])) else None)
    cap = MAX_SECTION_CHARS.get(base["target"], DEFAULT_MAX_SECTION_CHARS)
    texts, size, page_to = [base["text"]], len(base["text"]), base["page_to"]
    for j in range(i + 1, len(sections)):
        s = sections[j]
        if j in chosen or _CHAPTER.match(s["heading"]):
            break
        if (m := _DOTTED.match(s["heading"])) and not (number and m.group(1).startswith(number + ".")):
            break                                                    # 6.2 after 6.1: the next clause
        if size + len(s["text"]) > cap:
            break
        texts.append(s["text"])
        size += len(s["text"])
        page_to = s["page_to"]
    return {**base, "text": "\n".join(texts), "page_to": page_to}
