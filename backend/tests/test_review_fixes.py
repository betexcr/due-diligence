"""Regression tests for the defects found in review of ea416b8:
implicit acceptance of flagged values, unverified model evidence, permanent
parse errors retried as transient, blank exclusion reasons, unbounded amounts."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, worker
from app.contracts import DocumentContent, Evidence, ExtractedValue, PageText, SourceRef
from app.errors import PermanentError
from app.evidence import check_evidence
from app.extraction import MockExtractor
from app.main import app
from app.money import MAX_MINOR
from app.processing import extract_content
from app.report import readiness


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def drain():
    for _ in range(50):
        if not worker.run_once("test", isolation="thread"):
            return
    raise AssertionError("queue did not drain")


def new_project(client):
    return client.post("/api/projects", json={"name": "P", "company_name": "Alpha Manufacturing Inc."}).json()["id"]


def upload(client, pid, name, data):
    r = client.post(f"/api/projects/{pid}/documents", files={"file": (name, data)})
    assert r.status_code == 201, r.text
    return r.json()


def fields_of(client, doc_id):
    return {f["name"]: f for f in client.get(f"/api/documents/{doc_id}").json()["fields"]}


# --- 1. Approving a flagged value needs a justification ------------------------

def test_flagged_values_cannot_be_approved_without_a_note(client, samples):
    pid = new_project(client)
    doc = upload(client, pid, "fs24.pdf", (samples / "alpha_fs_2024.pdf").read_bytes())["document"]["id"]
    drain()
    flagged = [f for f in fields_of(client, doc).values() if f["status"] == "flagged"]
    assert {f["name"] for f in flagged} >= {"operating_income"}

    for f in flagged:
        url = f"/api/fields/{f['id']}/review"
        assert client.post(url, json={"action": "approve", "expected_version": f["version"]}).status_code == 422
        assert client.post(url, json={"action": "approve", "expected_version": f["version"],
                                      "note": "   "}).status_code == 422
    assert client.post(f"/api/documents/{doc}/approve-all").status_code == 200
    ready, blockers = readiness(pid)
    assert not ready and any("unresolved validation error" in b for b in blockers)

    # With a note per value the analyst can accept the issue, and the reason is on record.
    for f in flagged:
        r = client.post(f"/api/fields/{f['id']}/review", json={
            "action": "approve", "expected_version": f["version"], "note": "  Confirmed with CFO: one-off gain  "})
        assert r.status_code == 200, r.text
    detail = client.get(f"/api/documents/{doc}").json()
    issue = next(i for i in detail["issues"] if i["rule"] == "operating_income_identity")
    assert issue["resolved"] is True
    notes = {r["note"] for r in detail["reviews"] if r["field_id"] in {f["id"] for f in flagged}}
    assert notes == {"Confirmed with CFO: one-off gain"}  # stored trimmed


# --- 2. The model's cited evidence is checked in code ----------------------------

PAGES = DocumentContent(kind="pdf", pages=[
    PageText(page=1, text="Alpha Manufacturing Inc.\nIncome statement"),
    PageText(page=2, text="Revenue    12,500,000\nOperating income   (1,200.50)"),
])
SHEET = DocumentContent(kind="xlsx", pages=[PageText(sheet="P&L", text="A5=Revenue | B5=12500000")])


def ev(value, quote=None, page=None, sheet=None, cell=None):
    src = SourceRef(page=page, sheet=sheet, cell=cell, quote=quote) if quote is not None else None
    return ExtractedValue(value=value, source=src, confidence=0.95)


@pytest.mark.parametrize("value, content, expected", [
    (ev(12_500_000, "Revenue 12,500,000", page=2), PAGES, Evidence.verified),  # whitespace-insensitive
    (ev(-1200.5, "Operating income   (1,200.50)", page=2), PAGES, Evidence.verified),
    (ev(12_500_000, "Revenue 12,500,000", page=1), PAGES, Evidence.quote_not_found),  # wrong page
    (ev(9_000_000, "Cash 9,000,000", page=2), PAGES, Evidence.quote_not_found),  # fabricated quote
    (ev(13_500_000, "Revenue    12,500,000", page=2), PAGES, Evidence.value_not_in_quote),
    (ev(12_500_000), PAGES, Evidence.no_source),
    (ev(None), PAGES, None),
    (ev(12_500_000, "A5=Revenue | B5=12500000", sheet="P&L", cell="B5"), SHEET, Evidence.verified),
    (ev(5, "A5=Revenue | B5=12500000", sheet="P&L"), SHEET, Evidence.value_not_in_quote),  # not the "5" in B5
])
def test_check_evidence(value, content, expected):
    assert check_evidence(value, content) == expected


class FabricatingExtractor(MockExtractor):
    """The offline extractor, plus one invented figure with an invented quote."""

    def extract(self, content, file_name):
        x = super().extract(content, file_name)
        return x.model_copy(update={"cash": ev(9_000_000, "Cash and cash equivalents 9,000,000", page=2)})


def test_fabricated_quote_is_flagged_and_not_bulk_approved(client, samples):
    from app.pipeline import process_document

    pid = new_project(client)
    doc = upload(client, pid, "fs23.pdf", (samples / "alpha_fs_2023.pdf").read_bytes())["document"]["id"]
    drain()
    assert all(f["evidence"] in ("verified", None) for f in fields_of(client, doc).values())

    process_document(doc, extractor=FabricatingExtractor())
    cash = fields_of(client, doc)["cash"]
    assert cash["evidence"] == "quote_not_found" and cash["status"] == "flagged"
    issues = client.get(f"/api/documents/{doc}").json()["issues"]
    assert any(i["rule"] == "source_not_verified" and i["fields"] == ["cash"] and not i["resolved"] for i in issues)

    client.post(f"/api/documents/{doc}/approve-all")
    assert fields_of(client, doc)["cash"]["status"] == "flagged"  # still needs individual review


# --- 4. Unparseable files are permanent failures, not retried ----------------------

def test_non_utf8_text_goes_straight_to_dead_letter(client):
    pid = new_project(client)
    up = upload(client, pid, "notes.txt", "Revenue 100 café".encode("cp1252"))
    drain()
    job = next(j for j in client.get(f"/api/projects/{pid}").json()["jobs"] if j["id"] == up["job_id"])
    assert (job["status"], job["error_kind"], job["attempts"]) == ("dead", "permanent", 1)
    assert "UTF-8" in job["last_error"]


def test_parser_crashes_are_permanent(tmp_path):
    bad_xlsx = tmp_path / "x.xlsx"
    bad_xlsx.write_bytes(b"PK\x03\x04 not really a zip")
    with pytest.raises(PermanentError):
        extract_content(bad_xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    bad_pdf = tmp_path / "x.pdf"
    bad_pdf.write_bytes(b"%PDF-1.4\n1 0 obj << /Type /Pages /Kids [2 0 R] >>\ntrailer << /Root 1 0 R >>")
    with pytest.raises(PermanentError):
        extract_content(bad_pdf, "application/pdf")


# --- 5. Exclusions always carry a reason --------------------------------------------

def test_blank_exclusion_reason_rejected_by_api_and_database(client, samples):
    pid = new_project(client)
    doc = upload(client, pid, "fs23.pdf", (samples / "alpha_fs_2023.pdf").read_bytes())["document"]["id"]
    drain()
    for reason in ("   ", "\n\t"):
        assert client.post(f"/api/documents/{doc}/exclude", json={"reason": reason}).status_code == 422
    r = client.post(f"/api/documents/{doc}/exclude", json={"reason": "  Superseded by restated accounts "})
    assert r.status_code == 200 and r.json()["excluded_reason"] == "Superseded by restated accounts"

    with db.connect() as conn, pytest.raises(sqlite3.IntegrityError, match="non-blank reason"):
        conn.execute("UPDATE documents SET excluded_reason = ' ' WHERE id = ?", (doc,))


def test_migration_3_backfills_blank_exclusion_reasons(tmp_path):
    conn = sqlite3.connect(tmp_path / "v2.sqlite3", isolation_level=None)
    conn.row_factory = sqlite3.Row
    assert db.migrate(conn, target=2) == 2
    conn.execute("INSERT INTO projects VALUES ('p1', 'P', 'Alpha', '2026-01-01')")
    conn.execute("""INSERT INTO documents (id, project_id, file_name, content_type, size_bytes, sha256,
                    storage_path, status, created_at, excluded_at, excluded_reason)
                    VALUES ('d1', 'p1', 'a', 't', 1, 'h', '/x', 'failed', 't', '2026-01-02', '')""")
    assert db.migrate(conn) == 3
    assert conn.execute("SELECT excluded_reason FROM documents").fetchone()[0] == "(no reason recorded)"
    assert "evidence" in [c[1] for c in conn.execute("PRAGMA table_info(fields)")]


# --- 6. Amounts are bounded ------------------------------------------------------------

def test_correction_amount_is_bounded(client, samples):
    pid = new_project(client)
    doc = upload(client, pid, "fs23.pdf", (samples / "alpha_fs_2023.pdf").read_bytes())["document"]["id"]
    drain()
    f = fields_of(client, doc)["revenue"]
    url = f"/api/fields/{f['id']}/review"
    body = {"action": "correct", "note": "n", "expected_version": f["version"]}
    for too_big in (2**63, MAX_MINOR + 1, -MAX_MINOR - 1):
        assert client.post(url, json={**body, "value_minor": too_big}).status_code == 422
    r = client.post(url, json={**body, "value_minor": MAX_MINOR})
    assert r.status_code == 200 and r.json()["value_minor"] == MAX_MINOR
