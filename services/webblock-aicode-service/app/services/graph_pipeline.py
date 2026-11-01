import logging
import time
from typing import Any, TypedDict
from langgraph.graph import END, START, StateGraph
from sqlalchemy import text
from sqlalchemy.exc import NoResultFound
from app.core.config import settings
from app.db import SessionLocal
from app.services import artifacts, build_runner, deployment, event_bus, github, glm, portal_sso, site_catalog, store
from app.services.seed_generator import seed_business_type
from app.services.change_detector import detect_change
from app.services.scaffold import (
    SEED_PATH,
    apply_puck_update,
    implemented_types,
    refresh_generated,
    render_site,
)

log = logging.getLogger("webblock.graph_pipeline")


class PipelineState(TypedDict, total=False):
    run_id: str
    project: dict[str, Any]
    request: dict[str, Any]
    target: dict[str, Any]
    baseline: dict[str, Any] | None
    baseline_files: dict[str, str]
    files: dict[str, str]
    summary: str
    report: dict[str, Any]
    build_success: bool
    build_error: str | None
    repair_attempts: int
    fallback_files: dict[str, str] | None
    fallback_used: bool
    commit_sha: str | None
    error_code: str | None
    error_message: str | None


def _load_context(run_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        run = store.get_run(db, run_id)
        try:
            request = store.get_generation_request(db, run["generation_request_id"])
        except NoResultFound:
            # A request from Project's event: Project keeps the row, this service only its input version.
            request = {"generation_request_id": run["generation_request_id"], "status": "RUNNING",
                       "captured_edit_revision": 0, "captured_input_hash": run["input_hash"], "source_commit_sha": None}
        if request["status"] != "RUNNING":
            store.update_generation_request(request["generation_request_id"], status="RUNNING")
            request = {**request, "captured_input_hash": run["input_hash"], "status": "RUNNING"}
        target = store.input_version_for_request(db, request["generation_request_id"])
        if target and target.get("captured_edit_revision") is not None:
            # The frozen input records the revision on both paths (a Project event's request has no row here); the
            # commit message and the run log name it.
            request = {**request, "captured_edit_revision": target["captured_edit_revision"]}
        try:
            project = store.get_project(db, run["project_id"])
        except Exception:
            project = {
                "project_id": run["project_id"],
                "name": "project",
                "subdomain": str(run["project_id"])[:8],
                "repository_full_name": None,
                "repository_id": None,
                "repository_url": None,
                "component_catalog_version": "webblock.catalog.v1",
                "edit_revision": request.get("captured_edit_revision", 0),
            }
        ctx: dict[str, Any] = {
            "run": run,
            "project": project,
            "request": request,
            "target": target,
            "baseline": None,
            "baseline_files": {},
        }
        if run["baseline_version_id"]:
            baseline = store.get_input_version(db, run["baseline_version_id"])
            ctx["baseline"] = baseline
            base_req = None
            try:
                base_req = store.get_generation_request(db, baseline["generation_request_id"])
            except Exception:
                pass
            ctx["baseline_files"] = store.accepted_source(db, base_req)
        return ctx


def _save_source(run_id, project_id, files: dict[str, str], baseline_files: dict[str, str], report: dict[str, Any]) -> None:
    """SOURCE_ARCHIVE is the run's code as it stands: saved in PREPARE and again after every GLM change (code
    generation, update or compiler repair), so the files endpoint, the next run's baseline and GitHub see the same tree."""
    report["changed_files"] = sorted(p for p in set(files) | set(baseline_files) if files.get(p) != baseline_files.get(p))
    source_ref = store.run_ref(run_id, artifacts.SOURCE_ARCHIVE, project_id)
    store.save_artifact(run_id, "SOURCE_ARCHIVE", source_ref, artifacts.write_bytes(source_ref, artifacts.pack_source(files)))


def prepare_node(state: PipelineState) -> dict[str, Any]:
    run_id = state["run_id"]
    ctx = _load_context(run_id)
    store.set_step(run_id, "PREPARE")
    target = ctx["target"]["captured_input"]

    if ctx["baseline"] is None:
        files = render_site(target)
        summary = f"Generated Next.js + Drizzle project ({len(files)} files)."
    else:
        baseline = ctx["baseline"]["captured_input"]
        files = dict(ctx["baseline_files"]) or render_site(baseline)
        diff = detect_change(baseline, target, implemented_types=implemented_types(files))
        store.append_log(run_id, f"Strategy {diff['strategy']}: {diff['summary']}")
        added = apply_puck_update(files, target)
        if added:
            store.append_log(run_id, f"Added block sources from templates: {', '.join(added)}")
        summary = diff["summary"]

    report = state.get("report", {})
    report.update(strategy=ctx["run"]["strategy"], summary=summary)
    _save_source(run_id, ctx["project"]["project_id"], files, ctx["baseline_files"], report)

    ref = store.run_ref(run_id, artifacts.CHECK_REPORT)
    store.save_artifact(run_id, "CHECK_REPORT", ref, artifacts.write_json(ref, report))

    return {
        "project": ctx["project"],
        "request": ctx["request"],
        "target": ctx["target"],
        "baseline": ctx["baseline"],
        "baseline_files": ctx["baseline_files"],
        "files": files,
        "summary": summary,
        "report": report,
        "repair_attempts": 0,
    }


def ai_code_update_node(state: PipelineState) -> dict[str, Any]:
    """Let GLM write code: llm mode (Flow 1 from the Puck layout, Flow 2 from the Puck diff), or hybrid Flow 2 with
    instructions. GLM is optional unless the run exists to apply new instructions to a live site: then a GLM failure
    fails the run. Otherwise a failure keeps the PREPARE code (WARNING line), and when GLM did change the code, the
    PREPARE code is kept as fallback_files for template_fallback in case the change doesn't build."""
    run_id = state["run_id"]
    target = state["target"]["captured_input"]
    mode = target.get("codegen_mode") or settings.codegen_mode
    files = dict(state["files"])
    summary = state["summary"]
    report = state.get("report", {})
    ai_required = state.get("baseline") is not None and bool(target.get("instructions"))

    if state.get("baseline") is None and mode == "llm":
        store.set_step(run_id, "AI_CODE_UPDATE")
        store.append_log(run_id, f"Codegen mode is 'llm': invoking GLM ({settings.glm_model}) to generate React code from Puck JSON...")
        call, verb, label = (lambda: glm.request_code_from_puck(files, target)), "generated/updated", "AI (LLM mode)"
    elif state.get("baseline") is not None and mode == "llm":
        store.set_step(run_id, "AI_CODE_UPDATE")
        baseline = state["baseline"]["captured_input"]
        diff = detect_change(baseline, target, implemented_types=implemented_types(files))
        call, verb, label = (lambda: glm.request_code_update_from_puck(files, baseline, target, diff)), "updated", "AI (LLM mode)"
    elif state.get("baseline") is not None and target.get("instructions"):
        store.set_step(run_id, "AI_CODE_UPDATE")
        call, verb, label = (lambda: glm.request_code_update(files, target["instructions"])), "changed", "AI"
    else:
        return {}

    try:
        result = call()
    except glm.GLMError as e:
        if ai_required:
            raise
        store.append_log(run_id, f"WARNING: [AI_CODE_UPDATE] GLM failed, continuing with the code from before GLM: {e}")
        report["ai"] = {"fallback": f"GLM failed: {e}"[:500]}
        return {"summary": f"{summary} AI changes were not applied (GLM failed).", "report": report}

    usage = result["usage"]
    store.update_run(run_id, model_name=usage["model_name"], input_tokens=usage["input_tokens"], output_tokens=usage["output_tokens"])
    # GLM sometimes returns a file unchanged: count only real changes, in the log and the check report
    touched = [p for p in glm.apply_changes(files, result["changes"]) if files.get(p) != state["files"].get(p)]
    refresh_generated(files)
    store.append_log(run_id, f"GLM ({usage['model_name']}) {verb} {len(touched)} file(s): {', '.join(touched) or 'no changes needed'}")
    report["ai"] = {"summary": result["summary"], "files": touched, **usage}
    out: dict[str, Any] = {"files": files, "summary": f"{summary} {label}: {result['summary']}", "report": report}
    if files != state["files"]:
        _save_source(run_id, state["project"]["project_id"], files, state["baseline_files"], report)
        if not ai_required:
            out["fallback_files"] = state["files"]
    return out


def build_node(state: PipelineState) -> dict[str, Any]:
    run_id = state["run_id"]
    store.set_step(run_id, "BUILD")
    files = state["files"]
    report = state.get("report", {})
    started: dict[str, tuple[str, float]] = {}
    report["validations"] = []

    def on_phase(phase: str, status: str, phase_log: str) -> None:
        if status == "RUNNING":
            started[phase] = (store.start_validation(run_id, phase), time.monotonic())
            return
        validation_id, t0 = started[phase]
        result = {"duration_ms": round((time.monotonic() - t0) * 1000), "log": phase_log}
        store.finish_validation(validation_id, status, result, None if status == "PASSED" else f"{phase} failed")
        report["validations"].append({"type": phase, "status": status, "duration_ms": result["duration_ms"]})

    try:
        built = build_runner.build(files, on_phase)
        if built.status == "SKIPPED":
            for phase in ("INSTALL", "BUILD"):
                store.start_validation(run_id, phase, status="SKIPPED")
                report["validations"].append({"type": phase, "status": "SKIPPED"})
            store.append_log(run_id, f"SKIPPED: [BUILD] {built.log}")
        else:
            store.append_log(run_id, built.log)
        return {"build_success": True, "build_error": None, "report": report}
    except build_runner.BuildError as e:
        return {"build_success": False, "build_error": str(e), "report": report}


def repair_node(state: PipelineState) -> dict[str, Any]:
    run_id = state["run_id"]
    error_log = state.get("build_error") or ""
    current_attempt = state.get("repair_attempts", 0) + 1
    store.append_log(run_id, f"LangGraph autonomous repair loop (attempt {current_attempt}/2)...")

    files = dict(state["files"])
    try:
        repair = glm.request_compiler_repair(files, error_log)
        touched = glm.apply_changes(files, repair["changes"])
        refresh_generated(files)
        store.append_log(run_id, f"LangGraph self-healing repaired {len(touched)} file(s): {', '.join(touched)}.")
        if touched:
            _save_source(run_id, state["project"]["project_id"], files, state["baseline_files"], state.get("report", {}))
    except Exception as e:
        store.append_log(run_id, f"Self-healing repair failed: {e}")

    return {"files": files, "repair_attempts": current_attempt}


def template_fallback_node(state: PipelineState) -> dict[str, Any]:
    """Last resort when the code GLM changed doesn't build, even after compiler_repair: build the PREPARE code
    instead (the templates, or the accepted site plus this run's Puck edits), which needs no GLM. Runs at most once."""
    run_id = state["run_id"]
    files = dict(state["fallback_files"] or {})
    store.append_log(run_id, "WARNING: [BUILD] the code GLM changed does not build; building the code from before GLM instead.")
    report = state.get("report", {})
    report["ai"] = {**report.get("ai", {}), "fallback": "GLM's code did not build; built without it."}
    _save_source(run_id, state["project"]["project_id"], files, state["baseline_files"], report)
    return {"files": files, "fallback_used": True, "report": report,
            "summary": f"{state.get('summary', '')} GLM's changes did not build, so they were left out."}


def route_build_outcome(state: PipelineState) -> str:
    if state.get("build_success"):
        return "github_push"
    if state.get("fallback_used"):
        return "handle_failure"
    if state.get("repair_attempts", 0) < 2 and glm.is_configured():
        return "compiler_repair"
    if state.get("fallback_files"):
        return "template_fallback"
    return "handle_failure"


def github_push_node(state: PipelineState) -> dict[str, Any]:
    run_id = state["run_id"]
    store.set_step(run_id, "GITHUB_PUSH")
    if settings.publish_target == "project":
        store.append_log(run_id, "SKIPPED: [GITHUB_PUSH] the Project Service commits the source to Gitea (PUBLISH_TARGET=project).")
        return {"commit_sha": None}
    project = state["project"]
    files = state["files"]
    request = state["request"]
    request_id = request["generation_request_id"]
    flow = "initial generation" if state.get("baseline") is None else "update"
    message = f"WebBlock {flow} (edit revision {request.get('captured_edit_revision', 0)})\n\nGeneration request {request_id}\nHarness run {run_id}"

    pushed = github.push(project, files, message)
    if pushed.status == "SKIPPED":
        store.append_log(run_id, f"SKIPPED: [GITHUB_PUSH] {pushed.message}")
        commit_sha = None
    else:
        store.append_log(run_id, pushed.message)
        with SessionLocal() as db:
            try:
                db.execute(text("""
                  UPDATE project.projects SET repository_id=:repo_id, repository_full_name=:full_name, updated_at=now()
                  WHERE project_id=:pid
                """), {"repo_id": pushed.repository_id, "full_name": pushed.full_name, "pid": str(project["project_id"])})
                db.commit()
            except Exception:
                pass
        commit_sha = pushed.commit_sha
        store.update_generation_request(request_id, source_commit_sha=pushed.commit_sha)
    return {"commit_sha": commit_sha}


def _ensure_portal_sso(run_id: str, project: dict[str, Any], urls: list[str]) -> dict[str, str] | None:
    """Create or update the site's Keycloak client for the product portal (portal_sso). Returns the site's SSO
    settings, or None when SSO is not configured or Keycloak fails: the site then deploys with its portal
    disabled, and the next deploy tries again."""
    if not portal_sso.is_configured():
        store.append_log(run_id, "SKIPPED: [DEPLOY] portal SSO (KEYCLOAK_ADMIN_CLIENT_ID is not set); the product portal stays disabled.")
        return None
    try:
        sso = portal_sso.ensure_site_client(project, urls)
    except portal_sso.KeycloakError as e:
        store.append_log(run_id, f"WARNING: portal SSO setup failed, the product portal stays disabled until the next deploy: {e}")
        return None
    store.append_log(run_id, f"Portal SSO: Keycloak client {sso['KEYCLOAK_CLIENT_ID']} ready for {', '.join(urls) or 'no known URL yet'}.")
    return sso

def _prepare_site_catalog(run_id: str, project: dict[str, Any], files: dict[str, str],
                          seed: dict[str, Any] | None) -> dict[str, str]:
    """Give the site its role and website in the shared catalog and apply its seed (site_catalog.prepare_site).
    Returns the site's database (and object storage) settings, or {} when SITE_DB_SECRET is not set: the site then
    deploys without a database and shows the products from puck-data.json. A database failure fails the deploy,
    because the site would otherwise go live without the data it had."""
    if not site_catalog.is_configured():
        store.append_log(run_id, "SKIPPED: [DEPLOY] site catalog (SITE_DB_SECRET is not set); the site deploys without a database.")
        return {}
    try:
        prepared = site_catalog.prepare_site(project, files.get(SEED_PATH), seed_business_type(seed))
    except site_catalog.SiteCatalogError as e:
        raise deployment.DeploymentError(str(e)) from e
    store.append_log(run_id, f"Site catalog: role {site_catalog.role_name(project['project_id'])} and website ready "
                             f"(status {prepared.status}), seed applied.")
    if prepared.status != "active":
        store.append_log(run_id, f"WARNING: [DEPLOY] the website is {prepared.status} in the catalog, so the site gets no data.")
    if not site_catalog.storage_configured():
        store.append_log(run_id, "SKIPPED: [DEPLOY] object storage (SITE_STORAGE_* is not set); the portal can't upload images.")
    return prepared.env

def _deploy_release(state: PipelineState) -> None:
    """DEPLOY with PUBLISH_TARGET=github: a provisioning release (DEPLOY and ACTIVATE steps), the site's role and data
    in the shared catalog, the site's Keycloak client and the deploy webhook."""
    run_id = state["run_id"]
    project = state["project"]
    request_id = state["request"]["generation_request_id"]
    commit_sha = state.get("commit_sha")

    release_id = store.create_release(project, request_id, commit_sha)
    log_ref = artifacts.ref(project["project_id"], f"releases/{release_id}", "deploy.log")
    step_id = store.start_release_step(release_id, "DEPLOY", log_ref=log_ref)
    sso_env = _ensure_portal_sso(run_id, project, portal_sso.site_urls(project))
    try:
        seed = ((state.get("target") or {}).get("captured_input") or {}).get("seed")
        catalog_env = _prepare_site_catalog(run_id, project, state.get("files") or {}, seed)
        site_env = {**catalog_env, **(sso_env or {})} or None
        deployed = deployment.deploy(project, release_id, commit_sha, project.get("repository_url"), site_env=site_env)
    except deployment.DeploymentError as e:
        artifacts.append_text(log_ref, f"FAILED: {e}\n")
        store.finish_release_step(step_id, "FAILED", "DEPLOY_FAILED")
        store.finish_release(release_id, "FAILED", failure_code="DEPLOY_FAILED", failure_summary=str(e)[:2000])
        raise e
    artifacts.append_text(log_ref, deployed.message + (f" {deployed.url}" if deployed.url else "") + "\n")
    if sso_env and deployed.url and deployed.url.rstrip("/") not in portal_sso.site_urls(project):
        _ensure_portal_sso(run_id, project, portal_sso.site_urls(project, deployed.url))  # register the deploy URL too
    live = deployed.status == "SUCCEEDED"
    store.finish_release_step(step_id, "SUCCEEDED" if live else "SKIPPED")
    store.start_release_step(release_id, "ACTIVATE", status="SUCCEEDED" if live else "SKIPPED")
    store.finish_release(release_id, "SUCCEEDED", activate=live)
    if live:
        store.append_log(run_id, deployed.message + (f" {deployed.url}" if deployed.url else ""))
    else:
        store.append_log(run_id, f"SKIPPED: [DEPLOY] {deployed.message}")


def deploy_node(state: PipelineState) -> dict[str, Any]:
    run_id = state["run_id"]
    store.set_step(run_id, "DEPLOY")
    project = state["project"]
    request_id = state["request"]["generation_request_id"]
    if settings.publish_target == "project":
        store.append_log(run_id, "SKIPPED: [DEPLOY] Provisioning builds and deploys the commit the Project Service "
                                 "publishes (PUBLISH_TARGET=project).")
    else:
        _deploy_release(state)

    with SessionLocal() as db:
        db.execute(text("""
          UPDATE ai_codegen.harness_runs SET status='SUCCEEDED', completed_at=now(), lease_expires_at=NULL WHERE harness_run_id=:id
        """), {"id": run_id})
        try:
            db.execute(text("""
              UPDATE project.generation_requests SET status='SUCCEEDED', accepted_generation_run_id=:run, failure_code=NULL, updated_at=now()
              WHERE generation_request_id=:id
            """), {"run": run_id, "id": str(request_id)})
        except Exception:
            pass
        db.commit()

    store.append_log(run_id, f"Accepted as the project baseline (edit revision {state['request'].get('captured_edit_revision', 0)}).")
    report = state.get("report", {})
    ref = store.run_ref(run_id, artifacts.CHECK_REPORT)
    store.save_artifact(run_id, "CHECK_REPORT", ref, artifacts.write_json(ref, report))
    close_log_ref = store.run_ref(run_id, artifacts.RUN_LOG)
    store.save_artifact(run_id, "RUN_LOG", close_log_ref, artifacts.sha256_file(close_log_ref))

    event_bus.publish_generation_completed(request_id, run_id, project["project_id"], "SUCCEEDED")
    return {"status": "SUCCEEDED"}


def handle_failure_node(state: PipelineState) -> dict[str, Any]:
    error = state.get("build_error") or "Build failed"
    raise build_runner.BuildError(error)


def build_pipeline_graph():
    """Build and compile the LangGraph StateGraph pipeline with self-healing conditional edges."""
    builder = StateGraph(PipelineState)
    builder.add_node("prepare", prepare_node)
    builder.add_node("ai_code_update", ai_code_update_node)
    builder.add_node("build", build_node)
    builder.add_node("compiler_repair", repair_node)
    builder.add_node("template_fallback", template_fallback_node)
    builder.add_node("github_push", github_push_node)
    builder.add_node("deploy", deploy_node)
    builder.add_node("handle_failure", handle_failure_node)

    builder.add_edge(START, "prepare")
    builder.add_edge("prepare", "ai_code_update")
    builder.add_edge("ai_code_update", "build")
    builder.add_conditional_edges("build", route_build_outcome, {
        "github_push": "github_push",
        "compiler_repair": "compiler_repair",
        "template_fallback": "template_fallback",
        "handle_failure": "handle_failure",
    })
    builder.add_edge("compiler_repair", "build")
    builder.add_edge("template_fallback", "build")
    builder.add_edge("github_push", "deploy")
    builder.add_edge("deploy", END)
    builder.add_edge("handle_failure", END)

    return builder.compile()


PIPELINE_GRAPH = build_pipeline_graph()
