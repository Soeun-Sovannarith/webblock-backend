import uuid
from typing import Any
from fastapi import HTTPException
from app.core.config import settings
from app.db import SessionLocal
from app.services import store
from app.services.catalog import CATALOG, validate_puck
from app.services.change_detector import detect_change
from app.services.scaffold import implemented_types


# Request-time half of a generation: capture the project's pages into a project.generation_requests row, copy that
# frozen input to ai_codegen.input_versions, and queue an ai_codegen.harness_runs row. The worker
# (app/worker.py -> app/services/pipeline.py) does the generation, build, push and release.

def validate_tree(puck: dict[str, Any]) -> None:
    try:
        validate_puck(puck)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

def _noop_view(project: dict[str, Any], request_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Response for a request with nothing to generate (no harness run is created)."""
    return {"run_id": None, "project_id": project["project_id"], "generation_request_id": request_id,
            "flow": "EXISTING_APP_UPDATE", "status": request["status"], "step": None, "strategy": "PUCK_JSON",
            "summary": "No changes since the last deployment.", "changed_files": [], "validations": [],
            "target_version_id": None, "commit_sha": request["source_commit_sha"], "promoted": False,
            "error_code": None, "errors": None, "repository_url": project["repository_url"], "deployment": None}

def _request_view(db, project: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    run_id = store.latest_run_for_request(db, request["generation_request_id"])
    if run_id:
        return store.get_run_view(db, run_id, include_log=False)
    return _noop_view(project, str(request["generation_request_id"]), request)

def enqueue_generation(project_id, *, instructions: str | None, drizzle_schema: str | None,
                       idempotency_key: str | None = None, home_puck: dict[str, Any] | None = None,
                       codegen_mode: str | None = None, seed: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a generation request for the project's current pages and queue its harness run.

    drizzle_schema and seed (the data for the site's db/seed.sql, from business onboarding, see seed_generator.build_seed)
    of None keep the baseline's.

    home_puck (editor endpoints only) first replaces the home page's Puck tree. Repeating a request with the same
    idempotency_key returns the original request's run; reusing the key with a different body is a 422.
    """
    if home_puck is not None:
        validate_tree(home_puck)
    mode = codegen_mode or settings.codegen_mode
    with SessionLocal() as db:
        project = store.get_project(db, project_id, lock=True)
        hashed = {"instructions": instructions, "drizzle_schema": drizzle_schema, "home_puck": home_puck, "codegen_mode": mode}
        if seed is not None:  # keeps the hash of requests without a seed as it was before seeds existed
            hashed["seed"] = seed
        request_hash = store.digest(hashed)
        if idempotency_key:
            existing = store.find_generation_request(db, project_id, idempotency_key)
            if existing:
                if existing["request_hash"] != request_hash:
                    raise HTTPException(status_code=422, detail="Idempotency-Key was already used with a different request.")
                return _request_view(db, project, existing)

        if home_puck is not None:
            home = store.home_page(db, project_id)
            if home["puck_tree"] != home_puck:
                store.update_page(db, project_id, home["page_id"], {"puck_tree": home_puck})
                project["edit_revision"] = store.bump_edit_revision(db, project_id)

        baseline = store.baseline_request(db, project_id)
        baseline_version = store.input_version_for_request(db, baseline["generation_request_id"]) if baseline else None
        baseline_schema = baseline_version["captured_input"].get("drizzle_schema") if baseline_version else None
        schema_replaced = drizzle_schema is not None and drizzle_schema.strip() != (baseline_schema or "").strip()
        if drizzle_schema is None:
            drizzle_schema = baseline_schema
        # The onboarding seed fills onboarding tables, so a replaced schema falls back to seeding the Puck products.
        if seed is None and baseline_version and not schema_replaced:
            seed = baseline_version["captured_input"].get("seed")
        captured = store.capture_input(db, project_id, instructions=instructions, drizzle_schema=drizzle_schema,
                                       codegen_mode=mode, seed=seed)
        contract_id = store.get_or_create_contract(db, project)
        common = {"project_id": project_id, "contract_id": contract_id, "idempotency_key": idempotency_key or str(uuid.uuid4()),
                  "request_hash": request_hash, "edit_revision": project["edit_revision"], "captured_input": captured}

        strategy = "SOURCE_CODE"
        if baseline_version:
            implemented = implemented_types(store.accepted_source(db, baseline)) or set(CATALOG)
            diff = detect_change(baseline_version["captured_input"], captured, implemented_types=implemented)
            strategy = diff["strategy"]
            if mode == "llm" and diff["changed"]:
                strategy = "SOURCE_CODE" if strategy == "PUCK_JSON" else "MIXED"
            prev_mode = baseline_version["captured_input"].get("codegen_mode") or "hybrid"
            if not diff["changed"] and prev_mode == mode:
                request_id = store.create_generation_request(db, **common, status="SUCCEEDED",
                                                             source_commit_sha=baseline["source_commit_sha"])
                db.commit()
                return _noop_view(project, request_id, store.get_generation_request(db, request_id))


        request_id = store.create_generation_request(db, **common)
        request = store.get_generation_request(db, request_id)
        version_id = store.save_input_version(db, request, catalog_version=project["component_catalog_version"],
                                              baseline_commit_sha=baseline["source_commit_sha"] if baseline else None)
        try:
            run_id = store.create_run(db, project_id=project_id, request_id=request_id, contract_id=contract_id,
                                      baseline_version_id=baseline_version["input_version_id"] if baseline_version else None,
                                      input_hash=request["captured_input_hash"], strategy=strategy)
        except store.ActiveRunError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        db.commit()
        store.append_log(run_id, f"Queued generation request {request_id} (input version {version_id}).")
        return store.get_run_view(db, run_id, include_log=False)

def handle_generation_requested_event(event) -> dict[str, Any]:
    """project.generation_requested.v1 (Project's contract 2.1 and 2.2). One run set per request: a repeat for a
    request that already has a run re-publishes the completion when that run has finished, and otherwise does
    nothing. The input is read from Project's codegen-input and must hash (JCS, SHA-256) to the event's
    captured_input_hash; a mismatch ends the request with a failed run (INPUT_HASH_MISMATCH).
    Raises event_bus.RetryLater when Project cannot answer or another run of the project is active."""
    from app.services import event_bus, jcs, project_client
    request_id = str(event.generation_request_id)
    with SessionLocal() as db:
        existing = store.latest_run_for_request(db, request_id)
        run = store.get_run(db, existing) if existing else None
    if run:
        if run["status"] in ("SUCCEEDED", "FAILED"):
            event_bus.publish_generation_completed(request_id, existing, run["project_id"], run["status"])
            return {"run_id": existing, "status": "REPUBLISHED"}
        return {"run_id": existing, "status": "ALREADY_RUNNING"}

    result = project_client.fetch_codegen_input(request_id)
    if result.status == "unavailable":
        raise event_bus.RetryLater("the Project Service did not return the codegen input")
    if result.status != "ok":
        return {"run_id": None, "status": "IGNORED", "reason": result.status}  # the request is no longer generating
    doc = result.doc
    input_hash = jcs.sha256_hex(doc["capturedInput"])
    contract_id = ((doc.get("generationContract") or {}).get("generationContractId")
                   or doc["capturedInput"].get("generationContractId"))
    problem = None
    if str(doc.get("generationRequestId")) != request_id or str(doc.get("projectId")) != str(event.project_id):
        problem = "the codegen input belongs to another request or project"
    elif not (input_hash == event.captured_input_hash == doc.get("capturedInputHash")):
        problem = "the codegen input does not hash to the request's captured_input_hash"
    if problem or not contract_id:
        return _fail_request(event, contract_id, "INPUT_HASH_MISMATCH" if problem else "INVALID_INPUT",
                             problem or "the codegen input has no generation contract")

    captured = project_client.to_captured_input(doc)
    with SessionLocal() as db:
        request_dict = {"generation_request_id": request_id, "project_id": str(event.project_id),
                        "generation_contract_id": str(contract_id), "captured_edit_revision": 0,
                        "captured_input": captured, "captured_input_hash": input_hash}
        version_id = store.save_input_version(db, request_dict, catalog_version=captured["component_catalog_version"]
                                              or "webblock.catalog.v1")
        baseline = store.baseline_request(db, event.project_id)
        baseline_version = store.input_version_for_request(db, baseline["generation_request_id"]) if baseline else None
        strategy = "SOURCE_CODE"
        if baseline_version:
            implemented = implemented_types(store.accepted_source(db, baseline)) or set(CATALOG)
            diff = detect_change(baseline_version["captured_input"], captured, implemented_types=implemented)
            strategy = diff["strategy"]
            if captured["codegen_mode"] == "llm" and diff["changed"]:
                strategy = "SOURCE_CODE" if strategy == "PUCK_JSON" else "MIXED"
        try:
            run_id = store.create_run(
                db, project_id=event.project_id, request_id=request_id, contract_id=str(contract_id),
                baseline_version_id=baseline_version["input_version_id"] if baseline_version else None,
                input_hash=input_hash, strategy=strategy)
        except store.ActiveRunError as e:
            raise event_bus.RetryLater(str(e)) from e
        db.commit()
    store.append_log(run_id, f"Queued from project.generation_requested.v1 (input version {version_id}).")
    return {"run_id": run_id, "status": "QUEUED"}

def _fail_request(event, contract_id, code: str, message: str) -> dict[str, Any]:
    """A request this service refuses: recorded as a FAILED run, so Project's read API and the completion event
    report it like any other failure."""
    from app.services import pipeline
    with SessionLocal() as db:
        run_id = store.create_failed_run(db, project_id=event.project_id, request_id=str(event.generation_request_id),
                                         contract_id=str(contract_id or uuid.uuid4()), input_hash=event.captured_input_hash,
                                         error_code=code, message=message)
        db.commit()
    pipeline._fail(run_id, {}, code, message)  # log, check report and completion event
    return {"run_id": run_id, "status": "FAILED", "error_code": code}

def handle_generation_cancelled_event(event) -> dict[str, Any]:
    """Handle inbound project.generation_cancelled.v1 event from Project Service."""
    with SessionLocal() as db:
        cancelled_run_id = store.cancel_active_run_for_request(db, event.generation_request_id)
        if cancelled_run_id:
            return {"cancelled": True, "run_id": cancelled_run_id}
        return {"cancelled": False, "message": "No active run found to cancel"}

