# QA sweep (final): cqrs-layout at 48e56cf

Stage: 5 Verify (qa-reviewer, before the final review). Full sweep of `src/provisioning` (285 handwritten files,
`_gen/` skipped), with every PyCharm warning in scope because the owner wants the IDE clean.
IDs: TR-2 (layers), TR-12 (schema, corrupt rows), TR-17 (REST), TR-22 to TR-24 (events and envelope), TR-9
(dependencies), FR-54 and TR-53 (registry answer). Read-only on code: only this report was written.

The earlier sweep, `ai/reviews/qa-cqrs-layout.md`, fixed R1 to R14. It filed most PyCharm type warnings and the
`str(object)` calls as false positives because `mypy --strict` passes. This sweep reverses that for the warnings a
small local change removes, since the owner sees them. What stays is a PyCharm limit, an intended design, or an
IDE setting.

## Tools

| Step | Command | Exit | Findings |
| --- | --- | --- | --- |
| 1 duplicate code | `make qa-sweep`: pylint R0801, 8 lines or more | 0 | 2 pairs (both false positives) |
| 2 dead code | `make qa-sweep`: vulture with the whitelist | 0 | 13 names (all triaged before) |
| 2b test-only use | vulture on `src/` and `tests/` | 3 | 9 names used only by tests (kept before) |
| 3 ruff QA rules | `ERA,PIE,RET,FAST,DTZ,C90`, ceiling 18 | 0 | 0 |
| 3b complexity watch | `ruff --select C901` with ceiling 15 | 1 | 1 at 16 |
| 3c mutable defaults | `ruff --select B006,B008,RUF012` | 0 | 0 |
| 4 mypy deprecated | `mypy` with `deprecated` | 0 | 0 |
| 5 PyCharm | `lint_files`, 8 batches, `min_severity` warning | ok | about 600 (see below) |
| 6 grep | `str(x[`, `str(value)`, `cast(`, `# type: ignore`, `except Exception` | ok | see findings |

PyCharm ran on the repo `.venv` (Python 3.14.8, uv), so its findings are not caused by a wrong interpreter.

## Summary

| Class | Count |
| --- | --- |
| Fix now | 20 findings (N1 to N20), about 75 PyCharm warnings and 2 PyCharm errors removed, all effort S |
| Later, with reason | 12 groups (L1 to L12), about 520 PyCharm findings; most are one IDE setting |
| Owner questions | 3 |

About 330 of the PyCharm findings are "Duplicated code fragment" against the stale image build context
`bin/kind-state/provisioning-image/context/src/` (question Q4 of the earlier sweep is still open; see L1).

## Fix now

### N1. Command job decoder coerces any JSON value to text

| Field | Value |
| --- | --- |
| file:line | `presentation/messaging/codec.py:282-289` (`_job_of`) |
| Tool | PyCharm "Type 'object' string value might not be useful" (7) |
| What | `str(doc["message_id"])` and six more on `dict[str, object]` values |
| Why | `str()` turns a number, list or null into text. Today the schema check guards it; if a schema rule is |
| | dropped, a wrong type becomes a valid ID silently instead of a rejected message (TR-24) |
| Fix | A typed accessor `_text(doc, name) -> str` that returns the value when `isinstance(value, str)` and |
| | raises `TypeError` otherwise. The caller (`codec.py:247`) already maps `TypeError` to `DOMAIN_INVALID` |
| Effort | S |

### N2. `str()` before the domain enum parsers

| Field | Value |
| --- | --- |
| file:line | `infrastructure/postgres/job_store.py:508`, `rows.py:62`, `rows.py:70`, `hosting_intake.py:417`, |
| | `read_store.py:258` |
| Tool | PyCharm "Type 'object' string value" (job_store, rows); grep `str(` on row values |
| What | `parse_command_kind(str(value))`, `parse_offline_reason(str(v))`, `parse_source_event_status(str(...))` |
| Why | `_parse` (`core/domain/enums/states.py:160`) already takes `object` and refuses a non-string. `str()` |
| | first turns a wrong column type into text, so a corrupt row is judged by its text, not its type (TR-12) |
| Fix | Drop each `str()`. Make `parse_offline_reason` (`states.py:145`) and `parse_source_event_status` |
| | (`core/application/intake/ports.py:51`) take `object` like their siblings (the second needs an |
| | `isinstance(text, str)` check). Type `command_id` in `job_store._kind` and `project_id` in |
| | `rows.offline_reasons_of` as `UUID`; that clears the f-string warnings at `job_store.py:510`, `rows.py:69`, |
| | `rows.py:72` |
| Effort | S |

