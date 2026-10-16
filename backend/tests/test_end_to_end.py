"""The full workflow through the HTTP API: ingest -> queue -> extract ->
validate -> review -> report, using the offline extractor."""

import pytest
from fastapi.testclient import TestClient

from app import worker
from app.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def drain(max_runs=50):
    for _ in range(max_runs):
        if not worker.run_once("test", isolation="thread"):
            return
    raise AssertionError("queue did not drain")


def upload(client, pid, path):
    with open(path, "rb") as f:
        r = client.post(f"/api/projects/{pid}/documents", files={"file": (path.name, f)})
    assert r.status_code == 201, r.text
    return r.json()


def test_due_diligence_workflow(client, samples):
    pid = client.post("/api/projects", json={"name": "Target Alpha",
                                             "company_name": "Alpha Manufacturing Inc."}).json()["id"]
    names = ["alpha_fs_2023.pdf", "alpha_fs_2024.pdf", "alpha_mgmt_accounts_2024.xlsx",
             "alpha_bank_confirmation.txt", "corrupt_upload.pdf"]
    docs = {n: upload(client, pid, samples / n)["document"]["id"] for n in names}

    # Re-uploading is idempotent.
    again = upload(client, pid, samples / "alpha_fs_2024.pdf")
    assert again["duplicate"] and again["document"]["id"] == docs["alpha_fs_2024.pdf"]

    drain()
    project = client.get(f"/api/projects/{pid}").json()
    status = {d["file_name"]: d["status"] for d in project["documents"]}
    assert status == {
        "alpha_fs_2023.pdf": "validated",
        "alpha_fs_2024.pdf": "needs_review",
        "alpha_mgmt_accounts_2024.xlsx": "needs_review",  # conflicts with the audited PDF
        "alpha_bank_confirmation.txt": "validated",  # succeeded on the 3rd attempt
        "corrupt_upload.pdf": "failed",
    }
    jobs = {j["file_name"]: j for j in project["jobs"]}
    assert jobs["alpha_bank_confirmation.txt"]["attempts"] == 3
    assert jobs["alpha_bank_confirmation.txt"]["status"] == "succeeded"
    assert jobs["corrupt_upload.pdf"]["status"] == "dead"
    assert jobs["corrupt_upload.pdf"]["error_kind"] == "permanent"
    assert [j["id"] for j in client.get("/api/jobs?status=dead").json()] == [jobs["corrupt_upload.pdf"]["id"]]

    open_rules = sorted(i["rule"] for i in project["issues"] if not i["resolved"])
    assert open_rules == ["cross_document_conflict", "operating_income_identity"]

    # Analyst reviews FY2024: operating income is flagged with the calculated value.
    detail = client.get(f"/api/documents/{docs['alpha_fs_2024.pdf']}").json()
    fields = {f["name"]: f for f in detail["fields"]}
    assert fields["operating_income"]["status"] == "flagged"
    assert fields["operating_income"]["page"] == 2
    assert "4,500,000" in fields["operating_income"]["quote"]
    issue = next(i for i in detail["issues"] if i["rule"] == "operating_income_identity")
    assert issue["expected"] == 3_500_000 and issue["actual"] == 4_500_000

    # Corrections require a note.
    r = client.post(f"/api/fields/{fields['operating_income']['id']}/review",
                    json={"action": "correct", "value": 3_500_000})
    assert r.status_code == 422
    r = client.post(f"/api/fields/{fields['operating_income']['id']}/review",
                    json={"action": "correct", "value": 3_500_000,
                          "note": "Typo in statement; 7.5M - 4.0M confirmed with mgmt accounts"})
    assert r.json()["status"] == "corrected" and r.json()["original_value"] == 4_500_000

    # Correction fixes both the identity and the cross-document conflict.
    project = client.get(f"/api/projects/{pid}").json()
    assert [i for i in project["issues"] if not i["resolved"]] == []
    assert project["report_ready"] is False  # values still need sign-off; corrupt doc failed

    report = client.get(f"/api/projects/{pid}/report").json()
    assert any("failed processing" in b for b in report["blockers"])

    # Operator fixes the DLQ'd document by removing it... here we approve everything else
    # and check the remaining blocker is only the failed upload.
    for doc_id in docs.values():
        client.post(f"/api/documents/{doc_id}/approve-all")
    report = client.get(f"/api/projects/{pid}/report").json()
    assert report["blockers"] == ["corrupt_upload.pdf failed processing (see dead-letter queue)"]

    fy24 = next(i for i, p in enumerate(report["periods"]) if p["file_name"] == "alpha_fs_2024.pdf")
    opinc = next(r for r in report["rows"] if r["field"] == "operating_income")["cells"][fy24]
    assert opinc["value"] == 3_500_000 and opinc["corrected"]
    assert any(a["action"] == "correct" for a in report["audit_trail"])
    assert "Alpha Manufacturing" in client.get(f"/api/projects/{pid}/report.html").text


def test_dead_letter_requeue_reprocesses(client, samples):
    pid = client.post("/api/projects", json={"name": "P", "company_name": "Alpha"}).json()["id"]
    up = upload(client, pid, samples / "corrupt_upload.pdf")
    drain()
    assert client.post(f"/api/jobs/{up['job_id']}/requeue").status_code == 200
    assert client.post(f"/api/jobs/{up['job_id']}/requeue").status_code == 409  # no longer dead
    drain()
    assert client.get(f"/api/jobs?status=dead").json()[0]["attempts"] == 1


def test_rejects_unsupported_upload(client):
    pid = client.post("/api/projects", json={"name": "P", "company_name": "Alpha"}).json()["id"]
    r = client.post(f"/api/projects/{pid}/documents", files={"file": ("x.exe", b"MZ\x90\x00", "application/x-msdownload")})
    assert r.status_code == 422
