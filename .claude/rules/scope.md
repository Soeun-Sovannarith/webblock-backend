# Task scope rules (always apply)

## Do exactly the task
- Do what the task asks and nothing more. No drive-by refactors, renames, reformatting of untouched code, dependency upgrades or "while I am here" fixes. Note them as follow-ups instead.
- Prefer the smallest change that solves the problem and keeps existing conventions (naming, layering, error format).
- If the task is ambiguous, ask one specific question before writing code.

## Stay inside the service
- Work only inside the folder where the session started. Other services are read-only references.
- A change needed in another service is written as a note for its owner (what, why, contract file, deadline), never made.

## Plan, then edit
- State which files you will change and why, and wait for approval on anything beyond a one-line fix (`/plan`, then `/implement`).
- After changing code, run the service tests (command in `CLAUDE.md`) and report the real result. Never claim "tests pass" without running them.

## Guardrails
- Never edit `.claude/settings*.json`, `.claude/hooks/`, `.mcp.json`, CI workflows or CODEOWNERS. A human changes them.
- If a rule conflicts with the task, stop and ask; do not silently pick one.

Example: asked to fix a null check in `OrderService`, you notice an outdated library. Fix the null check only; list the library under "Follow-ups".
