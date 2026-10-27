"""Job: ingest one library document (convert, OCR, chunk, embed, suggest metadata)."""
from __future__ import annotations

import hashlib

import psycopg
from psycopg.types.json import Jsonb

from ..db import audit
from ..files import relative, resolve
from ..rag import store
from . import classify, project
from .chunker import chunk_pages
from .extract import Cancelled, Progress, extract_pages, to_pdf


def merge_suggestion(existing: dict, fresh: dict) -> dict:
    """What a person entered wins. Values filled automatically before (listed in _auto) are refreshed where this
    read suggests one, and otherwise kept: they may come from other documents (a completion certificate, a CV read)."""
    machine = set(existing.get("_auto") or [])
    person = {k: v for k, v in existing.items() if k not in machine and k != "_auto"}
    metadata = {k: v for k, v in existing.items() if k in machine}
    metadata.update({k: v for k, v in fresh.items() if k != "_auto"})
    metadata.update(person)
    auto = sorted(k for k in metadata if k not in person)
    if auto:
        metadata["_auto"] = auto
    return metadata


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:          # in blocks: uploads can be 300 MB
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


DELETED = {"skipped": "the document was deleted"}


def _progress(conn: psycopg.Connection, doc_id) -> Progress:
    def report(done: int, total: int) -> bool:
        """Shows how many scanned pages are read; False once the document has been deleted, to stop reading."""
        row = conn.execute("UPDATE library_documents SET metadata = metadata || %s WHERE id = %s RETURNING id",
                           (Jsonb({"reading": {"done": done, "total": total}}), doc_id)).fetchone()
        conn.commit()
        return row is not None
    return report


def run(conn: psycopg.Connection, job: dict) -> dict:
    doc_id = job["payload"]["document_id"]
    doc = conn.execute("SELECT * FROM library_documents WHERE id = %s", (doc_id,)).fetchone()
    if doc is None:
        return DELETED
    conn.execute("UPDATE library_documents SET status = 'processing', error = NULL, metadata = metadata - 'reading' "
                 "WHERE id = %s", (doc_id,))
    conn.commit()

    try:
        pdf, pages = extract_pages(to_pdf(resolve(doc["file_path"])), _progress(conn, doc_id))
    except Cancelled:
        return DELETED
    chunks = chunk_pages(pages)
    try:
        store.index_document(conn, doc_id, chunks)
    except psycopg.errors.ForeignKeyViolation:
        conn.rollback()
        return DELETED

    suggestion = classify.suggest(doc["category"], doc["title"], "\n".join(pages), doc["doc_type"], len(pages))
    # Merge into the row as it is now, not as it was when reading started: a person may have saved details
    # (or linked the document to a project) meanwhile.
    current = conn.execute("SELECT metadata, doc_type, file_sha256 FROM library_documents WHERE id = %s FOR UPDATE",
                           (doc_id,)).fetchone()
    if current is None:
        conn.rollback()
        return DELETED
    existing = {k: v for k, v in (current["metadata"] or {}).items() if k != "reading"}
    metadata, doc_type = merge_suggestion(existing, suggestion["metadata"]), current["doc_type"]
    if doc_type == "other":
        doc_type = suggestion["doc_type"]
    sha = current["file_sha256"] or _sha256(resolve(doc["file_path"]))    # for documents from before uploads recorded it
    conn.execute(
        "UPDATE library_documents SET status = 'ready', pdf_path = %s, page_count = %s, extracted_text = %s, "
        "metadata = %s, doc_type = %s, valid_until = COALESCE(valid_until, %s), file_sha256 = %s, "
        "updated_at = now() WHERE id = %s",
        (relative(pdf), len(pages), "\f".join(pages)[:400_000], Jsonb(metadata), doc_type,
         suggestion["valid_until"], sha, doc_id),
    )
    audit(conn, "worker", "document.indexed", "document", doc_id, {"pages": len(pages), "chunks": len(chunks)})
    # an Experience project's purchase order, completion certificate or RFP fills the project's details;
    # a CV in the HR library fills its own
    follow_up = ("extract_project" if project.extracts_from(doc["category"], doc_type, metadata)
                 else "extract_cv" if doc["category"] == "hr" and doc_type == "cv" else None)
    if follow_up:
        conn.execute("INSERT INTO jobs (type, payload) VALUES (%s, %s)", (follow_up, Jsonb({"document_id": str(doc_id)})))
        conn.execute("UPDATE library_documents SET metadata = metadata || %s WHERE id = %s",
                     (Jsonb({"extraction": {"status": "queued"}}), doc_id))
    conn.commit()
    return {"pages": len(pages), "chunks": len(chunks), "doc_type": doc_type}
