from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from . import jobs
from .config import settings
from .contracts import (
    Document,
    DocumentDetail,
    DocumentExclusion,
    DocumentStatus,
    ExtractedField,
    FieldStatus,
    Job,
    Project,
    ProjectCreate,
    ProjectDetail,
    Review,
    ReviewAction,
    UploadResult,
    ValidationIssue,
)
from .db import connect, init_db, iso, new_id, transaction
from .ingestion import IngestionError, ingest
from .pipeline import revalidate
from .report import build_report, readiness, render_html
from .worker import WorkerPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    pool = None
    if os.environ.get("DD_START_WORKERS", "1") == "1":
        pool = WorkerPool()
        pool.start()
    yield
    if pool:
        pool.stop()


app = FastAPI(title="Due Diligence Extraction API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("DD_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

DOC_SELECT = """SELECT d.*, e.fiscal_year FROM documents d
                LEFT JOIN extractions e ON e.document_id = d.id"""
JOB_SELECT = """SELECT j.*, d.file_name FROM jobs j JOIN documents d ON d.id = j.document_id"""


def _doc(r) -> Document:
    data = {k: r[k] for k in Document.model_fields if k in r.keys()}
    data["excluded"] = r["excluded_at"] is not None
    return Document(**data)


def _job(r) -> Job:
    return Job(**{k: r[k] for k in Job.model_fields if k in r.keys()})


def _issue(r) -> ValidationIssue:
    return ValidationIssue(**{**dict(r), "fields": json.loads(r["fields"]), "resolved": bool(r["resolved"])})


def _field(r) -> ExtractedField:
    return ExtractedField(**{k: r[k] for k in ExtractedField.model_fields})


def _project(conn, r) -> Project:
    counts = conn.execute(
        """SELECT (SELECT COUNT(*) FROM documents WHERE project_id = ? AND excluded_at IS NULL) AS docs,
                  (SELECT COUNT(*) FROM validation_issues WHERE project_id = ? AND resolved = 0
                     AND severity IN ('error','warning')) AS issues""",
        (r["id"], r["id"])).fetchone()
    return Project(id=r["id"], name=r["name"], company_name=r["company_name"], created_at=r["created_at"],
                   document_count=counts["docs"], open_issue_count=counts["issues"])


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "extractor": settings.resolved_extractor(), "model": settings.model}


# --- Projects --------------------------------------------------------------

@app.post("/api/projects", response_model=Project, status_code=201)
def create_project(body: ProjectCreate) -> Project:
    pid = new_id("prj")
    with transaction() as conn:
        conn.execute("INSERT INTO projects (id, name, company_name, created_at) VALUES (?, ?, ?, ?)",
                     (pid, body.name, body.company_name, iso()))
        return _project(conn, conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone())


@app.get("/api/projects", response_model=list[Project])
def list_projects() -> list[Project]:
    with connect() as conn:
        return [_project(conn, r) for r in conn.execute("SELECT * FROM projects ORDER BY created_at DESC")]


@app.get("/api/projects/{project_id}", response_model=ProjectDetail)
def get_project(project_id: str) -> ProjectDetail:
    with connect() as conn:
        r = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if r is None:
            raise HTTPException(404, "Project not found")
        docs = conn.execute(f"{DOC_SELECT} WHERE d.project_id = ? ORDER BY d.created_at", (project_id,))
        issues = conn.execute(
            "SELECT * FROM validation_issues WHERE project_id = ? ORDER BY resolved, severity, rule", (project_id,))
        job_rows = conn.execute(f"{JOB_SELECT} WHERE d.project_id = ? ORDER BY j.created_at", (project_id,))
        detail = ProjectDetail(project=_project(conn, r), documents=[_doc(d) for d in docs],
                               issues=[_issue(i) for i in issues], jobs=[_job(j) for j in job_rows],
                               report_ready=False)
    detail.report_ready = readiness(project_id)[0]
    return detail


