from datetime import datetime, timedelta, timezone

from app.drafting import sync
from app.drafting.sync import Draft, pq_labels

DRAFTED = datetime(2026, 10, 25, 12, 33, tzinfo=timezone.utc)


def _doc(id_, doc_type, category="statutory"):
    return {"id": id_, "doc_type": doc_type, "category": category, "title": id_.upper(), "metadata": {}, "valid_until": None}


DOCS = [_doc("coi", "incorporation"), _doc("pan", "pan"), _doc("gst", "gst"), _doc("moa", "moa_aoa"),
        _doc("ca", "ca_certificate"), _doc("fs23", "financial_statement"), _doc("fs24", "financial_statement"),
        _doc("fs25", "financial_statement"), _doc("po_moca", "work_order", "experience"),
        _doc("axom", "work_order", "experience"), _doc("s1700", "work_order", "experience")]


def _row(clause, text, category, evidence, decided=True):
    r = {"clause": clause, "text": text, "category": category, "status": "met", "evidence": evidence, "note": ""}
    if decided:
        r["decided"] = {"by": "bd@x.in", "at": "2026-10-26T05:00:00Z"}
    return r


# 6.1.1: the eligibility check run since the draft cites the PAN card; the draft encloses the certificate
MATCH = {"pq": [_row("6.1.1", "Registered company", "registration", ["pan"], decided=False),
                _row("6.1.3", "Average turnover of Rs 50 crore", "turnover", ["ca", "fs23", "fs24", "fs25"]),
                _row("6.1.4", "Positive net worth", "net_worth", ["ca", "fs23", "fs24", "fs25"]),
                _row("6.1.8", "Only a sole bidder", "other", []),
                _row("6.1.10", "Completed similar projects", "project_experience", ["s1700"])],
         "personnel": [], "summary": {}}
EXTRACTED = {"meta": {"bid_due": "2026-11-15"}, "pq_criteria": [{} for _ in MATCH["pq"]], "tech_criteria": []}


def _section(id_, kind, title, html="", edited=False):
    return {"id": id_, "kind": kind, "title": title, "html": html, "include": True,
            "updated_at": DRAFTED + (timedelta(hours=5) if edited else timedelta(seconds=1))}


def _draft(edited_separator=False):
    sections = [_section("matrix", "text", "Pre-qualification compliance", "old matrix"),
                _section("s611", "text", "PQ 6.1.1 – Registered company", 'see <span class="xref" data-doc="coi">'),
                _section("d611", "docs", "PQ 6.1.1 – supporting documents"),
                _section("s613", "text", "PQ 6.1.3 – Average turnover of Rs 50 crore"),
                _section("s614", "text", "PQ 6.1.4 – Positive net worth", "my own words", edited=edited_separator),
                _section("s618", "text", "PQ 6.1.8 – Only a sole bidder"),
                _section("d618", "docs", "PQ 6.1.8 – supporting documents"),
                _section("s6110", "text", "PQ 6.1.10 – Completed similar projects"),
                _section("d6110", "docs", "PQ 6.1.10 – supporting documents"),
                _section("tec1", "text", "TEC 6.3/1 – Years of operation", 'MOA <span class="xref" data-doc="moa">')]
    members = {"d611": ["coi", "pan", "gst"], "d618": ["moa", "po_moca"], "d6110": ["axom"]}
    return Draft(sections, members, DRAFTED)


class FakeConn:
    def execute(self, sql, params=()):
        class Rows:
            def __init__(self, one=None, many=()):
                self.one, self.many = one, list(many)

            def fetchone(self):
                return self.one

            def fetchall(self):
                return self.many
        if "FROM bids" in sql:
            return Rows({"analysis": {"match": MATCH, "extracted": EXTRACTED}})
        if "FROM library_documents" in sql:
            return Rows(many=DOCS)
        return Rows()

    def commit(self):
        pass


def _run(monkeypatch, draft, **kw):
    saved = {}
    monkeypatch.setattr(sync, "_load", lambda conn, bid_id: draft)
    monkeypatch.setattr(sync, "_save", lambda conn, bid_id, d: saved.setdefault("draft", d))
    result = sync.sync(FakeConn(), "bid-1", **kw)
    return result, saved["draft"]


def _layout(d: Draft):
    return [(d.sections[sid]["title"], d.members.get(sid)) for sid in d.order if d.sections[sid]["kind"] == "docs"]


def test_the_draft_encloses_the_documents_chosen_on_the_bid_response_screen(monkeypatch):
    result, d = _run(monkeypatch, _draft())
    assert _layout(d) == [
        ("PQ 6.1.1 – supporting documents", ["coi", "pan", "gst"]),                  # not decided: as drafted
        ("PQ 6.1.3 – supporting documents", ["ca", "fs23", "fs24", "fs25"]),       # new, after its separator
        ("PQ 6.1.10 – supporting documents", ["s1700"]),                            # AXOM replaced
        ("TEC 6.3/1 – supporting documents", ["moa"]),                               # still referred to there
    ]
    assert d.order.index(next(s for s in d.order if d.sections[s]["title"] == "PQ 6.1.3 – supporting documents")) == \
        d.order.index("s613") + 1
    assert "d618" in d.deleted                       # 6.1.8: no documents chosen
    assert result["criteria"] == ["PQ 6.1.3", "PQ 6.1.4", "PQ 6.1.8", "PQ 6.1.10"]
    assert result["kept_because_referred_to"] == [{"document": "MOA", "referred to by": "TEC 6.3/1 – Years of operation"}]


def test_separators_and_the_compliance_table_show_the_choice(monkeypatch):
    _, d = _run(monkeypatch, _draft())
    net_worth = d.sections["s614"]["html"]
    assert "Complied" in net_worth and 'data-doc="ca"' in net_worth and "(enclosed earlier)" in net_worth
    turnover = d.sections["s613"]["html"]
    assert 'data-doc="fs25"' in turnover and "(enclosed earlier)" not in turnover
    matrix = d.sections["matrix"]["html"]
    assert 'data-doc="s1700"' in matrix and 'data-doc="axom"' not in matrix
    assert 'data-doc="coi"' in matrix and 'data-doc="pan"' not in matrix       # as the draft has it, not the newer check


def test_a_separator_a_person_edited_keeps_its_text(monkeypatch):
    result, d = _run(monkeypatch, _draft(edited_separator=True))
    assert d.sections["s614"]["html"] == "my own words"
    assert result["text_kept_as_edited"] == ["PQ 6.1.4"]


def test_labels_follow_the_drafter_for_repeated_clauses():
    assert pq_labels([{"clause": "4"}, {"clause": "4"}, {"clause": ""}]) == ["PQ 4", "PQ 4 (2)", "PQ 3"]
