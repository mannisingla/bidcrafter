"""The single path from BidCraft to the external model.

Order of operations for every request:
1. Minimise   - drop restricted evidence, cap the number and size of extracts.
2. Redact     - pseudonymise identifiers and names (see redactor.py); a payload with an
                identifier the policy blocks (e.g. a bank account number) is not sent.
3. Record     - store the exact outbound payload, its hash and an encrypted token map
                (the audit trail of what left the server).
4. Send       - call the model with the redacted payload only.
5. Restore    - put real values back into the answer, locally.

There is no approval step. The payload hash makes a re-run job find the request already
sent (or recorded and interrupted) instead of sending it again.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import psycopg
from psycopg.types.json import Jsonb

from ..config import get_settings, load_policy
from ..db import audit
from ..llm import openai_client
from .redactor import Redactor, rehydrate


class EgressBlocked(Exception):
    pass


@dataclass
class Evidence:
    text: str
    ref: dict = field(default_factory=dict)   # kind, id, label, sensitivity


def known_people(conn: psycopg.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT metadata->>'name' AS name FROM library_documents "
        "WHERE category = 'hr' AND metadata ? 'name'"
    ).fetchall()
    return [r["name"] for r in rows if r["name"]]


def _minimise(evidence: list[Evidence], limits: dict) -> tuple[list[Evidence], list[dict]]:
    kept, dropped = [], []
    for ev in evidence:
        sensitivity = ev.ref.get("sensitivity", "internal")
        if sensitivity == "restricted":
            dropped.append({**ev.ref, "reason": "restricted document"})
            continue
        kept.append(ev)
    for ev in kept[limits["max_evidence_items"]:]:
        dropped.append({**ev.ref, "reason": "evidence item limit"})
    kept = kept[: limits["max_evidence_items"]]

    out, used = [], 0
    for ev in kept:
        room = limits["max_chars_per_request"] - used
        if room < 300:
            dropped.append({**ev.ref, "reason": "request size limit"})
            continue
        text = ev.text[: min(room, limits["max_chars_per_item"])]
        out.append(Evidence(text, ev.ref))
        used += len(text)
    return out, dropped


def _user_message(instruction: str, evidence: list[Evidence]) -> str:
    parts = [instruction.strip()]
    for i, ev in enumerate(evidence, 1):
        label = ev.ref.get("label") or f"Extract {i}"
        parts.append(f'<extract id="{i}" source="{label}">\n{ev.text.strip()}\n</extract>')
    return "\n\n".join(parts)


def _vault(conn: psycopg.Connection, egress_id) -> dict[str, str]:
    rows = conn.execute(
        "SELECT token, pgp_sym_decrypt(original_enc, %s) AS original FROM redaction_vault WHERE egress_id = %s",
        (get_settings().vault_key, egress_id),
    ).fetchall()
    return {r["token"]: r["original"] for r in rows}


def ask(conn: psycopg.Connection, *, purpose: str, system: str, instruction: str,
        evidence: list[Evidence], schema: dict | None = None, schema_name: str = "result",
        bid_id=None, job_id=None, actor: str = "worker", people: list[str] | None = None):
    """people: names to redact besides those in the HR library (e.g. of a CV not yet in it)."""
    settings = get_settings()
    policy = load_policy()
    limits = {**policy["limits"]["default"], **policy["limits"].get(purpose, {})}

    kept, dropped = _minimise(evidence, limits)
    redactor = Redactor(actions=policy.get("entities", {}), people=known_people(conn) + [p for p in people or [] if p],
                        custom_terms=policy.get("custom_terms") or [])
    system_out = redactor.redact(system)
    user_out = redactor.redact(_user_message(instruction, kept))
    summary = {**redactor.summary(), "dropped": dropped, "evidence_items": len(kept)}
    payload = {"model": settings.openai_model, "system": system_out, "user": user_out,
               "schema": schema_name if schema else None}
    digest = hashlib.sha256(
        json.dumps({"purpose": purpose, "bid": str(bid_id), **payload}, sort_keys=True).encode()
    ).hexdigest()

    row = conn.execute(
        "SELECT * FROM egress_requests WHERE payload_sha256 = %s AND purpose = %s "
        "ORDER BY created_at DESC LIMIT 1", (digest, purpose)
    ).fetchone()
    egress_id = None
    if row:
        if row["status"] == "sent":                     # the same request again (a re-run job): its answer on record
            return rehydrate(row["response_redacted"]["content"], _vault(conn, row["id"]))
        if row["status"] in ("approved", "pending"):    # recorded, then interrupted before it was sent
            egress_id = row["id"]

    if egress_id is None:
        refs = [ev.ref for ev in kept]
        if redactor.blocked:
            reason = "Blocked by policy: found " + ", ".join(sorted(set(redactor.blocked)))
            blocked_id = conn.execute(
                "INSERT INTO egress_requests (bid_id, job_id, purpose, status, payload_sha256, redacted_payload, "
                "redaction_summary, source_refs, char_count, model, error) "
                "VALUES (%s, %s, %s, 'rejected', %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (bid_id, job_id, purpose, digest, Jsonb(payload), Jsonb(summary), Jsonb(refs),
                 len(system_out) + len(user_out), settings.openai_model, reason),
            ).fetchone()["id"]
            audit(conn, actor, "egress.blocked", "egress", blocked_id, {"purpose": purpose, "reason": reason})
            conn.commit()
            raise EgressBlocked(reason)

        egress_id = conn.execute(
            "INSERT INTO egress_requests (bid_id, job_id, purpose, status, payload_sha256, redacted_payload, "
            "redaction_summary, source_refs, char_count, model) "
            "VALUES (%s, %s, %s, 'approved', %s, %s, %s, %s, %s, %s) RETURNING id",
            (bid_id, job_id, purpose, digest, Jsonb(payload), Jsonb(summary), Jsonb(refs),
             len(system_out) + len(user_out), settings.openai_model),
        ).fetchone()["id"]
        for token, original in redactor.mapping.items():
            conn.execute(
                "INSERT INTO redaction_vault (egress_id, token, original_enc) VALUES (%s, %s, pgp_sym_encrypt(%s, %s))",
                (egress_id, token, original, settings.vault_key),
            )
        audit(conn, actor, "egress.created", "egress", egress_id,
              {"purpose": purpose, "redactions": summary["counts"], "chars": len(user_out)})
        conn.commit()

    try:
        content, meta = openai_client.complete(system_out, user_out, schema, schema_name)
        answer = json.loads(content) if schema is not None else content
    except Exception as exc:
        conn.execute("UPDATE egress_requests SET status = 'failed', error = %s WHERE id = %s",
                     (str(exc)[:2000], egress_id))
        conn.commit()
        raise
    conn.execute(
        "UPDATE egress_requests SET status = 'sent', sent_at = now(), response_redacted = %s, "
        "response_meta = %s WHERE id = %s",
        (Jsonb({"content": answer}), Jsonb(meta), egress_id),
    )
    audit(conn, actor, "egress.sent", "egress", egress_id, {"purpose": purpose, "usage": meta.get("usage")})
    conn.commit()
    return rehydrate(answer, _vault(conn, egress_id))