@app.post("/api/projects/{project_id}/documents", response_model=UploadResult, status_code=201)
async def upload(project_id: str, file: UploadFile = File(...)) -> UploadResult:
    data = await file.read(settings.max_upload_bytes + 1)
    try:
        return ingest(project_id, file.filename or "document", file.content_type, data)
    except IngestionError as exc:
        raise HTTPException(422, str(exc)) from exc


# --- Documents & review ----------------------------------------------------

@app.get("/api/documents/{document_id}", response_model=DocumentDetail)
def get_document(document_id: str) -> DocumentDetail:
    with connect() as conn:
        d = conn.execute(f"{DOC_SELECT} WHERE d.id = ?", (document_id,)).fetchone()
        if d is None:
            raise HTTPException(404, "Document not found")
        e = conn.execute("SELECT * FROM extractions WHERE document_id = ?", (document_id,)).fetchone()
        fields = conn.execute("SELECT * FROM fields WHERE document_id = ? ORDER BY rowid", (document_id,))
        issues = conn.execute(
            "SELECT * FROM validation_issues WHERE document_id = ? OR (document_id IS NULL AND project_id = ?) "
            "ORDER BY resolved, severity", (document_id, d["project_id"]))
        reviews = conn.execute(
            "SELECT r.* FROM reviews r JOIN fields f ON f.id = r.field_id WHERE f.document_id = ? "
            "ORDER BY r.created_at DESC", (document_id,))
        job_rows = conn.execute(f"{JOB_SELECT} WHERE j.document_id = ?", (document_id,))
        return DocumentDetail(
            document=_doc(d),
            company_name=e["company_name"] if e else None,
            currency=e["currency"] if e else None,
            reporting_period_end=e["reporting_period_end"] if e else None,
            fields=[_field(f) for f in fields],
            issues=[_issue(i) for i in issues],
            reviews=[Review(**dict(r)) for r in reviews],
            jobs=[_job(j) for j in job_rows],
        )


@app.get("/api/documents/{document_id}/file")
def get_document_file(document_id: str) -> FileResponse:
    with connect() as conn:
        d = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if d is None or not Path(d["storage_path"]).exists():
        raise HTTPException(404, "File not found")
    return FileResponse(d["storage_path"], media_type=d["content_type"], filename=d["file_name"],
                        content_disposition_type="inline")


class Conflict(HTTPException):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


def _active_document(conn, document_id: str):
    d = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if d is None:
        raise HTTPException(404, "Document not found")
    if d["excluded_at"] is not None:
        raise Conflict("Document is excluded from the project; restore it first")
    return d


