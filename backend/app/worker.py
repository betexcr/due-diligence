"""Background workers.

Each job runs in a child process (isolation="process") so the timeout is a
real kill: Python cannot terminate a hung thread, and a stuck PDF parser or a
hanging socket must not pin a worker forever. job_timeout < lease, so a healthy
worker always finishes (or gives up) before another worker could steal its job.

isolation="thread" skips the subprocess for fast tests; the timeout is then
enforced by abandoning the thread, which is fine for tests only.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from . import jobs
from .config import settings
from .errors import JobTimeoutError, PipelineError

log = logging.getLogger("dd.worker")

HANDLERS = {"process_document"}


def _run_handler(kind: str, document_id: str, attempt: int) -> None:
    from .pipeline import process_document

    if kind != "process_document":
        raise PipelineError(f"Unknown job kind {kind}")
    process_document(document_id, attempt=attempt)


def _child(kind: str, document_id: str, attempt: int, conn) -> None:
    try:
        _run_handler(kind, document_id, attempt)
        conn.send(("ok", None))
    except PipelineError as exc:
        conn.send(("retryable" if exc.retryable else "permanent", str(exc)))
    except Exception as exc:  # unexpected bug: retry, it might be transient (e.g. DB lock)
        conn.send(("retryable", f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=5)}"))
    finally:
        conn.close()


def execute(kind: str, document_id: str, attempt: int, timeout: float,
            isolation: str = "process") -> tuple[str, str | None]:
    """Run one job with a hard timeout. Returns (outcome, error)."""
    if isolation == "thread":
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(_run_handler, kind, document_id, attempt)
        try:
            fut.result(timeout=timeout)
            return "ok", None
        except FutureTimeout:
            return "retryable", str(JobTimeoutError(f"Job exceeded {timeout:.0f}s timeout"))
        except PipelineError as exc:
            return ("retryable" if exc.retryable else "permanent"), str(exc)
        except Exception as exc:
            return "retryable", f"{type(exc).__name__}: {exc}"
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(kind, document_id, attempt, child), daemon=True)
    proc.start()
    child.close()
    try:
        if parent.poll(timeout):
            try:
                return parent.recv()
            except EOFError:
                pass
        if proc.is_alive():
            proc.terminate()
            proc.join(5)
            if proc.is_alive():
                proc.kill()
            return "retryable", f"Job exceeded {timeout:.0f}s timeout and was killed"
        proc.join(1)
        return "retryable", f"Worker process exited unexpectedly (code {proc.exitcode})"
    finally:
        parent.close()


def run_once(worker_id: str, isolation: str | None = None) -> bool:
    """Claim and execute one job. Returns False when the queue had nothing ready."""
    from .pipeline import mark_failed

    job = jobs.claim(worker_id)
    if job is None:
        return False
    isolation = isolation or os.environ.get("DD_WORKER_ISOLATION", "process")
    log.info("worker=%s job=%s attempt=%s/%s", worker_id, job["id"], job["attempts"], job["max_attempts"])
    outcome, error = execute(job["kind"], job["document_id"], job["attempts"],
                             settings.job_timeout_seconds, isolation)
    if outcome == "ok":
        jobs.complete(job, worker_id)
        return True

    result = jobs.fail(job, worker_id, error or "unknown error", retryable=(outcome == "retryable"))
    log.warning("worker=%s job=%s failed (%s) -> %s", worker_id, job["id"], outcome, result)
    if result == "dead":
        mark_failed(job["document_id"], error or "unknown error")
    return True


class WorkerPool:
    def __init__(self, count: int | None = None) -> None:
        self.count = count or settings.worker_count
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        for i in range(self.count):
            t = threading.Thread(target=self._loop, args=(f"w{os.getpid()}-{i}",), daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)

    def _loop(self, worker_id: str) -> None:
        while not self._stop.is_set():
            try:
                if not run_once(worker_id):
                    self._stop.wait(settings.worker_poll_seconds)
            except Exception:
                log.exception("worker loop error")
                self._stop.wait(settings.worker_poll_seconds)
