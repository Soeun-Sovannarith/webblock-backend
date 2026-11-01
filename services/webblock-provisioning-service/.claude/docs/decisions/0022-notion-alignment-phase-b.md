# 0022: Provisioning follows the Notion design, Phase B

Status: Accepted; built on branch `notion-alignment-b` (stacked on `notion-alignment-a`) and merged into `python`.
Section 5 records the gates and the Kind runs.

## 1. Context

Phase A (decision `0021`) moved the database and the system namespace to the Notion design. Phase B is the next slice:
the messaging and service-to-service edge, as in "Web Block architecture"
(https://app.notion.com/p/40358175966e8198a088f123a9ab23fe) sections 3 and 4.

Phase B covers:

- the shared contracts repository and the pin to it;
- version 1 events with the common envelope, replacing the prototype codec;
- the broker names of Notion 4.2 and 4.4;
- the owner-status consumer (decision `0021` D9);
- gRPC clients for Project access, the repository lookup and the entitlement check;
- one request ID from the API to the worker;
- OpenTelemetry tracing.

Rule for this work, as in Phase A: the Notion pages are read-only and the code follows them. Where Notion is silent, the
lead chooses inside what Notion defines and records the choice here. The design is in
docs/superpowers/specs/archive/2026-10-24-provisioning-phase-b-design.md (removed; in git tag docs-before-cleanup).

## 2. Global constraints

- Branch `notion-alignment-b`, stacked on `notion-alignment-a`. Never push. Never merge.
- The Notion pages are read-only. The code follows Notion section 3 and 4 of "Web Block architecture".
- No database migration in Phase B. `infrastructure/postgres/schema.py` does not change.
- The decision record changes before a rule changes in code: Task 1 writes
  `.claude/docs/decisions/0022-notion-alignment-phase-b.md`.
- Event names: exchange `<service>.events` (topic), routing key `<event>.v1`, queue `provisioning.<event>.v1` (quorum),
  dead letter queue `<queue>.dlq`, delivery limit 5.
- Envelope (body, snake_case): `schema_version` (1), `message_id` (UUID, not nil), `message_type` (`<svc>.<event>.v1`),
  `occurred_at` (RFC 3339), `correlation_id` (1 to 128 of `[A-Za-z0-9._:-]`), `project_id` (UUID, not nil). AMQP `type`
  equals `message_type`; AMQP `message_id` equals the body's; header `traceparent` when tracing is on.
- gRPC: deadline 2 s; retry only `UNAVAILABLE`, at most 2 retries, waits 100 ms then 300 ms; metadata `x-request-id` and
  `traceparent`; fail closed; no token.
- Request ID: inbound `X-Request-Id` accepted when it matches `^[A-Za-z0-9._:-]{1,128}$`, otherwise a new UUIDv7.
- Tracing off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set and not `none`. Sampler parent based, ratio 0.1.
- New dependencies: runtime `grpcio`, `grpcio-status`, `protobuf`, `opentelemetry-sdk`,
  `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-fastapi`; dev `grpcio-tools` (later removed:
  a pinned `protoc` binary generates the code, see E3 detail). Every version at least 14 days old by upload date
  (`make py-age`), `make py-vuln` clean, Python 3.14 wheels present.
- Production profile: `PROJECT_ACCESS_MODE=project-grpc` allowed; `REPOSITORY_MODE=project-grpc`,
  `ENTITLEMENT_MODE=payment-grpc` and `HOSTING_ENTITLEMENT_MODE=payment-grpc` refused (their protos are drafts; OD-01,
  OD-04 open).
- Unit tests under `tests/unit/` mirror the package and import only public names. No `__init__.py` under `tests/`.
  Dependency suites fail, never skip.
- Never log a body, a token or a credential. A rejected message is logged with `message_id`, size and SHA-256 only.
- Docs: plain, easy English, short, no em dash and no en dash.
- Never stage the owner's untracked files: `db-design-new.md`, `docs/new-platform-db-design.md`, `reports/`,
  `research_notes/`.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Fast gates after every task: `make py-fmt-check py-lint py-typecheck py-layers py-test`. Tasks that touch the broker
  also run `make py-test-messaging`; tasks that touch stores run `make py-test-integration`. One Testcontainers or Kind
  stack at a time.
- The real cluster is never touched (CLAUDE.md section 0).

## 3. Decisions E1 to E10

The owner delegates routine choices. Each is recorded in 0022 before code changes.

- **E1. The contracts repository.** A new local git repository `/home/roa/Documents/web-block/web-block-contracts`, laid
  out as Notion 3.7. The owner creates the GitLab project and pushes it. Claude never pushes.
- **E2. Who owns which file.** Each file starts with a header naming its owner and its status:
  - `accepted` (taken word for word from Notion): `events/envelope.v1.schema.json`,
    `proto/webblock/project/v1/access.proto`.
  - `owned by provisioning`: `events/provisioning/publish_completed.v1.schema.json`.
  - `draft, proposed by provisioning, waits for <owner>`: `proto/webblock/project/v1/repository.proto`,
    `proto/webblock/payment/v1/entitlement.proto`, and the event schemas `project/source_published`,
    `project/project_deleted`, `project/owner_status_changed`, `payment/hosting_entitlement_ended`,
    `payment/hosting_entitlement_restored`.
  The drafts follow the audit's section D and Payment's HTTP section 2.4 (same fields, snake_case, protobuf enums). The
  owning team may change a draft; Provisioning then follows.
- **E3. How Provisioning pins the repository.** `contracts.lock` at the module root holds one commit SHA.
  `make contracts-sync` copies the files Provisioning reads from that commit into `src/contracts/shared/` and generates
  the Python gRPC code into `src/provisioning/infrastructure/grpc/_gen/` (generated, excluded from lint and coverage).
  `make contracts-check` fails when either differs from what the pinned commit gives. No git submodule: there is no
  remote yet, and a copy is simpler to review.
  E3 detail: generated code is only `--python_out` and `--pyi_out`; clients call `channel.unary_unary` with the message
  classes, so no `_grpc.py` stubs are generated or typed. The code is generated by a pinned, checksum-verified `protoc`
  binary (36.2, matching the protobuf runtime 7.36.2; `make py-tools`), not by `grpcio-tools`, because `grpcio-tools`
  needs `setuptools`, which has an open advisory (GHSA-xmj3-9gf7-h52w) with no fix old enough for the 14-day rule (lead
  ruling).
- **E4. One switch to v1, no second codec.** The v1 codec replaces the prototype codec. The simulator publishes v1.
  `src/contracts/events/prototype/` is deleted. The event field `github_repository_id` becomes `repository_id` here
  (decision 0021 D3). REST and MCP keep `github_repository_id` until the camelCase release (Phase C).
- **E5. Inbox keys stay natural.** Source published: `(project_id, generation_request_id)`. Hosting events: the
  command's idempotency key. `message_id` is checked (UUID, required) and logged, never stored. No new table, so no DB
  change in Phase B. A Payment event for a project that was never deployed stays "acknowledge and log" (OD-42
  unchanged).
- **E6. Broker names.** As Notion 4.2 and 4.4:
  - Provisioning declares `provisioning.events` (topic) with alternate exchange `provisioning.events.unrouted` (fanout,
    with a queue `provisioning.events.unrouted` so nothing is lost).
  - It declares and owns five quorum queues: `provisioning.source_published.v1`, `provisioning.project_deleted.v1`,
    `provisioning.owner_status_changed.v1`, `provisioning.hosting_entitlement_ended.v1`,
    `provisioning.hosting_entitlement_restored.v1`, bound to `project.events` and `payment.events` with keys
    `<event>.v1`.
  - It checks `project.events` and `payment.events` passively and never declares them. In the prototype the simulator
    declares them, as their owners would.
  - Dead letters go to `<queue>.dlq` after 5 failed deliveries. `RABBITMQ_DELIVERY_LIMIT` defaults to 5 everywhere.
  - The internal command job queue keeps its exchange `provisioning.jobs` and its name `provisioning.command_job.v1`;
    its dead letter queue is renamed to `provisioning.command_job.v1.dlq` for one rule everywhere.
  - Provisioning stops declaring Payment's usage queue. Payment owns `payment.publish_completed.v1`; in the prototype
    the simulator declares it.
  - The operator CLI (`dlq list|redrive`) uses the new names.
- **E7. gRPC clients and their switches.**
  - `PROJECT_ACCESS_MODE` gains `project-grpc`. `project-service` (HTTP) stays until Project serves gRPC, then is
    deleted (audit step 8).
  - `REPOSITORY_MODE` gains `project-grpc` beside `file`.
  - `ENTITLEMENT_MODE` and the worker's `HOSTING_ENTITLEMENT_MODE` gain `payment-grpc`.
  - Production: `project-grpc` access is allowed (its proto is accepted). `GetRepository` and `GetEntitlement` stay
    refused in the production profile while their protos are drafts (OD-01, OD-04 stay open).
  - Every call: deadline 2 s; retry only on `UNAVAILABLE`, at most 2 times, 100 ms then 300 ms; metadata `x-request-id`
    and `traceparent`; fail closed. Access: no answer means `ProjectAccessUnavailable`. Repository: no answer means
    "unknown, retry". Entitlement: no answer means "could not be determined".
  - `ErrorInfo.reason` is read with `grpcio-status`. Only the reasons named in the proto change the outcome; anything
    else is "unavailable".
- **E8. Request ID.** The API accepts an inbound `X-Request-Id` that matches `^[A-Za-z0-9._:-]{1,128}$` and otherwise
  makes a UUIDv7. It returns it as `X-Request-Id` on every answer, logs it, stores it as the command's `correlation_id`,
  puts it in the job message and sends it on gRPC calls. The Problem body keeps the name `correlation_id` until the REST
  release (Phase C).
- **E9. Tracing.**
  - OpenTelemetry SDK with the OTLP/HTTP exporter. Off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set (default deny, like
    `SENTRY_DSN`).
  - Sampler: parent based, ratio 0.1 for new traces, so Istio's choice wins when a request arrives traced.
  - FastAPI by its instrumentation package. RabbitMQ and gRPC by a small inject and extract of `traceparent` in our own
    code (no extra instrumentation packages).
  - The worker continues the trace from the job message header, so one trace shows the request and the work it started.
  - No database instrumentation in Phase B.
- **E10. New dependencies (the owner approves).** Runtime: `grpcio`, `grpcio-status`, `protobuf`, `opentelemetry-sdk`,
  `opentelemetry-exporter-otlp-proto-http`, `opentelemetry-instrumentation-fastapi`. Dev: none (`grpcio-tools` was
  removed, see E3 detail; the pinned `protoc` binary is a tool in `scripts/py/versions.sh`). Each version at least 14
  days old (`make py-age`), and `make py-vuln` clean.
- **E11. Typed events.** Each v1 event has a pydantic model in `presentation/messaging/models.py`: `EnvelopeV1` and one
  subclass per event, frozen, `extra="forbid"`, with the same types, ranges and patterns as its JSON schema. The JSON
  schemas in `web-block-contracts` stay the shared contract; the models are Provisioning's typed view of them. A
  contract test runs every valid and invalid example through both the schema and the model and fails when they disagree.
  pydantic is already locked through FastAPI; it becomes a direct pin at the same version.

## 4. Consequences

- The prototype stack must be recreated, because the broker names change (E6) and the simulator declares the producers'
  exchanges.
- `src/contracts/events/prototype/` is removed (E4). The v1 schemas come from the pinned contracts commit into
  `src/contracts/shared/`.
- The wire field `repository_id` is used on events only. REST and MCP keep `github_repository_id` until the camelCase
  release (decision `0021` D3, Phase C).
- There is no database migration and `schema.py` does not change (E5).
- The dead letter queue of the command job is renamed to `provisioning.command_job.v1.dlq`. Queues and exchanges that
  exist on a running broker keep their old arguments, so a running broker is reset, not migrated.
- `GetRepository` and `GetEntitlement` stay refused in the production profile while their protos are drafts (E7).
- RabbitMQ 4.3.6 does not count a requeue-nack towards `x-delivery-limit`; only a return (for example a closed channel)
  counts. So each consumer bounds a message itself: after 5 counted failures it goes to its `.dlq`. The count is kept in
  the process per `message_id` (at most 1024 entries). It resets on a restart, and with several replicas a message can
  see up to 5 failures per replica.
- Replaced by decision `0023` G15: a database outage now counts towards the 5 tries like any failure; the rest of this
  item (no stream restart for an outage) stays. Old text: A database outage never sends a message to its `.dlq` (lead
  ruling, final review). When the database does not answer (connection failed or lost, a timeout, contention), the
  consumer holds the message with the capped store backoff and requeues it with a nack. It does not count it, does not
  park it after the retry limit, and does not restart the stream (a restart closes the channel, and the messages it held
  return to the queue, which counts towards `x-delivery-limit`). Only a failure while the database answered (a handler
  error) counts towards the 5. Repeated counted failures still restart the stream after `store_failure_limit`. The store
  raises `StoreUnreachable` for a lost connection (`DatabaseUnreachable` in `infrastructure.postgres`); the API still
  answers 500 for it, as before.
- The worker and the build worker wait until `project.events` and `payment.events` exist. In production Project and
  Payment must declare them first. In the prototype the simulator command `declare-producers` stands in for them.
- A dead letter re-drive publishes to Provisioning's own queue through the default exchange (never to a producer's
  exchange).
