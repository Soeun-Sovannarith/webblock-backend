from app.services.graph_pipeline import PIPELINE_GRAPH, route_build_outcome


def test_graph_pipeline_nodes():
    # Verify all nodes are present in the compiled StateGraph
    nodes = PIPELINE_GRAPH.nodes
    expected_nodes = {"prepare", "ai_code_update", "build", "compiler_repair", "template_fallback", "github_push", "deploy",
                      "handle_failure"}
    for node in expected_nodes:
        assert node in nodes


def test_route_build_outcome_success():
    state = {"build_success": True, "repair_attempts": 0}
    assert route_build_outcome(state) == "github_push"


def test_route_build_outcome_retry(monkeypatch):
    monkeypatch.setattr("app.services.glm.is_configured", lambda: True)
    state = {"build_success": False, "repair_attempts": 0, "build_error": "SyntaxError"}
    assert route_build_outcome(state) == "compiler_repair"


def test_route_build_outcome_max_retries_exceeded(monkeypatch):
    monkeypatch.setattr("app.services.glm.is_configured", lambda: True)
    state = {"build_success": False, "repair_attempts": 2, "build_error": "SyntaxError"}
    assert route_build_outcome(state) == "handle_failure"


def _node_env(monkeypatch, tmp_path):
    """Run pipeline nodes without a DB: artifacts go to tmp_path, store calls that touch Postgres are no-ops."""
    from app.core.config import settings
    from app.services import store
    monkeypatch.setattr(settings, "artifact_dir", str(tmp_path))
    monkeypatch.setattr(settings, "codegen_mode", "llm")
    saved = {"log": []}
    monkeypatch.setattr(store, "save_artifact", lambda run_id, kind, ref, digest: saved.__setitem__(kind, ref))
    monkeypatch.setattr(store, "append_log", lambda run_id, message: saved["log"].append(message))
    for name in ("set_step", "update_run"):
        monkeypatch.setattr(store, name, lambda *a, **k: None)
    return saved


def test_glm_code_reaches_the_saved_source(monkeypatch, tmp_path):
    from app.services import artifacts, glm, graph_pipeline
    saved = _node_env(monkeypatch, tmp_path)
    hero = "export default function Hero() { return <h1>GLM</h1>; }\n"
    monkeypatch.setattr(glm, "request_code_from_puck", lambda files, target: {
        "summary": "s", "changes": [{"path": "components/blocks/Hero.tsx", "action": "upsert", "content": hero}],
        "usage": {"model_name": "m", "input_tokens": 1, "output_tokens": 1}})
    state = {"run_id": "r1", "project": {"project_id": "p1"}, "target": {"captured_input": {"pages": []}},
             "baseline": None, "baseline_files": {}, "summary": "", "report": {},
             "files": {"components/blocks/Hero.tsx": "old", "db/schema.ts": "export const x = 1;\n"}}

    out = graph_pipeline.ai_code_update_node(state)
    source = artifacts.read_source(saved["SOURCE_ARCHIVE"])
    assert source["components/blocks/Hero.tsx"] == hero == out["files"]["components/blocks/Hero.tsx"]
    assert "components/blocks/Hero.tsx" in out["report"]["changed_files"]


def test_compiler_repair_reaches_the_saved_source(monkeypatch, tmp_path):
    from app.services import artifacts, glm, graph_pipeline
    saved = _node_env(monkeypatch, tmp_path)
    fixed = "export default function Hero() { return null; }\n"
    monkeypatch.setattr(glm, "request_compiler_repair", lambda files, log: {
        "changes": [{"path": "components/blocks/Hero.tsx", "action": "upsert", "content": fixed}]})
    state = {"run_id": "r1", "project": {"project_id": "p1"}, "baseline_files": {}, "report": {},
             "files": {"components/blocks/Hero.tsx": "broken"}, "build_error": "TS1005"}

    out = graph_pipeline.repair_node(state)
    assert out["repair_attempts"] == 1
    assert artifacts.read_source(saved["SOURCE_ARCHIVE"])["components/blocks/Hero.tsx"] == fixed


TEMPLATE_FILES = {"components/blocks/Hero.tsx": "template hero", "db/schema.ts": "export const x = 1;\n"}
HERO = "export default function Hero() { return <h1>GLM</h1>; }\n"


def _ai_state(**overrides):
    state = {"run_id": "r1", "project": {"project_id": "p1"}, "target": {"captured_input": {"pages": []}},
             "baseline": None, "baseline_files": {}, "summary": "Generated.", "report": {}, "files": dict(TEMPLATE_FILES)}
    return {**state, **overrides}


def _glm_writes_hero(files, *args):
    return {"summary": "s", "changes": [{"path": "components/blocks/Hero.tsx", "action": "upsert", "content": HERO}],
            "usage": {"model_name": "m", "input_tokens": 1, "output_tokens": 1}}


