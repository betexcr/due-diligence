"""SQLite persistence. One connection per unit of work; WAL lets the API and
worker threads read while another thread writes.

Schema changes are numbered migrations tracked in PRAGMA user_version; never
edit a migration that has shipped, add a new one.

The schema is deliberately relational (not a blob of LLM JSON) so the data
contract between extraction, validation and reporting stays stable even if the
prompt or model changes.
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from .config import settings

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    company_name TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    file_name TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    -- Idempotent ingestion: the same bytes uploaded twice to a project are one document.
    UNIQUE (project_id, sha256)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    kind TEXT NOT NULL,
    -- Idempotency key = document + pipeline version: re-enqueueing is a no-op.
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    next_run_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    error_kind TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs (status, next_run_at);

CREATE TABLE IF NOT EXISTS job_attempts (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    attempt INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    outcome TEXT,
    error TEXT,
    delay_seconds REAL
);

CREATE TABLE IF NOT EXISTS dead_letters (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES jobs(id),
    reason TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL,
    requeued_at TEXT
);

CREATE TABLE IF NOT EXISTS extractions (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL UNIQUE REFERENCES documents(id),
    pipeline_version TEXT NOT NULL,
    extractor TEXT NOT NULL,
    company_name TEXT,
    fiscal_year INTEGER,
    reporting_period_end TEXT,
    currency TEXT,
    unit_multiplier INTEGER NOT NULL,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fields (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    name TEXT NOT NULL,
    value REAL,
    original_value REAL,
    page INTEGER,
    sheet TEXT,
    cell TEXT,
    quote TEXT,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    UNIQUE (document_id, name)
);

CREATE TABLE IF NOT EXISTS validation_issues (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    document_id TEXT REFERENCES documents(id),
    rule TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    fields TEXT NOT NULL,
    expected REAL,
    actual REAL,
    resolved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id TEXT PRIMARY KEY,
    field_id TEXT NOT NULL REFERENCES fields(id),
    action TEXT NOT NULL,
    old_value REAL,
    new_value REAL,
    note TEXT,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


# Migration 2: money as integer minor units, optimistic concurrency, invariants
# enforced in the database, model/prompt provenance, document exclusion.
SCHEMA_V2 = """
CREATE TABLE fields_v2 (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id),
    name TEXT NOT NULL,
    value_minor INTEGER,
    original_value_minor INTEGER,
    page INTEGER CHECK (page IS NULL OR page >= 1),
    sheet TEXT,
    cell TEXT,
    quote TEXT,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    status TEXT NOT NULL CHECK (status IN ('extracted', 'flagged', 'missing', 'approved', 'corrected')),
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (document_id, name)
);
INSERT INTO fields_v2 (id, document_id, name, value_minor, original_value_minor, page, sheet, cell,
                       quote, confidence, status, version)
    SELECT id, document_id, name, CAST(ROUND(value * 100) AS INTEGER),
           CAST(ROUND(original_value * 100) AS INTEGER), page, sheet, cell, quote,
           MIN(MAX(confidence, 0), 1), status, 1
    FROM fields;
DROP TABLE fields;
ALTER TABLE fields_v2 RENAME TO fields;

CREATE TABLE validation_issues_v2 (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    document_id TEXT REFERENCES documents(id),
    rule TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error', 'warning', 'info')),
    message TEXT NOT NULL,
    fields TEXT NOT NULL,
    expected_minor INTEGER,
    actual_minor INTEGER,
    resolved INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0, 1)),
    created_at TEXT NOT NULL
);
INSERT INTO validation_issues_v2
    SELECT id, project_id, document_id, rule, severity, message, fields,
           CAST(ROUND(expected * 100) AS INTEGER), CAST(ROUND(actual * 100) AS INTEGER),
           resolved, created_at
    FROM validation_issues;