### N3. Free-text column coerced in the source event outcome

| Field | Value |
| --- | --- |
| file:line | `infrastructure/postgres/read_store.py:259` |
| Tool | grep `str(row[` |
| What | `outcome_code=None if ... else str(row["outcome_code"])` |
| Why | Same coercion as N2 on a text column; a corrupt value passes as text |
| Fix | `isinstance(value, str)` or raise `TypeError`; the `except (DomainError, ValueError, TypeError)` at |
| | `read_store.py:266` already turns it into `CorruptRow` |
| Effort | S |

### N4. JSON Schema subset: `str(value)` on a keyword value

| Field | Value |
| --- | --- |
| file:line | `presentation/messaging/schema.py:170` |
| Tool | PyCharm "Type 'object' string value" |
| What | `{...}[str(value)]` after `value not in _TYPES` |
| Why | Membership in a set of strings does not narrow `object` for the reader or the IDE |
| Fix | `if not isinstance(value, str) or value not in _TYPES: raise UnsupportedSchema(...)`, then index by `value` |
| Effort | S |

### N5. AMQP header table: two PyCharm errors, one `type: ignore`, one cast

| Field | Value |
| --- | --- |
| file:line | `infrastructure/rabbitmq/dlq.py:463-468`; `infrastructure/rabbitmq/client.py:446` |
| Tool | PyCharm ERROR "Parameters to generic types must be types" (2); "Unnecessary cast"; grep `type: ignore` |
| What | `dict[str, FieldValue]` uses pamqp's `FieldValue`, a runtime union, not a type alias; `client.py` does |
| | the same conversion with `cast("FieldTable", headers)` |
| Why | Red errors in the IDE; one conversion written twice, once with a `type: ignore` |
| Fix | One helper in the rabbitmq package, `field_table(headers: Mapping[str, object]) -> FieldTable`, using |
| | pamqp's `FieldTable` alias (`dict[str, 'FieldValue']`) and one `cast`; both modules call it |
| Effort | S |

### N6. Dead-letter header readers do not narrow

| Field | Value |
| --- | --- |
| file:line | `infrastructure/rabbitmq/dlq.py:259-264` (`_event_key`), `dlq.py:322-327` (`times_taken`) |
| Tool | PyCharm "Unresolved attribute reference 'isprintable' for class 'object'" (2), "Expected type" (2) |
| What | `if not isinstance(value, str) or ...: return None` then `value.isprintable()`; a list comprehension |
| | with a walrus filter feeding `max` |
| Why | The IDE cannot follow the `or` guard or the walrus filter, so it shows four warnings on correct code |
| Fix | Split the guard into `if not isinstance(value, str): return None` and the rest; build `counts: list[int]` |
| | in a plain loop |
| Effort | S |

### N7. Reconciler sweep: ten warnings from one comprehension

| Field | Value |
| --- | --- |
| file:line | `core/application/reconcile/background/reconcile.py:349-383` |
| Tool | PyCharm "Expected type", "No overload of 'pop'/'get'" (10) |
| What | `{o.release_id for o in objects if ... and o.release_id is not None}` is `set[UUID \| None]` to the IDE; |
| | the type spreads to `keys`, `_deferrals` and `_given_up` |
| Fix | A helper `_workload_releases(objects) -> list[UUID]` with a plain loop, sorted; the rest is unchanged |
| Effort | S |

### N8. Deploy failure code returns an optional

| Field | Value |
| --- | --- |
| file:line | `core/application/deploys/background/deploy.py:394-397` |
| Tool | PyCharm "Expected type 'str', got 'str \| None'" |
| Fix | `if reason is not None and reason in _WORKLOAD_FAILURES: return reason` |
| Effort | S |

