"""Local vector store on PostgreSQL + pgvector, with hybrid (vector + full-text) search."""
from __future__ import annotations

from collections import defaultdict

import psycopg

from .embedder import embed_passages, embed_query


def _insert(conn: psycopg.Connection, chunks: list[dict], *, document_id=None, bid_id=None) -> None:
    vectors = embed_passages([f"{c.get('heading') or ''}\n{c['content']}" for c in chunks])
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO doc_chunks (document_id, bid_id, chunk_index, page_from, page_to, heading, content, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [(document_id, bid_id, i, c.get("page_from"), c.get("page_to"), c.get("heading"), c["content"], v)
             for i, (c, v) in enumerate(zip(chunks, vectors))],
        )


def index_document(conn: psycopg.Connection, document_id, chunks: list[dict]) -> None:
    conn.execute("DELETE FROM doc_chunks WHERE document_id = %s", (document_id,))
    if chunks:
        _insert(conn, chunks, document_id=document_id)


def index_rfp(conn: psycopg.Connection, bid_id, chunks: list[dict]) -> None:
    conn.execute("DELETE FROM doc_chunks WHERE bid_id = %s", (bid_id,))
    if chunks:
        _insert(conn, chunks, bid_id=bid_id)


def search(conn: psycopg.Connection, query: str, *, categories: list[str] | None = None,
           doc_types: list[str] | None = None, bid_id=None, k: int = 8,
           include_restricted: bool = True) -> list[dict]:
    """Reciprocal-rank fusion of vector similarity and PostgreSQL full-text ranking."""
    # pgvector 0.6 applies the filters below after the HNSW scan, which looks at ef_search candidates (40 by
    # default): too few when one RFP or one category is a small share of all chunks
    conn.execute("SET hnsw.ef_search = 400")
    params: dict = {"qv": embed_query(query), "q": query}
    where = []
    if bid_id is not None:
        where.append("c.bid_id = %(bid)s")
        params["bid"] = bid_id
    else:
        where.append("c.document_id IS NOT NULL AND d.status = 'ready'")
        if categories:
            where.append("d.category = ANY(%(cats)s)")
            params["cats"] = list(categories)
        if doc_types:
            where.append("d.doc_type = ANY(%(types)s)")
            params["types"] = list(doc_types)
        if not include_restricted:
            where.append("d.sensitivity <> 'restricted'")
    base = (
        "SELECT c.id, c.document_id, c.bid_id, c.page_from, c.page_to, c.heading, c.content, "
        "d.title, d.category, d.doc_type, d.sensitivity, 1 - (c.embedding <=> %(qv)s) AS similarity "
        "FROM doc_chunks c LEFT JOIN library_documents d ON d.id = c.document_id WHERE " + " AND ".join(where)
    )
    by_vector = conn.execute(base + " ORDER BY c.embedding <=> %(qv)s LIMIT 40", params).fetchall()
    by_text = conn.execute(
        base + " AND c.tsv @@ websearch_to_tsquery('english', %(q)s) "
        "ORDER BY ts_rank_cd(c.tsv, websearch_to_tsquery('english', %(q)s)) DESC LIMIT 40", params
    ).fetchall()

    score: dict[int, float] = defaultdict(float)
    rows: dict[int, dict] = {}
    for ranked in (by_vector, by_text):
        for rank, row in enumerate(ranked):
            score[row["id"]] += 1.0 / (60 + rank)
            rows[row["id"]] = row
    best = sorted(score, key=score.get, reverse=True)[:k]
    return [{**rows[i], "score": round(score[i], 5), "similarity": float(rows[i]["similarity"])} for i in best]