DROP TABLE validation_issues;
ALTER TABLE validation_issues_v2 RENAME TO validation_issues;
CREATE INDEX validation_issues_project ON validation_issues (project_id, resolved);

CREATE TABLE reviews_v2 (
    id TEXT PRIMARY KEY,
    field_id TEXT NOT NULL REFERENCES fields(id),
    action TEXT NOT NULL CHECK (action IN ('approve', 'correct')),
    old_value_minor INTEGER,
    new_value_minor INTEGER,
    note TEXT,
    reviewer TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (action = 'approve' OR (note IS NOT NULL AND length(trim(note)) > 0))
);
INSERT INTO reviews_v2
    SELECT id, field_id, action, CAST(ROUND(old_value * 100) AS INTEGER),
           CAST(ROUND(new_value * 100) AS INTEGER), note, reviewer, created_at
    FROM reviews;
DROP TABLE reviews;
ALTER TABLE reviews_v2 RENAME TO reviews;

ALTER TABLE extractions ADD COLUMN model TEXT;
ALTER TABLE extractions ADD COLUMN prompt_version TEXT;

ALTER TABLE documents ADD COLUMN excluded_at TEXT;
ALTER TABLE documents ADD COLUMN excluded_reason TEXT;
ALTER TABLE documents ADD COLUMN excluded_by TEXT;
"""

# Migration 3: deterministic evidence check per field, and a non-blank reason on
# every exclusion. Given as a statement list because trigger bodies contain ';'.
_EXCLUSION_NEEDS_REASON = """
    WHEN NEW.excluded_at IS NOT NULL AND length(trim(coalesce(NEW.excluded_reason, ''))) = 0
    BEGIN SELECT RAISE(ABORT, 'An excluded document needs a non-blank reason'); END"""
SCHEMA_V3 = [
    """ALTER TABLE fields ADD COLUMN evidence TEXT
       CHECK (evidence IS NULL OR evidence IN ('verified', 'no_source', 'quote_not_found', 'value_not_in_quote'))""",
    # Exclusions saved before the API rejected blank reasons keep a visible marker.
    """UPDATE documents SET excluded_reason = '(no reason recorded)'
       WHERE excluded_at IS NOT NULL AND length(trim(coalesce(excluded_reason, ''))) = 0""",
    f"CREATE TRIGGER documents_exclusion_reason_insert BEFORE INSERT ON documents {_EXCLUSION_NEEDS_REASON}",
    f"""CREATE TRIGGER documents_exclusion_reason_update BEFORE UPDATE OF excluded_at, excluded_reason
        ON documents {_EXCLUSION_NEEDS_REASON}""",
]

MIGRATIONS: list[tuple[int, str | list[str]]] = [(1, SCHEMA_V1), (2, SCHEMA_V2), (3, SCHEMA_V3)]


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    conn = _connect(settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE takes the write lock up front, so claim-then-update
    sequences (job leasing) cannot interleave between workers."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        migrate(conn)


def migrate(conn: sqlite3.Connection, target: int | None = None) -> int:
    """Apply pending migrations in order; returns the resulting schema version.

    Each migration runs in its own transaction together with its user_version
    bump, so a failure leaves the database at the previous version. Foreign key
    enforcement is suspended during a migration (SQLite's documented procedure
    for table rebuilds) and checked before commit.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    for version, script in MIGRATIONS:
        if version <= current or (target is not None and version > target):
            continue
        conn.execute("PRAGMA foreign_keys = OFF")
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in (script if isinstance(script, list) else _split_sql(script)):
                conn.execute(statement)
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(f"Migration {version} broke foreign keys: {violations[:5]}")
            conn.execute(f"PRAGMA user_version = {version}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
        current = version
    return current


def _split_sql(script: str) -> list[str]:
    # Our migration scripts contain no semicolons inside literals or triggers,
    # so splitting on ';' is safe and lets every statement share one transaction
    # (executescript would commit implicitly).
    lines = [ln for ln in script.splitlines() if not ln.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