### N9. Invoker `_finish`: `outcome` possibly unbound

| Field | Value |
| --- | --- |
| file:line | `presentation/operations/invoke.py:539-566` |
| Tool | PyCharm "Local variable 'outcome' might be referenced before assignment" |
| Why | Correct today only because two `if` blocks cover every path; a later edit can break it (TR-17) |
| Fix | `outcome: Outcome \| None = None`; set it in the success `try`; then `if outcome is None:` build the problem |
| Effort | S |

### N10. Message key reads a property twice

| Field | Value |
| --- | --- |
| file:line | `presentation/messaging/consumers/messages.py:202-203` |
| Tool | PyCharm "No overload of '__add__' matches (str \| None)" |
| Fix | `message_id = delivery.message_id`, then test and use the local |
| Effort | S |

### N11. IPv4-mapped address read twice

| Field | Value |
| --- | --- |
| file:line | `infrastructure/config/net.py:154-155` |
| Tool | PyCharm "Expected type ... got 'IPv4Address \| None'" |
| Fix | `mapped = ip.ipv4_mapped` once, then `if mapped is not None: return mapped` |
| Effort | S |

### N12. Routing store: `live` and a reused `old`

| Field | Value |
| --- | --- |
| file:line | `infrastructure/postgres/routing_store.py:103-107`, `:163-172` |
| Tool | PyCharm "Expected type 'Release', got 'Release \| None'" (2), "Member 'None' has no attribute 'id'" |
| What | `live = loaded.get(...) or await _lock_release(...)` into a variable declared optional; `old` is reused |
| | for a second, different release at `:172` |
| Fix | `live = loaded.get(id)`, then `if live is None: live = await ...` in a non-optional local; rename the |
| | second `old` to `previous` |
| Effort | S |

### N13. `Path(provisioning.__file__)` is optional

| Field | Value |
| --- | --- |
| file:line | `hosts/api/wiring.py:218`, `:225`; `infrastructure/telemetry/sentry.py:120` |
| Tool | PyCharm "Expected type 'str \| PathLike[str]', got 'str \| None'" (3) |
| Why | `ModuleType.__file__` is `None` for a namespace package; the code would fail with a `TypeError` |
| Fix | `Path(__file__)` of the module itself with the right `parents[n]`, or `importlib.resources.files` |
| Effort | S |

### N14. Identity names re-exported through `config.api`

| Field | Value |
| --- | --- |
| file:line | `infrastructure/config/api.py:58-73` |
| Tool | PyCharm "Unused import statement" (2) |
| What | A re-export block left from moving the identity section to `config/identity.py`; |
| | `config/__init__.py:5-20` and `config/common.py:21` import identity names through `config.api` |
| Fix | Import them from `config.identity` directly; delete the block (TR-2) |
| Effort | S |

### N15. Registry answer check catches everything

| Field | Value |
| --- | --- |
| file:line | `core/application/intake/background/release_intake.py:299` |
| Tool | PyCharm "Too broad exception clause" |
| What | `except Exception` around `parse_repository_id` and `validate_subdomain` |
| Why | Both raise `DomainError` (`infrastructure/project/grpc_repository.py:58-61` catches only that). A bug |
| | here is recorded as `invalid_answer` instead of failing loudly (FR-54, TR-53) |
| Fix | `except DomainError:` |
| Effort | S |

### N16. Private methods that use no instance state

| Field | Value |
| --- | --- |
| file:line | `infrastructure/jenkins/build_system.py:370`, `:397`, `:411`, `:429`; `rabbitmq/client.py:344`; |
| | `rabbitmq/dlq.py:427`; `postgres/intake_store.py:294` |
| Tool | PyCharm "Method may be 'static'" (7) |
| Fix | Move each to a module-level function (the repo style elsewhere) |
| Effort | S |

### N17. `Subscription` reaches into `BrokerClient` private members

