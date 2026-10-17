# Due Diligence Desk — backend

FastAPI + Pydantic + SQLite API for the due diligence pipeline: document ingestion, a job queue with a
worker pool, LLM (or offline) extraction, deterministic business rules, human review and the report.
See the [root README](../README.md) for the architecture and the demo walkthrough.

## Requirements

- Python 3.12 (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/)

## Setup

Run these from `backend/`.

```bash
uv sync
```

```bash
cp env.example .env
```

`.env` is gitignored and holds local secrets. Leave `ANTHROPIC_API_KEY` empty to use the deterministic
offline extractor; set it to extract with Claude.

Optionally generate the demo documents into `../samples/` (needs the dev dependencies, which `uv sync`
installs by default):

```bash
uv run python scripts/make_samples.py
```

## Run

```bash
uv run --env-file .env uvicorn app.main:app --port 8000
```

uv does not read `.env` on its own: without `--env-file .env` the API key is ignored and the offline
extractor is used. uv exits with an error if a named env file is missing.

To enable the demo's simulated failures (`SIMULATE_FAILURE:` markers in sample documents), also load
`.env.development`:

```bash
uv run --env-file .env.development --env-file .env uvicorn app.main:app --port 8000
```

This is the command `.claude/launch.json` runs (as the `api` configuration). Add `--reload` while
developing.

Check it is up:

- http://localhost:8000/api/health returns `{"ok": true, "extractor": "mock" | "claude", "model": ...}`
- http://localhost:8000/docs is the interactive OpenAPI documentation

On startup the app creates or migrates the SQLite database, re-runs validation over stored values, and
starts the worker pool in the same process. Data lives in `data/` (`dd.sqlite3` and uploaded files
under `files/`); delete that directory to start from an empty database.

The frontend reaches the API through its own `/api/*` route handler, which forwards to `API_URL`
(default `http://localhost:8000`) server-side; the browser never calls the API directly.

## Authentication

Locally, with `DD_API_TOKEN` unset, the API is open. Anywhere the API is reachable from the internet,
set `DD_API_TOKEN` (at least 32 characters) and `DD_REQUIRE_API_TOKEN=1`. Every route except
`/api/health` then needs `Authorization: Bearer <token>`, and the app refuses to start if the token is
missing. The frontend sends the token from its server-side `DD_API_TOKEN`, so it never reaches the
browser. This is a single shared secret for a demo deployment, not user authentication.

## Deploying to Railway

`railway.json` sets the start command and health check; `.railwayignore` keeps `.env`, `.venv` and
`data/` out of uploads. The service needs a volume mounted at `/data` and these variables:
`DD_DATA_DIR=/data`, `DD_REQUIRE_API_TOKEN=1`, `DD_API_TOKEN`, and, for Claude extraction,
`DD_EXTRACTOR=claude` and `ANTHROPIC_API_KEY`. SQLite and the in-process workers mean one replica only.

## Configuration

All settings are environment variables with defaults; see `app/config.py`.

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables the Claude extractor. Secret: keep it in `.env` or your shell. |
| `DD_EXTRACTOR` | `auto` | `auto` (Claude if a key is set, else offline), `claude` or `mock`. |
| `DD_MODEL` | `claude-opus-4-8` | Model used by the Claude extractor. |
| `DD_DATA_DIR` | `backend/data` | Database and file storage location. |
| `DD_CORS_ORIGINS` | `http://localhost:3000` | Comma-separated allowed origins. |
| `DD_API_TOKEN` | unset | Shared bearer token required on all routes but `/api/health`. Secret; min. 32 characters. |
| `DD_REQUIRE_API_TOKEN` | `0` | `1` refuses to start without `DD_API_TOKEN`. Set it on every hosted deployment. |
| `DD_FAULT_INJECTION` | `0` | `1` honours `SIMULATE_FAILURE:` markers. Demo only: it lets document content affect processing. |
| `DD_START_WORKERS` | `1` | `0` runs the API without the worker pool (jobs queue up but do not run). |
| `DD_WORKERS` | `2` | Worker threads. |
| `DD_WORKER_ISOLATION` | `process` | `process` runs each job in a child process so it can be killed on timeout; `thread` is for fast tests only (a timed-out thread is abandoned, not killed). |
| `DD_JOB_TIMEOUT` | `180` | Seconds before a job's child process is killed. Must stay below `DD_LEASE_SECONDS`. |
| `DD_LEASE_SECONDS` | `240` | Job lease; an expired lease lets another worker reclaim the job. |
| `DD_MAX_ATTEMPTS` | `5` | Attempts before a retryable job goes to the dead-letter queue. |
| `DD_BACKOFF_BASE` / `DD_BACKOFF_CAP` | `2` / `120` | Exponential backoff with full jitter, in seconds. |
| `DD_POLL_SECONDS` | `0.5` | Worker queue poll interval. |
| `DD_LLM_TIMEOUT` | `120` | Seconds per LLM request. |
| `DD_MAX_UPLOAD_BYTES` | `26214400` (25 MiB) | Upload size limit. |
| `DD_TOLERANCE_PCT` | `0.5` | Tolerance for accounting identity checks. |
| `DD_MIN_CONFIDENCE` | `0.7` | Extracted values below this confidence are flagged. |

## Tests

```bash
uv run pytest
```

Tests use temporary data directories and the offline extractor, so they need no API key or network.

## Layout

| Path | Contents |
|---|---|
| `app/main.py` | FastAPI app, routes and startup |
| `app/contracts.py` | Pydantic API and LLM output contracts (mirrored by `frontend/lib/contracts.ts`) |
| `app/db.py` | Schema and numbered migrations. Add a new migration for schema changes; never edit an existing one. |
| `app/jobs.py`, `app/worker.py` | Job queue (leases, retries, dead letters) and worker pool |
| `app/ingestion.py`, `app/processing.py` | Upload validation, storage and text extraction |
| `app/extraction.py`, `app/evidence.py` | Claude / offline extraction and source-quote verification |
| `app/pipeline.py`, `app/rules.py` | Pipeline steps and deterministic business rules |
| `app/money.py` | Conversion to integer minor units (money is never a float) |
| `app/report.py` | Report readiness and rendering |
| `scripts/make_samples.py` | Generates the demo documents |
