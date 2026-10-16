"""Regression tests for the defects found in review of bfeecf2: issues accepted
implicitly after a correction, unchecked evidence passing as verified, a
magnitude-only evidence check, and unbounded extracted amounts."""

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
from app.pipeline import process_document, revalidate_project
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


def setup_doc(client, samples, name="alpha_fs_2023.pdf"):
    pid = client.post("/api/projects", json={"name": "P", "company_name": "Alpha Manufacturing Inc."}).json()["id"]
    r = client.post(f"/api/projects/{pid}/documents", files={"file": (name, (samples / name).read_bytes())})
    assert r.status_code == 201, r.text
    drain()
    return pid, r.json()["document"]["id"]


def fields_of(client, doc):
    return {f["name"]: f for f in client.get(f"/api/documents/{doc}").json()["fields"]}


def issues_of(client, doc):
    return client.get(f"/api/documents/{doc}").json()["issues"]


def review(client, f, **body):
    return client.post(f"/api/fields/{f['id']}/review", json={"expected_version": f["version"], **body})


# --- 1. A correction cannot implicitly accept the issues it creates ------------------

def test_issue_created_by_a_correction_stays_open_until_accepted(client, samples):
    pid, doc = setup_doc(client, samples)
    assert client.post(f"/api/documents/{doc}/approve-all").status_code == 200
    assert readiness(pid)[0]

    gp = fields_of(client, doc)["gross_profit"]
    assert review(client, gp, action="correct", value_minor=gp["value_minor"] + 100_000_000,
                  note="typo").status_code == 200

    open_errors = {i["rule"] for i in issues_of(client, doc) if i["severity"] == "error" and not i["resolved"]}
    assert open_errors == {"gross_profit_identity", "operating_income_identity"}
    ready, blockers = readiness(pid)
    assert not ready and any("unresolved validation error" in b for b in blockers)

    # The already-approved fields in the new issues need an explicit, justified acceptance.
    involved = {"gross_profit", "revenue", "cost_of_goods_sold", "operating_income", "operating_expenses"}
    fs = fields_of(client, doc)
    assert review(client, fs["revenue"], action="approve").status_code == 409
    for name in involved:
        r = review(client, fields_of(client, doc)[name], action="approve", note="Restated figure, see note 4")
        assert r.status_code == 200, r.text

    assert not [i for i in issues_of(client, doc) if i["severity"] == "error" and not i["resolved"]]
    assert readiness(pid)[0]
    # Accepting the corrected value keeps it marked as a correction.
    assert fields_of(client, doc)["gross_profit"]["status"] == "corrected"


def test_acceptance_covers_only_the_exact_issue(client, samples):
    pid, doc = setup_doc(client, samples, "alpha_fs_2024.pdf")
    flagged = [f for f in fields_of(client, doc).values() if f["status"] == "flagged"]
    for f in flagged:
        assert review(client, f, action="approve", note="as printed").status_code == 200
    assert all(i["resolved"] for i in issues_of(client, doc) if i["rule"] == "operating_income_identity")

    # Changing a value in the accepted identity produces a different issue: open again.
    opex = fields_of(client, doc)["operating_expenses"]
    assert review(client, opex, action="correct", value_minor=opex["value_minor"] - 50_000_000,
                  note="re-read").status_code == 200
    issue = next(i for i in issues_of(client, doc) if i["rule"] == "operating_income_identity")
    assert not issue["resolved"]