| Field | Value |
| --- | --- |
| file:line | `infrastructure/rabbitmq/client.py:523`, `:528`, `:540` |
| Tool | PyCharm "Access to a protected member" (3) |
| What | `self._client._logger`, `self._client._delay(...)`, `self._client._wait_connection()` |
| Fix | Pass the logger, the delay function and the connection waiter to `Subscription` at construction, or make |
| | them public methods of `BrokerClient` |
| Effort | S |

### N18. Shadowed names

| Field | Value |
| --- | --- |
| file:line | `hosts/operator/cli.py:198`, `:268`, `:284`; `hosts/operator/dlq_cli.py:232`; `hosts/api/signals.py:18`, |
| | `:25`; `jobs/background/dispatcher.py:130`; `tenants/background/bootstrap_loop.py:139`; |
| | `kubernetes/site_cluster.py:432` (`field`), `:785` and `kubernetes/bootstrap.py:164` (`annotations`) |
| Tool | PyCharm "Shadows name ... from outer scope" (15) |
| Why | Inner helpers take parameters named like the enclosing locals (`result`, `code`, `message`); a mixed-up |
| | name compiles and is wrong. `field` shadows `dataclasses.field` |
| Fix | Rename the inner names |
| Effort | S |

### N19. Unused parameters

| Field | Value |
| --- | --- |
| file:line | `hosts/api/signals.py:23` (`sig`); `hosts/build_worker/wiring.py:107` (`params`); |
| | `infrastructure/payment/grpc_entitlement.py:101` (`event_key`) |
| Tool | PyCharm "Parameter value is not used" (3) |
| Fix | `signals.py`: rename to `_sig`. The two port methods keep their names (protocol match) and say |
| | `del params` / `del event_key` with a short reason, as `config/telemetry.py:155` does with `port` |
| Effort | S |

### N20. Small cleanups

| Field | Value |
| --- | --- |
| file:line | `identity/verifier.py:246`, `telemetry/tracing.py:223`, `:388`; `hosts/operator/cli.py:127-132`, |
| | `dlq_cli.py:121-122`; `hosts/shared/observability.py:49`; `presentation/http/middleware.py:244` |
| Tool | grep `type: ignore`; PyCharm "Type 'object' string value" (2), "Remove redundant parentheses" (2) |
| What | Three `type: ignore[...]` without a reason (`.claude/rules/python-code.md` line 37 asks for one); |
| | CLI display helpers typed `object`; two redundant parentheses |
| Fix | Add the reason to each ignore; type the CLI helpers' parameter as the union the rows hold |
| | (`str \| int \| UUID \| datetime \| StrEnum \| None`); drop the parentheses |
| Effort | S |

## Later, with reason

### L1. Stale copy makes about 330 "Duplicated code fragment" findings

| Field | Value |
| --- | --- |
| Where | Almost every file; whole-file matches (for example `jobs/claims.py:1`, 225 lines) |
| Reason | The match is `bin/kind-state/provisioning-image/context/src/`, a git-ignored old image build context |
| Action | Owner: mark `bin/` Excluded in PyCharm or delete that folder (Q1). Then rerun; real short duplicates |
| | may show, for example the keyset paging check in `operations/queries/list_commands.py:61` and |
| | `list_releases.py:63` (same shape, different keys) |
| Effort | S (setting), M (any real duplicates found after) |

### L2. "SQL dialect is not configured" (about 25)

| Field | Value |
| --- | --- |
| Where | `hosts/devtools/catalog_platform.py`, `catalog/site_access.py`, `postgres/session_lock.py` |
| Reason | IDE setting, not code. Owner: set the project SQL dialect to PostgreSQL (Q1) |

### L3. "Package ... is not listed in the project requirements" (about 30)

| Field | Value |
| --- | --- |
| Where | Imports of `starlette`, `cryptography`, `aiohttp`, `pamqp`, `requests`, `mcp_types`; plus `contextvars` |
| | (standard library) and `opentelemetry` (a namespace of listed packages) |
| Reason | The owner ruled to keep transitive pins (Q3 of the earlier sweep). `contextvars` and `opentelemetry` are |
| | PyCharm false positives. Clearing the rest means new direct pins: a dependency change (TR-9, owner) |

