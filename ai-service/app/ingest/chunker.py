"""Split page texts into overlapping chunks for the local vector index."""
from __future__ import annotations

import re

_HEADING = re.compile(
    r"^\s*(?:(?:section|chapter|part|annexure|appendix|schedule|form)\b[\s\-:]*[\w.\-]*.*"
    r"|\d{1,2}(?:\.\d{1,2}){0,3}\.?\s+[A-Z][^\n]{2,90})\s*$",
    re.IGNORECASE,
)


def looks_like_heading(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 110 or line.endswith((".", ",", ";")):
        return False
    if len(line.split()) > 14:
        return False
    return bool(_HEADING.match(line)) or (line.isupper() and len(line) > 6)


def chunk_pages(pages: list[str], size: int = 1200, overlap: int = 200) -> list[dict]:
    chunks: list[dict] = []
    buf, start_page, heading = "", 1, None
    for page_no, text in enumerate(pages, 1):
        for para in (p.strip() for p in re.split(r"\n\s*\n", text)):
            if not para:
                continue
            first_line = para.splitlines()[0]
            if buf and len(buf) + len(para) > size:       # the text so far belongs under the previous heading
                chunks.append({"content": buf.strip(), "page_from": start_page, "page_to": page_no,
                               "heading": heading})
                buf, start_page = buf[-overlap:], page_no
            if looks_like_heading(first_line):
                heading = first_line.strip()[:160]
            while len(para) > size * 2:          # very long paragraph: hard split
                chunks.append({"content": (buf + para[:size]).strip(), "page_from": start_page,
                               "page_to": page_no, "heading": heading})
                para, buf, start_page = para[size - overlap:], "", page_no
            buf += para + "\n"
    if buf.strip():
        chunks.append({"content": buf.strip(), "page_from": start_page, "page_to": len(pages) or 1,
                       "heading": heading})
    return chunks
