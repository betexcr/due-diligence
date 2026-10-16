from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DD_DATA_DIR", BASE_DIR / "data")))

    # "auto" uses Claude when ANTHROPIC_API_KEY is set, otherwise the offline mock.
    extractor: str = field(default_factory=lambda: os.environ.get("DD_EXTRACTOR", "auto"))
    model: str = field(default_factory=lambda: os.environ.get("DD_MODEL", "claude-opus-4-8"))

    max_upload_bytes: int = field(default_factory=lambda: _env_int("DD_MAX_UPLOAD_BYTES", 25 * 1024 * 1024))

    # Worker / retry policy
    worker_count: int = field(default_factory=lambda: _env_int("DD_WORKERS", 2))
    worker_poll_seconds: float = field(default_factory=lambda: _env_float("DD_POLL_SECONDS", 0.5))
    job_timeout_seconds: float = field(default_factory=lambda: _env_float("DD_JOB_TIMEOUT", 180))
    lease_seconds: float = field(default_factory=lambda: _env_float("DD_LEASE_SECONDS", 240))
    max_attempts: int = field(default_factory=lambda: _env_int("DD_MAX_ATTEMPTS", 5))
    backoff_base_seconds: float = field(default_factory=lambda: _env_float("DD_BACKOFF_BASE", 2))
    backoff_cap_seconds: float = field(default_factory=lambda: _env_float("DD_BACKOFF_CAP", 120))
    llm_timeout_seconds: float = field(default_factory=lambda: _env_float("DD_LLM_TIMEOUT", 120))

    # Business validation
    tolerance_pct: float = field(default_factory=lambda: _env_float("DD_TOLERANCE_PCT", 0.5))
    min_confidence: float = field(default_factory=lambda: _env_float("DD_MIN_CONFIDENCE", 0.7))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "dd.sqlite3"

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "files"

    def resolved_extractor(self) -> str:
        if self.extractor != "auto":
            return self.extractor
        return "claude" if os.environ.get("ANTHROPIC_API_KEY") else "mock"


settings = Settings()