### L4. PyCharm type narrowing where `mypy --strict` passes (about 60)

| Field | Value |
| --- | --- |
| Where | `presentation/http/app.py:160-171` (Starlette `add_middleware` ParamSpec, 15); |
| | `hosts/operator/__main__.py:65`, `:79` and `hosts/shared/process.py:65` (`os.environ.get` overloads); |
| | `NoReturn` with `while True` |
| | (`bootstrap/singleton.py:138`, `postgres/session_lock.py:115`, `:207`); protobuf message types |
| | (`payment/grpc_entitlement.py:109`, `:169`, `:176`, `project/grpc_repository.py:59-81`); |
| | `mcp/endpoint.py:93`; a protocol property (`consumers/consumer.py:232`, `:369`); narrowing after an |
| | assignment or inside a comprehension (`worker/wiring.py:226`, `config/identity.py:198`, `metrics.py:338`, |
| | `tracing.py:174`, `domain/errors.py:141`, `routing/background/recovery.py:226`, `site_access.py:302-423`, |
| | `catalog_platform.py:243-476`, `job_store.py:380`, `session_lock.py:222`, `:271`); `str \| bytes` from |
| | `urlsplit` (`jenkins/build_system.py:450-462`, `app.py:251`); `jwks.py:139-141` "unreachable" (PyJWT's |
| | base `from_jwk` is typed as raising); `hosting_intake.py:367` (`~` on a SQLAlchemy expression); |
| | `sentry.py:270`; `config/telemetry.py:158`, `:160`; `site_cluster.py:801`; `grpc/call.py:82`; |
| | `rabbitmq/client.py:319` |
| Reason | The code is correct and mypy proves it. A fix would bend correct code around one checker's limits. |
| | Q2 proposes the IDE side |

### L5. "Too broad exception clause" with a stated reason (9)

| Field | Value |
| --- | --- |
| Where | `release_intake.py:292`, `grpc/call.py:67`, `identity/jwks.py:137`, `telemetry/logger.py:337`, |
| | `telemetry/metrics.py:384`, `telemetry/sentry.py:220`, `:249`, `http/operational.py:74`, `:86`, `dlq.py:357` |
| Reason | Each is a fail-safe on purpose: fail closed (`jwks.py:137` refuses the key set), best effort (Sentry, |
| | metrics, details blob), or "not ready, reason hidden". Narrowing `jwks.py:137` or `grpc/call.py:67` |
| | could let an unexpected error escape instead of being refused; do it only with a test per error class |

### L6. `x is True` / `x is False` on JSON values (7)

| Field | Value |
| --- | --- |
| Where | `jenkins/build_system.py:230`, `:276`, `:321`; `project/access.py:238`; `messaging/schema.py:274`, `:276`; |
| | `routing/background/routing.py:351` (a `bool \| None` tri-state) |
| Reason | Intended: a JSON `1` must not count as `true`, and `None` is a third state. "Simplified" code is wrong |

### L7. "Method may be static" on protocol implementations (about 12)

| Field | Value |
| --- | --- |
| Where | `ports/metrics.py:49-55`, `shared/audit.py:107-110`, `hosts/shared/runtime.py:158`, `:163`, |
| | `deploys/background/deploy_executor.py:74`, `build_worker/wiring.py:107` |
| Reason | The signature is fixed by the port; null objects and the system clock need no state |

### L8. Phantom type parameter and `{kind!r}`

| Field | Value |
| --- | --- |
| Where | `shared/cqrs.py:24`, `:30` ("Type parameter 'R' is not used"); `cqrs.py:68` ("Type 'type' string value") |
| Reason | `R` ties a command or query to its result (TR-54). `kind` may not be a type at that point; `!r` is meant |

### L9. Access to the protected `_gen` package (3)

| Field | Value |
| --- | --- |
| Where | `payment/grpc_entitlement.py:33`, `project/grpc_access.py:28`, `project/grpc_repository.py:28` |
| Reason | Generated stubs live there by design (decision `0022` E3). Renaming touches the Makefile, ruff, |
| | vulture and import-linter settings (Q3) |

### L10. "HTTP links are not secure"

