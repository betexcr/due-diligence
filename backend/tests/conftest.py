import os
from pathlib import Path

import pytest

os.environ.setdefault("DD_START_WORKERS", "0")
os.environ.setdefault("DD_EXTRACTOR", "mock")

from app.config import settings  # noqa: E402
from app.db import init_db  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent.parent / "samples"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    # Env var too, so spawned worker processes see the same database.
    monkeypatch.setenv("DD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DD_FAULT_INJECTION", "1")  # samples use SIMULATE_FAILURE markers
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "extractor", "mock")
    monkeypatch.setattr(settings, "backoff_base_seconds", 0.0)  # retries are due immediately
    init_db()
    yield tmp_path


@pytest.fixture
def samples() -> Path:
    if not (SAMPLES / "alpha_fs_2024.pdf").exists():
        import subprocess
        import sys
        subprocess.run([sys.executable, str(Path(__file__).parent.parent / "scripts" / "make_samples.py")],
                       check=True)
    return SAMPLES
