# QA cleanup: duplicates and PyCharm warnings, cqrs-layout

Stage: 4 Implement (QA cleanup after `ai/reviews/qa-cqrs-layout.md` and `qa-cqrs-layout-final.md`). IDs: TR-2, TR-3,
TR-6, TR-12, TR-17, TR-19, TR-35, TR-40, TR-47, TR-54 and the FRs each commit names. Behaviour is unchanged: every
merge keeps the same checks, messages and order, and the moved test doubles are the same code (checked by AST).

## How it was measured

- PyCharm `lint_files` (the same inspections as `get_file_problems`, in batches) on all 286 files of
  `src/provisioning` (not `_gen/`) and all 336 files of `tests/`, then again on every changed file and on the files
  that held the other copy.
- The first batch still matched the stale copy in `bin/kind-state/provisioning-image/context/src/`: the module file
  listed that folder as a source folder. The lead removed that line. Afterwards four whole-file matches remain:
  `ports/build_system.py` (60 lines), `ports/site_cluster.py` (70), `shared/errors.py` (123) and `domain/errors.py`
  (90). Each is a match against its own old copy in `bin/` (the files are identical to it or nearly so) and has no
  other copy in `src/` or `tests/`. They are ignored here; a project reload should clear them.
- PyCharm does not name the other copy of a fragment, so pylint R0801 (6 lines in `src/`, 8 lines in `tests/`) and an
  AST comparison found the partners.

## Counts before and after

`src/provisioning`, PyCharm:

| Inspection | Before | After | Note |
| --- | --- | --- | --- |
| Duplicated code fragment, inside `src/` | 13 | 7 | 6 merged; 7 kept with reason (below) |
| Duplicated code fragment, against `bin/` | 4 | 4 | Stale index, not code |
| No overload of `fullmatch` (`config/telemetry.py`) | 2 | 0 | DSN user and password read once |
| Every other warning | 166 | 166 | All in the "later" groups L2 to L10 of the final report |

pylint R0801 on `src/` at 6 lines: 9 pairs before, 3 after. `make qa-sweep` (8 lines) is unchanged at the 2 pairs
ruled in L12.

`tests/`, PyCharm: no "Duplicated code fragment" in any test file, before or after. Removed: 2 "Expected type `str`,
got `str | None`" (`tests/conftest.py`), 10 "Remove redundant parentheses", 1 redundant regex escape, 1 "Member `None`
has no attribute `sockets`" (`test_session_lock.py`). pylint R0801 on `tests/` at 8 lines: 74 pairs before, 47
after. The rest of the test warnings are listed under "Left".

## What was merged

- `020f263`, `66becd6`: `test_gate`, `test_acceptance`, `test_decide`, `test_reads`, `test_error_table`,
  `jobs/test_ports`. `tests/unit/core/application/operations/fakes.py`: fixed values, builders, recording doubles,
  `OperationsFixture`, `TYPED_ERRORS`, `assert_internal`. About 1,060 lines less. Same test IDs before and after.
- `a859e41`: `test_release`, `test_build`, `test_command_preconditions`, `test_narrowed_failure`, `test_outcome`,
  `test_replaced_candidate`. `tests/unit/core/domain/builders.py`: the IDs, `image_digest`, `base_release`, `valid_row`.
  Six spellings of one rule. Same test IDs.
- `183388a`: `tests/integration/policy/_kube.py`, `tests/integration/stack/_stack.py`.
  `tests/integration/kind_safety.py`: the local Kind safety check run before every kubectl call (CLAUDE.md section 0).
- `e75820a`: `hosts/operator/cli.py` re-drive and sub-domain backfill. `_operator_audit` builds the operator audit
  event.
- `1302bec`: `domain/rules/outcome.py` `restore_verified`, `route_removed_verified`. `_route_restored`: a promote ends
  failed, a rollback succeeds.
- `c5f9392`: `catalog/access_setup.py` `RuntimeEndpoint`, `catalog/site_access.py` `CatalogConnection`. `check_endpoint`
  (host, port, database, sslmode).
- `76db762`: `config/net.py` (2), `config/telemetry.py` (2). `split_url`: parse and refuse a bad port.
- `a1fda02`: `config/api.py`, `config/worker.py`. `read_entitlement_mode`.
- `409df81`: `hosts/devtools/simulate.py`: the simulator publishes through `publish_encoded`, as the usage
  publisher does.
