import hashlib
import json
import re
import uuid
from functools import lru_cache
from typing import Any
from uuid import UUID
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from app.core.config import settings
from app.db import SessionLocal
from app.services import artifacts
from app.services.catalog import CATALOG_VERSION, EMPTY_TREE
from app.services.github import repo_url

# Raw-SQL helpers for the four service schemas of the ERD: project, ai_codegen, ai_assistant, provisioning.
# Columns that reference another service's table are plain UUIDs (no foreign key); joins across schemas are
# only used for read views.

class ActiveRunError(Exception):
    pass

class IdempotencyConflict(Exception):
    pass

def digest(obj: Any) -> str:
    if isinstance(obj, str):
        return hashlib.sha256(obj.encode()).hexdigest()
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()

def _jsonb(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value

def _id(value) -> str | None:
    return str(value) if value else None

# --- project: projects and pages ------------------------------------------------

PROJECT_COLUMNS = """project_id, owner_user_id, name, subdomain, business_type_id, edit_revision, component_catalog_version,
  repository_id, repository_full_name, status, created_at, updated_at"""

def _project(row) -> dict[str, Any]:
    project = dict(row)
    project["repository_url"] = repo_url(project["repository_full_name"])
    return project

def get_project(db, project_id: UUID | str, *, lock: bool = False) -> dict[str, Any]:
    row = db.execute(text(f"""
      SELECT {PROJECT_COLUMNS} FROM project.projects WHERE project_id=:id AND deleted_at IS NULL
      {"FOR UPDATE" if lock else ""}
    """), {"id": str(project_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    return _project(row)

def _subdomain(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "site"
    return f"{slug}-{uuid.uuid4().hex[:8]}"

def create_project(db, *, name: str, repository_full_name: str | None, subdomain: str | None,
                   owner_user_id=None, business_type_id=None) -> dict[str, Any]:
    """Create a project with its home page ("/")."""
    try:
        with db.begin_nested():
            row = db.execute(text(f"""
              INSERT INTO project.projects(name,subdomain,repository_full_name,owner_user_id,business_type_id,component_catalog_version)
              VALUES (:name,:subdomain,:repo,:owner,:btype,:catalog) RETURNING {PROJECT_COLUMNS}
            """), {"name": name, "subdomain": subdomain or _subdomain(name), "repo": repository_full_name,
                   "owner": _id(owner_user_id), "btype": _id(business_type_id), "catalog": CATALOG_VERSION}).mappings().one()
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail="That subdomain is already taken.") from e
    create_page(db, row["project_id"], name="Home", path="/", puck_tree={"root": {"props": {"title": name}}, "content": []})
    return _project(row)

def bump_edit_revision(db, project_id, base_edit_revision: int | None = None) -> int:
    """Increment the project's edit_revision. With base_edit_revision, fail with 409 if another edit landed first."""
    revision = db.execute(text("""
      UPDATE project.projects SET edit_revision=edit_revision+1, updated_at=now()
      WHERE project_id=:id AND (CAST(:base AS bigint) IS NULL OR edit_revision=:base) RETURNING edit_revision
    """), {"id": str(project_id), "base": base_edit_revision}).scalar_one_or_none()
    if revision is None:
        raise HTTPException(status_code=409, detail="The project changed since base_edit_revision; reload and retry.")
    return revision

PAGE_COLUMNS = "page_id, project_id, name, path, puck_tree, seo_title, seo_description, sort_order, created_at, updated_at"

def _page(row) -> dict[str, Any]:
    page = dict(row)
    page["puck_tree"] = _jsonb(page["puck_tree"])
    return page

def list_pages(db, project_id) -> list[dict[str, Any]]:
    rows = db.execute(text(f"""
      SELECT {PAGE_COLUMNS} FROM project.pages WHERE project_id=:id AND deleted_at IS NULL ORDER BY sort_order, path
    """), {"id": str(project_id)}).mappings().all()
    return [_page(r) for r in rows]

def get_page(db, project_id, page_id) -> dict[str, Any]:
    row = db.execute(text(f"""
      SELECT {PAGE_COLUMNS} FROM project.pages WHERE project_id=:pid AND page_id=:id AND deleted_at IS NULL
    """), {"pid": str(project_id), "id": str(page_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Page not found")
    return _page(row)

def home_page(db, project_id) -> dict[str, Any]:
    pages = list_pages(db, project_id)
    home = next((p for p in pages if p["path"] == "/"), pages[0] if pages else None)
    if not home:
        raise HTTPException(status_code=409, detail="Project has no pages")
    return home

def create_page(db, project_id, *, name: str, path: str, puck_tree: dict[str, Any] | None = None,
                seo_title: str | None = None, seo_description: str | None = None, sort_order: int | None = None) -> dict[str, Any]:
    try:
        with db.begin_nested():
            row = db.execute(text(f"""
              INSERT INTO project.pages(project_id,name,path,puck_tree,seo_title,seo_description,sort_order)
              VALUES (:pid,:name,:path,CAST(:tree AS jsonb),:seo_title,:seo_description,
                      COALESCE(:sort, (SELECT COALESCE(MAX(sort_order)+1,0) FROM project.pages WHERE project_id=:pid AND deleted_at IS NULL)))
              RETURNING {PAGE_COLUMNS}
            """), {"pid": str(project_id), "name": name, "path": path, "tree": json.dumps(puck_tree or EMPTY_TREE),
                   "seo_title": seo_title, "seo_description": seo_description, "sort": sort_order}).mappings().one()
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail=f"A page with path {path} already exists.") from e
    return _page(row)

def update_page(db, project_id, page_id, fields: dict[str, Any]) -> dict[str, Any]:
    """Update page columns (name, path, puck_tree, seo_title, seo_description, sort_order). Caller bumps edit_revision."""
    get_page(db, project_id, page_id)
    if not fields:
        return get_page(db, project_id, page_id)
    sets = ",".join(f"{k}=CAST(:{k} AS jsonb)" if k == "puck_tree" else f"{k}=:{k}" for k in fields)
    params = {**fields, "pid": str(project_id), "id": str(page_id)}
    if "puck_tree" in params:
        params["puck_tree"] = json.dumps(params["puck_tree"])
    try:
        with db.begin_nested():
            db.execute(text(f"UPDATE project.pages SET {sets}, updated_at=now() WHERE project_id=:pid AND page_id=:id"), params)
    except IntegrityError as e:
        raise HTTPException(status_code=409, detail=f"A page with path {fields.get('path')} already exists.") from e
    return get_page(db, project_id, page_id)

def delete_page(db, project_id, page_id) -> None:
    if get_page(db, project_id, page_id)["path"] == "/":
        raise HTTPException(status_code=409, detail="The home page (/) cannot be deleted.")
    db.execute(text("UPDATE project.pages SET deleted_at=now(), updated_at=now() WHERE page_id=:id"), {"id": str(page_id)})

def capture_input(db, project_id, *, instructions: str | None, drizzle_schema: str | None,
                  codegen_mode: str | None = None, seed: dict[str, Any] | None = None) -> dict[str, Any]:
    """The frozen codegen input: every live page plus the Drizzle schema, seed data (onboarding only, else null),
    AI instructions and codegen_mode.
    Stored as generation_requests.captured_input and copied to ai_codegen.input_versions."""
    pages = [{"page_id": str(p["page_id"]), "name": p["name"], "path": p["path"], "seo_title": p["seo_title"],
              "seo_description": p["seo_description"], "sort_order": p["sort_order"], "puck_tree": p["puck_tree"]}
             for p in list_pages(db, project_id)]
    return {"pages": pages, "drizzle_schema": drizzle_schema, "seed": seed,
            "instructions": instructions.strip() if instructions and instructions.strip() else None,
            "codegen_mode": codegen_mode or settings.codegen_mode}


# --- project: generation contracts and requests -----------------------------------

def get_or_create_contract(db, project: dict[str, Any]) -> str:
    """The project's current generation contract (created as version 1 on first use)."""
    contract_id = db.execute(text("""
      SELECT generation_contract_id FROM project.generation_contracts WHERE project_id=:id ORDER BY version_number DESC LIMIT 1
    """), {"id": str(project["project_id"])}).scalar_one_or_none()
    if contract_id:
        return str(contract_id)
    return str(db.execute(text("""
      INSERT INTO project.generation_contracts(project_id,version_number,api_contract_version,created_by_user_id)
      VALUES (:id,1,:api,:owner) RETURNING generation_contract_id
    """), {"id": str(project["project_id"]), "api": settings.codegen_api_contract_version,
           "owner": _id(project["owner_user_id"])}).scalar_one())

def find_generation_request(db, project_id, idempotency_key: str) -> dict[str, Any] | None:
    row = db.execute(text("""
      SELECT * FROM project.generation_requests WHERE project_id=:pid AND idempotency_key=:key
    """), {"pid": str(project_id), "key": idempotency_key}).mappings().first()
    return dict(row) if row else None

def get_generation_request(db, request_id) -> dict[str, Any]:
    row = dict(db.execute(text("SELECT * FROM project.generation_requests WHERE generation_request_id=:id"),
                          {"id": str(request_id)}).mappings().one())
    row["captured_input"] = _jsonb(row["captured_input"])
    return row

def create_generation_request(db, *, project_id, contract_id: str, idempotency_key: str, request_hash: str,
                              edit_revision: int, captured_input: dict[str, Any], status: str = "QUEUED",
                              source_commit_sha: str | None = None) -> str:
    return str(db.execute(text("""
      INSERT INTO project.generation_requests(project_id,generation_contract_id,idempotency_key,request_hash,captured_edit_revision,
                                              captured_input,captured_input_hash,status,source_commit_sha)
      VALUES (:pid,:contract,:key,:hash,:rev,CAST(:input AS jsonb),:input_hash,:status,:sha) RETURNING generation_request_id
    """), {"pid": str(project_id), "contract": contract_id, "key": idempotency_key, "hash": request_hash,
           "rev": edit_revision, "input": json.dumps(captured_input), "input_hash": digest(captured_input),
           "status": status, "sha": source_commit_sha}).scalar_one())

def update_generation_request(request_id, **fields: Any) -> None:
    sets = ",".join(f"{k}=:{k}" for k in fields)
    with SessionLocal() as db:
        try:
            db.execute(text(f"UPDATE project.generation_requests SET {sets}, updated_at=now() WHERE generation_request_id=:id"),
                       {**fields, "id": str(request_id)})
            db.commit()
        except Exception:
            pass

def baseline_request(db, project_id) -> dict[str, Any] | None:
    """The project's baseline: its latest generation request whose run was accepted (built, pushed and released)."""
    row = db.execute(text("""
      SELECT * FROM project.generation_requests
      WHERE project_id=:id AND status='SUCCEEDED' AND accepted_generation_run_id IS NOT NULL
      ORDER BY created_at DESC LIMIT 1
    """), {"id": str(project_id)}).mappings().first()
    return dict(row) if row else None

def list_generation_requests(db, project_id, limit: int) -> list[dict[str, Any]]:
    rows = db.execute(text("""
      SELECT generation_request_id, generation_contract_id, idempotency_key, captured_edit_revision, captured_input_hash,
             accepted_generation_run_id, source_commit_sha, status, failure_code, created_at, updated_at,
             captured_input->>'instructions' AS instructions
      FROM project.generation_requests WHERE project_id=:id ORDER BY created_at DESC LIMIT :limit
    """), {"id": str(project_id), "limit": limit}).mappings().all()
    return [dict(r) for r in rows]

# --- ai_codegen: input versions, runs, validations, artifacts ------------------------

def save_input_version(db, request: dict[str, Any], *, catalog_version: str = "webblock.catalog.v1", baseline_commit_sha: str | None = None) -> str:
    """Copy a generation request's frozen input into ai_codegen."""
    return str(db.execute(text("""
      INSERT INTO ai_codegen.input_versions(project_id,generation_request_id,generation_contract_id,captured_edit_revision,
                                            component_catalog_version,captured_input,input_hash,baseline_commit_sha)
      VALUES (:pid,:req,:contract,:rev,:catalog,CAST(:input AS jsonb),:hash,:sha) RETURNING input_version_id
    """), {"pid": str(request["project_id"]), "req": str(request["generation_request_id"]),
           "contract": str(request["generation_contract_id"]), "rev": request["captured_edit_revision"],
           "catalog": catalog_version, "input": json.dumps(request["captured_input"]),
           "hash": request["captured_input_hash"], "sha": baseline_commit_sha}).scalar_one())

def _input_version(row) -> dict[str, Any] | None:
    if not row:
        return None
    version = dict(row)
    version["captured_input"] = _jsonb(version["captured_input"])
    return version

def get_input_version(db, version_id) -> dict[str, Any]:
    return _input_version(db.execute(text("SELECT * FROM ai_codegen.input_versions WHERE input_version_id=:id"),
                                     {"id": str(version_id)}).mappings().one())

def input_version_for_request(db, request_id) -> dict[str, Any] | None:
    return _input_version(db.execute(text("SELECT * FROM ai_codegen.input_versions WHERE generation_request_id=:id"),
                                     {"id": str(request_id)}).mappings().first())

def create_run(db, *, project_id, request_id: str, contract_id: str, baseline_version_id, input_hash: str,
               strategy: str) -> str:
    try:
        with db.begin_nested():
            return str(db.execute(text("""
              INSERT INTO ai_codegen.harness_runs(project_id,generation_request_id,generation_contract_id,baseline_version_id,
                                                  input_hash,strategy,branch_name)
              VALUES (:pid,:req,:contract,:baseline,:hash,:strategy,:branch) RETURNING harness_run_id
            """), {"pid": str(project_id), "req": request_id, "contract": contract_id, "baseline": _id(baseline_version_id),
                   "hash": input_hash, "strategy": strategy, "branch": settings.git_branch}).scalar_one())
    except IntegrityError as e:
        if "ux_one_active_run_per_project" in str(e.orig):
            raise ActiveRunError("A run is already queued or running for this project.") from e
        raise

def create_failed_run(db, *, project_id, request_id: str, contract_id: str, input_hash: str, error_code: str,
                      message: str) -> str:
    """A run that failed before it started (a refused request): inserted FAILED, so no worker ever claims it."""
    return str(db.execute(text("""
      INSERT INTO ai_codegen.harness_runs(project_id,generation_request_id,generation_contract_id,input_hash,strategy,
                                          status,error_code,errors,branch_name,started_at,completed_at)
      VALUES (:pid,:req,:contract,:hash,'SOURCE_CODE','FAILED',:code,:msg,:branch,now(),now()) RETURNING harness_run_id
    """), {"pid": str(project_id), "req": request_id, "contract": contract_id, "hash": input_hash, "code": error_code,
           "msg": message[:2000], "branch": settings.git_branch}).scalar_one())

def get_run(db, run_id) -> dict[str, Any] | None:
    row = db.execute(text("SELECT * FROM ai_codegen.harness_runs WHERE harness_run_id=:id"), {"id": str(run_id)}).mappings().first()
    return dict(row) if row else None

def latest_run_for_request(db, request_id) -> str | None:
    run_id = db.execute(text("""
      SELECT harness_run_id FROM ai_codegen.harness_runs WHERE generation_request_id=:id ORDER BY created_at DESC LIMIT 1
    """), {"id": str(request_id)}).scalar_one_or_none()
    return str(run_id) if run_id else None

def update_run(run_id, **fields: Any) -> None:
    """Update run columns in their own transaction so pollers see progress immediately."""
    sets = ",".join(f"{k}=:{k}" for k in fields)
    with SessionLocal() as db:
        db.execute(text(f"UPDATE ai_codegen.harness_runs SET {sets} WHERE harness_run_id=:run_id"), {**fields, "run_id": str(run_id)})
        db.commit()

@lru_cache(maxsize=1024)
def _run_project(run_id: str) -> str:
    with SessionLocal() as db:
        return str(db.execute(text("SELECT project_id FROM ai_codegen.harness_runs WHERE harness_run_id=:id"),
                              {"id": run_id}).scalar_one())

def run_ref(run_id, name: str, project_id=None) -> str:
    return artifacts.ref(project_id or _run_project(str(run_id)), run_id, name)

def append_log(run_id, message: str) -> None:
    artifacts.append_text(run_ref(run_id, artifacts.RUN_LOG), message.rstrip("\n") + "\n")

def set_step(run_id, step: str) -> None:
    """Start a pipeline step: write its [STEP] marker to the run log and extend the run's lease."""
    append_log(run_id, f"[{step}]")
    with SessionLocal() as db:
        db.execute(text("""
          UPDATE ai_codegen.harness_runs SET lease_expires_at=now() + (:lease * interval '1 second') WHERE harness_run_id=:id
        """), {"lease": settings.run_lease_seconds, "id": str(run_id)})
        db.commit()

def claim_next_run() -> str | None:
    with SessionLocal() as db:
        run_id = db.execute(text("""
          UPDATE ai_codegen.harness_runs SET status='RUNNING', started_at=now(),
                 lease_expires_at=now() + (:lease * interval '1 second')
          WHERE harness_run_id = (
            SELECT harness_run_id FROM ai_codegen.harness_runs WHERE status='QUEUED'
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
          RETURNING harness_run_id
        """), {"lease": settings.run_lease_seconds}).scalar_one_or_none()
        db.commit()
        return str(run_id) if run_id else None

def reap_expired_runs() -> int:
    """Requeue RUNNING runs whose worker died (lease expired); fail them, and their request, after run_max_attempts."""
    with SessionLocal() as db:
        rows = db.execute(text("""
          UPDATE ai_codegen.harness_runs SET
            status = CASE WHEN attempt < :max THEN 'QUEUED'::ai_codegen.run_status ELSE 'FAILED'::ai_codegen.run_status END,
            attempt = CASE WHEN attempt < :max THEN attempt + 1 ELSE attempt END,
            error_code = CASE WHEN attempt < :max THEN error_code ELSE 'LEASE_EXPIRED' END,
            completed_at = CASE WHEN attempt < :max THEN NULL ELSE now() END
          WHERE status='RUNNING' AND lease_expires_at < now()
          RETURNING harness_run_id, project_id, generation_request_id, status
        """), {"max": settings.run_max_attempts}).mappings().all()
        failed = [str(r["generation_request_id"]) for r in rows if r["status"] == "FAILED"]
        if failed:
            db.execute(text("""
              UPDATE project.generation_requests SET status='FAILED', failure_code='LEASE_EXPIRED', updated_at=now()
              WHERE generation_request_id = ANY(CAST(:ids AS uuid[]))
            """), {"ids": failed})
        db.commit()
    for r in rows:
        artifacts.append_text(run_ref(r["harness_run_id"], artifacts.RUN_LOG, r["project_id"]), "[reaper] lease expired\n")
    return len(rows)

def start_validation(run_id, validation_type: str, *, required: bool = True, status: str = "RUNNING") -> str:
    with SessionLocal() as db:
        vid = db.execute(text("""
          INSERT INTO ai_codegen.validation_runs(harness_run_id,validation_type,attempt,required,status,started_at,completed_at)
          VALUES (:run,:type,
                  (SELECT COALESCE(MAX(attempt),0)+1 FROM ai_codegen.validation_runs WHERE harness_run_id=:run AND validation_type=:type),
                  :required,:status,now(),CASE WHEN :done THEN now() END)
          RETURNING validation_run_id
        """), {"run": str(run_id), "type": validation_type, "required": required, "status": status,
               "done": status != "RUNNING"}).scalar_one()
        db.commit()
        return str(vid)

def finish_validation(validation_id, status: str, result: dict[str, Any], error: str | None = None) -> None:
    with SessionLocal() as db:
        db.execute(text("""
          UPDATE ai_codegen.validation_runs SET status=:status, result=CAST(:result AS jsonb), error=:error, completed_at=now()
          WHERE validation_run_id=:id
        """), {"status": status, "result": json.dumps(result), "error": error, "id": str(validation_id)})
        db.commit()

def list_validations(db, run_id) -> list[dict[str, Any]]:
    rows = db.execute(text("""
      SELECT validation_run_id, validation_type, attempt, required, status, error, started_at, completed_at,
             result - 'log' AS result
      FROM ai_codegen.validation_runs WHERE harness_run_id=:id ORDER BY created_at
    """), {"id": str(run_id)}).mappings().all()
    return [dict(r) for r in rows]

def save_artifact(run_id, artifact_type: str, storage_ref: str, content_hash: str) -> None:
    with SessionLocal() as db:
        db.execute(text("""
          INSERT INTO ai_codegen.artifacts(harness_run_id,artifact_type,storage_ref,content_hash)
          VALUES (:run,:type,:ref,:hash)
          ON CONFLICT(harness_run_id,artifact_type) DO UPDATE SET storage_ref=EXCLUDED.storage_ref, content_hash=EXCLUDED.content_hash
        """), {"run": str(run_id), "type": artifact_type, "ref": storage_ref, "hash": content_hash})
        db.commit()

def artifact_ref(db, run_id, artifact_type: str) -> str | None:
    return db.execute(text("SELECT storage_ref FROM ai_codegen.artifacts WHERE harness_run_id=:id AND artifact_type=:type"),
                      {"id": str(run_id), "type": artifact_type}).scalar_one_or_none()

def list_artifacts(db, run_id) -> list[dict[str, Any]]:
    rows = db.execute(text("""
      SELECT artifact_id, artifact_type, storage_ref, content_hash FROM ai_codegen.artifacts
      WHERE harness_run_id=:id ORDER BY artifact_type
    """), {"id": str(run_id)}).mappings().all()
    return [dict(r) for r in rows]

def get_artifact(db, artifact_id) -> dict[str, Any] | None:
    row = db.execute(text("""
      SELECT artifact_id, harness_run_id, artifact_type, storage_ref, content_hash FROM ai_codegen.artifacts
      WHERE artifact_id=:id
    """), {"id": str(artifact_id)}).mappings().first()
    return dict(row) if row else None

def run_ids_for_request(db, request_id, limit: int = 20) -> list[str]:
    return [str(r) for r in db.execute(text("""
      SELECT harness_run_id FROM ai_codegen.harness_runs WHERE generation_request_id=:rid ORDER BY created_at DESC LIMIT :limit
    """), {"rid": str(request_id), "limit": limit}).scalars().all()]

def accepted_source(db, request: dict[str, Any] | None) -> dict[str, str]:
    """Source tree of a generation request's accepted run (empty if it has none)."""
    if not request or not request.get("accepted_generation_run_id"):
        return {}
    ref = artifact_ref(db, request["accepted_generation_run_id"], "SOURCE_ARCHIVE")
    return artifacts.read_source(ref) if ref else {}

RUN_VIEW = """
  SELECT r.harness_run_id AS run_id, r.project_id, r.generation_request_id, r.generation_contract_id,
         CASE WHEN r.baseline_version_id IS NULL THEN 'INITIAL_GENERATION' ELSE 'EXISTING_APP_UPDATE' END AS flow,
         r.status, r.strategy, r.attempt, r.branch_name, r.model_name, r.input_tokens, r.output_tokens,
         r.error_code, r.errors, r.baseline_version_id, iv.input_version_id AS target_version_id,
         r.created_at, r.started_at, r.completed_at,
         g.status AS request_status, g.source_commit_sha AS commit_sha, g.captured_edit_revision,
         (g.accepted_generation_run_id = r.harness_run_id) AS promoted,
         p.repository_full_name, p.subdomain
  FROM ai_codegen.harness_runs r
  LEFT JOIN project.generation_requests g ON g.generation_request_id = r.generation_request_id
  LEFT JOIN project.projects p ON p.project_id = r.project_id
  LEFT JOIN ai_codegen.input_versions iv ON iv.generation_request_id = r.generation_request_id

"""

STEP_MARKER = re.compile(r"^\[([A-Z_]+)\]$", re.MULTILINE)

def _release_view(db, request_id) -> dict[str, Any] | None:
    row = db.execute(text("""
      SELECT rel.release_id, rel.status AS release_status, rel.failure_code, rel.failure_summary, rel.promoted_at,
             d.status AS deploy_status, h.hosting_status, h.subdomain, (h.live_release_id = rel.release_id) AS live
      FROM provisioning.releases rel
      LEFT JOIN provisioning.release_steps d ON d.release_id = rel.release_id AND d.step_name = 'DEPLOY'
      LEFT JOIN provisioning.project_hosting h ON h.project_id = rel.project_id
      WHERE rel.generation_request_id=:id ORDER BY d.attempt DESC NULLS LAST LIMIT 1
    """), {"id": str(request_id)}).mappings().first()
    if not row:
        return None
    url = None
    if row["live"] and row["hosting_status"] == "ONLINE" and row["subdomain"] and settings.site_url_template:
        url = settings.site_url_template.format(subdomain=row["subdomain"])
    return {"release_id": row["release_id"], "status": row["deploy_status"] or row["release_status"],
            "release_status": row["release_status"], "url": url, "promoted_at": row["promoted_at"],
            "error": row["failure_summary"], "failure_code": row["failure_code"]}

def get_run_view(db, run_id, *, include_log: bool = True) -> dict[str, Any] | None:
    row = db.execute(text(RUN_VIEW + " WHERE r.harness_run_id=:id"), {"id": str(run_id)}).mappings().first()
    if not row:
        return None
    view = dict(row)
    view["repository_url"] = repo_url(view.pop("repository_full_name"))
    log = artifacts.read_text(run_ref(run_id, artifacts.RUN_LOG, view["project_id"]))
    markers = STEP_MARKER.findall(log)
    view["step"] = "DONE" if view["status"] == "SUCCEEDED" else (markers[-1] if markers else None)
    report = artifacts.read_json(run_ref(run_id, artifacts.CHECK_REPORT, view["project_id"])) or {}
    view["summary"] = report.get("summary")
    view["changed_files"] = report.get("changed_files", [])
    view["validations"] = list_validations(db, run_id)
    view["deployment"] = _release_view(db, view["generation_request_id"])
    if include_log:
        view["log"] = log
    return view

def list_runs(db, project_id, limit: int) -> list[dict[str, Any]]:
    ids = db.execute(text("""
      SELECT harness_run_id FROM ai_codegen.harness_runs WHERE project_id=:pid ORDER BY created_at DESC LIMIT :limit
    """), {"pid": str(project_id), "limit": limit}).scalars().all()
    return [get_run_view(db, run_id, include_log=False) for run_id in ids]

def list_runs_for_request(db, request_id, limit: int = 20) -> list[dict[str, Any]]:
    """List runs for a given generation_request_id (called by Project Service)."""
    ids = db.execute(text("""
      SELECT harness_run_id FROM ai_codegen.harness_runs WHERE generation_request_id=:rid ORDER BY created_at DESC LIMIT :limit
    """), {"rid": str(request_id), "limit": limit}).scalars().all()
    return [get_run_view(db, run_id, include_log=False) for run_id in ids]

def cancel_active_run_for_request(db, request_id) -> str | None:
    """Cancel an active run for a generation_request_id (called on project.generation_cancelled.v1)."""
    run_id = db.execute(text("""
      UPDATE ai_codegen.harness_runs
      SET status='FAILED', error_code='CANCELLED', errors='Cancelled by project service', completed_at=now(), lease_expires_at=NULL
      WHERE generation_request_id=:rid AND status IN ('QUEUED','RUNNING')
      RETURNING harness_run_id
    """), {"rid": str(request_id)}).scalar_one_or_none()
    if run_id:
        try:
            db.execute(text("""
              UPDATE project.generation_requests SET status='FAILED', failure_code='CANCELLED', updated_at=now()
              WHERE generation_request_id=:rid
            """), {"rid": str(request_id)})
        except Exception:
            pass
        db.commit()
        return str(run_id)
    return None


def component_definitions(db, catalog_version: str) -> dict[str, set[str]]:
    """component_type -> allowed operations, for a catalog version."""
    rows = db.execute(text("""
      SELECT component_type, allowed_operations FROM ai_codegen.puck_component_definitions WHERE component_catalog_version=:v
    """), {"v": catalog_version}).all()
    return {r.component_type: set(_jsonb(r.allowed_operations)) for r in rows}

# --- ai_assistant: conversations, messages, change sets -------------------------------

def create_conversation(db, project_id, *, title: str | None, user_id=None) -> dict[str, Any]:
    return dict(db.execute(text("""
      INSERT INTO ai_assistant.conversations(project_id,user_id,title) VALUES (:pid,:uid,:title) RETURNING *
    """), {"pid": str(project_id), "uid": _id(user_id), "title": title}).mappings().one())

def get_conversation(db, conversation_id, *, lock: bool = False) -> dict[str, Any]:
    row = db.execute(text(f"SELECT * FROM ai_assistant.conversations WHERE conversation_id=:id {'FOR UPDATE' if lock else ''}"),
                     {"id": str(conversation_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return dict(row)

def add_message(db, conversation_id, *, role: str, content: str, page_id=None, usage: dict[str, Any] | None = None) -> dict[str, Any]:
    """Append a message. Callers lock the conversation (get_conversation(lock=True)) so message numbers don't race."""
    usage = usage or {}
    row = db.execute(text("""
      INSERT INTO ai_assistant.messages(conversation_id,message_number,role,content,page_id,model_name,input_tokens,output_tokens)
      VALUES (:cid,(SELECT COALESCE(MAX(message_number),0)+1 FROM ai_assistant.messages WHERE conversation_id=:cid),
              :role,:content,:page,:model,:tin,:tout)
      RETURNING *
    """), {"cid": str(conversation_id), "role": role, "content": content, "page": _id(page_id),
           "model": usage.get("model_name"), "tin": usage.get("input_tokens"), "tout": usage.get("output_tokens")}).mappings().one()
    db.execute(text("UPDATE ai_assistant.conversations SET last_message_at=now() WHERE conversation_id=:id"), {"id": str(conversation_id)})
    return dict(row)

def list_messages(db, conversation_id) -> list[dict[str, Any]]:
    rows = db.execute(text("SELECT * FROM ai_assistant.messages WHERE conversation_id=:id ORDER BY message_number"),
                      {"id": str(conversation_id)}).mappings().all()
    return [dict(r) for r in rows]

def create_change_set(db, *, conversation_id, message_id, project_id, page_id, catalog_version: str,
                      base_edit_revision: int, items: list[dict[str, Any]]) -> str:
    change_set_id = str(db.execute(text("""
      INSERT INTO ai_assistant.change_sets(conversation_id,message_id,project_id,page_id,component_catalog_version,base_edit_revision)
      VALUES (:cid,:mid,:pid,:page,:catalog,:rev) RETURNING change_set_id
    """), {"cid": str(conversation_id), "mid": str(message_id), "pid": str(project_id), "page": str(page_id),
           "catalog": catalog_version, "rev": base_edit_revision}).scalar_one())
    if items:
        db.execute(text("""
          INSERT INTO ai_assistant.change_items(change_set_id,item_order,operation,component_id,component_type,position,props_patch)
          VALUES (:cs,:ord,:op,:cid,:ctype,CAST(:pos AS jsonb),CAST(:patch AS jsonb))
        """), [{"cs": change_set_id, "ord": i + 1, "op": it["operation"], "cid": it["component_id"], "ctype": it["component_type"],
                "pos": json.dumps(it["position"]) if it["position"] is not None else None,
                "patch": json.dumps(it["props_patch"]) if it["props_patch"] is not None else None}
               for i, it in enumerate(items)])
    return change_set_id

def get_change_set(db, change_set_id, *, lock: bool = False) -> dict[str, Any]:
    row = db.execute(text(f"SELECT * FROM ai_assistant.change_sets WHERE change_set_id=:id {'FOR UPDATE' if lock else ''}"),
                     {"id": str(change_set_id)}).mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Change set not found")
    change_set = dict(row)
    items = db.execute(text("""
      SELECT change_item_id, item_order, operation, component_id, component_type, position, props_patch
      FROM ai_assistant.change_items WHERE change_set_id=:id ORDER BY item_order
    """), {"id": str(change_set_id)}).mappings().all()
    change_set["items"] = [{**dict(i), "position": _jsonb(i["position"]), "props_patch": _jsonb(i["props_patch"])} for i in items]
    return change_set

def list_change_sets(db, conversation_id) -> list[dict[str, Any]]:
    ids = db.execute(text("SELECT change_set_id FROM ai_assistant.change_sets WHERE conversation_id=:id ORDER BY created_at"),
                     {"id": str(conversation_id)}).scalars().all()
    return [get_change_set(db, i) for i in ids]

def decide_change_set(db, change_set_id, status: str, *, rejection_code: str | None = None,
                      applied_edit_revision: int | None = None) -> None:
    db.execute(text("""
      UPDATE ai_assistant.change_sets SET status=:status, rejection_code=:code, applied_edit_revision=:rev, decided_at=now()
      WHERE change_set_id=:id
    """), {"status": status, "code": rejection_code, "rev": applied_edit_revision, "id": str(change_set_id)})

# --- provisioning: releases, steps, commands, hosting --------------------------------

def create_release(project: dict[str, Any], request_id, commit_sha: str | None) -> str:
    """Record a release and the PUBLISH command that triggers it (one release per generation request)."""
    with SessionLocal() as db:
        release_id = str(db.execute(text("""
          INSERT INTO provisioning.releases(project_id,generation_request_id,repository_id,git_commit_sha,status)
          VALUES (:pid,:req,:repo,:sha,'RUNNING')
          ON CONFLICT(generation_request_id) DO UPDATE SET status='RUNNING', git_commit_sha=EXCLUDED.git_commit_sha,
            repository_id=EXCLUDED.repository_id, failure_code=NULL, failure_summary=NULL, updated_at=now()
          RETURNING release_id
        """), {"pid": str(project["project_id"]), "req": str(request_id), "repo": project.get("repository_id"),
               "sha": commit_sha}).scalar_one())
        number = db.execute(text("""
          INSERT INTO provisioning.project_hosting(project_id,subdomain,last_command_number) VALUES (:pid,:sub,1)
          ON CONFLICT(project_id) DO UPDATE SET last_command_number=provisioning.project_hosting.last_command_number+1,
            subdomain=EXCLUDED.subdomain, updated_at=now()
          RETURNING last_command_number
        """), {"pid": str(project["project_id"]), "sub": project["subdomain"]}).scalar_one()
        db.execute(text("""
          INSERT INTO provisioning.commands(project_id,release_id,kind,idempotency_key,request_fingerprint,command_number,
                                            requested_by_subject,status)
          VALUES (:pid,:rel,'PUBLISH',:key,:fp,:num,'ai_codegen:harness','PENDING')
          ON CONFLICT(project_id,idempotency_key) DO UPDATE SET status='PENDING', failure_code=NULL, updated_at=now()
        """), {"pid": str(project["project_id"]), "rel": release_id, "key": f"publish:{request_id}",
               "fp": digest({"release_id": release_id, "commit": commit_sha}), "num": number})
        db.commit()
        return release_id

def start_release_step(release_id, step_name: str, *, status: str = "RUNNING", log_ref: str | None = None) -> str:
    with SessionLocal() as db:
        step_id = db.execute(text("""
          INSERT INTO provisioning.release_steps(release_id,step_name,attempt,status,started_at,deadline_at,finished_at,log_ref)
          VALUES (:rel,:step,
                  (SELECT COALESCE(MAX(attempt),0)+1 FROM provisioning.release_steps WHERE release_id=:rel AND step_name=:step),
                  :status,now(),now() + (:timeout * interval '1 second'),CASE WHEN :done THEN now() END,:log)
          RETURNING release_step_id
        """), {"rel": str(release_id), "step": step_name, "status": status, "log": log_ref, "done": status != "RUNNING",
               "timeout": settings.request_timeout_seconds}).scalar_one()
        db.commit()
        return str(step_id)

def finish_release_step(step_id, status: str, failure_code: str | None = None) -> None:
    with SessionLocal() as db:
        db.execute(text("""
          UPDATE provisioning.release_steps SET status=:status, failure_code=:code, finished_at=now() WHERE release_step_id=:id
        """), {"status": status, "code": failure_code, "id": str(step_id)})
        db.commit()

def finish_release(release_id, status: str, *, failure_code: str | None = None, failure_summary: str | None = None,
                   activate: bool = False) -> None:
    """Close a release and its PUBLISH command. activate=True makes it the project's live release."""
    with SessionLocal() as db:
        project_id = db.execute(text("""
          UPDATE provisioning.releases SET status=:status, failure_code=:code, failure_summary=:summary, updated_at=now(),
                 promoted_at=CASE WHEN :activate THEN now() END
          WHERE release_id=:id RETURNING project_id
        """), {"status": status, "code": failure_code, "summary": failure_summary, "activate": activate,
               "id": str(release_id)}).scalar_one()
        db.execute(text("""
          UPDATE provisioning.commands SET status=:status, failure_code=:code, updated_at=now() WHERE release_id=:id
        """), {"status": "SUCCEEDED" if status == "SUCCEEDED" else "FAILED", "code": failure_code, "id": str(release_id)})
        if activate:
            db.execute(text("""
              UPDATE provisioning.project_hosting SET live_release_id=:id, routing_status='ROUTED', hosting_status='ONLINE',
                     offline_reasons='{}', updated_at=now()
              WHERE project_id=:pid
            """), {"id": str(release_id), "pid": str(project_id)})
        db.commit()

def get_hosting(db, project_id) -> dict[str, Any] | None:
    row = db.execute(text("SELECT * FROM provisioning.project_hosting WHERE project_id=:id"), {"id": str(project_id)}).mappings().first()
    return dict(row) if row else None

def list_releases(db, project_id, limit: int) -> list[dict[str, Any]]:
    rows = db.execute(text("""
      SELECT rel.*, COALESCE((SELECT json_agg(json_build_object('step_name',s.step_name,'attempt',s.attempt,'status',s.status,
                                     'failure_code',s.failure_code,'started_at',s.started_at,'finished_at',s.finished_at)
                                     ORDER BY s.started_at)
                              FROM provisioning.release_steps s WHERE s.release_id=rel.release_id), '[]') AS steps
      FROM provisioning.releases rel WHERE rel.project_id=:id ORDER BY rel.created_at DESC LIMIT :limit
    """), {"id": str(project_id), "limit": limit}).mappings().all()
    return [{**dict(r), "steps": _jsonb(r["steps"])} for r in rows]
