"""Whitelist HTML for bid sections (model output and browser edits alike)."""
from __future__ import annotations

import re

import nh3

TAGS = {"h3", "h4", "p", "br", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td",
        "b", "strong", "i", "em", "u", "span", "mark", "div", "small"}
ATTRIBUTES = {
    "span": {"class", "data-sec", "data-doc"},
    "mark": {"class"},
    "div": {"class"},
    "p": {"class"},
    "ol": {"start"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}
_FILL = re.compile(r"\[\[FILL:\s*([^\]]+?)\s*\]\]")


def clean_html(html: str) -> str:
    html = _FILL.sub(lambda m: f'<mark class="fill">[{m.group(1)}]</mark>', html or "")
    return nh3.clean(html, tags=TAGS, attributes=ATTRIBUTES, link_rel=None)
