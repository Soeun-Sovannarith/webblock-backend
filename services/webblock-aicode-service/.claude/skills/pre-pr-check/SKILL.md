---
name: pre-pr-check
description: Final quality gate before committing or opening a PR - lint, tests, cross-file contract review, docs sync and an honest summary of what was and wasn't verified. Use when the user says a change is done, asks to commit/open a PR, or before you declare a multi-file change complete.
---

# Pre-PR check

1. **Scope.** Run `git status`, then `git diff main...HEAD --stat` plus `git diff --stat` for uncommitted work. List the areas touched: catalog/blocks, SQL, events, routes, pipeline, templates, settings, `.claude/`.
2. **Mechanical checks.** Both must pass, or report the exact failure:
   ```bash
   ruff check .
   pytest -q      # the *_not_found tests need Postgres up
   ```
3. **Contracts.** For each touched area, confirm the other sides moved too:
   - **Component**: catalog + `<Type>.tsx` + new SQL seed + a frontend note (`add-puck-component`).
   - **Event**: model + bus + README §4 + `test_event_bus.py`.
   - **Route**: README §6/§3 + route test.
   - **Setting**: `.env.example` + README §2.
   - **Pipeline**: step names unchanged, or the frontend change is flagged. `SKIPPED:`/`STRICT_PROMOTION` honoured.
   - **SQL**: new numbered file, applied twice.

   For a large diff, delegate this to the `contract-reviewer` subagent, plus `security-reviewer` if GLM, the sandbox, SQL or input validation changed. Or run the `review-branch` workflow.
4. **`.claude/` drift.** If a convention changed, update the matching rule or skill in this same change.
5. **Summary for the user / PR body.**
   - What changed and why.
   - What was verified (commands and results) and what was not (for example, "real Docker build not run").
   - Follow-ups in other repos (the frontend's `puck-config.tsx`, Project Service, …).
6. Commit only if the user asked. Use a new branch if you're on `main`.
