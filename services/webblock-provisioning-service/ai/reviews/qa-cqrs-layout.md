# QA sweep: cqrs-layout at ab96da8

Stage: 5 Verify (qa-reviewer, decision `0026` Q6). Full sweep of `src/provisioning` (283 handwritten files, `_gen/`
skipped). IDs: TR-2 (layers), TR-3 (one operation table), TR-8 (stack), TR-17 (REST), TR-54 (buses), FR-1 to FR-8,
FR-38, FR-46, P-4, P-8, P-10. Read-only on code: nothing in `src/`, `tests/` or configuration was changed.

## Tools

| Step | Command | Exit | Findings |
| --- | --- | --- | --- |
| 1 duplicate code | `make qa-sweep`: pylint R0801, at least 8 similar lines | 0 (target) | 8 |
| 2 dead code | `make qa-sweep`: vulture on `src/` and `scripts/py/vulture_whitelist.py` | 0 (target) | 27 |
| 2b test-only use | vulture on `src/` and `tests/` (qa-sweep skill step 1) | 3 | 18 names used only by tests |
| 3 ruff QA rules | `ERA,PIE,RET,FAST,DTZ,C90` (`max-complexity = 18`) | 0 | 0 |
| 3b complexity watch | `ruff check --select C901 --config 'lint.mccabe.max-complexity=15'` | 1 | 5 at 16 to 18 |
| 4 mypy deprecated | `mypy` with `enable_error_code = ["deprecated"]` | 0 | 0 |
| 5 PyCharm | `get_python_environment` (`.venv`, Python 3.14.8), then `lint_files` in 6 batches, `min_severity` warning | ok | about 560 (section "PyCharm") |
| 6 docstring scan | `grep -rnE '\.go\b\|Go: ' src/provisioning`; `grep -rn 'ARCHITECTURE.md\|REQUIREMENT.md' src tests` | ok | 108 Go lines in 73 files; 7 retired-file names |

## Summary

| Class | Count |
| --- | --- |
| Real problem | 14 findings (R1 to R14), about 140 lines of code or text to change |
| False positive | 17 vulture names (9 whitelisted), 3 pylint pairs, about 540 PyCharm findings (most against a stale copy) |
| Owner ruling | 4 questions (Q1 to Q4) |
| Kept on purpose | 9 names used only by tests whose test proves a real rule (table below) |

The most important fact for anyone reading PyCharm output: almost every PyCharm "Duplicated code fragment" compares
a file with its own old copy in `bin/kind-state/provisioning-image/context/src/` (the git-ignored image build context
of an earlier run, before the `cqrs-layout` folders). Example: `infrastructure/config/operator.py` is reported as a
127-line duplicate; the old copy differs from it in one import line, and `config/common.py` shares only the
11-line AMQP URL check with it (R2). `CommandBus` and `QueryBus` in `cqrs.py` share no logic: both delegate to
`_Registry` and stay two types on purpose (decision `0025` CQ10). Only pylint's eight pairs are intra-`src/`
duplicates. Fix for the IDE: Q4.

## Findings

Real problems, most important first:

| # | Tool | file:line | What | IDs | Reason |
| --- | --- | --- | --- | --- | --- |
| R1 | pylint | `infrastructure/config/files.py:60-75`, `presentation/operations/body.py:13-33` | Two copies of the strict JSON parser (`_Duplicate`, `_pairs`, `_constant`, `parse_json_strict`) | FR-7, TR-2 | "Never keep a second copy of a parser" (`python-code.md`) |
| R2 | pylint | `infrastructure/config/common.py:204-215`, `infrastructure/config/operator.py:115-126` | Two copies of the AMQP URL check | TR-8 | Second copy of a validation rule |
| R3 | ruff C901 watch | `presentation/operations/invoke.py:345` | `Invoker._dispatch` is at the ceiling (18); the next endpoint's `case` fails `make py-lint` | TR-3, FR-46 | Split it before the next REST task (`fastapi-endpoint` skill step 6) |
| R4 | vulture 2b | see "Code used only by tests" | 7 names exist only for tests | P-8 | "Delete code that nothing calls" |
| R5 | vulture | `infrastructure/telemetry/metrics.py:139-144` | `STATUS_CLASSES`, `INBOX_STATUSES`, `TENANT_ROUND_RESULTS`, `LABEL_NAMES` unused; the module docstring (line 18) says a test scans the registry against `LABEL_NAMES`, but `test_metrics.py:270` uses its own `ALLOWED_LABELS` | P-10, TR-8 | Dead constants and a docstring that is not true |
| R6 | vulture | `core/application/shared/profile.py:37`, `infrastructure/postgres/errors.py:24` | `OD_PROJECT_EVENTS` and `SQLSTATE_CHECK_VIOLATION` unused anywhere | TR-2 | Dead code |
| R7 | grep | 73 files, 108 lines (for example `jobs/background/dispatcher.py:1`, `shared/backoff.py:1`, `config/loader.py:1`, `identity/verifier.py:1`, `http/auth.py:1`) | Docstrings name Go files (`port of src/.../*.go`, `Go: id.go`) | P-10 | The Go reference lives only on branch `go-reference`; the names point nowhere |
| R8 | grep | `routing/background/routing.py:2`, `usage/ports.py:1`, `hosting/background/hosting_job.py:3`, `telemetry/metrics.py:1`, `telemetry/samples.py:1`, `src/contracts/openapi.yaml:7`, `tests/e2e/test_scenarios_intake.py:2` | Name the retired `ARCHITECTURE.md` | P-10 | Retired file; point to the Notion architecture page or a decision |
| R9 | pylint | `hosts/{api,bootstrap,worker,build_worker}/__main__.py` (for example `bootstrap/__main__.py:102-123`, `worker/__main__.py:66-96`) | The start sequence (observability, `InvalidSettings` to `EXIT_CONFIG`, logger, `asyncio.run`) is written four times | TR-2 | One rule (exit code on a refused setting) in four places |
| R10 | pylint | `infrastructure/project/access.py:127-157`, `infrastructure/project/grpc_access.py:54-92` | The "no user ID" refusal and the fail-closed log are copied | FR-3, FR-5 | Second copy of an access rule; see Q1 first |
| R11 | ruff C901 watch | `infrastructure/identity/verifier.py:251` | `_validate` at 17, one below the ceiling | FR-1 | Split by claim group before it grows |
| R12 | PyCharm | `core/domain/entities/command.py:31` | Imports the private `_check_target_by_kind` from `values/command_request.py` | TR-2 | A cross-module name should be public |
| R13 | PyCharm | `infrastructure/identity/jwks.py:289` | `cause = ""` is never read (every path sets it) | FR-1 | Dead store |
| R14 | PyCharm | `infrastructure/telemetry/sentry.py:88`, `presentation/http/app.py:286` | Redundant `\]` and `\}` escapes in regexes | none | Cosmetic; fix with R5 and R3 |

False positives:

| Tool | file:line | What | Reason |
| --- | --- | --- | --- |
| vulture | `core/domain/enums/states.py:39-42` | `PROMOTION`, `ACCESS_SETUP`, `OBSERVATION` | Stored enum values (`src/migrations/00001_schema_and_enums.sql:35`, Notion step kinds) |
| vulture | `core/application/hosting/ports.py:103` | `OwnerStatus.ACTIVE` | Built by value: `OwnerStatus(command.status)` (`accept_owner_status.py:118`) |
| vulture | `rabbitmq/client.py:207-217`, `:496` | `connected`, `connections`, `connect_failures`, `subscriptions` | Used only by `tests/messaging`; kept, see the table below |
| vulture | whitelisted (9 names) | `should_exit`, `capture_signals`, `handleError`, `model_config`, `trust_env`, `client_side_validation`, `tb`, `protocol`, `memo` | Framework or protocol callers (`scripts/py/vulture_whitelist.py`) |
| pylint | `core/application/operations/admin.py:112`, `views.py:192` | Command view fields listed twice | Two views on purpose: only the admin view has the requester (decision `0023` G9) |
| pylint | `intake/background/inbox_processor.py:41`, `config/build_worker.py:78` | Same settings fields | Application settings may not import infrastructure config (TR-2) |
| pylint | `hosts/*/__main__.py` second pair | Part of R9 | Counted once |
| PyCharm | about 300 "Duplicated code fragment" | Against `bin/kind-state/provisioning-image/context/src/` | Stale ignored copy (Q4) |
| PyCharm | `shared/cqrs.py:24`, `:30` | "Type parameter 'R' is not used" | Phantom type that ties a command or query to its result |
| PyCharm | `shared/cqrs.py:86`, `:110` | Buses share 21 lines | Thin typed facades over `_Registry` (decision `0025` CQ10) |
| PyCharm | about 120 type warnings | "Expected type", "Member 'None' ... has no attribute", `os.environ.get` overload, `NoReturn`, `add_middleware` ParamSpec, protobuf `\|`, `FieldValue` generic, unreachable after `isinstance` or an exhaustive `match` | `mypy --strict` passes; PyCharm's checker does not narrow these |
| PyCharm | about 30 | "Package ... is not listed in the project requirements" | Transitive pins or the standard library (`contextvars`); see Q3 |
| PyCharm | about 20 | "Method may be static" | Protocol implementations and adapters |
| PyCharm | about 30 | Shadowed names, redundant parentheses, `str(object)`, `is True` on JSON values, broad `except` with a stated reason | Style; each case is intended |
| PyCharm | `config/api.py:59`, `:71` | "Unused import" | `import x as x` re-exports |
| PyCharm | `api/signals.py:23`, `payment/grpc_entitlement.py:101`, `build_worker/wiring.py:107` | Unused parameters | Signatures fixed by `signal` and by the ports |
| PyCharm | `grpc_*.py` | Access to protected `_gen` | Generated code lives there by design (decision `0022` E3) |
| PyCharm | `kubernetes/site_cluster.py:185` | "HTTP links are not secure" | The in-cluster gateway URL on Kind; checked on purpose |
| PyCharm | about 25 | "SQL dialect is not configured" | IDE setting (Q4) |

Not run in this sweep: nothing; every tool above ran on `ab96da8`.

## Code used only by tests

vulture on `src/` alone against `src/` plus `tests/`. "Delete" removes the code and rewrites or drops only the assertion
that used it.

| Name | file:line | Test | Decision | Reason |
| --- | --- | --- | --- | --- |
| `AuditedJobStore.remembered` | `core/application/jobs/audited.py:252` | `tests/unit/core/application/jobs/test_audited.py:492` | keep | Proves the memory stays bounded at `MAX_REMEMBERED` (no unbounded state) |
| `Dispatcher.unfinished_without_dispatch` (and `_orphans`) | `core/application/jobs/background/dispatcher.py:122` | `test_dispatcher.py:212` | delete | The real effect is the error log with the count; assert that instead |
| `BootstrapLoop.is_known_ready` | `core/application/tenants/background/bootstrap_loop.py:117` | `test_subdomain_taken.py:90`, `tests/unit/hosts/test_loops.py:159` | keep | Proves the loop forgets known tenants every `recheck_rounds` and on a taken sub-domain (FR-54) |
| `bootstrap_keys` | `infrastructure/config/bootstrap.py:116` | `test_process_sections.py:72` | delete | One-line wrapper; tests call `required_keys(read_bootstrap)` (`config/loader.py:197`) |
| `build_worker_keys` | `infrastructure/config/build_worker.py:148` | `test_process_sections.py:71` | delete | Same: `required_keys(read_build_worker)` |
| `operator_broker_keys` | `infrastructure/config/operator.py:92` | `tests/unit/hosts/operator/test_dlq_config.py:27` | delete | Same: `required_keys(read_operator_broker)`; the "no PG key" assertion stays |
| `worker_keys` | `infrastructure/config/worker.py:224` | `test_process_sections.py:70` | delete | Same: `required_keys(read_worker)` |
| `PrototypeDevTokenIdentity.subjects` | `infrastructure/standins/dev_tokens.py:101` | `tests/unit/infrastructure/standins/test_dev_tokens.py:106` | delete | Its docstring says "for the start record", but nothing logs it; assert `verify` instead |
| `PrototypePaymentEventRecord.event_count` | `infrastructure/standins/entitlement_record.py:49` | `test_g_standins.py:69`, `:92` | keep | Proves the per-project event bound of the stand-in |
| `tracing_enabled` | `infrastructure/telemetry/tracing.py:262` | `test_tracing.py:87`, `tests/unit/hosts/shared/test_observability.py` | keep | Proves tracing is off unless configured and after shutdown (P-4) |
| `ALLOWED_TAGS` (constant) | `infrastructure/telemetry/sentry.py:65` | `tests/unit/infrastructure/telemetry/test_sentry.py:184` | delete | The code filters by the three `TAG_*` names; the test should state the expected set itself |
| `BrokerClient.connected`, `connections`, `connect_failures`, `subscriptions` | `infrastructure/rabbitmq/client.py:207`, `:212`, `:217`, `:496` | `tests/messaging/test_broker.py`, `test_topology_v1.py` | keep | Prove reconnect and resubscribe after a broker outage (P-3) |
| `Claim.expires_at` | `core/application/jobs/claims.py:108` | `tests/integration/test_store_jobs.py:191` | keep | Proves a heartbeat extends the lease (decision `0005`) |
| `Principal.expires_at` | `core/application/shared/principal.py:23` | `tests/unit/infrastructure/identity/test_jwt.py:138` | owner ruling (Q2) | Set from the token's `exp`, read by nothing in `src/` |

