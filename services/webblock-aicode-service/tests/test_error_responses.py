"""Errors no route handled answer JSON: 503 when the database is unreachable, 500 otherwise. /internal/v1 answers in
Problem Details, /v1 in {"detail": …}; neither echoes the exception."""
import uuid
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from app.main import app
from app.core.config import settings
from app.services import assistant, store

client = TestClient(app)

SECRET = "postgresql://webblock:hunter2@db/webblock"


def _db_down(*args, **kwargs):
    raise OperationalError("SELECT 1", {}, Exception(f"could not connect to {SECRET}"))


def _boom(*args, **kwargs):
    raise RuntimeError(f"unexpected failure near {SECRET}")


def test_unreachable_database_is_503_with_retry_after(monkeypatch):
    monkeypatch.setattr(store, "get_project", _db_down)
    resp = client.get(f"/v1/projects/{uuid.uuid4()}")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "5"
    assert resp.json() == {"detail": "The database is unavailable; retry shortly."}
    assert "hunter2" not in resp.text


def test_unexpected_error_is_a_json_500_without_the_exception(monkeypatch):
    monkeypatch.setattr(store, "get_project", _boom)
    resp = client.get(f"/v1/projects/{uuid.uuid4()}", headers={"Origin": "http://localhost:3000"})
    assert resp.status_code == 500
    assert resp.json() == {"detail": "An unexpected error occurred."}
    assert resp.headers["access-control-allow-origin"] == "http://localhost:3000"  # the editor can read it
    assert "hunter2" not in resp.text


def test_internal_errors_are_problem_details(monkeypatch):
    monkeypatch.setattr(store, "get_run", _db_down)
    resp = client.get(f"/internal/v1/generation-runs/{uuid.uuid4()}")
    assert resp.status_code == 503
    assert resp.headers["content-type"].startswith("application/problem+json")
    assert resp.json()["code"] == "SERVICE_UNAVAILABLE"

    monkeypatch.setattr(store, "get_run", _boom)
    resp = client.get(f"/internal/v1/generation-runs/{uuid.uuid4()}")
    assert resp.status_code == 500
    body = resp.json()
    assert body["code"] == "INTERNAL_ERROR" and body["status"] == 500
    assert "hunter2" not in resp.text


def test_assistant_without_glm_is_503_before_saving_the_message(monkeypatch):
    monkeypatch.setattr(settings, "glm_api_key", "")
    monkeypatch.setattr(store, "get_conversation", lambda *a, **k: (_ for _ in ()).throw(AssertionError("touched the DB")))
    resp = client.post(f"/v1/conversations/{uuid.uuid4()}/messages", json={"content": "Make the hero blue"})
    assert resp.status_code == 503
    assert "GLM_API_KEY" in resp.json()["detail"]
    assert assistant.glm.is_configured() is False
