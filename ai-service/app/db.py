"""Database helpers (psycopg 3 + pgvector)."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .config import get_settings


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(get_settings().database_url, row_factory=dict_row)
    try:
        register_vector(conn)
        yield conn
    finally:
        conn.close()


def audit(conn: psycopg.Connection, actor: str, action: str, entity: str | None = None,
          entity_id: Any = None, detail: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO audit_log (actor, action, entity, entity_id, detail) VALUES (%s, %s, %s, %s, %s)",
        (actor, action, entity, str(entity_id) if entity_id is not None else None, Jsonb(detail or {})),
    )
