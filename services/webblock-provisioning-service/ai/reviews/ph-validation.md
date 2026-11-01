# Independent validation of phase P-H (H-V)

Validator: task H-V (independent; no implementation file changed). Base: `python` at `5257357`.
Date of the runs: 11 and 12 October 2026. Machine used alone; Kind stack `kind-webblock-proto` on
`https://127.0.0.1:42499` (context checked before every cluster call; no `~/.kube`, no real cluster, no
`.env` value read or printed; the owner's containers untouched). The stack was left down.

Requirements touched: PR-01 (identity, JWT), PR-03/PR-04 (claims, serialization, replicas), PR-11
(recovery, bootstrap singleton), PR-12 (failure codes, no leak), and the section 4 quality gates.

## Verdict: approve with findings

0 critical, 1 high, 3 medium, 6 low. The high finding (F-1) is a required suite that fails from nothing on
the merged tip, caused by H5c; it is a small, contained fix, but "port done" should not be declared
before `make proto-e2e` passes in one run on the merged tip.

## 1. Findings

### High

**F-1. `make proto-e2e` fails from nothing at `5257357`: the bootstrap exits on a 20 s database stall and nothing restarts it (H5c).**
Evidence: `/tmp/claude-1000/hv/e2e.log`, bootstrap log, `docker events`.
- Full `make proto-e2e` on a fresh stack: the first 8 files and `test_qa_database_paused.py` pass (21%), then
  `test_qa_spoofed_release_header`, `test_qa_worker_killed`, `test_recovery_deploy_margin` and
  `test_recovery_reconciler` fail, and the rest cascade (I stopped the run).
- Cause: `test_qa_database_paused` pauses `webblock-provisioning-db` (docker events: pause 23:33:28, unpause
  23:33:48). The bootstrap logs `bootstrap singleton lock lost; its rounds stop and the process exits
  (error=LockLost)` at 16:33:48.54 UTC (the unpause) and stops. `run-provisioning.sh` has no supervisor, so
  from then on no tenant is made ready: the worker logs `deploy command ... retried later: tenant_not_ready`
  every 10 s, and `scripts/prototype/run-provisioning.sh status` shows `bootstrap stopped`.
- After `start-process bootstrap` and `proto-reset-sites`, the same suite with only that test deselected:
  **54 passed** (1649 s). So the four failures are collateral of the dead bootstrap, not separate defects.
- The lead's H5c proof ran the five single-worker files and `proto-e2e-replicas`, not the full `proto-e2e`
  (handoff, 12 October), which is why this was not seen. `docs/runbooks/replicas.md` says "its supervisor
  restarts it"; the prototype has none.
- Fix options: restart a stopped bootstrap in `run-provisioning.sh` (a supervise loop), or have the
  singleton retry instead of exiting on a transient stall, and run the whole `proto-e2e` once.

### Medium

**F-2. The exit-on-lock-loss design widens BT's still-open window.** Evidence: F-1 plus BT report section 5.
`FreshTenants` marks are per-process memory; a restart between a namespace's creation and `access_setup`
leaves `runtime_secret_missing` for an operator (decision `0014` section 24, documented as open). Before
H5c the bootstrap restarted only on a crash or deploy; now any database stall longer than the statement
timeout (a failover, as with CloudNativePG, or a 20 s pause) stops it. The takeover/standby behaviour is
correct (section 3, H5c); the cost is more restarts. For the owner's attention with P-P (supervisor,
`replicas: 1`, `Recreate`).

**F-3. The coverage and freshness gates' thresholds are not pinned by any unit test.** Evidence: planted
defects, section 2. `CORE_MINIMUM = 80.0` to `8.0`, `TOTAL_MINIMUM = 70.0` to `7.0`, the verdict forced to
`passed`, and `vuln.py` `MAX_AGE_DAYS = 14` to `14000` all pass `tests/unit/scripts/` (18 and 23 tests).
The gates do work at run time (I set the minimums to 99/99.5 and `make py-cover` printed `FAILED` three
times and exited 2), so this is a missing regression test, not a broken gate. A weakening of the section 4
numbers would not be caught.