| Field | Value |
| --- | --- |
| Where | `kubernetes/site_cluster.py:185` |
| Reason | The in-cluster gateway URL on Kind; the check accepts `http` on purpose |

### L11. Complexity watch

| Field | Value |
| --- | --- |
| Where | `core/domain/rules/command_preconditions.py:26` `check_command_preconditions` at 16 (ceiling 18) |
| Reason | Within 2 of the ceiling. Real only when a planned change adds a command kind; split it then (TR-2) |

### L12. Unchanged from the earlier sweep

| Field | Value |
| --- | --- |
| pylint | `operations/admin.py:112` with `views.py:192`; `inbox_processor.py:41` with `config/build_worker.py:78`. |
| | Two views on purpose (decision `0023` G9); application may not import infrastructure config (TR-2) |
| vulture | `PROMOTION`, `ACCESS_SETUP`, `OBSERVATION` (`enums/states.py:39-42`, stored values in |
| | `src/migrations/00001_schema_and_enums.sql:35`) and `OwnerStatus.ACTIVE` (`hosting/ports.py:103`, built |
| | by value). Proposed whitelist lines: `_.PROMOTION`, `_.ACCESS_SETUP`, `_.OBSERVATION`, `_.ACTIVE` |
| test-only | `remembered`, `is_known_ready`, `event_count`, `tracing_enabled`, `Claim.expires_at`, and the four |
| | `BrokerClient` counters: kept, each test proves a rule the service relies on (earlier sweep table) |

## Tasks

All in the small change lane: existing TR or FR, no new rule. Each ends with `make py-fmt-check py-lint
py-typecheck py-layers py-test`, `make qa-sweep`, and PyCharm `lint_files` on the touched files showing none of the
listed warnings.

1. **Typed reads in messaging** (N1, N4, N6, N10; TR-22, TR-24). Tests first: a command job whose `message_id` is a
   number is rejected with `DOMAIN_INVALID` (`tests/unit/presentation/messaging/test_codec.py`); a schema node with a
   non-string `type` raises `UnsupportedSchema` (`test_schema.py`).
2. **No coercion of stored values** (N2, N3; TR-12). Tests first: a command row with an integer `kind`, a hosting row
   with a non-string reason and a source event row with an integer `outcome_code` each raise `CorruptRow`.
3. **One AMQP header table** (N5, N17; TR-22, TR-23). No `type: ignore` left in `dlq.py`; the dead-letter and
   publish tests pass (`tests/unit/infrastructure/rabbitmq/`, `make py-test-messaging`).
4. **Local narrowing fixes** (N7, N8, N9, N11, N12, N13; TR-2, TR-17). Behaviour unchanged; existing reconcile,
   deploy, invoke, config and routing store tests pass.
5. **Small cleanups** (N14, N15, N16, N18, N19, N20; TR-2, FR-54, TR-53). Test first for N15: a registry whose
   answer check raises a non-domain error is not recorded as `invalid_answer`.

## Owner questions

1. **Q1. PyCharm settings (your IDE, no repo change).** Still open from the earlier sweep. Recommendation: mark
   `bin/` Excluded (or delete `bin/kind-state/provisioning-image/context/`) and set the SQL dialect to PostgreSQL.
   That removes about 355 of the 600 findings (L1, L2).
2. **Q2. Inspections that disagree with intended code** (L4 to L8, L10). Recommendation: keep the code; lower these
   inspections in your IDE profile ("Too broad exception clause", "Expression can be simplified", "Method may be
   static", "Type parameter is not used") and rely on `mypy --strict` for types. The alternative, per-line
   `# noinspection` comments, is a suppression the repo rules do not list.
3. **Q3. The `_gen` package name** (L9). Recommendation: keep it (decision `0022` E3); three warnings only.

## Not checked

- Duplicates inside `src/` hidden behind the stale-copy matches (L1); rerun the PyCharm part after Q1.
- Duplicates shorter than 8 lines, and logic that is the same in meaning but not in text.
- `tests/` itself was not swept for duplicates or dead helpers.
- PyCharm "weak warning" items were triaged by group, not each one opened; every group names its locations.
