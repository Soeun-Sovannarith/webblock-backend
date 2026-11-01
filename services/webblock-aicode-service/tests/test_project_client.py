import uuid
from unittest.mock import MagicMock
import pytest
from app.core.config import settings
from app.services import project_client

DOC = {"generationRequestId": "r", "projectId": "p", "businessTypeCode": "clothing_shop", "capturedInputHash": "h",
       "capturedInput": {"componentCatalogVersion": "0.1.0", "generationContractId": "c",
                         "pages": [{"pageId": "pg", "path": "/about", "name": "About", "sortOrder": 2,
                                    "seoTitle": "About us", "seoDescription": None, "puckTree": {"root": {}, "content": []}}]}}


def _response(status, body=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = body
    return resp


def test_fetch_codegen_input_unwraps_projects_envelope(monkeypatch):
    monkeypatch.setattr(settings, "project_service_url", "http://project:8081")
    seen = {}
    monkeypatch.setattr("httpx.get", lambda url, timeout: seen.setdefault("url", url) and _response(200, {
        "success": True, "data": DOC, "meta": {"requestId": "x"}}))
    rid = uuid.uuid4()
    result = project_client.fetch_codegen_input(rid)
    assert (result.status, result.doc) == ("ok", DOC)
    assert seen["url"] == f"http://project:8081/internal/v1/generation-requests/{rid}/codegen-input"


@pytest.mark.parametrize("status, body, expected", [
    (409, {"code": "REQUEST_NOT_GENERATING"}, "not_generating"),
    (404, {"code": "GENERATION_REQUEST_NOT_FOUND"}, "not_found"),
    (503, {"code": "CATALOG_UNAVAILABLE"}, "unavailable"),
    (200, {"success": True, "data": {"capturedInput": {}}}, "unavailable"),
])
def test_fetch_codegen_input_reports_each_outcome(monkeypatch, status, body, expected):
    monkeypatch.setattr(settings, "project_service_url", "http://project:8081")
    monkeypatch.setattr("httpx.get", lambda url, timeout: _response(status, body))
    assert project_client.fetch_codegen_input(uuid.uuid4()).status == expected


def test_fetch_codegen_input_unreachable(monkeypatch):
    monkeypatch.setattr(settings, "project_service_url", "http://project:8081")
    monkeypatch.setattr("httpx.get", lambda url, timeout: (_ for _ in ()).throw(Exception("connection refused")))
    assert project_client.fetch_codegen_input(uuid.uuid4()).status == "unavailable"


def test_to_captured_input_maps_projects_pages(monkeypatch):
    monkeypatch.setattr(settings, "codegen_mode", "hybrid")
    captured = project_client.to_captured_input(DOC)
    assert captured["pages"] == [{"page_id": "pg", "name": "About", "path": "/about", "seo_title": "About us",
                                  "seo_description": None, "sort_order": 2, "puck_tree": {"root": {}, "content": []}}]
    assert (captured["instructions"], captured["seed"], captured["codegen_mode"]) == (None, None, "hybrid")
    assert captured["business_type_code"] == "clothing_shop"


def test_check_project_owner_success(monkeypatch):
    calls = []
    def fake_post(url, json, timeout):
        calls.append((url, json))
        return _response(200, {"success": True, "data": {"allowed": True}})

    pid = uuid.uuid4()
    uid = uuid.uuid4()
    monkeypatch.setattr("app.services.project_client.httpx.post", fake_post)
    assert project_client.check_project_owner(pid, uid) is True
    assert len(calls) == 1
    assert calls[0][0].endswith("/internal/v1/access-checks")
    assert calls[0][1] == {"userId": str(uid), "projectId": str(pid), "action": "manage_site"}