- Tracing: the API trace does not continue into the worker for commands, for the usage event or for the inbox. The job
  is published later from its database row, and carrying `traceparent` across would need a new column, which Phase B
  does not add. The request ID in the logs links the two sides. A column for it is a possible later migration, for the
  owner to decide.
- Logs carry `trace_id` also for spans that are not sampled. Such an ID resolves to no trace in the trace backend.
- HTTP spans drop the client address, the port, the user agent and the query string, so no personal data is exported.
- Exception record (CLAUDE.md section 4): `# noqa: TID251` on the `requests` import in
  `src/provisioning/infrastructure/telemetry/tracing.py`. Reason: the OTLP/HTTP exporter needs a `requests.Session` with
  `trust_env=False`, so that proxy and netrc settings are not read. Reviewer: the lead. Expiry: none, while the
  OTLP/HTTP exporter needs a requests session. Follow-up: drop the exception if the exporter gains another transport.
- Rulings of the lead, for the owner to confirm:
  - Replaced by decision `0023` G17: a version at or below the stored one changes nothing and is acked as `STALE`,
    whatever its status. Old text: An owner-status event that repeats a version with a different status is dead-lettered
    (`IdempotencyConflict`), not dropped. Notion says to drop an event whose version is not above the stored one; the
    owner confirms which rule holds for a conflicting repeat.
  - "Entitlement could not be determined" reuses `DependencyUnavailable` (503). There is no new error class.
  - The gRPC deadline is 2 s per attempt. A retry is a new call with its own 2 s.