**F-4. The headline demo failed on its first run after the e2e: the shop answered 200 with 0 product cards.**
Evidence: `/tmp/claude-1000/hv/demo.log` (`test_the_clothing_shop_from_commit_to_live`: `0 cards (status 200)`,
expected 12). The immediate rerun passed 2 of 2. It is not the catalog-outage path that L-5 fixed (that now
answers 503); the query succeeded and returned no rows. The root cause was not established (candidates: the
seed step racing the site role, or a catalog credential rotation by the bootstrap). One failure in two first
runs here; W6-V recorded one failure (memory) in six.

### Low

- **F-5. `ssl.create_default_context()` honours `SSL_CERT_FILE` and `SSL_CERT_DIR`.** `0006` section 5 and
  `docs/runbooks/identity.md` say the JWKS fetch ignores trust variables of the environment. `trust_env=False`
  does that for httpx2, but the verifier builds its context with `create_default_context()`
  (`verifier.py:92`), and I made a self-signed local issuer trusted through `SSL_CERT_FILE` alone (it is how I
  ran the production-API probe). Needs control of the process environment, so low; correct the wording or load
  the roots explicitly. (Proxy variables are ignored: `HTTPS_PROXY` pointed at a dead address did not matter.)
- **F-6. The API process imports the Jenkins client and the build store.** `hosts/runtime.py` imports
  `JenkinsBuildSystem`, and `infrastructure/postgres/__init__.py` imports `PgBuildStore`, so
  `provisioning.infrastructure.jenkins.build_system` and `...postgres.build_store` are in the API's
  `sys.modules`. The API holds no token or login (its config refuses all `JENKINS_*` and `PG_BUILD_*` keys by
  name; planted removal caught), but the code is linked.
- **F-7. Two claim guards are redundant, so no test pins them.** Removing `claim_number == dispatch.claim_number`
  from the claim UPDATE, or `claim_number == claim.claim_number` from the extend UPDATE, passes
  `test_claim_race.py` (row lock and `claimed_by_worker` already protect). The guard that matters, the
  dispatch row `FOR UPDATE`, is pinned (planted, caught). Matches H5b's own open note.
- **F-8. `py-age` prints two stale waivers** (`aio-pika 10.1.0`, `wrapt 2.5.0`: "no longer needed; remove it")
  and still exits 0. The 14 waivers expire 2026-10-19.
- **F-9. `.claude/ARCHITECTURE.md` section 10 is stale in two rows:** "More than one replica of each process:
  Not run" (H5b ran it, 9 passed here) and "Node pressure, quota, unschedulable: precise codes proposed" (H5a
  built `insufficient_resources` and `quota_exceeded`). W6-V L-2 (two `goose Down` sections in `00012` and
  `00013`) and L-4 (fence admission checks form, not growth) stay as they were, by decision.
- **F-10. `make image-scan` cannot build the sample site on this machine** (Docker 29.8.2 has no buildx, and
  `COPY --chmod` needs BuildKit); I used the documented path (scan the image Jenkins built, from the registry).
  "No Provisioning service image exists yet" is printed, so the brief's "the service image has no High or
  Critical" is vacuous today.

## 2. Gates and suites, from nothing

