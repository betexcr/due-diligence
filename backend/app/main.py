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
from .pipeline import revalidate_project
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
    return Document(**{k: r[k] for k in Document.model_fields if k in r.keys()})


def _job(r) -> Job:
    return Job(**{k: r[k] for k in Job.model_fields if k in r.keys()})


def _issue(r) -> ValidationIssue:
    return ValidationIssue(**{**dict(r), "fields": json.loads(r["fields"]), "resolved": bool(r["resolved"])})


def _field(r) -> ExtractedField:
    return ExtractedField(**{k: r[k] for k in ExtractedField.model_fields})


def _project(conn, r) -> Project:
    counts = conn.execute(
        """SELECT (SELECT COUNT(*) FROM documents WHERE project_id = ?) AS docs,
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


@app.post("/api/fields/{field_id}/review", response_model=ExtractedField)
def review_field(field_id: str, body: ReviewAction) -> ExtractedField:
    if body.action == "correct" and body.value is None:
        raise HTTPException(422, "A correction needs a value")
    if body.action == "correct" and not (body.note and body.note.strip()):
        raise HTTPException(422, "A correction needs a note explaining the source of the new value")
    with transaction() as conn:
        f = conn.execute(
            "SELECT f.*, d.project_id FROM fields f JOIN documents d ON d.id = f.document_id WHERE f.id = ?",
            (field_id,)).fetchone()
        if f is None:
            raise HTTPException(404, "Field not found")
        new_value = body.value if body.action == "correct" else f["value"]
        status = FieldStatus.corrected if body.action == "correct" else FieldStatus.approved
        conn.execute("UPDATE fields SET value = ?, status = ? WHERE id = ?", (new_value, status, field_id))
        conn.execute(
            """INSERT INTO reviews (id, field_id, action, old_value, new_value, note, reviewer, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id("rev"), field_id, body.action, f["value"], new_value, body.note, body.reviewer, iso()))
        project_id = f["project_id"]
    revalidate_project(project_id)
    with connect() as conn:
        return _field(conn.execute("SELECT * FROM fields WHERE id = ?", (field_id,)).fetchone())


@app.post("/api/documents/{document_id}/approve-all", response_model=DocumentDetail)
def approve_all_clean(document_id: str) -> DocumentDetail:
    """Bulk-approve values that passed every check. Flagged values still need individual review."""
    with transaction() as conn:
        d = conn.execute("SELECT project_id FROM documents WHERE id = ?", (document_id,)).fetchone()
        if d is None:
            raise HTTPException(404, "Document not found")
        rows = conn.execute("SELECT * FROM fields WHERE document_id = ? AND status = 'extracted'",
                            (document_id,)).fetchall()
        for f in rows:
            conn.execute("UPDATE fields SET status = 'approved' WHERE id = ?", (f["id"],))
            conn.execute(
                """INSERT INTO reviews (id, field_id, action, old_value, new_value, note, reviewer, created_at)
                   VALUES (?, ?, 'approve', ?, ?, 'bulk approve: passed all checks', 'analyst', ?)""",
                (new_id("rev"), f["id"], f["value"], f["value"], iso()))
    revalidate_project(d["project_id"])
    return get_document(document_id)


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
    if not jobs.requeue_dead(job_id):
        raise HTTPException(409, "Only dead-lettered jobs can be requeued")
    with transaction() as conn:
        j = conn.execute(f"{JOB_SELECT} WHERE j.id = ?", (job_id,)).fetchone()
        conn.execute("UPDATE documents SET status = 'uploaded', error = NULL WHERE id = ?", (j["document_id"],))
        return _job(j)


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
