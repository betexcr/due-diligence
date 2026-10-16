# Due Diligence Desk

A working vertical slice of an AI-assisted investment due diligence pipeline:
**ingestion → orchestration → AI extraction → deterministic business validation → persistence → human approval → report.**

```
 Analyst ──► Next.js review UI ──(Zod-validated JSON)──► FastAPI
                                                          │
             upload ─► ingest (type sniff, size cap, SHA-256, content-addressed storage)
                          │  idempotent: UNIQUE(project, sha256), UNIQUE(job idempotency key)
                          ▼
                     jobs table (SQLite) ◄──── worker pool (leases, timeouts, retries, DLQ)
                          │
                          ▼  child process per job, hard timeout
             text extraction (pypdf per page / openpyxl per cell)
                          ▼
             LLM extraction: Claude messages.parse → FinancialExtraction (Pydantic)
                          ▼
             contract re-validation ─► business rules (pure functions) ─► issues
                          ▼
             fields / issues / reviews ─► analyst approves or corrects (with note)
                          ▼
             report built only from approved values, each with its source reference
```

## Run it

Requirements: Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 20+.

```bash
cd backend && uv sync && uv run python scripts/make_samples.py
```

```bash
cd backend && uv run uvicorn app.main:app --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:3000. Without `ANTHROPIC_API_KEY` the backend uses a deterministic offline
extractor (label matching), so the full workflow works with no network access. Set the key to use
Claude (`claude-opus-4-8`, override with `DD_MODEL`). Force a mode with `DD_EXTRACTOR=claude|mock`.

Tests: `cd backend && uv run pytest`

## Demo script (≈5 minutes)

1. Create a project for *Alpha Manufacturing Inc.* and drag all five files from `samples/` onto it.
2. Watch the pipeline column:
   - `alpha_fs_2023.pdf` → **validated** (all accounting identities hold).
   - `alpha_fs_2024.pdf` → **needs review**: operating income printed as 4,500,000, but
     gross profit − opex = 3,500,000 (`operating_income_identity`).
   - `alpha_mgmt_accounts_2024.xlsx` → **needs review**: FY2024 operating income conflicts with
     the audited PDF (`cross_document_conflict`).
   - `alpha_bank_confirmation.txt` → fails twice with a simulated transient error, retried with
     backoff + jitter, succeeds on attempt 3.
   - `corrupt_upload.pdf` → permanent error → straight to the **dead-letter queue**, no retries.
3. Drop `alpha_fs_2024.pdf` again: reported as a duplicate, nothing reprocessed.
4. Open the 2024 review. The flagged value shows the page-2 quote and the PDF at that page.
   Correct it to 3,500,000 (a note is mandatory). Both issues disappear, because validation is
   recomputed from current values.
5. *Approve clean values* on each document, then open the report: only approved figures,
   corrections marked, sources cited, full audit trail.
6. **Pipeline ops** page: attempt history with the actual jittered delays, and DLQ requeue.

## Where each reliability concern lives

| Concern | Implementation |
|---|---|
| **Idempotency** | Content-addressed storage + `UNIQUE(project_id, sha256)` on documents ([ingestion.py](backend/app/ingestion.py)); `UNIQUE(idempotency_key)` = `document:pipeline_version` on jobs; all pipeline writes are upserts, and reprocessing never overwrites an analyst-reviewed field ([pipeline.py](backend/app/pipeline.py)). |
| **At-least-once + leases** | `claim()` uses `BEGIN IMMEDIATE` and a lease; expired leases are reclaimed; a worker that lost its lease cannot ack or fail the job ([jobs.py](backend/app/jobs.py)). |
| **Exponential backoff + jitter** | Full jitter: `uniform(0, min(cap, base·2^attempt))` so retries from many workers don't synchronise against a recovering API. SDK retries are disabled so there is exactly one retry policy. |
| **Error taxonomy** | `RetryableError` (429/5xx/network/timeout/malformed LLM output) vs `PermanentError` (corrupt file, unsupported type, 400/401, refusal) ([errors.py](backend/app/errors.py), [extraction.py](backend/app/extraction.py)). |
| **Dead-letter queue** | Permanent errors, or exhausted retries, set `status=dead` and write a `dead_letters` row; operators requeue with a fresh budget. |
| **Worker timeouts** | Each job runs in a child process killed after `DD_JOB_TIMEOUT` (Python can't kill a hung thread). `job_timeout < lease`, so a healthy worker finishes before anyone can steal its job ([worker.py](backend/app/worker.py)). |
| **Structured I/O contracts** | Pydantic `FinancialExtraction` is the LLM output schema (`messages.parse`), re-validated in the pipeline; `extra="forbid"` rejects hallucinated keys. The UI parses every API response, and validates every request body, with Zod mirrors ([contracts.py](backend/app/contracts.py), [contracts.ts](frontend/lib/contracts.ts)). |
| **LLM doesn't decide** | The model must return `null` instead of guessing, plus a verbatim quote and page/cell per value. All checks are deterministic code ([rules.py](backend/app/rules.py)). |
| **Human approval** | Report readiness requires: no open errors, every extracted value approved/corrected, no failed documents. Corrections require a note and are kept in an audit trail. |

## Prototype shortcuts → production path

| Prototype | Production |
|---|---|
| SQLite job table, in-process worker threads | Postgres (`SELECT … FOR UPDATE SKIP LOCKED`) or SQS/Cloud Tasks with a native DLQ; workers as a separate deployment |
| Local disk storage | S3/GCS with object versioning; signed URLs for the source viewer |
| Text-layer PDFs only | OCR route for scanned PDFs (Textract / Document Intelligence), or page images sent to Claude |
| Hand-written Zod mirror of Pydantic | Generate Zod from the FastAPI OpenAPI schema in CI so the two can't drift |
| No auth | SSO, per-project permissions, reviewer identity from the session |
| Fault injection via `SIMULATE_FAILURE:` marker | Remove, or gate behind a test flag (`DD_FAULT_INJECTION=0` disables it) |
| No extraction accuracy metrics | Golden set of documents with known answers; track field-level precision/recall per model and prompt version |
| Make / n8n not used | Orchestration triggers (email inbox, SharePoint drop, Slack notifications) are good no-code fits *around* this API; the retry/validation core stays in code |
