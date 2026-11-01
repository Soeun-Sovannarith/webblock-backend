---
description: Implement the approved plan, only what was approved, then run tests
argument-hint: [plan reference or short note]
---

Implement the plan approved in this conversation. Note: $ARGUMENTS

1. Confirm a plan was approved. If not, stop and tell the user to run `/plan` first.
2. Check the current branch (`git branch --show-current`). If it is `main`, `master` or `develop`, stop and ask the user to create `feature/<service>-<short-desc>`.
3. Contract first: if the plan changes an API or event, edit `contracts/` before code (skill `contract-change`).
4. Touch only the files named in the plan. If another file is needed, stop and ask; do not widen scope.
5. Follow `.claude/rules/` and the layering in `CLAUDE.md`. Write or update tests together with the code, not afterwards.
6. Run the service test command from `CLAUDE.md` (and the linter if listed). Fix failures you caused. Report pre-existing failures without fixing them.
7. Do not commit or push; the user runs `/commit` when ready.

Output format:
- **Files changed** (path, one-line reason)
- **Tests**: command, real result (passed/failed/skipped)
- **Not done / follow-ups**
- **Next step**: `/review`, `/security-review`, then `/commit` and `/pr`

Stop conditions: plan missing, protected file needed (settings, hooks, `.mcp.json`, CI, CODEOWNERS, committed migrations), test failures you cannot explain (switch to skill `bug-investigation`).