- Owner items:
  - Re-sign the two `certifi` vulnerability exceptions. The exposure now includes the trace exporter.
  - Push `web-block-contracts` to GitLab (no remote exists yet).
  - The Project and Payment teams review the draft protos and event schemas.

## 5. What was built and tested

Commits on `notion-alignment-b` from `f558427` (this record) to the Task 11 docs commit. Gates were run on the tree of
the Task 11 commit, one at a time.

| Task | What was built | Evidence |
| --- | --- | --- |
| 1 | This record (E1 to E10) | `f558427` |
| 2 | Contracts repository `web-block-contracts`, `contracts.lock`, copy in `src/contracts/shared/`, generated protobuf code, pinned `protoc` | `28fc2e4`; `make contracts-check` exit 0 |
| 3, 4b | v1 event codec; pydantic models for every event (E11) | `13c89f8`, `cf3e2a6`, `dacfdb4` |
| 4 | Notion's broker names, `.dlq` queues, per-message failure bound, re-drive to the own queue | `b1aab63`, `3d3e0b8`; `make py-test-messaging` 31 passed |
| 5 | Owner-status consumer (`0021` D9) | `7ff0bfa` |
| 6 to 8 | gRPC clients: Project `CheckAccess`, `GetRepository`, Payment `GetEntitlement` | `8316055`, `dfce39a`, `92d81a6`, `1435ab9` |
| 9 | `X-Request-Id` on REST (declared in `openapi.yaml` in Task 11) | `0b445bf` |
| 10 | OpenTelemetry tracing | `8b46a54`, `697bed2` |
| 11 | Docs, this record, the change report | the gates below |

