"""A hung job is killed by the worker timeout and retried, not left running forever."""

import time

from app import worker
from app.db import connect, iso, transaction
from app.ingestion import ingest


def test_hung_job_is_killed_and_rescheduled(monkeypatch):
    monkeypatch.setattr(worker.settings, "job_timeout_seconds", 4.0)
    with transaction() as conn:
        conn.execute("INSERT INTO projects VALUES ('p1', 'P', 'X', ?)", (iso(),))
    up = ingest("p1", "hang.txt", "text/plain", b"Company: X\nSIMULATE_FAILURE: timeout\n")

    started = time.monotonic()
    assert worker.run_once("w1", isolation="process")
    elapsed = time.monotonic() - started
    assert elapsed < 30  # the 1-hour sleep in the child was cut short

    with connect() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (up.job_id,)).fetchone()
    assert job["status"] == "queued" and job["error_kind"] == "retryable"
    assert "timeout" in job["last_error"]
