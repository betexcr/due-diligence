"""A durable job queue on top of SQLite.

Guarantees:
- Idempotent enqueue: UNIQUE(idempotency_key) means enqueueing the same
  document + pipeline version twice yields one job.
- At-least-once execution with leases: a worker claims a job by setting
  lease_expires_at. If the worker crashes, the lease expires and another
  worker reclaims the job. Handlers must therefore be idempotent (they are:
  all writes are upserts keyed by document_id).
- Retries with capped exponential backoff and full jitter.
- Dead-letter queue: permanent errors, or retryable errors that exhaust
  max_attempts, move the job to status=dead with a dead_letters row that an
  operator can requeue.
"""

from __future__ import annotations

import json
import random
import sqlite3
from datetime import timedelta

from .config import settings
from .contracts import JobStatus
from .db import iso, new_id, now, transaction


def backoff_delay(attempt: int, *, base: float | None = None, cap: float | None = None,
                  rng: random.Random | None = None) -> float:
    """Full jitter (AWS Architecture Blog): sleep = uniform(0, min(cap, base * 2**attempt)).

    Full jitter spreads retries from many workers so they don't hit a
    recovering dependency (e.g. a rate-limited LLM API) in synchronized waves.
    `attempt` is the number of attempts already made (1 after the first failure).
    """
    base = settings.backoff_base_seconds if base is None else base
    cap = settings.backoff_cap_seconds if cap is None else cap
    ceiling = min(cap, base * (2 ** attempt))
    return (rng or random).uniform(0, ceiling)


def enqueue(conn: sqlite3.Connection, document_id: str, kind: str, idempotency_key: str,
            max_attempts: int | None = None) -> tuple[str, bool]:
    """Returns (job_id, created). Must run inside the caller's transaction."""
    existing = conn.execute(
        "SELECT id FROM jobs WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if existing:
        return existing["id"], False
    job_id = new_id("job")
    ts = iso()
    conn.execute(
        """INSERT INTO jobs (id, document_id, kind, idempotency_key, status, attempts,
               max_attempts, next_run_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?, ?)""",
        (job_id, document_id, kind, idempotency_key, JobStatus.queued,
         max_attempts or settings.max_attempts, ts, ts, ts),
    )
    return job_id, True


def claim(worker_id: str) -> sqlite3.Row | None:
    """Atomically lease the next runnable job.

    A job is runnable if it is queued and due, or if it is 'running' but its
    lease expired (the previous worker died or hung past the lease).
    """
    ts = iso()
    with transaction() as conn:
        row = conn.execute(
            """SELECT * FROM jobs
               WHERE (status = ? AND next_run_at <= ?)
                  OR (status = ? AND lease_expires_at < ?)
               ORDER BY next_run_at LIMIT 1""",
            (JobStatus.queued, ts, JobStatus.running, ts),
        ).fetchone()
        if row is None:
            return None
        lease_until = iso(now() + timedelta(seconds=settings.lease_seconds))
        conn.execute(
            """UPDATE jobs SET status = ?, attempts = attempts + 1, lease_owner = ?,
                   lease_expires_at = ?, updated_at = ? WHERE id = ?""",
            (JobStatus.running, worker_id, lease_until, ts, row["id"]),
        )
        # A still-open attempt means the previous worker lost its lease.
        conn.execute(
            """UPDATE job_attempts SET finished_at = ?, outcome = 'lease_expired'
               WHERE job_id = ? AND finished_at IS NULL""",
            (ts, row["id"]),
        )
        conn.execute(
            "INSERT INTO job_attempts (id, job_id, attempt, started_at) VALUES (?, ?, ?, ?)",
            (new_id("att"), row["id"], row["attempts"] + 1, ts),
        )
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()


def _close_attempt(conn: sqlite3.Connection, job: sqlite3.Row, outcome: str,
                   error: str | None = None, delay: float | None = None) -> None:
    conn.execute(
        """UPDATE job_attempts SET finished_at = ?, outcome = ?, error = ?, delay_seconds = ?
           WHERE job_id = ? AND attempt = ? AND finished_at IS NULL""",
        (iso(), outcome, error, delay, job["id"], job["attempts"]),
    )


def _owns(conn: sqlite3.Connection, job: sqlite3.Row, worker_id: str) -> bool:
    """Guard against a slow worker finishing after its lease was taken over."""
    row = conn.execute("SELECT lease_owner, status, attempts FROM jobs WHERE id = ?",
                       (job["id"],)).fetchone()
    return (row is not None and row["lease_owner"] == worker_id
            and row["status"] == JobStatus.running and row["attempts"] == job["attempts"])


def complete(job: sqlite3.Row, worker_id: str) -> bool:
    with transaction() as conn:
        if not _owns(conn, job, worker_id):
            return False
        conn.execute(
            """UPDATE jobs SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                   last_error = NULL, error_kind = NULL, updated_at = ? WHERE id = ?""",
            (JobStatus.succeeded, iso(), job["id"]),
        )
        _close_attempt(conn, job, "succeeded")
        return True


def fail(job: sqlite3.Row, worker_id: str, error: str, retryable: bool) -> str:
    """Record a failure. Returns 'retry', 'dead' or 'stale' (lease lost)."""
    with transaction() as conn:
        if not _owns(conn, job, worker_id):
            return "stale"
        kind = "retryable" if retryable else "permanent"
        exhausted = job["attempts"] >= job["max_attempts"]
        if retryable and not exhausted:
            delay = backoff_delay(job["attempts"])
            conn.execute(
                """UPDATE jobs SET status = ?, next_run_at = ?, lease_owner = NULL,
                       lease_expires_at = NULL, last_error = ?, error_kind = ?, updated_at = ?
                   WHERE id = ?""",
                (JobStatus.queued, iso(now() + timedelta(seconds=delay)), error[:2000], kind,
                 iso(), job["id"]),
            )
            _close_attempt(conn, job, "retry", error[:2000], delay)
            return "retry"

        reason = "permanent_error" if not retryable else "max_attempts_exceeded"
        conn.execute(
            """UPDATE jobs SET status = ?, lease_owner = NULL, lease_expires_at = NULL,
                   last_error = ?, error_kind = ?, updated_at = ? WHERE id = ?""",
            (JobStatus.dead, error[:2000], kind, iso(), job["id"]),
        )
        conn.execute(
            "INSERT INTO dead_letters (id, job_id, reason, payload, created_at) VALUES (?, ?, ?, ?, ?)",
            (new_id("dlq"), job["id"], reason,
             json.dumps({"document_id": job["document_id"], "kind": job["kind"],
                         "attempts": job["attempts"], "error": error[:2000]}), iso()),
        )
        _close_attempt(conn, job, "dead", error[:2000])
        return "dead"


def requeue_dead(conn: sqlite3.Connection, job_id: str) -> bool:
    """Operator action: move a dead-lettered job back onto the queue with a fresh
    budget. Runs in the caller's transaction so related state changes commit with it."""
    row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None or row["status"] != JobStatus.dead:
        return False
    ts = iso()
    conn.execute(
        """UPDATE jobs SET status = ?, attempts = 0, next_run_at = ?, last_error = NULL,
               error_kind = NULL, updated_at = ? WHERE id = ?""",
        (JobStatus.queued, ts, ts, job_id),
    )
    conn.execute(
        "UPDATE dead_letters SET requeued_at = ? WHERE job_id = ? AND requeued_at IS NULL",
        (ts, job_id),
    )
    return True
