# Runbook: metrics and Sentry (task H4)

Decision `0016` section 5 item 4 (the owner approved `prometheus-client` and the Sentry SDK in phase P-H, with redaction
tests); TRD TR-40. The amendment of decision `0006` section 7 for `/metrics` and the new operational listeners is the
lead's (proposed wording in the H4 report).

## 1. Where `/metrics` is served

| Process | Listener | Routes |
| --- | --- | --- |
| API (`provisioning.hosts.api`) | `OPS_LISTEN_ADDR` (beside the public `HTTP_LISTEN_ADDR`, which it must not equal) | `GET/HEAD /healthz`, `GET/HEAD /readyz`, `GET /metrics` |
| Worker (`provisioning.hosts.worker`) | its own `OPS_LISTEN_ADDR` | `GET/HEAD /healthz`, `GET /metrics` |
| Build worker (`provisioning.hosts.build_worker`) | its own `OPS_LISTEN_ADDR` | `GET/HEAD /healthz`, `GET /metrics` |
| Bootstrap (`provisioning.hosts.bootstrap`, a long-lived loop) | its own `OPS_LISTEN_ADDR` | `GET/HEAD /healthz`, `GET /metrics` |
| Operator CLI | none | none |

The public listener never serves `/metrics`: there it is an unknown route behind the token (401 without one, 404 with
one). No listener serves a profiling route. `/readyz` still names no dependency, version or error. A `/metrics` that
cannot be produced within its bound answers `503 metrics unavailable`.

The listeners of the worker, build worker and bootstrap process are internal: at most 16 connections, a 5 s keep-alive,
a 2 s shutdown, no access log, no Server header. Bind them to a pod or loopback address that only the scraper reaches.

On the prototype stack (`scripts/prototype/run-provisioning.sh`):

| Process | Address | Override |
| --- | --- | --- |
| API | `127.0.0.1:18081` | `PROV_OPS_PORT` |
| Worker | `127.0.0.1:18082` | `PROV_WORKER_OPS_PORT` |
| Build worker | `127.0.0.1:18083` | `PROV_BUILD_WORKER_OPS_PORT` |
| Bootstrap | `127.0.0.1:18084` | `PROV_BOOTSTRAP_OPS_PORT` |

```sh
curl -s http://127.0.0.1:18082/metrics | grep '^provisioning_'
```

## 2. The metrics

Every label value comes from a fixed set; anything else becomes `other`. No label ever carries a project, release,
command, step or subject ID (those are in the logs). A unit test scans the whole registry against an allow-list of label
names and refuses any identifier-like value.

The `failure_code` label's set is derived from the failure-code catalogue,
`src/provisioning/core/domain/values/failure_codes.py` (task H4-FU): every code of its `FAILURE_CODES` table (the codes
a release, step or command can record, build-system codes such as `clone_failed`, `pipeline_failed` and `push_failed`
among them) and the worker consumer's three codes of its `OTHER_CODES` table (`transient_failure`, `attempt_timeout`,
`worker_stopping`). A code the catalogue does not list counts as `other`, so a rising `failure_code="other"` series
means a code is written that the catalogue misses. A code added to the catalogue becomes a label value with no change
here. `.claude/docs/runbooks/failure-codes.md` explains each code.