def test_migration_4_keeps_previously_accepted_issues_resolved(tmp_path):
    conn = sqlite3.connect(tmp_path / "v3.sqlite3", isolation_level=None)
    conn.row_factory = sqlite3.Row
    assert db.migrate(conn, target=3) == 3
    conn.execute("INSERT INTO projects VALUES ('p1', 'P', 'Alpha', '2026-01-01')")
    conn.execute("""INSERT INTO documents (id, project_id, file_name, content_type, size_bytes, sha256,
                    storage_path, status, created_at) VALUES ('d1', 'p1', 'a', 't', 1, 'h', '/x', 'validated', 't')""")
    for fid, name in (("f1", "revenue"), ("f2", "cash")):
        conn.execute("""INSERT INTO fields (id, document_id, name, value_minor, original_value_minor, quote,
                        confidence, status, version) VALUES (?, 'd1', ?, 100, 100, 'q', 0.9, 'approved', 2)""",
                     (fid, name))
    conn.execute("""INSERT INTO reviews VALUES ('r1', 'f1', 'approve', 100, 100, 'ok', 'a', '2026-01-02')""")
    conn.execute("""INSERT INTO validation_issues VALUES ('i1', 'p1', 'd1', 'revenue_swing', 'warning', 'm',
                    '["revenue"]', 1, 100, 1, 't')""")

    assert db.migrate(conn) == 4
    acc = conn.execute("SELECT field, rule, fields, expected_minor, actual_minor, review_id FROM issue_acceptances").fetchall()
    assert [tuple(a) for a in acc] == [("revenue", "revenue_swing", '["revenue"]', 1, 100, "r1")]
    # Values saved before evidence checks existed no longer pass as verified.
    assert {r["evidence"] for r in conn.execute("SELECT evidence FROM fields")} == {"not_checked"}
    with pytest.raises(sqlite3.IntegrityError, match="supported range"):
        conn.execute("UPDATE fields SET value_minor = ? WHERE id = 'f1'", (MAX_MINOR + 1,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE fields SET evidence = 'bogus' WHERE id = 'f1'")


# --- 2. A value without a recorded evidence check is not "verified" -------------------

def test_unchecked_evidence_fails_closed(client, samples):
    pid, doc = setup_doc(client, samples)
    with db.connect() as conn:
        conn.execute("UPDATE fields SET evidence = 'not_checked' WHERE document_id = ? AND name = 'cash'", (doc,))
        conn.execute("UPDATE fields SET evidence = NULL WHERE document_id = ? AND name = 'revenue'", (doc,))
    revalidate_project(pid)

    unverified = {i["fields"][0] for i in issues_of(client, doc) if i["rule"] == "source_not_verified"}
    assert unverified == {"cash", "revenue"}
    client.post(f"/api/documents/{doc}/approve-all")
    fs = fields_of(client, doc)
    assert fs["cash"]["status"] == fs["revenue"]["status"] == "flagged"


def test_corrected_values_are_not_held_to_the_models_citation(client, samples):
    pid, doc = setup_doc(client, samples)
    with db.connect() as conn:
        conn.execute("UPDATE fields SET evidence = 'value_not_in_quote' WHERE document_id = ? AND name = 'cash'", (doc,))
    revalidate_project(pid)
    cash = fields_of(client, doc)["cash"]
    assert review(client, cash, action="correct", value_minor=cash["value_minor"], note="checked p.3").status_code == 200
    assert not any(i["rule"] == "source_not_verified" for i in issues_of(client, doc))


# --- 3. The evidence check compares signs and needs an unambiguous figure ----------------

PAGE = DocumentContent(kind="pdf", pages=[PageText(page=1, text=(
    "Net loss (1,200,000)\n"
    "Revenue 12,500,000 9,800,000\n"
    "Revenue for 2024: 12,500,000\n"
    "Cost of sales (7,000,000)\n"
    "9,800,000"))])
SHEET = DocumentContent(kind="xlsx", pages=[PageText(sheet="P&L", text="A5=Revenue | B5=12500000 | C5=9800000")])


def ev(value, quote, cell=None, sheet=None):
    return ExtractedValue(value=value, source=SourceRef(page=None if sheet else 1, sheet=sheet, cell=cell, quote=quote),
                          confidence=0.95)


@pytest.mark.parametrize("field, value, content, expected", [
    ("net_income", ev(1_200_000, "Net loss (1,200,000)"), PAGE, Evidence.value_not_in_quote),  # sign flip
    ("net_income", ev(-1_200_000, "Net loss (1,200,000)"), PAGE, Evidence.verified),
    ("revenue", ev(9_800_000, "Revenue 12,500,000 9,800,000"), PAGE, Evidence.ambiguous),  # which column?
    ("revenue", ev(12_500_000, "Revenue 12,500,000 9,800,000"), PAGE, Evidence.ambiguous),
    ("revenue", ev(9_800_000, "9,800,000"), PAGE, Evidence.ambiguous),  # no line item
    ("revenue", ev(12_500_000, "Revenue for 2024: 12,500,000"), PAGE, Evidence.verified),  # a year is not a figure
    ("cost_of_goods_sold", ev(7_000_000, "Cost of sales (7,000,000)"), PAGE, Evidence.verified),  # cost convention
    ("revenue", ev(12_500_000, "A5=Revenue | B5=12500000 | C5=9800000", cell="B5", sheet="P&L"), SHEET,
     Evidence.verified),  # the cited cell pins the figure
    ("revenue", ev(9_800_000, "A5=Revenue | B5=12500000 | C5=9800000", cell="B5", sheet="P&L"), SHEET,
     Evidence.value_not_in_quote),
    ("revenue", ev(12_500_000, "A5=Revenue | B5=12500000 | C5=9800000", sheet="P&L"), SHEET, Evidence.ambiguous),
])
def test_check_evidence_is_strict(field, value, content, expected):
    assert check_evidence(value, content, field) == expected


def test_all_samples_still_verify(client, samples):
    pid = client.post("/api/projects", json={"name": "P", "company_name": "Alpha Manufacturing Inc."}).json()["id"]
    for name in ("alpha_fs_2023.pdf", "alpha_fs_2024.pdf", "alpha_mgmt_accounts_2024.xlsx", "alpha_bank_confirmation.txt"):
        client.post(f"/api/projects/{pid}/documents", files={"file": (name, (samples / name).read_bytes())})
    drain()
    for d in client.get(f"/api/projects/{pid}").json()["documents"]:
        for f in fields_of(client, d["id"]).values():
            assert f["evidence"] in ("verified", None), (d["file_name"], f["name"], f["evidence"], f["quote"])


# --- 4. Extracted amounts are bounded like corrections ------------------------------------

class HugeExtractor(MockExtractor):
    def extract(self, content, file_name):
        x = super().extract(content, file_name)
        return x.model_copy(update={"total_assets": ExtractedValue(
            value=90_100_000_000_000, source=SourceRef(page=1, quote="Total assets 90,100,000,000,000"),
            confidence=0.95)})


def test_out_of_range_extracted_amount_is_a_permanent_failure(client, samples):
    pid, doc = setup_doc(client, samples)
    before = fields_of(client, doc)["total_assets"]
    with pytest.raises(PermanentError, match="Unsupported amount"):
        process_document(doc, extractor=HugeExtractor())
    assert fields_of(client, doc)["total_assets"]["value_minor"] == before["value_minor"]  # rolled back


def test_startup_revalidation_refreshes_stored_issues(client, samples):
    from app.pipeline import revalidate_all

    pid, doc = setup_doc(client, samples)
    with db.connect() as conn:  # as migration 4 leaves rows: changed data, stale issues
        conn.execute("UPDATE fields SET evidence = 'not_checked' WHERE document_id = ? AND name = 'cash'", (doc,))
    assert not any(i["rule"] == "source_not_verified" for i in issues_of(client, doc))
    revalidate_all()
    assert any(i["rule"] == "source_not_verified" and i["fields"] == ["cash"] for i in issues_of(client, doc))


def test_evidence_backfill_checks_legacy_values_from_the_stored_file(client, samples):
    from app.pipeline import backfill_evidence

    pid, doc = setup_doc(client, samples)
    with db.connect() as conn:  # as migration 4 leaves rows saved before the check existed
        conn.execute("UPDATE fields SET evidence = 'not_checked' WHERE document_id = ? AND value_minor IS NOT NULL",
                     (doc,))
        conn.execute("UPDATE fields SET quote = 'Cash and cash equivalents 9,000,000' WHERE document_id = ? "
                     "AND name = 'cash'", (doc,))
    assert backfill_evidence() == 11
    evidence = {n: f["evidence"] for n, f in fields_of(client, doc).items() if f["value_minor"] is not None}
    assert evidence.pop("cash") == "quote_not_found"
    assert set(evidence.values()) == {"verified"}
    assert backfill_evidence() == 0  # nothing left to check


def test_evidence_backfill_leaves_unreadable_documents_not_checked(client, samples):
    from pathlib import Path

    from app.pipeline import backfill_evidence

    pid, doc = setup_doc(client, samples)
    with db.connect() as conn:
        conn.execute("UPDATE fields SET evidence = 'not_checked' WHERE document_id = ? AND value_minor IS NOT NULL",
                     (doc,))
        path = conn.execute("SELECT storage_path FROM documents WHERE id = ?", (doc,)).fetchone()[0]
    Path(path).unlink()
    assert backfill_evidence() == 0
    assert {f["evidence"] for f in fields_of(client, doc).values() if f["value_minor"] is not None} == {"not_checked"}
