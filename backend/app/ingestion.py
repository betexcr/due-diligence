"""Ingestion: validate, store the original bytes, register, enqueue.

Idempotent end to end: the file is content-addressed by SHA-256, documents are
unique per (project, sha256), and jobs are unique per (document, pipeline
version). Re-uploading the same file, or a client retrying a timed-out upload,
returns the existing document instead of creating duplicates.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .config import settings
from .contracts import PIPELINE_VERSION, Document, DocumentStatus, UploadResult
from .db import iso, new_id, transaction
from .jobs import enqueue
from .processing import ALLOWED_TYPES, sniff_content_type


class IngestionError(ValueError):
    pass


def _safe_name(name: str) -> str:
    name = Path(name).name
    return re.sub(r"[^\w.\- ]", "_", name)[:200] or "document"


def ingest(project_id: str, file_name: str, declared_type: str | None, data: bytes) -> UploadResult:
    if not data:
        raise IngestionError("File is empty")
    if len(data) > settings.max_upload_bytes:
        raise IngestionError(f"File exceeds {settings.max_upload_bytes // (1024 * 1024)} MB limit")
    file_name = _safe_name(file_name)
    content_type = sniff_content_type(file_name, declared_type, data[:8])
    if content_type not in ALLOWED_TYPES:
        raise IngestionError(f"Unsupported file type {content_type!r}. Allowed: PDF, XLSX, TXT, MD, CSV")

    sha = hashlib.sha256(data).hexdigest()
    # Content-addressed storage: writing the same bytes twice is harmless.
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    path = settings.storage_dir / f"{sha}{Path(file_name).suffix.lower()}"
    if not path.exists():
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)  # atomic rename: readers never see a half-written file

    with transaction() as conn:
        if conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone() is None:
            raise IngestionError(f"Project {project_id} not found")
        row = conn.execute("SELECT * FROM documents WHERE project_id = ? AND sha256 = ?",
                           (project_id, sha)).fetchone()
        duplicate = row is not None
        if not duplicate:
            doc_id = new_id("doc")
            conn.execute(
                """INSERT INTO documents (id, project_id, file_name, content_type, size_bytes, sha256,
                       storage_path, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, project_id, file_name, content_type, len(data), sha, str(path),
                 DocumentStatus.uploaded, iso()),
            )
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()
        job_id, _ = enqueue(conn, row["id"], "process_document", f"{row['id']}:{PIPELINE_VERSION}")

    return UploadResult(document=row_to_document(row), duplicate=duplicate, job_id=job_id)


def row_to_document(row) -> Document:
    return Document(
        id=row["id"], project_id=row["project_id"], file_name=row["file_name"],
        content_type=row["content_type"], size_bytes=row["size_bytes"], sha256=row["sha256"],
        status=row["status"], error=row["error"], created_at=row["created_at"],
        fiscal_year=row["fiscal_year"] if "fiscal_year" in row.keys() else None,
    )