| Metric | Type | Labels | Process | Source |
| --- | --- | --- | --- | --- |
| `provisioning_commands_finished_total` | counter | `kind`, `status` (succeeded, failed, skipped), `failure_code` (or `none`) | worker | the worker's audit events |
| `provisioning_command_execution_seconds` | histogram | `kind`, `result` (finished, permanent_failure, transient_failure, try_failed, attempt_timeout, claim_lost, fenced, worker_stopping) | worker | the command consumer, per execution attempt; deploy and promote durations are the `kind="deploy"` and `kind="promote"` series; `try_failed` is a failed go-live try 1 or 2, whose claim is given back with the try's own wait (decision `0027`) |
| `provisioning_dispatch_lag_seconds` | gauge | none | worker | SQL on scrape: age of the oldest unfinished command whose job the broker never confirmed |
| `provisioning_unfinished_command_age_seconds`, `provisioning_unfinished_commands` | gauge | none | worker | SQL on scrape (`provisioning_ops.command_dispatch`) |
| `provisioning_routes_uncertain` | gauge | none | worker | SQL on scrape (`provisioning.project_hosting`) |
| `provisioning_route_uncertain_alerts_total` | counter | none | worker | the `route_uncertain` audit alert |
| `provisioning_build_attempts_finished_total` | counter | `outcome` (succeeded, failed), `failure_code` | build worker | the build audit events |
| `provisioning_build_attempt_duration_seconds` | histogram | `outcome` | build worker | from the attempt's start to its end, when the same process saw both |
| `provisioning_build_round_results_total` | counter | `round` (poll, pending, sweep), `outcome` (the round's outcomes, `round_failed`) | build worker | the build poller; `outcome="unavailable"` means Jenkins could not be reached |
| `provisioning_inbox_events` | gauge | `status` (pending, failed) | build worker | SQL on scrape (`provisioning_ops.source_published_events`) |
| `provisioning_http_requests_total` | counter | `route` (template), `method`, `status_class` | API | the public listener |
| `provisioning_http_request_duration_seconds` | histogram | `route`, `method` | API | the public listener |
| `provisioning_identity_refusals_total` | counter | `auth_cause` (fixed causes, [identity.md](identity.md)) | API | the identity's log events, whatever `LOG_LEVEL` is |
| `provisioning_identity_key_fetch_failures_total` | counter | `auth_cause` (`fetch_*`, `discovery_*`, `jwks_*`) | API | the identity's log events |
| `provisioning_bootstrap_rounds_total` | counter | `result` (completed, failed) | bootstrap | the tenant loop |
| `provisioning_bootstrap_tenants_not_ready` | gauge | none | bootstrap | the last completed round |
| `provisioning_metrics_sample_failures_total` | counter | `sample` (dispatch, inbox) | worker, build worker | a SQL sample that failed or timed out; its gauges then read NaN |
| `process_*`, `python_gc_*` | standard | `generation` | all | `prometheus-client`'s process and GC collectors |

The dead-letter queues' depth is not exported here: RabbitMQ's own `rabbitmq_prometheus` plugin reports queue depths.

Each scrape of the worker and build worker runs one short read-only transaction (bounded by `DB_TX_TIMEOUT`) with the
process's own login and existing grants. Scrape them every 15 to 60 seconds, not faster.

The worker writes one info log line per failed go-live try (decision `0027`), from
`core/application/routing/background/routing.py`:

```text
go-live try failed: command_id=C project_id=P release_id=R try=1 of=3 failure_code=site_not_answering
  next_try_after_seconds=30
```

It is one line in the log; it is wrapped here. `next_try_after_seconds` is `none` after the last try, or when the route
could not be put back. The line holds these seven fields only: never an error's text, a token, the preview probe token,
a credential or site content. The consumer's line for the same claim, `transient execution failure; releasing the
claim`, names its `retry_after`.

## 3. Sentry

| Key | Rule |
| --- | --- |
| `SENTRY_DSN` | Required in every long-lived process, no default. `none` turns error reports off (no client is built). Otherwise a DSN, a secret that prints redacted. |
| `SENTRY_ENVIRONMENT` | Required with a DSN; must be unset with `none`. 1 to 64 lowercase letters, digits, `.`, `_`, `-`. |
| `SENTRY_TRACES_SAMPLE_RATE` | Required with a DSN; must be unset with `none`. 0 to 1. Nothing starts a transaction today, and any transaction is dropped until tracing is decided (OD-65). |

The prototype runs with `SENTRY_DSN=none`; nothing is ever sent to a real Sentry from it.

What is reported: an exception that is being handled while a record of level error or above is written, by the
structured logger or the standard library's logging (a loop that ended with an error, a request that ended `internal`, a
start-up failure). A 5xx request with an expected code (`timeout`, `overloaded`, `dependency_unavailable`,
`identity_unavailable`) is not reported.

What leaves the process (`infrastructure/telemetry/sentry.py`, `scrub_event`, default-deny):

- the exception's type, module, mechanism and stack frames (file, function, module, line, in-app), never its message;
- the event ID, time, level, platform, release (`webblock-provisioning@0.0.0`), environment and server name (the process
  name);
- the tags `process`, `profile` and `correlation_id` only (never the project ID);
- nothing else: no request, headers, cookies, body, environment, user, extra, contexts, breadcrumbs, local variables or
  source lines.

The SDK runs with no default or auto-enabled integration (nothing in another library is patched),
`send_default_pii=False`, `include_local_variables=False`, no proxy from the environment, and its own background thread
for the HTTP transport. On shutdown each process waits at most 2 s for queued events.

## 4. Checks

```sh
make py-test                     # redaction, cardinality, config, listener and contract tests
make py-layers                   # only infrastructure.telemetry and the hosts import the two libraries
make proto-up proto-run
for p in 18081 18082 18083 18084; do curl -s http://127.0.0.1:$p/metrics | grep -cE '[0-9a-f]{8}-[0-9a-f]{4}-'; done   # 0 each
```
