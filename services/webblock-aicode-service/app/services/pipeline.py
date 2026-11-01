import logging
import time
from typing import Any
from sqlalchemy import text
from app.core.config import settings
from app.db import SessionLocal
from app.services import artifacts, build_runner, deployment, event_bus, github, glm, store
from app.services.change_detector import detect_change

from app.services.scaffold import REGISTRY_PATH, apply_puck_update, implemented_types, render_registry, render_site
from app.services.graph_pipeline import PIPELINE_GRAPH

log = logging.getLogger("webblock.pipeline")

# Worker half of a generation request. Records: ai_codegen.harness_runs (+ RUN_LOG, CHECK_REPORT and SOURCE_ARCHIVE
# artifacts, validation_runs for the build), project.generation_requests (commit, acceptance) and provisioning
# releases/release_steps/commands/project_hosting for the deploy.

class StepError(Exception):
    def __init__(self, code: str, message: str, detail_log: str = ""):
        super().__init__(message)
        self.code = code
        self.detail_log = detail_log

def execute_run(run_id: str) -> None:
    """Run a claimed harness run to completion. Never raises; failures are recorded on the run."""
    report: dict[str, Any] = {}
    try:
        _execute(run_id, report)
    except StepError as e:
        _fail(run_id, report, e.code, str(e), e.detail_log)
    except Exception as e:  # any crash must end the run as FAILED
        log.exception("run %s crashed", run_id)
        _fail(run_id, report, "INTERNAL_ERROR", f"{type(e).__name__}: {e}")

def _write_report(run_id: str, report: dict[str, Any]) -> None:
    ref = store.run_ref(run_id, artifacts.CHECK_REPORT)
    store.save_artifact(run_id, "CHECK_REPORT", ref, artifacts.write_json(ref, report))

def _close_log(run_id: str) -> None:
    ref = store.run_ref(run_id, artifacts.RUN_LOG)
    store.save_artifact(run_id, "RUN_LOG", ref, artifacts.sha256_file(ref))

def _fail(run_id: str, report: dict[str, Any], code: str, message: str, detail_log: str = "") -> None:
    if detail_log:
        store.append_log(run_id, detail_log)
    store.append_log(run_id, f"FAILED [{code}] {message}")
    with SessionLocal() as db:
        row = db.execute(text("""
          UPDATE ai_codegen.harness_runs SET status='FAILED', error_code=:code, errors=:msg, completed_at=now(), lease_expires_at=NULL
          WHERE harness_run_id=:id RETURNING generation_request_id, project_id
        """), {"code": code, "msg": message[:2000], "id": run_id}).mappings().one()
        request_id, project_id = row["generation_request_id"], row["project_id"]
        db.commit()
    try:
        store.update_generation_request(request_id, status="FAILED", failure_code=code)
    except Exception:
        pass
    _write_report(run_id, {**report, "error": {"code": code, "message": message}})
    _close_log(run_id)
    event_bus.publish_generation_completed(request_id, run_id, project_id, "FAILED")


def _skipped(run_id: str, step: str, message: str) -> None:
    store.append_log(run_id, f"SKIPPED: {message}")
    if settings.strict_promotion:
        raise StepError(f"{step}_SKIPPED", f"{message} STRICT_PROMOTION requires every step to run.")

def _load(run_id: str) -> dict[str, Any]:
    with SessionLocal() as db:
        run = store.get_run(db, run_id)
        try:
            request = store.get_generation_request(db, run["generation_request_id"])
        except Exception:
            target = store.input_version_for_request(db, run["generation_request_id"])
            request = {
                "generation_request_id": run["generation_request_id"],
                "project_id": run["project_id"],
                "captured_edit_revision": target["captured_edit_revision"] if target else 0,
                "captured_input": target["captured_input"] if target else {},
                "captured_input_hash": run["input_hash"],
                "status": "RUNNING",
            }
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
        ctx: dict[str, Any] = {"run": run, "project": project, "request": request,
                               "target": store.input_version_for_request(db, request["generation_request_id"]),
                               "baseline": None, "baseline_files": {}}
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

def _prepare_update(run_id: str, ctx: dict[str, Any], report: dict[str, Any]) -> tuple[dict[str, str], str]:
    """Flow 2: start from the baseline source snapshot, apply the JSON edit, then GLM code changes if requested."""
    baseline, target = ctx["baseline"]["captured_input"], ctx["target"]["captured_input"]
    files = dict(ctx["baseline_files"]) or render_site(baseline)
    diff = detect_change(baseline, target, implemented_types=implemented_types(files))
    store.append_log(run_id, f"Strategy {diff['strategy']}: {diff['summary']}")

    added = apply_puck_update(files, target)
    if added:
        store.append_log(run_id, f"Added block sources from templates: {', '.join(added)}")
    summary = diff["summary"]

    mode = target.get("codegen_mode") or settings.codegen_mode
    if mode == "llm":
        store.set_step(run_id, "AI_CODE_UPDATE")
        store.append_log(run_id, f"Codegen mode is 'llm': invoking GLM ({settings.glm_model}) to map visual Puck edits into code...")
        try:
            result = glm.request_code_update_from_puck(files, baseline, target, diff)
        except glm.GLMError as e:
            raise StepError("GLM_FAILED", str(e)) from e
        usage = result["usage"]
        store.update_run(run_id, model_name=usage["model_name"], input_tokens=usage["input_tokens"],
                         output_tokens=usage["output_tokens"])
        touched = glm.apply_changes(files, result["changes"])
        files[REGISTRY_PATH] = render_registry(implemented_types(files))
        store.append_log(run_id, f"GLM ({usage['model_name']}) updated {len(touched)} file(s): {', '.join(touched)}")
        report["ai"] = {"summary": result["summary"], "files": touched, **usage}
        summary = f"{summary} AI (LLM mode): {result['summary']}"
    elif target.get("instructions"):
        store.set_step(run_id, "AI_CODE_UPDATE")
        try:
            result = glm.request_code_update(files, target["instructions"])
        except glm.GLMError as e:
            raise StepError("GLM_FAILED", str(e)) from e
        usage = result["usage"]
        store.update_run(run_id, model_name=usage["model_name"], input_tokens=usage["input_tokens"],
                         output_tokens=usage["output_tokens"])
        touched = glm.apply_changes(files, result["changes"])
        files[REGISTRY_PATH] = render_registry(implemented_types(files))
        store.append_log(run_id, f"GLM ({usage['model_name']}) changed {len(touched)} file(s): {', '.join(touched)}")
        report["ai"] = {"summary": result["summary"], "files": touched, **usage}
        summary = f"{summary} AI: {result['summary']}"
    return files, summary


