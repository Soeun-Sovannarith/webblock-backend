from uuid import UUID
from fastapi import APIRouter, Header, Query, Response
from app.db import SessionLocal
from app.schemas.business import BusinessOnboardingRequest, BusinessOnboardingResponse
from app.schemas.harness import GenerationRequestCreate, PageCreate, PageUpdate, ProjectCreate
from app.services import store
from app.services.harness import enqueue_generation, validate_tree
from app.services.onboarding import prepare_site_inputs

router = APIRouter()
onboarding_router = APIRouter()  # POST /v1/onboardings, which creates its project

def _locate_run(response: Response, result: dict) -> dict:
    """202 with Location: /v1/generation-runs/{id} while the request's run is queued or running. 200 when there is
    nothing left to do: no change since the baseline (run_id null), or an Idempotency-Key replay of a finished run."""
    if result.get("run_id") and result.get("status") in ("QUEUED", "RUNNING"):
        response.headers["Location"] = f"/v1/generation-runs/{result['run_id']}"
    else:
        response.status_code = 200
    return result

def _with_baseline(db, project: dict) -> dict:
    """Project plus last_successful_version_id: the ai_codegen input version of its accepted baseline (or null)."""
    baseline = store.baseline_request(db, project["project_id"])
    version = store.input_version_for_request(db, baseline["generation_request_id"]) if baseline else None
    return {**project, "last_successful_version_id": version["input_version_id"] if version else None,
            "baseline_generation_request_id": baseline["generation_request_id"] if baseline else None}

@router.post("", status_code=201)
def create_project(payload: ProjectCreate, response: Response):
    with SessionLocal() as db:
        project = store.create_project(db, name=payload.name, repository_full_name=payload.repository_full_name(),
                                       subdomain=payload.subdomain)
        db.commit()
        response.headers["Location"] = f"/v1/projects/{project['project_id']}"
        return _with_baseline(db, project)

@router.get("/{project_id}")
def get_project(project_id: UUID):
    with SessionLocal() as db:
        return _with_baseline(db, store.get_project(db, project_id))

# --- pages -------------------------------------------------------------------

@router.get("/{project_id}/pages")
def list_pages(project_id: UUID):
    with SessionLocal() as db:
        store.get_project(db, project_id)
        return store.list_pages(db, project_id)

@router.post("/{project_id}/pages", status_code=201)
def create_page(project_id: UUID, payload: PageCreate, response: Response):
    if payload.puck_tree is not None:
        validate_tree(payload.puck_tree)
    with SessionLocal() as db:
        store.get_project(db, project_id, lock=True)
        page = store.create_page(db, project_id, **payload.model_dump())
        revision = store.bump_edit_revision(db, project_id)
        db.commit()
        response.headers["Location"] = f"/v1/projects/{project_id}/pages/{page['page_id']}"
        return {**page, "edit_revision": revision}

@router.get("/{project_id}/pages/{page_id}")
def get_page(project_id: UUID, page_id: UUID):
    with SessionLocal() as db:
        return store.get_page(db, project_id, page_id)

@router.patch("/{project_id}/pages/{page_id}")
def update_page(project_id: UUID, page_id: UUID, payload: PageUpdate):
    fields = payload.model_dump(exclude_unset=True, exclude={"base_edit_revision"})
    if "puck_tree" in fields:
        validate_tree(fields["puck_tree"])
    with SessionLocal() as db:
        store.get_project(db, project_id, lock=True)
        page = store.update_page(db, project_id, page_id, fields)
        revision = store.bump_edit_revision(db, project_id, payload.base_edit_revision)
        db.commit()
        return {**page, "edit_revision": revision}

@router.delete("/{project_id}/pages/{page_id}")
def delete_page(project_id: UUID, page_id: UUID):
    with SessionLocal() as db:
        store.get_project(db, project_id, lock=True)
        store.delete_page(db, project_id, page_id)
        revision = store.bump_edit_revision(db, project_id)
        db.commit()
        return {"deleted": True, "edit_revision": revision}

@router.get("/{project_id}/versions/latest")
def latest_version(project_id: UUID):
    """The editor's document: the home page's current Puck tree."""
    with SessionLocal() as db:
        project = store.get_project(db, project_id)
        home = store.home_page(db, project_id)
        return {"page_id": home["page_id"], "edit_revision": project["edit_revision"], "puck_json": home["puck_tree"]}

# --- generation requests and runs --------------------------------------------

