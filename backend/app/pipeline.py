"""The processing pipeline for one document, plus project-wide revalidation.

Every write here is idempotent (upserts keyed by document_id / field name),
because the queue gives at-least-once delivery: a job whose worker died after
writing but before acknowledging will run again.

The LLM call has no side effects, so a call that timed out after the provider
actually answered costs one extra call on retry and nothing else: the result is
upserted, never appended.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from .contracts import (
    FINANCIAL_FIELDS,
    PIPELINE_VERSION,
    DocumentStatus,
    Evidence,
    ExtractedValue,
    FieldStatus,
    FinancialExtraction,
    Severity,
    SourceRef,
)
from .db import connect, iso, new_id, transaction
from .errors import PermanentError, RetryableError
from .evidence import check_evidence
from .extraction import PROMPT_VERSION, Extractor, get_extractor
from .money import MINOR_PER_UNIT, AmountOutOfRange, to_minor
from .processing import extract_content
from .rules import DocFacts, validate_document, validate_project

REVIEWED = (FieldStatus.approved, FieldStatus.corrected)

log = logging.getLogger(__name__)

# Demo hook: a document containing e.g. "SIMULATE_FAILURE: transient x2" fails
# its first two attempts with a retryable error. Lets you show backoff, jitter
# and the DLQ live without breaking a real dependency. Off unless
# DD_FAULT_INJECTION=1 (set in backend/.env.development and in tests), because
# it lets document content influence processing.
FAULT_RE = re.compile(r"SIMULATE_FAILURE:\s*(transient|permanent|timeout)(?:\s*x(\d+))?", re.I)


def _fault_injection(text: str, attempt: int) -> None:
    if os.environ.get("DD_FAULT_INJECTION", "0") != "1":
        return
    m = FAULT_RE.search(text)
    if not m:
        return
    kind, times = m.group(1).lower(), int(m.group(2) or 1_000_000)
    if attempt > times:
        return
    if kind == "transient":
        raise RetryableError(f"Simulated transient failure (attempt {attempt}/{times})")
    if kind == "permanent":
        raise PermanentError("Simulated permanent failure")
    if kind == "timeout":
        import time
        time.sleep(3600)  # the worker's timeout must fire


def process_document(document_id: str, attempt: int = 1, extractor: Extractor | None = None) -> None:
    with connect() as conn:
        doc = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if doc is None:
        raise PermanentError(f"Document {document_id} not found")

    _set_doc_status(document_id, DocumentStatus.processing)

    path = Path(doc["storage_path"])
    if not path.exists():
        raise PermanentError(f"Stored file missing: {path}")
    content = extract_content(path, doc["content_type"])
    _fault_injection("\n".join(p.text for p in content.pages), attempt)

    extractor = extractor or get_extractor()
    result = extractor.extract(content, doc["file_name"])
    # Defence in depth: whatever the extractor returns must satisfy the contract.
    result = FinancialExtraction.model_validate(result.model_dump())

    # Saving and revalidating commit together: no window where fields exist
    # without their validation issues.
    evidence = {name: check_evidence(getattr(result, name), content, name) for name in FINANCIAL_FIELDS}
    try:
        with transaction() as conn:
            save_extraction(conn, document_id, result, extractor.name, extractor.model, evidence)
            revalidate(conn, doc["project_id"], processed_document_id=document_id)
    except AmountOutOfRange as exc:
        # The same document will produce the same amount on every retry.
        raise PermanentError(f"Unsupported amount: {exc}") from exc


def save_extraction(conn: sqlite3.Connection, document_id: str, x: FinancialExtraction,
                    extractor_name: str, model: str | None,
                    evidence: dict[str, Evidence | None]) -> None:
    mult = x.unit_multiplier
    conn.execute(
        """INSERT INTO extractions (id, document_id, pipeline_version, extractor, model, prompt_version,
               company_name, fiscal_year, reporting_period_end, currency, unit_multiplier, raw_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(document_id) DO UPDATE SET
               pipeline_version = excluded.pipeline_version, extractor = excluded.extractor,
               model = excluded.model, prompt_version = excluded.prompt_version,
               company_name = excluded.company_name, fiscal_year = excluded.fiscal_year,
               reporting_period_end = excluded.reporting_period_end, currency = excluded.currency,
               unit_multiplier = excluded.unit_multiplier, raw_json = excluded.raw_json,
               created_at = excluded.created_at""",
        (new_id("ext"), document_id, PIPELINE_VERSION, extractor_name, model,
         PROMPT_VERSION if model else None, x.company_name, x.fiscal_year, x.reporting_period_end,
         x.currency, mult, x.model_dump_json(), iso()),
    )
    for name in FINANCIAL_FIELDS:
        ev = getattr(x, name)
        value = to_minor(ev.value, mult) if ev.value is not None else None
        src = ev.source
        status = FieldStatus.missing if value is None else FieldStatus.extracted
        # Never overwrite an analyst's decision on reprocessing. Bumping the
        # version invalidates any review form opened against the old value.
        conn.execute(
            """INSERT INTO fields (id, document_id, name, value_minor, original_value_minor, page, sheet,
                   cell, quote, confidence, status, evidence, version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(document_id, name) DO UPDATE SET
                   value_minor = excluded.value_minor, original_value_minor = excluded.original_value_minor,
                   page = excluded.page, sheet = excluded.sheet, cell = excluded.cell,
                   quote = excluded.quote, confidence = excluded.confidence,
                   status = excluded.status, evidence = excluded.evidence, version = fields.version + 1
               WHERE fields.status NOT IN ('approved', 'corrected')""",
            (new_id("fld"), document_id, name, value, value,
             src.page if src else None, src.sheet if src else None,
             src.cell if src else None, src.quote if src else None,
             ev.confidence, status, evidence.get(name)),
        )


def _set_doc_status(document_id: str, status: DocumentStatus, error: str | None = None) -> None:
    with transaction() as conn:
        conn.execute("UPDATE documents SET status = ?, error = ? WHERE id = ?",
                     (status, error, document_id))


def mark_failed(document_id: str, error: str) -> None:
    _set_doc_status(document_id, DocumentStatus.failed, error[:2000])


def _load_facts(conn: sqlite3.Connection, project_id: str) -> list[DocFacts]:
    rows = conn.execute(
        """SELECT d.id, d.file_name, e.company_name, e.fiscal_year, e.currency
           FROM documents d JOIN extractions e ON e.document_id = d.id
           WHERE d.project_id = ? AND d.excluded_at IS NULL
           ORDER BY e.fiscal_year, d.created_at""",
        (project_id,),
    ).fetchall()
    facts = []
    for r in rows:
        fields = conn.execute(
            "SELECT name, value_minor, confidence, status, evidence FROM fields WHERE document_id = ?",
            (r["id"],)).fetchall()
        facts.append(DocFacts(
            document_id=r["id"], file_name=r["file_name"], company_name=r["company_name"],
            fiscal_year=r["fiscal_year"], currency=r["currency"],
            values={f["name"]: f["value_minor"] for f in fields},
            # Analyst-reviewed values are trusted regardless of model confidence.
            confidence={f["name"]: (1.0 if f["status"] in REVIEWED else f["confidence"]) for f in fields},
            evidence={f["name"]: f["evidence"] for f in fields},
            corrected={f["name"] for f in fields if f["status"] == FieldStatus.corrected},
        ))
    return facts


def revalidate_project(project_id: str, processed_document_id: str | None = None) -> None:
    with transaction() as conn:
        revalidate(conn, project_id, processed_document_id)


def backfill_evidence() -> int:
    """Run the evidence check for values marked not_checked (saved before the check
    existed), from the stored file and the model's original value and citation.
    Values whose file can no longer be read stay not_checked. Returns the number
    of fields updated."""
    with connect() as conn:
        rows = conn.execute(
            """SELECT f.id, f.document_id, f.name, f.original_value_minor, f.page, f.sheet, f.cell, f.quote,
                      d.storage_path, d.content_type, e.unit_multiplier
               FROM fields f JOIN documents d ON d.id = f.document_id
               JOIN extractions e ON e.document_id = d.id
               WHERE f.evidence = 'not_checked' AND f.original_value_minor IS NOT NULL
               ORDER BY f.document_id""").fetchall()
    by_doc: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_doc.setdefault(r["document_id"], []).append(r)

    updated = 0
    for document_id, fields in by_doc.items():
        try:
            content = extract_content(Path(fields[0]["storage_path"]), fields[0]["content_type"])
        except (PermanentError, OSError) as exc:
            log.warning("evidence backfill skipped document %s: %s", document_id, exc)
            continue
        results = []
        for f in fields:
            printed = Decimal(f["original_value_minor"]) / (MINOR_PER_UNIT * f["unit_multiplier"])
            try:
                source = SourceRef(page=f["page"], sheet=f["sheet"], cell=f["cell"],
                                   quote=f["quote"]) if f["quote"] is not None else None
                value = ExtractedValue(value=float(printed), source=source, confidence=1)
            except ValidationError:
                continue  # a stored citation the current contract rejects stays not_checked
            results.append((check_evidence(value, content, f["name"]), f["id"]))
        with transaction() as conn:
            for evidence, field_id in results:
                updated += conn.execute(
                    "UPDATE fields SET evidence = ? WHERE id = ? AND evidence = 'not_checked'",
                    (evidence, field_id)).rowcount
    return updated


def revalidate_all() -> None:
    """Recompute stored issues for every project. Run at startup: rules, or a
    migration such as 4 (which marks old values not_checked), may have changed
    since the issues were stored."""
    with connect() as conn:
        project_ids = [r["id"] for r in conn.execute("SELECT id FROM projects")]
    for project_id in project_ids:
        revalidate_project(project_id)


def _acceptance_key(document_id: str, field: str, rule: str, fields_json: str,
                    expected: int | None, actual: int | None) -> tuple:
    # An acceptance covers one exact issue: if any value in it changes, the
    # expected/actual figures change and the new issue needs a new decision.
    return (document_id, field, rule, fields_json, expected, actual)


def record_acceptances(conn: sqlite3.Connection, document_id: str, field: str, review_id: str) -> None:
    """Record that a review of `field` accepted the issues open on it right now.

    Called for every analyst review, before revalidation. The reviewer saw these
    issues (the field was flagged by them, or listed under them) when deciding.
    Issues that only appear after the review are not covered by it.
    """
    for r in conn.execute(
            """SELECT rule, fields, expected_minor, actual_minor FROM validation_issues
               WHERE document_id = ? AND resolved = 0""", (document_id,)).fetchall():
        if field in json.loads(r["fields"]):
            conn.execute(
                """INSERT OR IGNORE INTO issue_acceptances (id, document_id, field, rule, fields,
                       expected_minor, actual_minor, review_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_id("acc"), document_id, field, r["rule"], r["fields"], r["expected_minor"],
                 r["actual_minor"], review_id, iso()))


def open_issue_count(conn: sqlite3.Connection, document_id: str, field: str) -> int:
    """Unresolved error/warning issues that involve `field`."""
    rows = conn.execute(
        """SELECT fields FROM validation_issues WHERE document_id = ? AND resolved = 0
           AND severity IN ('error', 'warning')""", (document_id,)).fetchall()
    return sum(field in json.loads(r["fields"]) for r in rows)


def revalidate(conn: sqlite3.Connection, project_id: str, processed_document_id: str | None = None) -> None:
    """Recompute every validation issue for the project from current field values.

    Runs inside the caller's transaction so a review or an extraction and the
    issues it implies commit atomically. Recomputing (rather than patching)
    keeps issues a pure function of the data: a correction that fixes an
    identity makes its issue disappear. An issue is resolved (accepted) only
    when every field in it was reviewed while that exact issue was open (see
    record_acceptances); one that first appears after its fields were reviewed,
    e.g. because a correction broke an identity, stays open. Excluded documents
    take no part.
    """
    project = conn.execute("SELECT company_name FROM projects WHERE id = ?", (project_id,)).fetchone()
    facts = _load_facts(conn, project_id)
    issues = [i for d in facts for i in validate_document(d)]
    issues += validate_project(project["company_name"], facts)
    accepted = {
        _acceptance_key(r["document_id"], r["field"], r["rule"], r["fields"], r["expected_minor"], r["actual_minor"])
        for r in conn.execute(
            """SELECT a.* FROM issue_acceptances a JOIN documents d ON d.id = a.document_id
               WHERE d.project_id = ?""", (project_id,))
    }

    rows = conn.execute(
        """SELECT f.document_id, f.name, f.status, f.value_minor FROM fields f
           JOIN documents d ON d.id = f.document_id
           WHERE d.project_id = ? AND d.excluded_at IS NULL""", (project_id,)).fetchall()
    statuses = {(r["document_id"], r["name"]): r["status"] for r in rows}
    values = {(r["document_id"], r["name"]): r["value_minor"] for r in rows}

    conn.execute("DELETE FROM validation_issues WHERE project_id = ?", (project_id,))
    flagged: set[tuple[str, str]] = set()
    for i in issues:
        refs = [(i.document_id, f) for f in i.fields] if i.document_id else []
        fields_json = json.dumps(i.fields)
        resolved = bool(refs) and all(
            statuses.get(r) in REVIEWED
            and _acceptance_key(*r, i.rule, fields_json, i.expected, i.actual) in accepted
            for r in refs)
        if not resolved and i.severity != Severity.info:
            flagged.update(refs)
        conn.execute(
            """INSERT INTO validation_issues (id, project_id, document_id, rule, severity, message,
                   fields, expected_minor, actual_minor, resolved, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id("iss"), project_id, i.document_id, i.rule, i.severity, i.message,
             fields_json, i.expected, i.actual, int(resolved), iso()),
        )

    for key, status in statuses.items():
        if status in REVIEWED:
            continue
        if key in flagged:
            new = FieldStatus.flagged
        else:
            new = FieldStatus.missing if values[key] is None else FieldStatus.extracted
        if new != status:
            # Status changes driven by validation do not bump the version: the
            # value the reviewer saw is unchanged.
            conn.execute("UPDATE fields SET status = ? WHERE document_id = ? AND name = ?", (new, *key))

    for d in facts:
        open_issues = conn.execute(
            """SELECT COUNT(*) FROM validation_issues WHERE document_id = ? AND resolved = 0
               AND severity IN ('error', 'warning')""", (d.document_id,)).fetchone()[0]
        status = DocumentStatus.needs_review if open_issues else DocumentStatus.validated
        # Leave other documents that are mid-processing or failed alone; the
        # document we just processed always gets its final status.
        conn.execute(
            """UPDATE documents SET status = ?, error = NULL
               WHERE id = ? AND (id = ? OR status NOT IN ('processing', 'failed'))""",
            (status, d.document_id, processed_document_id))