Gates (Task 11). They ran on the tree that became `2aa7a4f`; only docs changed between the run and the commit.

| Command | Commit | Result |
| --- | --- | --- |
| `make py-fmt-check py-lint py-typecheck py-layers` | `2aa7a4f` | exit 0; import-linter 9 contracts kept, 0 broken |
| `make py-test` | `2aa7a4f` | exit 0; 6553 passed |
| `make contracts-check` | `2aa7a4f` | exit 0; shared copy and generated code match commit `afff924` |
| `make py-test-integration` | `2aa7a4f` | exit 0; 490 passed (real PostgreSQL 18) |
| `make py-test-messaging` | `2aa7a4f` | exit 0; 31 passed (real RabbitMQ) |
| `make chart-check` | `2aa7a4f` | exit 0; 23 passed |
| `make py-vuln` | `2aa7a4f` | exit 0; 2 findings (both `certifi`), each under a current exception |
| `make py-age` | `2aa7a4f` | exit 0; 100 lock pins, 10 tool pins, 1 image pin, 0 waived, 0 failures |
| `make proto-down; make proto-up` | `2aa7a4f` | exit 0 (run 1, the lead) |
| `make proto-test-kind` | `2aa7a4f` | exit 0; 225 passed (run 1) |
| `make proto-run; make proto-e2e` | `2aa7a4f` | exit 2; 55 passed, 1 failed: `test_hosting_deletion` expected `entitlement_ended`, the code answers `hosting_already_online` (`0021` D5); the expectation was stale and is fixed in `f8a6b62` (run 1) |