def _record_review(conn, field, action: str, new_value: int | None, note: str | None, reviewer: str) -> None:
    status = FieldStatus.corrected if action == "correct" else FieldStatus.approved
    # Compare-and-set on version: a concurrent review or a reprocessing run that
    # changed the field since the reviewer loaded it makes this a no-op.
    cur = conn.execute(
        "UPDATE fields SET value_minor = ?, status = ?, version = version + 1 WHERE id = ? AND version = ?",
        (new_value, status, field["id"], field["version"]))
    if cur.rowcount != 1:
        raise Conflict("This value changed since you loaded it. Reload and review again.")
    conn.execute(
        """INSERT INTO reviews (id, field_id, action, old_value_minor, new_value_minor, note, reviewer, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (new_id("rev"), field["id"], action, field["value_minor"], new_value, note, reviewer, iso()))


@app.post("/api/fields/{field_id}/review", response_model=ExtractedField)
def review_field(field_id: str, body: ReviewAction) -> ExtractedField:
    with transaction() as conn:
        f = conn.execute("SELECT * FROM fields WHERE id = ?", (field_id,)).fetchone()
        if f is None:
            raise HTTPException(404, "Field not found")
        d = _active_document(conn, f["document_id"])
        if f["version"] != body.expected_version:
            raise Conflict("This value changed since you loaded it. Reload and review again.")
        new_value = body.value_minor if body.action == "correct" else f["value_minor"]
        _record_review(conn, f, body.action, new_value, body.note, body.reviewer)
        revalidate(conn, d["project_id"])
        return _field(conn.execute("SELECT * FROM fields WHERE id = ?", (field_id,)).fetchone())


@app.post("/api/documents/{document_id}/approve-all", response_model=DocumentDetail)
def approve_all_clean(document_id: str) -> DocumentDetail:
    """Bulk-approve values that passed every check. Flagged values still need individual review."""
    with transaction() as conn:
        d = _active_document(conn, document_id)
        rows = conn.execute("SELECT * FROM fields WHERE document_id = ? AND status = 'extracted'",
                            (document_id,)).fetchall()
        for f in rows:
            _record_review(conn, f, "approve", f["value_minor"], "bulk approve: passed all checks", "analyst")
        revalidate(conn, d["project_id"])
    return get_document(document_id)


@app.post("/api/documents/{document_id}/exclude", response_model=Document)
def exclude_document(document_id: str, body: DocumentExclusion) -> Document:
    """Take a document out of validation and the report (e.g. an unreadable duplicate).
    Reversible, and the reason is kept for the report's provenance section."""
    with transaction() as conn:
        d = _active_document(conn, document_id)
        if d["status"] in (DocumentStatus.uploaded, DocumentStatus.processing):
            raise Conflict("Document is still being processed; wait for it to finish or fail")
        conn.execute("UPDATE documents SET excluded_at = ?, excluded_reason = ?, excluded_by = ? WHERE id = ?",
                     (iso(), body.reason.strip(), body.reviewer, document_id))
        revalidate(conn, d["project_id"])
        return _doc(conn.execute(f"{DOC_SELECT} WHERE d.id = ?", (document_id,)).fetchone())


@app.post("/api/documents/{document_id}/restore", response_model=Document)
def restore_document(document_id: str) -> Document:
    with transaction() as conn:
        d = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if d is None:
            raise HTTPException(404, "Document not found")
        if d["excluded_at"] is None:
            raise Conflict("Document is not excluded")
        conn.execute("UPDATE documents SET excluded_at = NULL, excluded_reason = NULL, excluded_by = NULL "
                     "WHERE id = ?", (document_id,))
        revalidate(conn, d["project_id"])
        return _doc(conn.execute(f"{DOC_SELECT} WHERE d.id = ?", (document_id,)).fetchone())


# --- Jobs / DLQ --------------------------------------------------------------

@app.get("/api/jobs", response_model=list[Job])
def list_jobs(status: str | None = None) -> list[Job]:
    with connect() as conn:
        if status:
            rows = conn.execute(f"{JOB_SELECT} WHERE j.status = ? ORDER BY j.updated_at DESC", (status,))
        else:
            rows = conn.execute(f"{JOB_SELECT} ORDER BY j.updated_at DESC LIMIT 200")
        return [_job(r) for r in rows]


@app.get("/api/jobs/{job_id}/attempts")
def job_attempts(job_id: str) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM job_attempts WHERE job_id = ? ORDER BY started_at", (job_id,))]


@app.post("/api/jobs/{job_id}/requeue", response_model=Job)
def requeue(job_id: str) -> Job:
    with transaction() as conn:
        j = conn.execute(f"{JOB_SELECT} WHERE j.id = ?", (job_id,)).fetchone()
        if j is None:
            raise HTTPException(404, "Job not found")
        _active_document(conn, j["document_id"])
        if not jobs.requeue_dead(conn, job_id):
            raise Conflict("Only dead-lettered jobs can be requeued")
        conn.execute("UPDATE documents SET status = 'uploaded', error = NULL WHERE id = ?", (j["document_id"],))
        return _job(conn.execute(f"{JOB_SELECT} WHERE j.id = ?", (job_id,)).fetchone())


# --- Report ------------------------------------------------------------------

@app.get("/api/projects/{project_id}/report")
def report_json(project_id: str) -> dict:
    with connect() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise HTTPException(404, "Project not found")
    return build_report(project_id)


@app.get("/api/projects/{project_id}/report.html", response_class=HTMLResponse)
def report_html(project_id: str) -> str:
    return render_html(report_json(project_id))