MAX_BUILD_REPAIRS = 2

def _run_build_phase(run_id: str, files: dict[str, str], report: dict[str, Any]) -> build_runner.BuildResult:
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

    return build_runner.build(files, on_phase)


def _build(run_id: str, files: dict[str, str], report: dict[str, Any]) -> None:
    """BUILD step: each build phase becomes an ai_codegen.validation_runs row.
    Includes an autonomous self-healing compiler repair loop if TypeScript/Next.js build fails."""
    for attempt in range(MAX_BUILD_REPAIRS + 1):
        try:
            built = _run_build_phase(run_id, files, report)
        except build_runner.BuildError as e:
            if attempt < MAX_BUILD_REPAIRS and glm.is_configured():
                store.append_log(
                    run_id,
                    f"Build error encountered (attempt {attempt + 1}/{MAX_BUILD_REPAIRS + 1}): {e}. Entering self-healing repair loop...",
                )
                try:
                    repair = glm.request_compiler_repair(files, e.log)
                    touched = glm.apply_changes(files, repair["changes"])
                    files[REGISTRY_PATH] = render_registry(implemented_types(files))
                    store.append_log(
                        run_id,
                        f"Self-healing repaired {len(touched)} file(s): {', '.join(touched)}. Retrying build...",
                    )
                    continue
                except Exception as repair_err:
                    store.append_log(run_id, f"Self-healing repair failed: {repair_err}")
            raise StepError("BUILD_FAILED", str(e), e.log) from e

        if built.status == "SKIPPED":
            for phase in ("INSTALL", "BUILD"):
                store.start_validation(run_id, phase, status="SKIPPED")
                report["validations"].append({"type": phase, "status": "SKIPPED"})
            _skipped(run_id, "BUILD", built.log)
        else:
            store.append_log(run_id, built.log)
        return

def _deploy(run_id: str, project: dict[str, Any], request_id, commit_sha: str | None) -> None:
    """DEPLOY step: a provisioning release with DEPLOY and ACTIVATE steps, triggered by a PUBLISH command."""
    release_id = store.create_release(project, request_id, commit_sha)
    log_ref = artifacts.ref(project["project_id"], f"releases/{release_id}", "deploy.log")
    step_id = store.start_release_step(release_id, "DEPLOY", log_ref=log_ref)
    try:
        deployed = deployment.deploy(project, release_id, commit_sha, project.get("repository_url"))
    except deployment.DeploymentError as e:
        artifacts.append_text(log_ref, f"FAILED: {e}\n")
        store.finish_release_step(step_id, "FAILED", "DEPLOY_FAILED")
        store.finish_release(release_id, "FAILED", failure_code="DEPLOY_FAILED", failure_summary=str(e)[:2000])
        raise StepError("DEPLOY_FAILED", str(e)) from e
    artifacts.append_text(log_ref, deployed.message + (f" {deployed.url}" if deployed.url else "") + "\n")
    live = deployed.status == "SUCCEEDED"
    store.finish_release_step(step_id, "SUCCEEDED" if live else "SKIPPED")
    store.start_release_step(release_id, "ACTIVATE", status="SUCCEEDED" if live else "SKIPPED")
    if not live and settings.strict_promotion:
        store.finish_release(release_id, "FAILED", failure_code="DEPLOY_SKIPPED", failure_summary=deployed.message)
    else:
        store.finish_release(release_id, "SUCCEEDED", activate=live)
    if live:
        store.append_log(run_id, deployed.message + (f" {deployed.url}" if deployed.url else ""))
    else:
        _skipped(run_id, "DEPLOY", deployed.message)


def _execute(run_id: str, report: dict[str, Any]) -> None:
    """Execute the generation workflow via the compiled LangGraph StateGraph with self-healing feedback loops."""
    try:
        PIPELINE_GRAPH.invoke({"run_id": run_id, "report": report})
    except build_runner.BuildError as e:
        raise StepError("BUILD_FAILED", str(e), getattr(e, "log", "")) from e
    except glm.GLMError as e:
        raise StepError("GLM_FAILED", str(e)) from e
    except github.GitHubError as e:
        raise StepError("GITHUB_FAILED", str(e)) from e
    except deployment.DeploymentError as e:
        raise StepError("DEPLOY_FAILED", str(e)) from e