- `17fd342`: `deploys/background/deploy.py`, `routing/background/recovery.py`. `DELETE_ERRORS` of the cluster port.
- `4620606`: tests: the narrowing and parentheses fixes above.

`1905ace` tried a local variable in `presentation/http/app.py` route listing; PyCharm still sees Starlette's
`Route.methods` as partly untyped, so `0362479` reverts it (the file is as before).

## Left, with reason

Duplicates that look alike but change for different reasons:

- `operations/commands/deploy_release.py`, `promote_release.py`, `rollback_release.py` (whole files): One intent, one
  handler, one module (decision `0025`); each differs in its intent, action and audit text.
- `shared/cqrs.py` `CommandBus` and `QueryBus`: Two typed facades over `_Registry` (decision `0025` CQ10), as ruled
  before.
- `config/build_worker.py` `InboxSettings` and `intake/background/inbox_processor.py`: Application may not import
  infrastructure config (TR-2); L12.
- `operations/admin.py` and `operations/views.py`: Two views on purpose, decision `0023` G9; L12.
- `config/worker.py` `SiteSettingsConfig` and `kubernetes/site_cluster.py` `SiteClusterSettings`: The read settings hold
  `timedelta`, `Path` and `Secret`; the adapter holds seconds and plain values.
- `builds/background/poll_builds.py` and `start_build.py`: Both construct a `BuildDispatcher` from their own arguments;
  argument passing, no rule.
- `config/api.py`, `config/identity.py`, `config/common.py`, `rabbitmq/config.py` URL checks: Each refuses a different
  thing (port 0, user info, scheme); one helper would change what is accepted.
- `tests/integration/catalog/conftest.py` and `kubernetes/conftest.py` fixtures: Sibling conftests on purpose: a shared
  parent conftest would load the Kubernetes client for every integration suite, and that client fixes its default
  kubeconfig path at import.
- `tests/integration/test_schema_000NN.py` and `_catalog_00011.py` pairs: Each migration test pins the schema of its own
  version; a shared helper would change an old test when a new one changes.
- Domain `_request_for` and `_command_in` (`test_release`, `test_replaced_candidate`, `test_command`): They read
  different hosting kind sets (one includes `sync_domains`).
- `shared/test_errors.py` `_CLASSES` and `TYPED_ERRORS`: Application errors only, typed `ApplicationError`;
  `TYPED_ERRORS` also holds domain errors.
- Smaller test pairs (9 to 29 lines: hosting job and routing doubles, gRPC stubs, live Kind suites, e2e helpers):
  Doubles shaped for one suite each, or Kind-only suites that cannot be run here to prove a merge.

Other warnings left:

- `src/`: the 166 warnings are the groups L2 to L10 of `qa-cqrs-layout-final.md` (SQL dialect setting, transitive
  packages, PyCharm type narrowing where `mypy --strict` passes, broad except with a stated reason, `is True` on JSON,
  methods fixed by a port, the phantom type parameter, `_gen`, the Kind HTTP URL). `app.py:185` joins L4.
- `tests/`: "SQL dialect is not configured" (an IDE setting; 608 in the integration schema and store batch alone),
  unused parameters and "may be static" on fakes that implement a port, deliberate `cast` of an invalid value to test a
  refusal, the e2e "Expected `tuple[str, ...]`" on `asyncio.to_thread(kube_json, ...)` (PyCharm does not follow its
  ParamSpec; `kube_json` takes `*args: str`), "Access to a protected member" on `tests/unit/_buses.py` and similar
  helpers, and about 25 shadowed names, mostly in Kind-only e2e and live suites. Renaming those is safe but cannot be
  run here; do it when a suite is next touched.

## Gates

- `make py-fmt-check py-lint py-typecheck py-layers`: passed (631 files formatted, ruff clean, mypy 625 files, 16
  contracts kept).
- `make py-test`: passed, 7142 tests.
- `make qa-sweep`: ran; 2 pylint pairs and 13 vulture names, all triaged before (L12).
- `make spec-check`: 0 problems.
- `make py-test-integration`, `make py-test-messaging`: not run: `free -m` showed 3066 MB and then 3955 MB free, under 4
  GB.

The integration and messaging suites cover `simulate.py` (messaging), `test_session_lock.py` and the store paths.
The changed integration and e2e files that cannot run here were checked by AST equality (parentheses) or import
and collection (`kind_safety.py`, `_kube.py`, `_stack.py`). Run both suites when memory allows.