Gates (final review fix wave). Commit `102f7a5` holds the code. The next commit changes only this table and the stale
e2e expectation of `tests/e2e/faults/test_hosting_deletion.py` (`0021` D5).

| Command | Commit | Result |
| --- | --- | --- |
| `make py-fmt-check py-lint py-typecheck py-layers` | `102f7a5` | exit 0; import-linter 9 contracts kept, 0 broken |
| `make py-test` | `102f7a5` | exit 0; 6581 passed |
| `make chart-check` | `102f7a5` | exit 0; 23 passed |
| `make contracts-check` | `102f7a5` | exit 0; matches commit `afff924` |
| `make py-test-messaging` | `102f7a5` | exit 0; 32 passed (real RabbitMQ) |
| `make py-test-integration` | `102f7a5` | exit 0; 490 passed (real PostgreSQL 18) |

Kind run 2 (the lead) on `f8a6b62`, after `make proto-down`, one Kind stack, no Testcontainers suite alongside:

| Command | Commit | Result |
| --- | --- | --- |
| `make proto-up` | `f8a6b62` | exit 0 |
| `make proto-test-kind` | `f8a6b62` | exit 0; 225 passed |
| `make proto-run` | `f8a6b62` | exit 0 |
| `make proto-e2e` | `f8a6b62` | exit 0; 56 passed in 38 min (host mode; `tests/e2e/incluster` not selected) |

`make py-cover`, `make image-scan` and the Jenkins and catalog live suites were not run in Phase B.

## 6. What stays open

- OD-01 (repository part) and OD-04 (entitlement): the Project and Payment protos are drafts until their owners accept
  them.
- OD-42: Payment events for a project that was never deployed are acknowledged and logged only.
- OD-65: Sentry hosting and the trace backend. The tracing code is built in Phase B; the backend is not chosen.
- The ruling on a repeated owner-status version with another status (section 4): closed by decision `0023` G17.
- Trace continuation from the API into the worker, which needs a column (section 4).
- The GitLab project for the contracts repository and its first push: the owner.
- The REST and MCP rename to camelCase, the `/api/v1` prefix and the Problem body name: Phase C.
- Kind suites and `proto-e2e` for this phase: run before any merge (section 5 has the state).

OD-26 is settled: the name of the internal job message is Notion's (`provisioning.command_job.v1`), so it is not listed
here as open.
