---
paths:
  - "app/services/assistant.py"
  - "app/services/change_sets.py"
  - "app/api/routes/assistant.py"
  - "tests/test_change_sets.py"
---

# Design assistant

The flow:
1. `assistant.send_message` saves the user message.
2. It asks GLM (`glm.propose_change_set`) for operations on one page.
3. `change_sets.normalize_operations` validates them, and the result is stored as a change set with its items.
4. `apply_change_set`/`discard_change_set` decide the change set.

- **Operations** are `OPERATIONS = ("ADD", "UPDATE", "REMOVE", "MOVE")`. `component_id: "root"` targets root props (`ROOT_PROPS`). Props are checked against `catalog.CATALOG`/`STYLE_PROPS`, and an operation must also be in the component's `allowed_operations` (`ai_codegen.puck_component_definitions`).
- **GLM output is untrusted.** An invalid proposal raises `ChangeSetError`. Its items are dropped (never stored half-valid), and the reason is appended to the assistant's reply as a warning. `send_message` also dry-runs `apply_items` before storing. Keep validation in `normalize_operations`, not in the route.
- **Optimistic concurrency.** A change set records the project's `edit_revision`. `apply_change_set` rejects it with `STALE_EDIT_REVISION` (409, status `REJECTED`) if any other edit landed first. Otherwise it rewrites the page and bumps `edit_revision`. Any code path that edits pages must bump `edit_revision` too, or stale change sets will apply.
- **Applying a change set only edits Puck data.** It does not queue a generation. The editor triggers that separately.
- Status strings (`PROPOSED`, `APPLIED`, `REJECTED`, `DISCARDED`, …) must match the `ai_assistant` enums in `sql/003_ai_assistant.sql`.
- Tests: `tests/test_change_sets.py` exercises `normalize_operations`/`apply_items` as pure functions. Patch `glm._chat` for the GLM side.