@router.post("/{project_id}/generation-requests", status_code=202)
def create_generation_request(project_id: UUID, payload: GenerationRequestCreate, response: Response,
                              idempotency_key: str | None = Header(default=None, max_length=255),
                              x_codegen_mode: str | None = Header(default=None)):
    """Generate (first time) or update the site from the project's current pages, after replacing the home page's
    Puck tree with puck_json when given. Returns the queued run; Location points at it."""
    mode = payload.codegen_mode or (x_codegen_mode if x_codegen_mode in ("hybrid", "llm") else None)
    return _locate_run(response, enqueue_generation(project_id, instructions=payload.instructions,
                                                    drizzle_schema=payload.drizzle_schema, idempotency_key=idempotency_key,
                                                    home_puck=payload.puck_json, codegen_mode=mode))


@router.post("/{project_id}/onboardings", status_code=202, response_model=BusinessOnboardingResponse,
             response_model_exclude_unset=True, summary="Onboard Existing Project")
def onboard_and_generate(project_id: UUID, payload: BusinessOnboardingRequest, response: Response):
    """Onboarding endpoint: accepts structured business details (Step 1 details + Step 2 contact & hours),
    synthesizes a tailored Drizzle ORM PostgreSQL schema, the seed script for the site's database and pre-filled
    Puck page blocks matching the editor preview, and enqueues the generation pipeline to build the Next.js + Drizzle app."""
    inputs = prepare_site_inputs(payload)
    res = enqueue_generation(
        project_id,
        instructions=payload.instructions,
        drizzle_schema=inputs["drizzle_schema"],
        home_puck=inputs["puck_tree"],
        codegen_mode=payload.codegen_mode,
        seed=inputs["seed"],
    )
    _locate_run(response, res)
    return {
        "project_id": str(project_id),
        "generation_request_id": res.get("generation_request_id"),
        "run_id": res.get("run_id"),
        "status": res.get("status"),
        "drizzle_schema_preview": inputs["drizzle_schema"],
        "seed_sql_preview": inputs["seed_sql"],
        "seed_source": inputs["seed_source"],
        "puck_blocks": inputs["puck_tree"].get("content", []),
        "message": "Business onboarding completed: custom Drizzle schema and seed data synthesized and Next.js + Drizzle app build enqueued.",
    }


@onboarding_router.post("/onboardings", status_code=202, response_model=BusinessOnboardingResponse,
                        summary="Create Project From Onboarding")
def generate_from_business(payload: BusinessOnboardingRequest, response: Response):
    """One-step endpoint: creates a new project from business details, generates the Drizzle backend schema,
    synthesizes the seed data and Puck layout, and kicks off the Next.js + Drizzle generation pipeline."""
    inputs = prepare_site_inputs(payload)
    with SessionLocal() as db:
        project = store.create_project(
            db,
            name=payload.business_name,
            repository_full_name=None,
            subdomain=None,
        )
        db.commit()
        project_id = project["project_id"]

    res = enqueue_generation(
        project_id,
        instructions=payload.instructions,
        drizzle_schema=inputs["drizzle_schema"],
        home_puck=inputs["puck_tree"],
        codegen_mode=payload.codegen_mode,
        seed=inputs["seed"],
    )
    _locate_run(response, res)
    return {
        "project_id": str(project_id),
        "project_name": payload.business_name,
        "generation_request_id": res.get("generation_request_id"),
        "run_id": res.get("run_id"),
        "status": res.get("status"),
        "drizzle_schema_preview": inputs["drizzle_schema"],
        "seed_sql_preview": inputs["seed_sql"],
        "seed_source": inputs["seed_source"],
        "puck_blocks": inputs["puck_tree"].get("content", []),
        "message": f"Project '{payload.business_name}' created, custom Drizzle schema synthesized, and Next.js + Drizzle build enqueued.",
    }


@router.get("/{project_id}/generation-requests")
def list_generation_requests(project_id: UUID, limit: int = Query(default=20, ge=1, le=100)):
    with SessionLocal() as db:
        store.get_project(db, project_id)
        return store.list_generation_requests(db, project_id, limit)

@router.get("/{project_id}/generation-runs", summary="List Generation Runs")
def list_runs(project_id: UUID, limit: int = Query(default=20, ge=1, le=100)):
    with SessionLocal() as db:
        store.get_project(db, project_id)
        return store.list_runs(db, project_id, limit)

# --- provisioning (read-only) -------------------------------------------------

@router.get("/{project_id}/hosting")
def get_hosting(project_id: UUID):
    with SessionLocal() as db:
        store.get_project(db, project_id)
        return store.get_hosting(db, project_id) or {"project_id": project_id, "hosting_status": "OFFLINE",
                                                      "routing_status": "UNROUTED", "offline_reasons": ["NO_RELEASE"]}

@router.get("/{project_id}/releases")
def list_releases(project_id: UUID, limit: int = Query(default=20, ge=1, le=100)):
    with SessionLocal() as db:
        store.get_project(db, project_id)
        return store.list_releases(db, project_id, limit)
