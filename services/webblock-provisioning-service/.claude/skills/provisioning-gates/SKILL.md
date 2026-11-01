---
name: provisioning-gates
description: Use when about to say Provisioning work is done, before a commit a reviewer will see, or when asked
  to run the checks, gates or tests of this repo.
---

# Provisioning gates

Run from the repo root (this repository or its worktree). No gate target reads
`.env`. Report every command with its real result. A check you did not run is "not run", never "passed". There
is no CI (OD-20), so every result is a local run.

Full reference: `.claude/docs/runbooks/python-p0.md` (targets, coverage, py-vuln, py-age, image-scan), CLAUDE.md
section 4 and `.claude/rules/testing.md`. Targets live in `mk/python.mk`, `mk/contracts.mk`, `mk/chart.mk`.

## What "done" needs

- Format and static: `ruff format --check`, `ruff check`, `mypy --strict`, `import-linter` pass.
- Unit and contract tests pass. Coverage: 80% of statements in each of domain and application, 70% over
  handwritten code.
- Every security and recovery scenario in `.claude/docs/architecture/acceptance-scenarios.md` has a passing
  assertion.
- osv-scanner offline on `uv.lock`: no open finding. Runtime image: no open high or critical finding without a
  reviewed, time-bounded exception (`security-exceptions`).
- Real PostgreSQL 18: migrations, constraints, conflicts, idempotency. Real RabbitMQ: duplicates, crashes,
  redelivery, outage, recovery.
- Kind (Gateway API, cert-manager, admission policies, RBAC): manifest, admission, reconcile, RBAC, readiness,
  route and recovery tests pass.
- Generated files show no diff (`deploy/prototype/manifests/` match the renderer). REST, MCP and handlers agree.

Review rules: no TODO stubs or fake success (unsupported means an explicit error); every error handled and every
resource closed; no unbounded tasks, queues, request bodies or retries; tests check behaviour and negative cases,
not mocks that repeat the code; immutable migrations, separate app and migration privileges, upgrade tests with old
rows.

## Before the first run

- `python3.14` on PATH (every target checks it and stops with exit 2 otherwise).
- `make py-tools` once: pinned uv, osv-scanner, goose, grype and protoc into `./bin`.
- `make kind-tools` once: pinned helm and friends (needed by `chart-check`).
- The clone `../web-block-contracts` (or `CONTRACTS_REPO`) for `py-test` and `contracts-check`.

## Fast tier (always, in this order)

1. `make py-fmt-check py-lint py-typecheck py-layers` (ruff format, ruff check, mypy --strict, import-linter),
   then `make spec-check` (`.claude/docs/requirements/spec.yaml` against the spec, the test IDs, the branch's
   commits, designs and plans). It is a report, not a gate yet: it exits 0; quote its counts and problems.
2. `make py-test` (unit and contract tests; any warning is an error; collecting no test fails)
3. `make contracts-check` (generated code and `src/contracts/shared/` match `contracts.lock`)
4. `make chart-check` (helm lint --strict and `pytest -m chart`, no cluster)

One test while you work: `.venv/bin/python -X dev -m pytest -m unit tests/unit/<path> -k '<name>'`.

## Slow tier (only when the change touches it)

| Change touches | Run | Needs |
| --- | --- | --- |
| Schema, stores, SQL, migrations | `make py-test-integration` | Docker; PostgreSQL 18 through Testcontainers |
| Broker code, workers, outbox, inbox | `make py-test-messaging` | Docker; RabbitMQ through Testcontainers |
| Dependencies (`uv.lock`, tool pins) | `make py-vuln py-age` | network (OSV, PyPI, GitHub) |
| Cluster code, chart, policies, e2e | the `kind-slot-run` skill | a Kind slot from the owner |

Machine slot rules (shared machine):

- Check `free -m` first.
- Never run a Testcontainers suite while a Kind stack runs, with one exception: `py-test-messaging` may run next to
  one Kind stack while at least 2000 MB are available.
- One heavy suite at a time.

## Coverage (when asked, or before a phase merge)

Record each suite once with `PY_COVER=1` in its own slot, then run the gate:

```sh
make py-test-integration py-test-messaging PY_COVER=1     # no Kind stack up
make proto-up
make proto-test-kind py-test-catalog-live py-test-jenkins PY_COVER=1
make proto-down
make py-cover
```

A missing, failed, skipped or stale recording fails `py-cover`. Never lower a gate or skip a failing path.

## Rules that make a result count

- The dependency suites fail, never skip, when their server is missing (`tests/conftest.py`). A skip is a failure.
- Do not suppress diagnostics broadly, add `# type: ignore` or `noqa` without a reason, or write assertion-free
  tests to make a gate pass (CLAUDE.md section 4). An exception needs reason, reviewer, expiry and follow-up in
  `.claude/docs/decisions/` or the exception files.
- If a gate fails for a reason outside your change, say so and show the output; do not fix unrelated code
  without asking.

## Report

A table: command, result (passed N, failed N, not run), notes. Name every check that was not run and why.
Example:

| Command | Result | Notes |
| --- | --- | --- |
| `make py-fmt-check py-lint py-typecheck py-layers` | passed | |
| `make spec-check` | report: 0 problems | FR with a test 4 of 51 |
| `make py-test` | passed 6958 | |
| `make py-test-integration` | not run | no schema change |
