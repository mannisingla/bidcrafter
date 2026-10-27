from app.privacy import gateway
from app.privacy.gateway import Evidence, _minimise


class _Rows:
    def __init__(self, one=None, many=()):
        self.one, self.many = one, list(many)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class FakeConn:
    """Records the SQL the gateway runs; answers the few reads it makes."""

    def __init__(self, recorded=None):
        self.sql, self.recorded = [], recorded

    def execute(self, sql, params=()):
        self.sql.append((" ".join(sql.split()), params))
        if sql.lstrip().startswith("SELECT * FROM egress_requests"):
            return _Rows(self.recorded)
        if "RETURNING id" in sql:
            return _Rows({"id": "egress-1"})
        return _Rows()

    def commit(self):
        pass


def test_a_request_is_sent_without_waiting_for_a_reviewer_even_from_a_confidential_document(monkeypatch):
    sent = []
    monkeypatch.setattr(gateway.openai_client, "complete",
                        lambda system, user, schema, name: (sent.append(user) or '{"answer": "ok"}', {"usage": {}}))
    conn = FakeConn()
    answer = gateway.ask(conn, purpose="section_drafting", system="Draft.", instruction="Write the approach.",
                         evidence=[Evidence("Our approach to the portal.", {"label": "Approach", "sensitivity": "confidential"})],
                         schema={"type": "object"}, schema_name="narrative")
    assert answer == {"answer": "ok"} and len(sent) == 1
    inserted = next(s for s, _ in conn.sql if s.startswith("INSERT INTO egress_requests"))
    assert "'approved'" in inserted                     # recorded as allowed, not waiting
    assert any(s.startswith("UPDATE egress_requests SET status = 'sent'") for s, _ in conn.sql)


def test_a_rerun_gets_the_answer_on_record_instead_of_sending_again(monkeypatch):
    monkeypatch.setattr(gateway.openai_client, "complete", lambda *a: (_ for _ in ()).throw(AssertionError("sent again")))
    conn = FakeConn(recorded={"id": "egress-1", "status": "sent", "response_redacted": {"content": {"answer": "ok"}}})
    answer = gateway.ask(conn, purpose="rfp_extraction", system="Read.", instruction="Extract.",
                         evidence=[Evidence("Clause 1", {"sensitivity": "public"})], schema={"type": "object"})
    assert answer == {"answer": "ok"}


def test_restricted_documents_never_leave_and_confidential_ones_go_redacted():
    kept, dropped = _minimise([Evidence("secret", {"label": "A", "sensitivity": "restricted"}),
                               Evidence("internal note", {"label": "B", "sensitivity": "confidential"})],
                              {"max_evidence_items": 5, "max_chars_per_request": 10000, "max_chars_per_item": 5000})
    assert [e.ref["label"] for e in kept] == ["B"] and dropped[0]["reason"] == "restricted document"
