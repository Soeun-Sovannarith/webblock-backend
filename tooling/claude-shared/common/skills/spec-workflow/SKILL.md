---
name: spec-workflow
description: Use when building a new feature end to end with approval gates - audit, requirements (BRD/PRD/FRD), technical rules, spec plus failing tests, test-driven build, and two-reviewer verification. Not for bug fixes (use bug-investigation) or one-line changes.
---

# Spec workflow (5 stages)

## Purpose
Build one feature in one service with traceable requirements, checkable rules, tests written before code, and independent review. Stages 1, 2, 3 and 5 end with a STOP: wait for the user's approval. Stage 4 is the only autonomous stage.

## When to use / not use
- Use: a new feature or endpoint with real behavior to specify.
- Do not use: bug fixes (`bug-investigation`), typo or one-line changes, pure contract edits (`contract-change`), docs-only work.

## Fit with this repo (read first)
- One service per feature. The only cross-service exception is the contract change, which goes through `contract-change` first.
- Approval of Stage 3 counts as the plan approval required by `.claude/rules/scope.md`. Stage 4 needs no second plan approval, but if the work would leave the approved files or service, stop and ask.
- The contract owns the API shape (`contracts/openapi/`, `contracts/events/`). The FRD owns behavior. `spec.md` links to the contract file and never copies the schema.
- `rules.md` adds to `.claude/rules/` and the service `CLAUDE.md`; it never contradicts them. If it would, say so and ask which wins.
- Guardrail files (`.claude/settings*.json`, hooks, `.mcp.json`, CI, CODEOWNERS) are never edited by this workflow.

## Step 0: Audit (read-only, no changes)
Report briefly:
- Stack, framework versions, build tool (from the build file, not guesses).
- Folder structure and where tests live; test framework and the exact command to run all tests (`./gradlew test` or `pytest -q`).
- Conventions: naming, layering (clean architecture), lint/format.
- Existing docs for this feature (BRD/PRD/FRD/README, contracts, ADRs). Update, never duplicate.
- Gaps: no build file, no test setup, no contract. If there is no test setup, propose the smallest one that fits the stack and wait for approval. Do not restructure anything.

## Where docs live
One folder `docs/spec/<feature-name>/` with only: `brd.md`, `prd.md`, `frd.md`, `rules.md`, `spec.md`. No other new files unless a stage needs them.

## Stage 01: Requirements (you ask, user approves)
1. Ask at most 8 questions in ONE batch, each with your recommended answer so the user can reply "yes".
2. Write three docs with different purposes and no repeated content:
   - `brd.md` (WHY): business goal, problem, value and success metrics, stakeholders, constraints, out of scope. IDs `BR-1...`.
   - `prd.md` (WHAT): features, roles, user stories with acceptance criteria, business rules, non-functional needs. IDs `PR-1...`, each tracing to a `BR-x`.
   - `frd.md` (HOW): per function/endpoint/screen: inputs, outputs, validations, error cases, state changes, edge cases. IDs `FR-1...`, each tracing to a `PR-x`.
3. STOP. Wait for approval.

## Stage 02: Technical rules (user approves)
Read `.claude/rules/*` and the service `CLAUDE.md` first. Write `rules.md` covering:
- Security: auth, validation, secrets, access control (role AND ownership), data exposure.
- Accuracy: calculations, rounding, time zones, data integrity, idempotency.
- Compliance: privacy, PII in logs, audit trail, anything regulated.
- Conventions found in Step 0.
Every rule is checkable: a reviewer can answer pass or fail. IDs `RL-1...`. STOP. Wait for approval.

## Stage 03: Spec and tests (user approves)
1. `spec.md`: concrete input to output examples, formulas, expected results, edge cases; link to the contract file; a test list mapped 1:1 to `FR-x` and `RL-x`. It must not repeat the FRD; it only makes it testable.
2. Write the test code from the spec. No implementation yet.
3. Run the tests and show they fail for the right reason (missing behavior, not a typo or build error).
4. STOP. Wait for approval.

## Stage 04: Build (autonomous)
- Small steps, each driven by a failing test.
- Run the test command after EVERY edit. Never stack edits.
- Fix the code, not the tests. If a test or doc looks wrong, stop and ask.
- Targeted changes only: no unrelated refactors, no extra files or classes.
- Done when all tests pass and lint/build is clean. Report the real command output.

## Stage 05: Verify (user signs off)
Spawn two reviewers with fresh context (use existing agents where they fit):
1. Requirements reviewer: code vs `brd.md`, `prd.md`, `frd.md`, `spec.md`. Finds missing features, wrong behavior, wrong formulas, untested cases, anything built that nobody asked for.
2. Security/quality reviewer (`security-auditor` and `code-reviewer`): code vs `rules.md` and project rules.
Each returns a pass/fail list with `file:line` evidence. Fix issues (re-run tests after every edit) and re-run the reviewers until both pass. Then summarize: what was built, test results, verdicts, open risks. STOP. Wait for sign-off.

## Ground rules
- Start each stage by stating "Stage 0X: <name>".
- Read the requirements twice before writing code once. Be concise; recommend instead of re-asking.
- If a user request conflicts with an approved doc, point it out and ask which wins.
- Never skip or merge stages, and never move on without the required approval.

## Common mistakes
- Copying the contract schema into `spec.md` (two sources of truth).
- FRD items with no `PR-x`, or PRD items with no `BR-x`.
- Rules that cannot be checked ("code should be secure").
- Tests that fail because of a build error instead of missing behavior.
- Editing a test to make it pass.

## Definition of done
All five docs approved, tests green, lint/build clean, both reviewers pass, user signed off.

## Related
Skills `contract-change`, `bug-investigation`; agents `code-reviewer`, `security-auditor`, `contract-checker`; rules `scope.md`, `security.md`.