| Command | Result |
| --- | --- |
| `make kind-tools py-tools`, `uv sync` | ok |
| `make py-fmt-check py-lint py-typecheck py-layers py-test py-vuln py-age` | exit 0. 459 files formatted; ruff passed; mypy strict, 455 source files; import-linter 7 kept, 0 broken; **unit 5269 passed**; `py-vuln`: 26219 advisories (newest 2026-10-11), canary found, 84 packages, no known vulnerability; `py-age`: 84 pins + 8 tool pins, 14 waived, 0 failures |
| `make image-scan` (built path) | exit 1: Docker lacks buildx (F-10) |
| `make image-scan IMAGE_SCAN_REFS=...` (registry digest of the image Jenkins built) | exit 2, as expected: **12 High, 17 Medium, 8 Low, 1 Unknown, 0 Critical**, no exception. High: libcrypto3/libssl3 3.5.8-r0 (CVE-2026-54873, -84782, -84784, -72897), libexpat 2.8.4-r0 (CVE-2026-93990, fix 2.8.5-r0), tiff 4.7.1-r0 (CVE-2026-4775), zlib 1.3.2-r0 (CVE-2026-85091), pcre2 10.48-r0 (CVE-2026-103111). H1 reported 13; the database moved. Nothing else was scanned (no service image) |
| `PY_COVER=1 make py-test-integration` | **387 passed** (203 deselected), real PostgreSQL 18 via Testcontainers, no Kind up |
| `PY_COVER=1 make py-test-messaging` | **20 passed**, real RabbitMQ |
| `make proto-up` | exit 0, 445 s |
| `make proto-run`, `make proto-smoke` | ok; **smoke 15 of 15** |
| `PY_COVER=1 make proto-test-kind` | **187 passed** (235 s) |
| `PY_COVER=1 make py-test-catalog-live`, `py-test-jenkins` | **9 passed**, **7 passed** |
| `make py-cover` | exit 0. **domain 99.4%** (min 80), **application 95.6%** (min 80), **total 92.4%** of 16209 statements (min 70) |
| `make proto-e2e` (run 1, from nothing) | **fails**, see F-1 (8 files and the database-paused test pass, then 4 failures; aborted) |
| `proto-e2e` run 2 (bootstrap restarted, `proto-reset-sites`, `test_qa_database_paused` deselected) | **54 passed**, 1 deselected (55 collected), 27 min 30 s |
| `make proto-demo` | run 1: 1 failed, 1 passed (F-4); run 2: **2 passed** (56 s) |
| `make proto-stop`, `proto-run-replicas`, `make proto-e2e-replicas` | **9 passed** (6 min 22 s) |
| `make proto-stop proto-down` | exit 0; `docker ps` shows only `boring_tu` and `mcp-excalidraw-canvas` |

Skipped, with reasons: the `make image-scan` build path (F-10); the full `proto-e2e` in one uninterrupted run
(F-1: split in two, never counted as one pass); the Go reference gates (the Go code is out of scope, and the
lead recorded its contract tests as failing since before H5a); production-only and external rows of section 10
(OD-01, OD-04, OD-05, OD-60, OD-61, OD-63, OD-70); no GKE/DOKS. A real Kubernetes restart of a crashed
bootstrap was not run.

## 3. Planted defects

Every plant was a temporary one-shot edit by `/tmp/claude-1000/hv/plant.py`, which runs the named tests, then
runs `git checkout` of the file and prints `git status --short`. **After every plant, `git status` was clean.**

