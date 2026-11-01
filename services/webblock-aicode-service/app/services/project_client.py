import logging
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID
import httpx
from app.core.config import settings

log = logging.getLogger("webblock.project_client")

@dataclass(frozen=True)
class CodegenInputResult:
    """The outcome of a codegen-input read. status: "ok" (doc is the input document), "not_generating" (409
    REQUEST_NOT_GENERATING: the request failed, timed out or was cancelled, so stop), "not_found" (404), or
    "unavailable" (not configured, unreachable, 5xx, or a malformed body: try again later)."""
    status: Literal["ok", "not_generating", "not_found", "unavailable"]
    doc: dict[str, Any] | None = None


def fetch_codegen_input(generation_request_id: UUID | str) -> CodegenInputResult:
    """Outbound call: AI Codegen -> Project Service
    GET /internal/v1/generation-requests/{generationRequestId}/codegen-input
    The request's frozen input. Project wraps every successful /internal response in {success, data, meta}; errors
    are Problem Details with a `code`. Never raises."""
    if not settings.project_service_url:
        return CodegenInputResult("unavailable")
    url = f"{settings.project_service_url.rstrip('/')}/internal/v1/generation-requests/{generation_request_id}/codegen-input"
    try:
        resp = httpx.get(url, timeout=settings.downstream_timeout_seconds)
    except Exception as e:
        log.warning("Could not reach the Project Service for codegen-input of request %s: %s", generation_request_id, type(e).__name__)
        return CodegenInputResult("unavailable")
    if resp.status_code == 409:
        return CodegenInputResult("not_generating")
    if resp.status_code == 404:
        return CodegenInputResult("not_found")
    if resp.status_code != 200:
        log.warning("Project Service answered HTTP %s for codegen-input of request %s", resp.status_code, generation_request_id)
        return CodegenInputResult("unavailable")
    try:
        body = resp.json()
        doc = body["data"] if isinstance(body, dict) and "success" in body else body
        if not (isinstance(doc, dict) and isinstance(doc.get("capturedInput"), dict)
                and isinstance(doc["capturedInput"].get("pages"), list)):
            raise ValueError("no capturedInput.pages")
    except ValueError as e:
        log.warning("Malformed codegen-input for request %s: %s", generation_request_id, e)
        return CodegenInputResult("unavailable")
    return CodegenInputResult("ok", doc)


def to_captured_input(doc: dict[str, Any]) -> dict[str, Any]:
    """Project's codegen-input document as this service's captured input (store.capture_input's shape). Project's
    input carries only pages: no Drizzle schema, seed or AI instructions, and the mode is this service's
    CODEGEN_MODE. db/seed.sql then falls back to the products in the Puck ProductCards blocks."""
    captured = doc["capturedInput"]
    pages = [{"page_id": p.get("pageId"), "name": p["name"], "path": p["path"], "seo_title": p.get("seoTitle"),
              "seo_description": p.get("seoDescription"), "sort_order": p.get("sortOrder", 0), "puck_tree": p["puckTree"]}
             for p in captured["pages"]]
    return {"pages": pages, "drizzle_schema": None, "seed": None, "instructions": None,
            "codegen_mode": settings.codegen_mode,
            "component_catalog_version": captured.get("componentCatalogVersion"),
            "business_type_code": doc.get("businessTypeCode")}


def check_project_owner(project_id: UUID | str, user_id: UUID | str) -> bool:
    """Outbound call: AI Assistant -> Project Service
    POST /internal/v1/access-checks
    Verifies that user owns or has access to the project before chat thread actions."""
    if not settings.project_service_url:
        return True
    url = f"{settings.project_service_url.rstrip('/')}/internal/v1/access-checks"
    try:
        resp = httpx.post(
            url,
            json={"userId": str(user_id), "projectId": str(project_id), "action": "manage_site"},
            timeout=settings.downstream_timeout_seconds,
        )
        if resp.status_code == 200:
            body = resp.json()
            data = body.get("data") if isinstance(body, dict) and "data" in body else body
            return bool(data.get("allowed", False)) if isinstance(data, dict) else False
        return False
    except Exception as e:
        log.debug("Access check failed against Project Service (%s): %s", url, e)
        return True

