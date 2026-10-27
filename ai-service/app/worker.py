"""Background worker: claims jobs from the `jobs` table and runs them.

Run one instance (systemd unit bidcraft-worker).
"""
from __future__ import annotations

import logging
import signal
import time

import psycopg
from psycopg.types.json import Jsonb

from .db import connect
from .drafting import drafter, sync
from .ingest import pipeline as ingest
from .ingest import cv, project
from .pdf import assembler
from .rfp import analyzer

HANDLERS = {
    "ingest_document": ingest.run,
    "analyze_rfp": analyzer.run,
    "draft_bid": drafter.run,
    "assemble_pdf": assembler.run,
    "redraft_section": drafter.redraft,
    "extract_project": project.run,
    "extract_cv": cv.run,
    "match_rfp": analyzer.run_eligibility,
    "sync_draft": sync.run,
}
FAILURE_TARGET = {
    "ingest_document": ("library_documents", "document_id"),
    "extract_project": None,      # the error is shown on the document's extraction status instead
    "extract_cv": None,
    "match_rfp": None,            # the error is shown on the bid response screen; the RFP analysis stays
    "analyze_rfp": ("bids", "bid_id"),
    "draft_bid": ("bids", "bid_id"),
    "assemble_pdf": ("bids", "bid_id"),
    "redraft_section": None,      # a failed redraft leaves the bid as it was
    "sync_draft": None,           # nor does a failed update of the draft (the error shows on the bid response screen)
}
log = logging.getLogger("bidcraft.worker")
_stopping = False


def _stop(*_):
    global _stopping
    _stopping = True


def _claim(conn: psycopg.Connection):
    job = conn.execute(
        "UPDATE jobs SET status = 'running', started_at = now(), attempts = attempts + 1 "
        "WHERE id = (SELECT id FROM jobs WHERE status = 'queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1) "
        "RETURNING *"
    ).fetchone()
    conn.commit()
    return job


def _process(conn: psycopg.Connection, job: dict) -> None:
    try:
        result = HANDLERS[job["type"]](conn, job) or {}
        conn.execute("UPDATE jobs SET status = 'done', result = %s, error = NULL, waiting_egress_id = NULL, "
                     "finished_at = now() WHERE id = %s", (Jsonb(result), job["id"]))
        conn.commit()
        log.info("job %s (%s) done", job["id"], job["type"])
    except Exception as exc:  # noqa: BLE001 - record any failure against the job
        conn.rollback()
        log.exception("job %s (%s) failed", job["id"], job["type"])
        _fail(conn, job, f"{type(exc).__name__}: {exc}")
        conn.commit()


def _fail(conn: psycopg.Connection, job: dict, message: str) -> None:
    message = message[:4000]
    conn.execute("UPDATE jobs SET status = 'failed', error = %s, finished_at = now() WHERE id = %s", (message, job["id"]))
    target = FAILURE_TARGET.get(job["type"])
    if target and job["payload"].get(target[1]):
        table, key = target
        # a failed re-analysis leaves a bid that is already drafted or built at its stage (the error is still shown)
        keep = analyzer.ADVANCED if job["type"] == "analyze_rfp" and table == "bids" else "''"
        conn.execute(f"UPDATE {table} SET status = CASE WHEN status IN ({keep}) THEN status ELSE 'failed' END, "
                     "error = %s, updated_at = now() WHERE id = %s", (message[:2000], job["payload"][key]))


MAX_ATTEMPTS = 3


def _recover(conn: psycopg.Connection) -> None:
    """Jobs still 'running' were interrupted (the worker was killed, e.g. at its memory limit, or the connection
    dropped). Single worker: retry them, but give up after MAX_ATTEMPTS so one job cannot block the queue."""
    for job in conn.execute("SELECT * FROM jobs WHERE status = 'running'").fetchall():
        if job["attempts"] >= MAX_ATTEMPTS:
            log.error("job %s (%s) interrupted %s times; giving up", job["id"], job["type"], job["attempts"])
            _fail(conn, job, f"Stopped after {job['attempts']} attempts: the worker was interrupted while running "
                             "this job each time (a very large document or out of memory?)")
        else:
            conn.execute("UPDATE jobs SET status = 'queued' WHERE id = %s", (job["id"],))
    conn.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while not _stopping:
        try:
            with connect() as conn:
                _recover(conn)          # also after a lost connection, so no job stays 'running' for ever
                while not _stopping:
                    job = _claim(conn)
                    if job is None:
                        time.sleep(2)
                        continue
                    log.info("job %s (%s) started", job["id"], job["type"])
                    _process(conn, job)
        except psycopg.Error:
            log.exception("database error; reconnecting in 5 s")
            time.sleep(5)


if __name__ == "__main__":
    main()
