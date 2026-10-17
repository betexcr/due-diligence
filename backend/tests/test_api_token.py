"""Shared-token gate used when the API is hosted publicly (DD_API_TOKEN)."""

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, settings
from app.main import app

TOKEN = "t" * 40


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def gated(monkeypatch):
    monkeypatch.setattr(settings, "api_token", TOKEN)


def test_no_token_configured_leaves_api_open(client):
    assert client.get("/api/projects").status_code == 200


def test_health_stays_public(client, gated):
    assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("headers", [
    {},
    {"Authorization": "Bearer wrong"},
    {"Authorization": f"Basic {TOKEN}"},
    {"Authorization": TOKEN},
])
def test_missing_or_wrong_token_is_rejected(client, gated, headers):
    res = client.get("/api/projects", headers=headers)
    assert res.status_code == 401
    assert res.headers["www-authenticate"] == "Bearer"


def test_gate_covers_writes_files_and_docs(client, gated):
    assert client.post("/api/projects", json={"name": "x", "company_name": "y"}).status_code == 401
    assert client.get("/api/documents/doc_missing/file").status_code == 401
    assert client.get("/openapi.json").status_code == 401


def test_correct_token_is_accepted(client, gated):
    res = client.get("/api/projects", headers={"Authorization": f"Bearer {TOKEN}"})
    assert res.status_code == 200


def test_short_token_is_refused(monkeypatch):
    monkeypatch.setenv("DD_API_TOKEN", "short")
    with pytest.raises(ValueError, match="DD_API_TOKEN"):
        Settings()


def test_blank_token_means_unset(monkeypatch):
    monkeypatch.setenv("DD_API_TOKEN", "  ")
    assert Settings().api_token is None


def test_required_token_must_be_set(monkeypatch):
    monkeypatch.setenv("DD_REQUIRE_API_TOKEN", "1")
    monkeypatch.delenv("DD_API_TOKEN", raising=False)
    with pytest.raises(ValueError, match="DD_API_TOKEN is not set"):
        Settings()
    monkeypatch.setenv("DD_API_TOKEN", TOKEN)
    assert Settings().api_token == TOKEN