The 10 functions and 1 constant named in the brief: delete 6 functions and the constant
(`unfinished_without_dispatch`, the four `*_keys`, `subjects`, `ALLOWED_TAGS`); keep 4 (`remembered`, `is_known_ready`,
`event_count`, `tracing_enabled`).

## Tasks

All in the small change lane (`.claude/WORKFLOW.md`): existing FR or TR, no new rule. Each ends with `make
py-fmt-check py-lint py-typecheck py-layers py-test` and `make qa-sweep` no longer showing the finding.

1. **One strict JSON parser** (R1; FR-7, TR-2). Keep one `parse_json_strict` in a module both `config/files.py` and
   `presentation/operations/body.py` may import without breaking a layer contract (the lead picks the home; the
   domain imports no framework, so a standard-library helper fits there). Tests: the existing body and config file
   tests pass unchanged.
2. **One AMQP URL check** (R2; TR-8). `config/operator.py` uses the check in `config/common.py`. Tests:
   `tests/unit/hosts/operator/test_dlq_config.py` and the config tests pass.
3. **Split `_dispatch`** (R3; TR-3, FR-46). Group the cases (client commands, admin commands, reads, listings) into
   helpers so `_dispatch` drops well below 18. Tests: `tests/unit/presentation/operations/test_invoke.py`, the contract
   conformance tests. Do it before the next endpoint task.
4. **Delete test-only code** (R4; P-8). The six functions and the constant marked "delete" above, each with its test
   change. Acceptance: vulture on `src/` no longer lists them.
5. **Dead constants and the metrics docstring** (R5, R6; P-10). Delete the six unused constants; make the
   `metrics.py` docstring say what `test_metrics.py` checks.
6. **Docstrings without Go or retired files** (R7, R8; P-10). Drop the `port of ...go` and `Go: ...` phrases (keep the
   decision references); replace `ARCHITECTURE.md` with the Notion "Web Block architecture" section or the decision
   record. A docs-only change; `docs-keeper` for `openapi.yaml`'s description after the owner allows a contract text
   edit.
7. **One process start sequence** (R9; TR-2). A helper in `hosts/shared/runtime.py` used by the four `__main__`
   modules. Tests: `tests/unit/hosts/test_processes.py`.
8. **Small cleanups** (R11 to R14; FR-1, TR-2). Split `verifier._validate`; make `_check_target_by_kind` public; drop
   the dead `cause = ""`; remove the redundant regex escapes.
9. **Project access duplicates** (R10; FR-3, FR-5). After Q1: delete the HTTP adapter, or share the two log helpers.

## Owner questions

1. **Q1. HTTP project access mode.** `ProjectServiceAccess` (`infrastructure/project/access.py`,
   `PROJECT_ACCESS_MODE=project-service`) and `GrpcProjectAccess` both exist. TR-20 and decision `0022` E7 name gRPC
   `CheckAccess`. Recommendation: remove the HTTP mode, its config keys and tests; that also closes R10.
2. **Q2. Unread `Principal.expires_at`.** The verifier fills it from the token's `exp`; nothing in `src/` reads it.
   Recommendation: remove it (the verifier already refuses an expired token, FR-1), unless you want the API to bound
   a request by the token's expiry.