def _glm_fails(*args):
    from app.services.glm import GLMError
    raise GLMError("GLM did not finish within 180 s.")


def test_glm_failure_on_a_new_site_keeps_the_template_code(monkeypatch, tmp_path):
    from app.services import glm, graph_pipeline
    saved = _node_env(monkeypatch, tmp_path)
    monkeypatch.setattr(glm, "request_code_from_puck", _glm_fails)

    out = graph_pipeline.ai_code_update_node(_ai_state())
    assert "files" not in out and "fallback_files" not in out  # the PREPARE code goes on to the build unchanged
    assert out["report"]["ai"]["fallback"].startswith("GLM failed: GLM did not finish")
    assert any(line.startswith("WARNING: [AI_CODE_UPDATE] GLM failed") for line in saved["log"])


def test_glm_failure_on_new_instructions_fails_the_run(monkeypatch, tmp_path):
    import pytest
    from app.core.config import settings
    from app.services import glm, graph_pipeline
    _node_env(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "codegen_mode", "hybrid")
    monkeypatch.setattr(glm, "request_code_update", _glm_fails)
    state = _ai_state(baseline={"captured_input": {"pages": []}},
                      target={"captured_input": {"pages": [], "instructions": "Add a newsletter form"}})
    with pytest.raises(glm.GLMError):  # falling back would accept the run, and resending the request would be a no-op
        graph_pipeline.ai_code_update_node(state)


def test_glm_change_keeps_the_prepare_code_as_fallback(monkeypatch, tmp_path):
    from app.services import glm, graph_pipeline
    _node_env(monkeypatch, tmp_path)
    monkeypatch.setattr(glm, "request_code_from_puck", _glm_writes_hero)

    out = graph_pipeline.ai_code_update_node(_ai_state())
    assert out["files"]["components/blocks/Hero.tsx"] == HERO
    assert out["fallback_files"] == TEMPLATE_FILES


def test_requested_glm_change_has_no_fallback(monkeypatch, tmp_path):
    from app.core.config import settings
    from app.services import glm, graph_pipeline
    _node_env(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "codegen_mode", "hybrid")
    monkeypatch.setattr(glm, "request_code_update", _glm_writes_hero)
    state = _ai_state(baseline={"captured_input": {"pages": []}},
                      target={"captured_input": {"pages": [], "instructions": "Make the hero bigger"}})
    assert "fallback_files" not in graph_pipeline.ai_code_update_node(state)


def test_route_build_outcome_falls_back_after_the_repairs(monkeypatch):
    """When a build with fallback files fails, it attempts compiler repair up to 2 times before falling back to
    template files; if the fallback also fails, it terminates with handle_failure."""
    monkeypatch.setattr("app.services.glm.is_configured", lambda: True)
    failed = {"build_success": False, "fallback_files": TEMPLATE_FILES}
    assert route_build_outcome(failed) == "compiler_repair"
    assert route_build_outcome({**failed, "repair_attempts": 0}) == "compiler_repair"
    assert route_build_outcome({**failed, "repair_attempts": 1}) == "compiler_repair"
    assert route_build_outcome({**failed, "repair_attempts": 2}) == "template_fallback"
    assert route_build_outcome({**failed, "repair_attempts": 2, "fallback_used": True}) == "handle_failure"
    assert route_build_outcome({**failed, "repair_attempts": 0, "fallback_used": True}) == "handle_failure"
    monkeypatch.setattr("app.services.glm.is_configured", lambda: False)
    assert route_build_outcome({**failed, "repair_attempts": 0}) == "template_fallback"


def test_template_fallback_builds_the_code_from_before_glm(monkeypatch, tmp_path):
    from app.services import artifacts, graph_pipeline
    saved = _node_env(monkeypatch, tmp_path)
    state = _ai_state(files={**TEMPLATE_FILES, "components/blocks/Hero.tsx": "broken"}, fallback_files=TEMPLATE_FILES,
                      report={"ai": {"files": ["components/blocks/Hero.tsx"]}})

    out = graph_pipeline.template_fallback_node(state)
    assert out["files"] == TEMPLATE_FILES and out["fallback_used"] is True
    assert out["report"]["ai"]["fallback"] and out["report"]["ai"]["files"] == ["components/blocks/Hero.tsx"]
    assert artifacts.read_source(saved["SOURCE_ARCHIVE"]) == TEMPLATE_FILES
    assert saved["log"][0].startswith("WARNING: [BUILD]")


