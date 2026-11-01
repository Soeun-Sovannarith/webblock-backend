# Spec-driven development

The spec changes first; the code follows it. The stages, agents and owner gates are explained once, in
`.claude/WORKFLOW.md`. These rules bind every stage:

- Every FR has acceptance criteria a test can check. Every TR links up to the FR it serves.
- The change design names the FR and TR IDs it covers. Each plan task names its FR and TR IDs and the acceptance
  tests it must make pass.
- No FR ID, no code. A code change (`src/`, `tests/`, `deploy/`, migrations) cites at least one FR ID; a purely
  technical change cites its TR and that TR's FR. A change with no ID is refused and goes back to stage 1.
- Agent tooling: a commit that changes only the hooks (`.claude/hooks/`), spec-check (`scripts/py/spec_check.py`)
  or the QA tools (`mk/qa.mk`) cites TR-56. TR-56 serves all FRs (as TR-2 does), so it needs no FR ID beside it.
- Tests are written first, from the FR acceptance criteria. Each test names its ID in its docstring
  (`"""FR-12: ..."""`) or its name (`test_fr12_...`).
- Drift: if the code must differ from the spec, the implementer stops and reports Drift with both sides. Nobody
  edits the spec to match the code without the owner's decision; then the spec changes first.
- The spec-writer and the designer change `.claude/docs/requirements/` only after an owner decision, and say which.
- Before a merge, the `checking-requirements-direction` skill updates `.claude/docs/requirements/DIRECTION.md` on the
  final code. Drift on a Must item blocks the merge until the owner rules.
- Notion stays the authority for the database and the API contract; the spec points to it and never copies it.
- Every report names the stage it serves and the IDs it covers.

There is no exception: every task cites FR IDs.
