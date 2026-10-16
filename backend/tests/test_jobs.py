import random
from datetime import timedelta

from app import jobs
from app.db import connect, iso, now, transaction
from app.ingestion import ingest


def _project_doc(text="Company: X\nRevenue 100\n"):
    with transaction() as conn:
        conn.execute("INSERT INTO projects VALUES ('p1', 'P', 'X', ?)", (iso(),))
    return ingest("p1", "doc.txt", "text/plain", text.encode())


def _job(job_id):
    with connect() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()


def test_backoff_is_full_jitter_and_capped():
    rng = random.Random(42)
    for attempt in range(1, 12):
        ceiling = min(60, 2 * 2 ** attempt)
        samples = [jobs.backoff_delay(attempt, base=2, cap=60, rng=rng) for _ in range(200)]
        assert all(0 <= s <= ceiling for s in samples)
        assert max(samples) > ceiling * 0.8  # actually uses the whole range
        assert min(samples) < ceiling * 0.2  # ...including near zero: no thundering herd


def test_enqueue_and_upload_are_idempotent():
    first = _project_doc()
    again = ingest("p1", "renamed.txt", "text/plain", b"Company: X\nRevenue 100\n")
    assert again.duplicate and again.document.id == first.document.id and again.job_id == first.job_id
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_retryable_failure_requeues_with_backoff(monkeypatch):
    monkeypatch.setattr(jobs.settings, "backoff_base_seconds", 10.0)
    up = _project_doc()
    job = jobs.claim("w1")
    assert job["attempts"] == 1 and job["status"] == "running"
    assert jobs.fail(job, "w1", "429 rate limited", retryable=True) == "retry"
    row = _job(up.job_id)
    assert row["status"] == "queued" and row["error_kind"] == "retryable"
    assert row["next_run_at"] > iso()  # scheduled in the future...
    assert jobs.claim("w2") is None  # ...so not claimable yet


def test_exhausted_retries_go_to_dead_letter_queue():
    up = _project_doc()
    for _ in range(5):
        job = jobs.claim("w1")
        result = jobs.fail(job, "w1", "boom", retryable=True)
    assert result == "dead"
    assert _job(up.job_id)["status"] == "dead"
    with connect() as conn:
        dlq = conn.execute("SELECT * FROM dead_letters").fetchone()
        attempts = conn.execute("SELECT outcome FROM job_attempts ORDER BY started_at").fetchall()
    assert dlq["reason"] == "max_attempts_exceeded"
    assert [a["outcome"] for a in attempts] == ["retry"] * 4 + ["dead"]


def test_permanent_failure_skips_retries():
    up = _project_doc()
    job = jobs.claim("w1")
    assert jobs.fail(job, "w1", "corrupt pdf", retryable=False) == "dead"
    with connect() as conn:
        assert conn.execute("SELECT reason FROM dead_letters").fetchone()["reason"] == "permanent_error"
    with transaction() as conn:
        assert jobs.requeue_dead(conn, up.job_id)
    row = _job(up.job_id)
    assert row["status"] == "queued" and row["attempts"] == 0


def test_expired_lease_is_reclaimed_and_stale_worker_cannot_ack():
    up = _project_doc()
    stale = jobs.claim("w1")
    with transaction() as conn:  # simulate w1 hanging past its lease
        conn.execute("UPDATE jobs SET lease_expires_at = ? WHERE id = ?",
                     (iso(now() - timedelta(seconds=1)), up.job_id))
    fresh = jobs.claim("w2")
    assert fresh["id"] == up.job_id and fresh["attempts"] == 2 and fresh["lease_owner"] == "w2"
    assert jobs.complete(stale, "w1") is False  # w1 woke up late: ignored
    assert jobs.complete(fresh, "w2") is True
    with connect() as conn:
        outcomes = [r["outcome"] for r in conn.execute("SELECT outcome FROM job_attempts ORDER BY attempt")]
    assert outcomes == ["lease_expired", "succeeded"]
