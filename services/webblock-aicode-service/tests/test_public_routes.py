"""The public /v1 resource paths, their status codes and Location headers."""
import uuid
from unittest.mock import MagicMock
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.api.routes import projects, runs
from app.services import assistant

client = TestClient(app)

HOME_PUCK = {"root": {"props": {"title": "Petal & Stem"}},
             "content": [{"type": "Hero", "props": {"id": "hero-1", "title": "Fresh flowers", "subtitle": "Daily"}}]}


def _queued(run_id):
    return MagicMock(return_value={"run_id": run_id, "generation_request_id": str(uuid.uuid4()), "status": "QUEUED"})


def test_generation_request_with_puck_json_replaces_the_home_page_and_locates_the_run(monkeypatch):
    run_id, project_id = str(uuid.uuid4()), uuid.uuid4()
    enqueue = _queued(run_id)
    monkeypatch.setattr(projects, "enqueue_generation", enqueue)

    resp = client.post(f"/v1/projects/{project_id}/generation-requests",
                       json={"puck_json": HOME_PUCK, "instructions": "Bigger hero"}, headers={"Idempotency-Key": "k1"})

    assert resp.status_code == 202
    assert resp.headers["Location"] == f"/v1/generation-runs/{run_id}"
    kwargs = enqueue.call_args.kwargs
    assert enqueue.call_args.args == (project_id,)
    assert kwargs["home_puck"] == HOME_PUCK
    assert kwargs["instructions"] == "Bigger hero"
    assert kwargs["idempotency_key"] == "k1"


def test_generation_request_with_nothing_to_do_answers_200_without_location(monkeypatch):
    monkeypatch.setattr(projects, "enqueue_generation", MagicMock(return_value={
        "run_id": None, "generation_request_id": str(uuid.uuid4()), "status": "SUCCEEDED"}))
    resp = client.post(f"/v1/projects/{uuid.uuid4()}/generation-requests", json={})
    assert resp.status_code == 200
    assert "Location" not in resp.headers
    assert projects.enqueue_generation.call_args.kwargs["home_puck"] is None


@pytest.mark.parametrize("status, code", [("QUEUED", 202), ("RUNNING", 202), ("SUCCEEDED", 200), ("FAILED", 200)])
def test_idempotent_replay_is_202_only_while_the_run_is_active(monkeypatch, status, code):
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(projects, "enqueue_generation", MagicMock(return_value={
        "run_id": run_id, "generation_request_id": str(uuid.uuid4()), "status": status}))
    resp = client.post(f"/v1/projects/{uuid.uuid4()}/generation-requests", json={}, headers={"Idempotency-Key": "k"})
    assert resp.status_code == code
    assert ("Location" in resp.headers) == (code == 202)


def _fake_db(monkeypatch, module):
    db = MagicMock()
    db.__enter__.return_value = db
    monkeypatch.setattr(module, "SessionLocal", lambda: db)


def test_generation_run_path(monkeypatch):
    _fake_db(monkeypatch, runs)
    run_id = uuid.uuid4()
    monkeypatch.setattr(runs.store, "get_run_view", lambda db, rid: {"run_id": str(rid), "status": "RUNNING"} if rid == run_id else None)

    assert client.get(f"/v1/generation-runs/{run_id}").json()["status"] == "RUNNING"
    missing = client.get(f"/v1/generation-runs/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "Run not found"


def test_patch_change_set_applies_or_discards(monkeypatch):
    applied, discarded = MagicMock(return_value={"change_set": {"status": "APPLIED"}, "page": {}, "edit_revision": 4}), \
        MagicMock(return_value={"status": "DISCARDED"})
    monkeypatch.setattr(assistant, "apply_change_set", applied)
    monkeypatch.setattr(assistant, "discard_change_set", discarded)
    change_set_id = uuid.uuid4()

    resp = client.patch(f"/v1/change-sets/{change_set_id}", json={"status": "APPLIED"})
    assert resp.status_code == 200 and resp.json()["edit_revision"] == 4
    applied.assert_called_once_with(change_set_id)

    resp = client.patch(f"/v1/change-sets/{change_set_id}", json={"status": "DISCARDED"})
    assert resp.json() == {"change_set": {"status": "DISCARDED"}}
    discarded.assert_called_once_with(change_set_id)

    assert client.patch(f"/v1/change-sets/{change_set_id}", json={"status": "PROPOSED"}).status_code == 422


@pytest.mark.parametrize("method, path", [
    ("post", "/v1/harness/generate"), ("post", "/v1/harness/update"), ("get", "/v1/harness/runs/{id}"),
    ("post", "/v1/projects/generate-from-business"), ("post", "/v1/projects/{id}/onboard-and-generate"),
    ("get", "/v1/projects/{id}/runs"), ("post", "/v1/change-sets/{id}/apply"), ("post", "/v1/change-sets/{id}/discard"),
])
def test_removed_verb_paths_are_gone(method, path):
    resp = getattr(client, method)(path.replace("{id}", str(uuid.uuid4())))
    assert resp.status_code in (404, 405)


def test_openapi_has_no_deprecated_operations_and_unique_labels():
    ops = [(m, p, op) for p, path_ops in app.openapi()["paths"].items() for m, op in path_ops.items()]
    assert not [(m, p) for m, p, op in ops if op.get("deprecated")]
    summaries = [op["summary"] for _, p, op in ops if p.startswith("/v1")]
    assert len(summaries) == len(set(summaries)), "two endpoints share a Swagger label"
