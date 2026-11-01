from collections.abc import Callable
from typing import Any
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from app.api import envelope
from app.db import SessionLocal
from app.services import artifacts, portal_sso, store

router = APIRouter()

# --- Project's read of a run (its contract 2.4): enveloped camelCase bodies, Problem Details errors -----------

def _run_document(db, run: dict[str, Any]) -> dict[str, Any]:
    """A run as Project reads it: the authority for its outcome, with each artifact's SHA-256, size and a presigned
    download URL (null without ARTIFACT_URL_SECRET)."""
    project_id = run["project_id"]
    docs = []
    for a in store.list_artifacts(db, run["harness_run_id"]):
        docs.append({"artifactType": a["artifact_type"].lower(), "contentHash": a["content_hash"].strip(),
                     "byteSize": artifacts.byte_size(a["storage_ref"]), "downloadUrl": artifacts.download_url(a["artifact_id"])})
    return {"generationRunId": run["harness_run_id"], "generationRequestId": run["generation_request_id"],
            "projectId": project_id, "inputHash": run["input_hash"].strip(), "status": run["status"].lower(),
            "attempt": run["attempt"], "errorCode": run["error_code"], "artifacts": docs}

@router.get("/generation-runs/{run_id}")
def get_generation_run(run_id: UUID, request: Request):
    """Project -> AI Codegen: GET /internal/v1/generation-runs/{runId}, after the completion event."""
    with SessionLocal() as db:
        run = store.get_run(db, run_id)
        if not run:
            return envelope.problem(request, 404, "GENERATION_RUN_NOT_FOUND", "No generation run with this ID was found.")
        return envelope.ok(request, _run_document(db, run))

@router.get("/generation-requests/{request_id}/runs")
def list_generation_request_runs(request_id: UUID, request: Request, limit: int = Query(default=20, ge=1, le=100)):
    """Project -> AI Codegen: every run of a request, newest first (reconciliation after Project's deadline)."""
    with SessionLocal() as db:
        runs = [store.get_run(db, run_id) for run_id in store.run_ids_for_request(db, request_id, limit=limit)]
        return envelope.ok(request, [_run_document(db, run) for run in runs if run])

@router.get("/artifacts/{artifact_id}")
def download_artifact(artifact_id: UUID, request: Request, expires: int = Query(...), signature: str = Query(..., max_length=64)):
    """The presigned download behind an artifact's downloadUrl: the stored bytes, whose SHA-256 is its contentHash."""
    if not artifacts.verify_download(artifact_id, expires, signature):
        return envelope.problem(request, 403, "DOWNLOAD_URL_INVALID", "The download URL is invalid or has expired.")
    with SessionLocal() as db:
        artifact = store.get_artifact(db, artifact_id)
    if not artifact:
        return envelope.problem(request, 404, "ARTIFACT_NOT_FOUND", "No artifact with this ID was found.")
    name = artifact["storage_ref"].rsplit("/", 1)[-1]
    return FileResponse(artifacts.file_path(artifact["storage_ref"]), media_type=artifacts.MEDIA_TYPES.get(name),
                        filename=name)

# --- this service's own reads ------------------------------------------------------------------------------------

@router.get("/generation-runs/{run_id}/files")
def get_run_files(run_id: UUID):
    """Internal mesh endpoint to download generated source snapshot."""
    with SessionLocal() as db:
        if not store.get_run(db, run_id):
            raise HTTPException(status_code=404, detail="Generation run not found")
        ref = store.artifact_ref(db, run_id, "SOURCE_ARCHIVE")
        return artifacts.read_source(ref) if ref else {}

@router.get("/generation-runs/{run_id}/check-report")
def get_check_report(run_id: UUID):
    """Internal mesh endpoint to download check report artifact."""
    with SessionLocal() as db:
        if not store.get_run(db, run_id):
            raise HTTPException(status_code=404, detail="Generation run not found")
        ref = store.artifact_ref(db, run_id, "CHECK_REPORT")
        return (artifacts.read_json(ref) if ref else None) or {}

# --- product portal access (the only write endpoints under /internal/v1) ------------------------------------

def _portal(action: Callable[[], Any]) -> Any:
    if not portal_sso.is_configured():
        raise HTTPException(status_code=503, detail="Portal SSO is not configured on the AI Code Service.")
    try:
        return action()
    except portal_sso.SiteClientMissing as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except portal_sso.KeycloakError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

@router.get("/projects/{project_id}/portal-owners")
def list_portal_owners(project_id: UUID):
    """Project -> AI Codegen: the Keycloak users who may manage the site's products (the site client's owner role)."""
    return _portal(lambda: portal_sso.list_owners(project_id))

@router.put("/projects/{project_id}/portal-owners/{user_id}", status_code=204)
def grant_portal_owner(project_id: UUID, user_id: UUID):
    """Project -> AI Codegen: let a Keycloak user manage the site's products (idempotent)."""
    _portal(lambda: portal_sso.grant_owner(project_id, str(user_id)))
    return Response(status_code=204)

@router.delete("/projects/{project_id}/portal-owners/{user_id}", status_code=204)
def revoke_portal_owner(project_id: UUID, user_id: UUID):
    """Project -> AI Codegen: remove a Keycloak user's access to the product portal (idempotent)."""
    _portal(lambda: portal_sso.revoke_owner(project_id, str(user_id)))
    return Response(status_code=204)

@router.get("/health")
def internal_health():
    return {"status": "ok", "service": "ai_codegen", "mesh_protocol": "internal/v1"}