3. **Q3. Direct imports of transitive packages.** The code imports `starlette`, `cryptography`, `aiohttp`, `pamqp`,
   `requests` and `mcp_types`, which are pinned only through `fastapi`, `pyjwt[crypto]`, `kubernetes-asyncio`,
   `aio-pika`, the OTLP exporter and `mcp`. Exact versions are locked in `uv.lock`, so nothing floats today.
   Recommendation: leave as is; list them in `pyproject.toml` only if a direct dependency ever drops one.
4. **Q4. PyCharm settings (your IDE, no repo change).** Mark `bin/` as Excluded (or delete
   `bin/kind-state/provisioning-image/context/`, which the image build writes again) so duplicate detection stops
   comparing with the old copy; set the SQL dialect to PostgreSQL. The interpreter is already `.venv`.

## Not checked

- Duplicates shorter than 8 lines, and logic that is the same in meaning but not in text.
- Dead code reached only through strings or dynamic dispatch that vulture cannot follow (none known; dynamic imports
  are banned by ruff TID251).
- `tests/` itself was not swept for duplicates or dead helpers; `scripts/` only for the whitelist.
- PyCharm duplicate findings inside `src/` that are hidden behind the stale-copy matches; rerun the PyCharm part after
  Q4.

## After the fixes

All 14 real findings are fixed, one commit each (R4 with its test changes in one). Owner rulings applied: Q1 keep the
HTTP Project access mode, Q2 remove `Principal.expires_at`, Q3 transitive pins as they are.

| Finding | Commit | What changed |
| --- | --- | --- |
| R1 | `10d7437` | One strict JSON decoder, `core/application/shared/strict_json.py`, used by `config/files.py` and `presentation/operations/body.py` |
| R2 | `41763b9` | `config/operator.py` uses `common.amqp_url` |
| R3 | `6792f06` | `Invoker._dispatch` sends to `_write`, `_read`, `_list`; highest complexity in `invoke.py` is 9 |
| R4 | `ae04485` | Deleted `unfinished_without_dispatch`, the four `*_keys`, `subjects`, `ALLOWED_TAGS`; tests assert the real effect |
| R5 | `f3d2c25` | Deleted the four unused metric constants; the docstring says what `test_metrics.py` checks |
| R6 | `22ce29d` | Deleted `OD_PROJECT_EVENTS`, `SQLSTATE_CHECK_VIOLATION` |
| Q2 | `3403c86` | Removed `Principal.expires_at` |
| R9 | `59cd295` | `hosts/shared/process.py` `run_process` holds the start sequence of the four processes |
| R10 | `c7c2a5e` | `user_id_or_deny`, `denied`, `failed_internally` in `project/access.py`, used by both adapters |
| R11 | `401bd7b` | `verifier._validate` runs `_validate_claims` and `_validate_bounds` |
| R12 | `288e192` | `check_target_by_kind` is public |
| R13 | `27930ae` | The dead `cause = ""` is gone |
| R14 | `dd933b3` | Two redundant regex escapes removed |
| R7 | `c549911` | No Go file or symbol names in `src/provisioning` docstrings |
| R8 | `6adaccb` | No pointer to the retired `ARCHITECTURE.md` in `src/`, `tests/` or `deploy/` |

`make qa-sweep` and the extra checks, before and after:

| Category | Before | After | What remains |
| --- | --- | --- | --- |
| Duplicate code (pylint R0801 pairs) | 8 | 2 | The two false positives above (admin and command views; inbox and build worker settings) |
| Dead code (vulture names) | 27 | 13 | The 9 names kept on purpose and the 4 stored or by-value enum names |
| ruff `ERA,PIE,RET,FAST,DTZ,C90` | 0 | 0 | none |
| Complexity watch (C901 above 15) | 5 | 1 | `check_command_preconditions` at 16 |
| mypy deprecated APIs | 0 | 0 | none |
| Go file names (`\.go\b\|Go: ` in `src/provisioning`) | 98 lines in 73 files | 0 | Five mentions of Go's duration, host:port and RFC 3339 formats describe input formats |
| Retired `ARCHITECTURE.md` names (`src/`, `tests/`, `deploy/`) | 17 | 0 | none |

Open for docs-keeper: decision `0004` section 1 still says the principal carries the expiry (Q2).
