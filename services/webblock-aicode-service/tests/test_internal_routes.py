import uuid
from fastapi.testclient import TestClient
from app.main import app
from app.services import store

client = TestClient(app)

def test_internal_health():
    resp = client.get("/internal/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["service"] == "ai_codegen"
    assert data["mesh_protocol"] == "internal/v1"

RUN_ID, REQUEST_ID, PROJECT_ID, ARTIFACT_ID = (str(uuid.uuid4()) for _ in range(4))
RUN = {"harness_run_id": RUN_ID, "generation_request_id": REQUEST_ID, "project_id": PROJECT_ID, "input_hash": "a" * 64,
       "status": "SUCCEEDED", "attempt": 1, "error_code": None}


def _stored_archive(monkeypatch, tmp_path, secret="s3cret"):
    from app.core.config import settings
    from app.services import artifacts
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path))
    monkeypatch.setattr(settings, "artifact_url_secret", secret)
    monkeypatch.setattr(settings, "artifact_base_url", "http://ai-codegen:8000")
    ref = artifacts.ref(PROJECT_ID, RUN_ID, artifacts.SOURCE_ARCHIVE)
    digest = artifacts.write_bytes(ref, artifacts.pack_source({"app/x.ts": "export {};\n"}))
    artifact = {"artifact_id": ARTIFACT_ID, "harness_run_id": RUN_ID, "artifact_type": "SOURCE_ARCHIVE",
                "storage_ref": ref, "content_hash": digest}
    monkeypatch.setattr(store, "get_run", lambda db, rid: RUN if str(rid) == RUN_ID else None)
    monkeypatch.setattr(store, "list_artifacts", lambda db, rid: [artifact])
    monkeypatch.setattr(store, "get_artifact", lambda db, aid: artifact if str(aid) == ARTIFACT_ID else None)
    return artifacts.file_path(ref).read_bytes(), digest


def test_internal_get_generation_run_is_projects_run_document(monkeypatch, tmp_path):
    data, digest = _stored_archive(monkeypatch, tmp_path)
    resp = client.get(f"/internal/v1/generation-runs/{RUN_ID}", headers={"X-Request-Id": "req-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True and body["meta"]["requestId"] == "req-1"
    run = body["data"]
    assert {k: run[k] for k in ("generationRunId", "generationRequestId", "projectId", "status", "attempt", "errorCode")} == {
        "generationRunId": RUN_ID, "generationRequestId": REQUEST_ID, "projectId": PROJECT_ID, "status": "succeeded",
        "attempt": 1, "errorCode": None}
    [archive] = run["artifacts"]
    assert (archive["artifactType"], archive["contentHash"], archive["byteSize"]) == ("source_archive", digest, len(data))
    assert archive["downloadUrl"].startswith(f"http://ai-codegen:8000/internal/v1/artifacts/{ARTIFACT_ID}?expires=")


def test_presigned_download_returns_the_hashed_bytes(monkeypatch, tmp_path):
    import hashlib
    _, digest = _stored_archive(monkeypatch, tmp_path)
    url = client.get(f"/internal/v1/generation-runs/{RUN_ID}").json()["data"]["artifacts"][0]["downloadUrl"]
    resp = client.get(url.replace("http://ai-codegen:8000", ""))
    assert resp.status_code == 200 and resp.headers["content-type"] == "application/gzip"
    assert hashlib.sha256(resp.content).hexdigest() == digest


def test_presigned_download_refuses_a_changed_or_expired_url(monkeypatch, tmp_path):
    import time
    from app.services import artifacts
    _stored_archive(monkeypatch, tmp_path)
    url = artifacts.download_url(ARTIFACT_ID).replace("http://ai-codegen:8000", "")
    other = str(uuid.uuid4())
    assert client.get(url.replace(ARTIFACT_ID, other)).status_code == 403
    expired = int(time.time()) - 1
    resp = client.get(f"/internal/v1/artifacts/{ARTIFACT_ID}?expires={expired}&signature={artifacts._signature(ARTIFACT_ID, expired)}")
    assert resp.status_code == 403
    assert resp.json()["code"] == "DOWNLOAD_URL_INVALID"


def test_runs_carry_no_download_url_without_a_secret(monkeypatch, tmp_path):
    _stored_archive(monkeypatch, tmp_path, secret="")
    assert client.get(f"/internal/v1/generation-runs/{RUN_ID}").json()["data"]["artifacts"][0]["downloadUrl"] is None


def test_internal_get_generation_run_not_found_is_problem_details(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda db, rid: None)
    resp = client.get(f"/internal/v1/generation-runs/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert (body["type"], body["code"], body["status"]) == (
        "urn:webblock:problem:generation-run-not-found", "GENERATION_RUN_NOT_FOUND", 404)


def test_internal_list_request_runs_empty(monkeypatch):
    monkeypatch.setattr(store, "run_ids_for_request", lambda db, rid, limit: [])
    resp = client.get(f"/internal/v1/generation-requests/{uuid.uuid4()}/runs")
    assert resp.status_code == 200
    assert resp.json()["data"] == []

def test_internal_get_run_files_not_found(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda db, rid: None)
    random_id = uuid.uuid4()
    resp = client.get(f"/internal/v1/generation-runs/{random_id}/files")
    assert resp.status_code == 404

def test_internal_get_check_report_not_found(monkeypatch):
    monkeypatch.setattr(store, "get_run", lambda db, rid: None)
    random_id = uuid.uuid4()
    resp = client.get(f"/internal/v1/generation-runs/{random_id}/check-report")
    assert resp.status_code == 404
