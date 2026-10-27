"""Pseudonymising redactor.

Sensitive values are replaced by stable tokens such as ``[[PAN_1]]`` before any text
leaves the server. The token map never leaves: the gateway stores it encrypted in
``redaction_vault`` and uses it to put the real values back into the model's answer.

Detection is deliberately conservative and rule-based (no external service):
Indian identifiers (PAN, GSTIN, Aadhaar with Verhoeff check, IFSC, bank account
numbers next to an "A/c" label), e-mail addresses, phone numbers, names of people
in the HR library, honorific + name patterns, and custom terms from the policy.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff_valid(number: str) -> bool:
    digits = re.sub(r"\D", "", number)
    check = 0
    for i, ch in enumerate(reversed(digits)):
        check = _D[check][_P[i % 8][int(ch)]]
    return check == 0


# (kind, pattern, capture group holding the sensitive value, extra validator)
_BUILTIN: list[tuple[str, re.Pattern, int, Callable[[str], bool] | None]] = [
    ("GSTIN", re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b"), 0, None),
    ("PAN", re.compile(r"\b[A-Z]{3}[ABCFGHJLPT][A-Z]\d{4}[A-Z]\b"), 0, None),
    ("AADHAAR", re.compile(r"(?<!\d)[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?!\d)"), 0, verhoeff_valid),
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"), 0, None),
    ("PHONE", re.compile(r"(?<![\w+])(?:\+91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"), 0, None),
    ("PHONE", re.compile(r"(?<!\d)0\d{2,4}[\s-]\d{6,8}(?!\d)"), 0, None),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"), 0, None),
    ("BANK_ACCOUNT", re.compile(r"(?i)\b(?:a/?c|account)\s*(?:no\.?|number|#)?\s*[:\-]?\s*(\d{9,18})\b"), 1, None),
    ("PERSON", re.compile(
        r"\b(?:Mr|Mrs|Ms|Miss|Dr|Shri|Smt|Sh|Kumari)\.?\s+([A-Z][a-zA-Z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-zA-Z]+){0,2})"), 1, None),
]
_ACCOUNT = next(rx for kind, rx, _, _ in _BUILTIN if kind == "BANK_ACCOUNT")


def drop_account_numbers(text: str) -> str:
    """Remove the lines that carry a bank account number (an RFP's EMD payment details, say): the model never
    needs them, and the BANK_ACCOUNT rule would otherwise block the whole request."""
    return "\n".join(line for line in text.splitlines() if not _ACCOUNT.search(line))

TOKEN_RE = re.compile(r"\[\[[A-Z_]+_\d+\]\]")


@dataclass
class Redactor:
    actions: dict[str, str]
    people: list[str] = field(default_factory=list)
    custom_terms: list[str] = field(default_factory=list)
    mapping: dict[str, str] = field(default_factory=dict)          # token -> original
    _reverse: dict[tuple[str, str], str] = field(default_factory=dict)
    counts: Counter = field(default_factory=Counter)
    blocked: list[str] = field(default_factory=list)

    def _patterns(self):
        yield from _BUILTIN
        names = sorted({n.strip() for n in self.people if n and len(n.strip()) > 3}, key=len, reverse=True)
        if names:
            yield "PERSON", re.compile(r"\b(?:" + "|".join(re.escape(n) for n in names) + r")\b"), 0, None
        terms = sorted({t.strip() for t in self.custom_terms if t and t.strip()}, key=len, reverse=True)
        if terms:
            yield "CUSTOM", re.compile(r"(?i)\b(?:" + "|".join(re.escape(t) for t in terms) + r")\b"), 0, None

    def _token(self, kind: str, value: str) -> str:
        norm = value.strip() if kind in ("PERSON", "CUSTOM") else re.sub(r"[\s-]", "", value).upper()
        key = (kind, norm)
        if key not in self._reverse:
            n = sum(1 for k in self._reverse if k[0] == kind) + 1
            token = f"[[{kind}_{n}]]"
            self._reverse[key] = token
            self.mapping[token] = value
        self.counts[kind] += 1
        return self._reverse[key]

    def redact(self, text: str) -> str:
        spans = []
        for kind, rx, group, check in self._patterns():
            action = self.actions.get(kind, "redact")
            if action == "allow":
                continue
            for m in rx.finditer(text):
                value = m.group(group)
                if not value or (check and not check(value)):
                    continue
                start, end = m.span(group)
                spans.append((start, end, kind, value, action))
        spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
        out, pos = [], 0
        for start, end, kind, value, action in spans:
            if start < pos:            # overlaps an earlier, longer match
                continue
            if action == "block":
                self.blocked.append(kind)
            out.append(text[pos:start])
            out.append(self._token(kind, value))
            pos = end
        out.append(text[pos:])
        return "".join(out)

    def summary(self) -> dict:
        return {"counts": dict(self.counts), "blocked": sorted(set(self.blocked))}


def rehydrate(obj: Any, mapping: dict[str, str]) -> Any:
    """Put original values back into a (possibly nested) model answer."""
    if isinstance(obj, str):
        return TOKEN_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), obj)
    if isinstance(obj, list):
        return [rehydrate(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: rehydrate(v, mapping) for k, v in obj.items()}
    return obj
