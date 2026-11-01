---
name: add-pipeline-step
description: Add, reorder or change a node in the LangGraph generation pipeline (graph_pipeline.py) - new step, new conditional edge, or retry/repair behaviour - without breaking the run-log contract the frontend parses. Use for any change to how a harness run executes.
argument-hint: "<step and where it goes>"
---

# Change the pipeline graph

The graph is in `app/services/graph_pipeline.py`:
- `START → prepare → ai_code_update → build → (github_push | compiler_repair → build | template_fallback → build | handle_failure) → deploy → END`

`pipeline.execute_run` invokes it and turns service errors into `StepError`. Rules: `.claude/rules/python-app.md`.

1. **Is it a step or a node?**
   - A *step* is visible to the frontend. It writes a `[STEP]` marker via `store.set_step(run_id, "NAME")` and extends the lease. The frontend renders the known names: `PREPARE`, `AI_CODE_UPDATE`, `BUILD`, `GITHUB_PUSH`, `DEPLOY`. A new marker is a frontend contract change, so tell the user what the editor must render.
   - An internal *node* (like `compiler_repair`) writes plain log lines only.
2. **Write the node.** `def x_node(state: PipelineState) -> dict[str, Any]`:
   - Copy before mutating: `files = dict(state["files"])`.
   - Return only the keys you change.
   - Add any new keys to the `PipelineState` TypedDict.
   - Open short DB sessions, and never hold one across GLM, build, push or deploy.
3. **A skippable step** writes `SKIPPED: [STEP] <why>` and must respect `STRICT_PROMOTION`: failing the run when it's true is the intended behaviour, and `pipeline._skipped` has it.
4. **Wire it** in `build_pipeline_graph()` with `add_node` and edges. A conditional edge gets a pure router function like `route_build_outcome`, and every cycle is bounded by a counter in state.
5. **Errors.** Raise the service's error (`BuildError`, `GLMError`, `GitHubError`, `DeploymentError`) or a `StepError(CODE, ...)`. A new exception type must be mapped in `pipeline._execute`. New codes are UPPERCASE and appear in `error_code`/`failure_code`.
6. **Artifacts and report.** Add results to `state["report"]` so they reach `CHECK_REPORT`, and rewrite the `SOURCE_ARCHIVE` if `files` changed after `prepare`.
7. **Tests** in `tests/test_graph_pipeline.py`:
   - router functions as pure unit tests;
   - nodes with `store`, `glm._chat` and `build_runner.build` patched;
   - check that the graph compiles and has the expected nodes.
8. **README** section 1.2: update the state-machine diagram and the `PipelineState` table.