| Task | Plant | Result |
| --- | --- | --- |
| H1 | `age.py` `days >= min_days` to `>= -1` | caught (`test_a_young_pin_without_a_waiver_fails`) |
| H1 | `image_scan.py` `FAILING` loses `"High"` | caught |
| H1 | `cover.py` `CORE_MINIMUM 80 to 8`; `TOTAL_MINIMUM 70 to 7`; verdict forced to `passed` | **not caught** (F-3) |
| H1 | `vuln.py` `MAX_AGE_DAYS 14 to 14000` | **not caught** (F-3) |
| H1 | live: minimums 99 / 99.5 | `make py-cover` exit 2, three `FAILED` lines (the gate works) |
| H2 | `refuse_in_production` always returns False | caught |
| H2 | `stand_ins._prototype_only` never refuses | caught |
| H2 | consumer kind gate `if not enabled(kind)` to `if False` (production would run, not refuse, a planted command) | caught |
| H2 | illegal import of `infrastructure.standins` from `hosts/api/wiring.py` | `make py-layers`: contract broken, 1 broken |
| H2 | `JENKINS_API_TOKEN` removed from the API's refused keys | caught |
| H2 | hint loop reconnect replaced by `SystemExit` | caught |
| H3 | `aud` matched as a substring (`want in value`) | caught |
| H3 | `follow_redirects=True` | caught |
| H3 | `trust_env=True` | caught |
| H3 | issuer compared by `startswith` | caught |
| H3 | keys unavailable answered 401 instead of 503 | caught |
| H4 | exception `value` (message) copied into the Sentry event | caught |
| H4 | `tags` copied without the allow-list | caught |
| H4 | `bounded()` accepts any string | caught |
| H4 | `kubernetes_asyncio` removed from the quiet loggers | caught |
| H5a | catalogue entry `crash_loop` removed | caught |
| H5a | a failure code literal that the catalogue lacks passed to a write path | caught |
| H5a | `failure_retryable` emitted without a code | caught |
| H5a | `image_pull_failed` retryable flipped | caught |
| H5a | ready-endpoint rule `and` to `or` | caught |
| H5b | engine without `idle_in_transaction_session_timeout` | caught (integration) |
| H5b | cluster fence accepts a higher stored number | caught |
| H5b | SQL fence `claim_number` comparison disabled | caught |
| H5c | dispatch row `FOR UPDATE` removed | caught (`test_claim_race.py`) |
| H5c | claim UPDATE `claim_number` guard; extend UPDATE `claim_number` guard | **not caught** (F-7) |
| H5c | singleton runs rounds without waiting for the lock | caught |
| H5c | a lost lock swallowed instead of raised | caught |
| BT | `on_created` never called; called after the later steps | both caught |
| Own | metric label built from `str(release_id)` in `build_attempt_finished` (a path outside the allow-list tests' usual inputs) | caught |
| Own | `validate_requester(sub)` removed (control characters in `sub`) | caught |
| Own | `aud` substring (also run live, below) | caught; live: 401 |
| Own | live Sentry bypass attempt: an exception whose message, note, cause and group text carry a secret, logged through `logging.error(exc_info=True)` with `extra`, plus `report_active` | secret in no envelope (3 events) |

## 4. Each P-H deliverable against its decision

**H2 (`0016` section 6).**
- Production refuses every stand-in by name. Live probe of all four hosts with `PROVISIONING_PROFILE=production`
  and each key set: API refuses `AUTH_DEV_TOKENS_FILE`, `ENTITLEMENT_MODE/FILE`, `PROJECT_ACCESS_MODE/FILE`,
  `IDENTITY_MODE` (must be `jwt`), `REPOSITORY_MODE`, `PROTOTYPE_REPOSITORIES_FILE`; the worker refuses
  `ENTITLEMENT_*` and `HOSTING_ENTITLEMENT_MODE`; the build worker refuses `REPOSITORY_MODE` and
  `PROTOTYPE_REPOSITORIES_FILE`; the bootstrap refuses the production profile outright (OD-46).
- The production worker refuses (fails `operation_unavailable`) rather than drops a planted command:
  `test_a_planted_command_is_refused_in_production_never_dropped`, plant caught.
- API environment: no `JENKINS_*` or `PG_BUILD_*` key (refused by name; the stack's `api.env` holds none).
  Code: F-6.
- `hosts/stand_ins.py` is the only importer of `infrastructure.standins` (grep of `src/`, the protected-module
  import contract, and the layer plant above).
- Lost hint, live (`/tmp/claude-1000/hv`, temporary e2e test, deleted): after attempt 1 failed and attempt 2 was
  pending, I SIGKILLed the build worker, called `build_release` (202, hint to nobody), restarted the worker:
  attempt 2 started 29.8 s after the hint, by the grace scan.

**H3 (`0006` section 5).** All 52 verifier tests plus transport tests walked against the list; 5 plants caught.
Live, with the production profile and `IDENTITY_MODE=jwt` against a local https JWKS and a real RS256 token:
no token 401; valid token read 404 (project access closed); deploy, promote and rollback each 503
`operation_unavailable`; `aud` holding the value only as a substring 401; `aud` list holding it passes (404);
issuer by prefix 401; scope by substring 403; expired 401. JWKS answering 302: not followed (one hit, 503
`identity_unavailable`); JWKS port closed: 503, not 401; `HTTPS_PROXY` to a dead port ignored. See F-5.

**H4 (`0006` section 7 amendment).** `/metrics` on the public listener: 401 without a token, **404 with a valid
token** (also `/healthz`, `/readyz`); ops listener: 404 for `/debug/pprof`. After the full e2e, ports
18081-18084: 288, 191, 115 and 71 lines, **0 UUIDs, 0 32-hex strings, 0 `sha256`**; label names seen are only
`failure_code, generation, kind, le, method, outcome, result, round, route, status, status_class`. Sentry
`scrub_event` read in full (allow-list rebuild; messages, locals, request, tags beyond three, breadcrumbs dropped);
my bypass attempt failed. `LOG_LEVEL=debug` on the bootstrap, live: 1 debug line total (`asyncio`), 0 lines from
`kubernetes_asyncio` or `urllib3`, 0 tenant Secret values in any log or in Loki.

**H5a.** The completeness test fails both ways (a catalogue entry removed: written-but-unlisted; a code no
longer written: listed-but-unwritten). `failure_retryable` is set by one function in `presentation/ops/wire.py`,
shared by REST and MCP, and only with a code (plant caught). No condition message reaches a row, log or
response: the secret scans in section 6 found nothing. The ready-endpoint rule (503 with the candidate's header
counts as `dependency_unavailable` only when its Service has a ready EndpointSlice) is live in the metrics:
`provisioning_commands_finished_total{failure_code="dependency_unavailable"}` was counted in the e2e.

**BT.** `ensure_tenant` calls `on_created` synchronously right after the namespace apply succeeds, before any
later step (both plants caught). `test_bootstrap_namespace_rebuilt` passed in the e2e (2 tests, twice). The two
documented open cases are still open and unchanged (cancellation during the namespace apply; restart between
creation and `access_setup`, now more exposed, F-2).

**H5c.** Independently reproduced: a second bootstrap (`start-process bootstrap-2`) logs `bootstrap standby:
another bootstrap holds the lock` and reports `provisioning_bootstrap_standby 1.0` while the holder reports
`0.0` and 336 completed rounds; `kill-process bootstrap`: within 4 s the standby reported `0.0` and logged
`bootstrap holds the singleton lock; its rounds start`. A lost lock (`pg_terminate_backend` of the lock session
from the bootstrap's own login): rounds stop, the process stops (`bootstrap stopped with an error`), lock rows 0.
Claim-race tests: one guard (the dispatch row lock) is pinned; two redundant ones are not (F-7). The lead's proof
was incomplete (F-1).

**H5b.** `proto-e2e-replicas` 9 passed on a fresh replica stack: cross-API serialization, no duplicate work,
stale worker fenced (SIGSTOP past its lease, takeover, resumed worker refused), two build workers, two
reconcilers, bootstrap standby. The idle-in-transaction timeout is pinned (plant caught) and the fence plants
(SQL and cluster) are caught. The documented reason for a single bootstrap is now superseded by the singleton lock.

## 5. ARCHITECTURE.md section 10

Rows marked built and their results this run:
- identity: `tests/unit/infrastructure/identity/` (unit 5269 passed) and the live production-API probe above;
- the e2e rows: 54 passed in run 2 (api_surface, scenarios_mcp, intake, build, promote, audit, usage event,
  dlq re-drive, operator, edge cases, flows, and the `faults/` files), plus `test_qa_database_paused` passed in
  run 1; the replicas row: 9 passed;
- Kind rows (bootstrap scope, worker RBAC, tenant writer, site policies, route shape, routed release, tenant
  network, probe live, inventory live, Loki): `proto-test-kind` 187 passed, smoke 15 of 15;
- store and messaging rows: integration 387, messaging 20; live catalog 9; live Jenkins 7.

Open by design (listed, not exercised): Project's access API (OD-01), Payment's entitlement and events (OD-04),
real issuer and scopes (OD-05), "never lower" fence admission (L-4), Docker Hub pull limits (OD-60, OD-61),
restored-database detection (OD-63), the dead-letter policy (OD-70), the Payment-restore ordering in production,
the PR-07 isolation proof (OD-37, OD-62). F-9 lists the two stale rows.

## 6. Database, secrets, ownership

- **No migration in P-H.** `git diff cade048..HEAD -- src/migrations` is empty; no `.sql` file changed; the only
  `GRANT/ALTER ROLE` hit in the diff is prose in a runbook. `.claude/PLATFORM_DB_DESIGN.md` is unchanged since
  `42cef92` (revision 19, no P-H commit touches it). Grants unchanged.
- **Secrets.** After the full e2e and demo: 38 secret values (env files, token file) and 83 tenant Secret values
  searched in 26 log files (about 4.5 MB) and in Loki (`{job="webblock-provisioning"}`): **0 hits**. The three
  hits for token "subject" names (`prototype-operator`, `-tenant`, `-reader`) are requester names, not tokens.
  Patterns for `Bearer`, URLs with credentials, private keys and `SCRAM-SHA-256$`: 0 in logs, 0 in Loki. Sentry
  coverage: `test_sentry.py` plants secrets in message, notes, cause, locals, request, extra, tags and
  breadcrumbs; my probe added `logging` arguments and an `ExceptionGroup`.
- **File ownership** (`git diff --name-only <merge>^1 <merge>` for the ten merges). The pairs that ran in parallel:
  H4 with H5a share no file; H5c with BT share none; H5b with BT share none. H5b with H4-FU (`hosts/runtime.py`)
  share one file (H5b was stacked on H4's head by the lead, so this is sequential in effect). Other overlaps
  (H2/H3/H4 in `hosts/api/*` and `config/api.py`; H2/H5b/H5c in `run-provisioning.sh`; H5a/BT in
  `ports/site_cluster.py`, a lead-owned file) are sequential merges.

## 7. The W6-V findings

- **M-1** (`py-cover` could not pass): fixed. It combines six recorded suites and passes: 99.4 / 95.6 / 92.4.
- **M-2** (repeated runs exhaust the Kind node): fixed in this run: one stack held one e2e (two parts), three
  demo runs and the replica suite without a memory failure; `proto-reset-sites` runs before each.
- **L-1** (stale architecture text): the "eight tools" and custom-domain text are fixed; two section 10 rows are
  stale (F-9).
- **L-2** (`goose Down` in `00012`, `00013`): unchanged; migrations are immutable.
- **L-3** (API held the Jenkins token and build login): fixed (H2), see F-6 for the linked code.
- **L-4** (fence admission checks form only): unchanged by decision.
- **L-5** (sample shop 200 on catalog outage): fixed (503 and `Retry-After` in `outage.ts`); but see F-4.

## 8. Blockers and commands

No blocker for the report. F-1 should be fixed and the full `make proto-e2e` shown passing in one run before
"port done". Temporary files used (outside the repository): `/tmp/claude-1000/hv/` (plant helper, probes,
logs). The temporary e2e test I added was deleted; `git status` shows only this report.

## 9. Recheck of corrections

Base: `python` at `2846c70` (HF-B `b49d429`, HF-A `45d7753`, decisions `7be4e76` and `2846c70`), fresh stack,
machine held alone, context `kind-webblock-proto` on `https://127.0.0.1:35673` checked first. The stack was
left down (`docker ps`: only `boring_tu` and `mcp-excalidraw-canvas`).

### Updated verdict: approve (the P-H findings F-1, F-3, F-5, F-6, F-8 are corrected; the open items are medium or low)

### Status of each finding

| Finding | New status | Evidence |
| --- | --- | --- |
| F-1 (high) `proto-e2e` fails from nothing | **Fixed** | `make proto-up proto-run proto-smoke` (smoke 15 of 15), then `make proto-e2e` in one uninterrupted run, nothing deselected: **55 passed in 29 min 33 s, make exit 0**. `test_qa_database_paused` is in that run; the bootstrap log shows `lock uncertain` at 19:35:53 and `confirmed on the same session; its rounds resume` at 19:35:59, no exit. Replica run: `proto-e2e-replicas` **9 passed** (6 min 19 s) |
| F-2 (medium) restarts widen BT's window | **Open, narrowed** | Stalls no longer restart the bootstrap; a crash, kill or a stall over 60 s still does, and the `FreshTenants` marks are still process memory. A proposal for the owner only; no code change. Remains medium |
| F-3 (medium) gate constants unpinned | **Fixed** | Replanted: `CORE_MINIMUM 80 to 8`, `TOTAL_MINIMUM 70 to 7`, the core verdict forced to `passed`, the total verdict forced to `passed`, `vuln.py MAX_AGE_DAYS 14 to 14000`: all five **caught** by `tests/unit/scripts`. `PY_VULN_MAX_AGE_DAYS` can only tighten (0 to 14) |
| F-4 (medium) demo 200 with 0 cards | **Not reproduced, cause unknown (now low)** | `proto-demo` right after the full e2e, then twice more: **2 passed, 2 passed, 2 passed** (the first took 104 s, the others 50 s). HF-A did not establish the cause; one failure in my earlier runs, none in four since |
| F-5 (low) `SSL_CERT_FILE` trusted | **Fixed** | Plant `create_default_context(cafile=..., capath=...)` back to `create_default_context()`: caught. Live: my production-API probe with `SSL_CERT_FILE` pointing at the local issuer's certificate now answers **503 `identity_unavailable`** for a valid token (before: it was trusted and answered 404/503 as designed). The runbook text matches |
| F-6 (low) API imports the Jenkins client | **Fixed** | Plant (an import of `JenkinsBuildSystem` in `hosts/api/wiring.py`): `make py-layers` 1 broken (`api-no-build-system`), and `test_the_api_loads_neither_the_jenkins_client_nor_the_build_store` fails. Contracts at the tip: 8 kept, 0 broken |
| F-7 (low) redundant claim guards | **Accepted** (agree: row lock and worker id already protect; the lock itself is pinned) | not re-run |
| F-8 (low) stale waivers | **Fixed** | `py-age`: 12 waived, 0 failures, no "no longer needed" line |
| F-9 (low) stale section 10 rows | **Fixed** | Both rows now read "Built" (lines 478 and 481) |
| F-10 (low) no buildx | **Open**, as the lead said | |

Other gates at the tip: fmt, ruff, mypy strict (456 files), layers (8 kept), **unit 5312**, `py-vuln` no known
vulnerability, `py-age` 0 failures (exit 0).

### Planted defects of HF-A (all caught, `git status` clean after each)

- uncertain treated as held (`Singleton._paused` made a no-op): caught (`tests/unit/hosts/bootstrap`);
- the stalled check cancelled instead of awaited (`_answered` cancels): caught (the connection would be closed);
- the stall bound removed (`continue` instead of `LockLost`): caught;
- the supervisor restarts after a stop (`stopping` test dropped): caught;
- the supervisor never restarts (`return "$status"`): caught.

### Live

- **20 s `docker pause webblock-provisioning-db`** (19:58:54 to 19:59:14): the bootstrap kept the same pid
  (`bootstrap running (pid 435745)` before and after); the log has `uncertain` at 19:59:05.4 and `confirmed ...
  rounds resume` at 19:59:14.6, **and no round or `tenant ready` line between them** (the next round's lines
  start 19:59:19); the rounds counter went from 74 to 82 after the unpause.
- **SIGKILL of the bootstrap process** (the supervisor left alive): `bootstrap exited with status 137; the run
  script restarts it after 1000 ms`, a new process, `bootstrap holds the singleton lock` 1.8 s later,
  `provisioning_bootstrap_standby 0`.
- The earlier `kill-process` and standby checks of section 4 are covered by the replica suite's
  `test_bootstrap_standby.py` (passed).

### Critical reading of the lock code (HF-A)

- **Can a round run while the lock is uncertain or after it is lost?** No path found. `Singleton._paused`
  clears the held event and cancels the current round synchronously, from inside the lock's own task, so no
  round can start between the stall and the pause. `_rounds_while_held` starts a round only when the event is
  set and re-checks it after waking. Lost: `keep_lock` raises, the task group cancels the rounds, the process
  exits 1 and the supervisor restarts it (as a standby if someone else took the lock). The unit plants above
  and the live pause (no round lines inside the window) agree.
- **Soundness of "a stall is not a loss":** a session lock lives as long as its session, and the same session
  answers twice (the first answer may predate the stall, so a second check confirms) before rounds resume;
  an ended session fails the read or the check and is lost; a silent connection is lost after 60 s. I found no
  gap in that argument.
- **The psycopg cancel claim is true.** `psycopg 3.3.6` `AsyncConnection.wait`, on an interrupted wait with an
  active transaction, calls `_try_cancel(timeout=5.0)`, waits 5 s more for the query to end, and on timeout logs
  `query not terminated after cancellation: closing connection` and calls `pgconn.finish()`, which ends the
  session and frees the lock. That is why the code never cancels the check. In the live pause the log shows a
  `query cancellation failed: cancellation timeout expired` warning from psycopg; it belongs to the rounds'
  pooled connections (cancelled with the rounds), not the lock session, which stayed up (same pid, lock
  confirmed).
- **N-1 (low).** The holder runs rounds for up to `check` (5 s) plus the statement timeout (10 s) after a
  network partition begins, before it is told "uncertain". If the partition also made a standby able to take the
  lock (a failover to a new primary, where no lock exists), two bootstraps could overlap inside that window. This
  is not new (it was the same before HF-A, with a hard loss instead), and the bound is tight; it belongs with
  the P-P decision on fencing a failed-over database. Not a defect of the correction.

### New findings in the recheck: 0 critical, 0 high, 0 medium, 1 low (N-1)

### Counts after the recheck

Open: **0 critical, 0 high, 1 medium (F-2, owner's decision), 3 low (F-4 not reproduced, F-10, N-1)**;
F-7 accepted. Fixed and verified: F-1, F-3, F-5, F-6, F-8, F-9.

### Commands run in the recheck

`make py-fmt-check py-lint py-typecheck py-layers py-test py-vuln py-age` (exit 0); the plants above through
`/tmp/claude-1000/hv/plant.py`; `make proto-up proto-run proto-smoke` (all exit 0, smoke 15 of 15);
`make proto-e2e` (55 passed, one run); `make proto-demo` three times (2 passed each); the live pause and the
SIGKILL; the production-API probe with `SSL_CERT_FILE`; `make proto-stop proto-run-replicas
proto-e2e-replicas` (9 passed); `make proto-stop proto-down`. Skipped: integration, messaging, `proto-test-kind`,
catalog and Jenkins suites and `py-cover` (the lead ran integration 389 on the tip; none of the corrections
touched their code except the lock, whose behaviour the live runs cover); `make image-scan` (F-10).