def test_glm_returning_a_file_unchanged_is_not_a_change(monkeypatch, tmp_path):
    from app.services import glm, graph_pipeline
    saved = _node_env(monkeypatch, tmp_path)
    monkeypatch.setattr(glm, "request_code_from_puck", lambda files, target: {
        "summary": "s", "changes": [{"path": "components/blocks/Hero.tsx", "action": "upsert", "content": "template hero"}],
        "usage": {"model_name": "m", "input_tokens": 1, "output_tokens": 1}})

    from app.services.scaffold import refresh_generated
    files = dict(TEMPLATE_FILES)
    refresh_generated(files)  # as PREPARE leaves them: registry and site tables already in place

    out = graph_pipeline.ai_code_update_node(_ai_state(files=files))
    assert out["report"]["ai"]["files"] == [] and "fallback_files" not in out
    assert saved["log"][-1].endswith("0 file(s): no changes needed")


def test_project_publish_target_skips_the_push_and_the_deploy(monkeypatch, tmp_path):
    """PUBLISH_TARGET=project: Project commits to Gitea and Provisioning deploys, so the run only ends and reports."""
    from app.core.config import settings
    from app.services import deployment, event_bus, github, graph_pipeline
    saved = _node_env(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "publish_target", "project")
    for module, name in ((github, "push"), (deployment, "deploy")):
        monkeypatch.setattr(module, name, lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    published = []
    monkeypatch.setattr(event_bus, "publish_generation_completed", lambda *a: published.append(a))
    from app.services import artifacts, store
    monkeypatch.setattr(store, "run_ref", lambda run_id, name, project_id=None: artifacts.ref("p1", run_id, name))

    class _Db:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k): pass
        def commit(self): pass
    monkeypatch.setattr(graph_pipeline, "SessionLocal", _Db)
    state = {"run_id": "r1", "project": {"project_id": "p1"}, "request": {"generation_request_id": "q1"},
             "files": {}, "report": {}}

    assert graph_pipeline.github_push_node(state) == {"commit_sha": None}
    assert graph_pipeline.deploy_node(state) == {"status": "SUCCEEDED"}
    skipped = [line for line in saved["log"] if line.startswith("SKIPPED:")]
    assert [line.split("]")[0] for line in skipped] == ["SKIPPED: [GITHUB_PUSH", "SKIPPED: [DEPLOY"]
    assert published == [("q1", "r1", "p1", "SUCCEEDED")]


def test_a_request_known_only_to_project_still_loads(monkeypatch):
    """A run from Project's event has an input version here but no project.generation_requests row."""
    from sqlalchemy.exc import NoResultFound
    from app.services import graph_pipeline, store
    run = {"harness_run_id": "r1", "generation_request_id": "q1", "project_id": "p1", "input_hash": "h" * 64,
           "baseline_version_id": None}
    monkeypatch.setattr(store, "get_run", lambda db, rid: run)
    monkeypatch.setattr(store, "get_generation_request", lambda db, rid: (_ for _ in ()).throw(NoResultFound()))
    monkeypatch.setattr(store, "update_generation_request", lambda *a, **k: None)
    monkeypatch.setattr(store, "get_project", lambda db, pid: (_ for _ in ()).throw(LookupError()))
    monkeypatch.setattr(store, "input_version_for_request",
                        lambda db, rid: {"captured_input": {"pages": []}, "captured_edit_revision": 4})
    ctx = graph_pipeline._load_context("r1")
    assert ctx["request"]["generation_request_id"] == "q1" and ctx["request"]["captured_input_hash"] == "h" * 64
    assert ctx["project"]["project_id"] == "p1"
    assert ctx["request"]["captured_edit_revision"] == 4  # the commit message and run log name it


def test_a_queued_request_keeps_its_captured_edit_revision(monkeypatch):
    # Picking up a QUEUED request used to overwrite the revision with the run row's (it has none), so every commit
    # said "edit revision 0" (found by the GitHub e2e run of 31 Oct 2026).
    from app.services import graph_pipeline, store
    run = {"harness_run_id": "r1", "generation_request_id": "q1", "project_id": "p1", "input_hash": "h" * 64,
           "baseline_version_id": None}
    monkeypatch.setattr(store, "get_run", lambda db, rid: run)
    monkeypatch.setattr(store, "get_generation_request", lambda db, rid: {"generation_request_id": "q1", "status": "QUEUED",
                                                                          "captured_edit_revision": 2})
    monkeypatch.setattr(store, "update_generation_request", lambda *a, **k: None)
    monkeypatch.setattr(store, "get_project", lambda db, pid: {"project_id": "p1"})
    monkeypatch.setattr(store, "input_version_for_request",
                        lambda db, rid: {"captured_input": {"pages": []}, "captured_edit_revision": 2})
    ctx = graph_pipeline._load_context("r1")
    assert ctx["request"]["captured_edit_revision"] == 2 and ctx["request"]["status"] == "RUNNING"
