---
name: langgraph-pipeline
description: Use this skill when inspecting, extending, or debugging the LangGraph state machine pipeline, conditional edges, compiler repair loops, or execution telemetry.
---

# LangGraph Pipeline Skill

This skill guides working with the LangGraph state machine pipeline in `app/services/graph_pipeline.py`.

## Pipeline Graph Architecture

The workflow is a compiled `StateGraph(PipelineState)`:

```
START ──> prepare ──> ai_code_update ──> build ──┬──(success)───────────> github_push ──> deploy ──> END
                                                 │
                                                 ├──(failure & retries)─> compiler_repair ──> build (loop)
                                                 │
                                                 └──(max retries)───────> handle_failure ──> END
```

### Graph Nodes
1. **`prepare`**: Loads run context, renders base Next.js + Drizzle project files, writes `SOURCE_ARCHIVE` and `CHECK_REPORT`.
2. **`ai_code_update`**: Invokes GLM (streamed) to update React components from Puck JSON or user instructions.
3. **`build`**: Executes `npm install` and `npm run build` using the Docker Python SDK in isolated containers (`node:22-alpine`).
4. **`compiler_repair`**: Autonomous self-healing loop. Parses compiler diagnostics and requests LLM to fix syntax/type errors.
5. **`github_push`**: Commits full source tree snapshot to the project repository.
6. **`deploy`**: Produces a provisioning release and updates hosting routing.
7. **`handle_failure`**: Marks run as failed with `StepError`.

## Adding a New Graph Node

To add a step (e.g. `security_audit` or `lint_validation`):

1. Define the node function in `app/services/graph_pipeline.py`:
   ```python
   def audit_node(state: PipelineState) -> dict[str, Any]:
       run_id = state["run_id"]
       store.set_step(run_id, "AUDIT")
       # perform audit checks on state["files"]
       return {"report": {**state.get("report", {}), "audit": "passed"}}
   ```
2. Register the node in `build_pipeline_graph()`:
   ```python
   builder.add_node("audit", audit_node)
   builder.add_edge("build", "audit")
   builder.add_edge("audit", "github_push")
   ```
3. Re-compile graph:
   ```python
   PIPELINE_GRAPH = builder.compile()
   ```

## Running & Testing the Graph

Run isolated unit tests for the LangGraph pipeline:

```bash
pytest tests/test_graph_pipeline.py
```
