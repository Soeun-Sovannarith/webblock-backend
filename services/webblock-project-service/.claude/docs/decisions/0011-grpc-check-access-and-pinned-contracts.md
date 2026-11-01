# 0011 gRPC CheckAccess and pinned contracts

## Decision

- One gRPC server runs in the same process on port 9090 (Service port `grpc`). It is plain grpc-java started by a
  lifecycle bean, `common/presentation/grpc/GrpcServer`, not a gRPC Spring starter. It registers every
  `BindableService` bean and refuses to start if one sits outside a `presentation.grpc` package of this service, so
  a library's health, reflection or admin service is never served unseen. It runs on virtual threads, accepts an
  inbound message of at most 64 KiB and metadata of at most 16 KiB, has no reflection or health service, and stops
  gracefully within `webblock.grpc.shutdown-grace` (default 10 s). The port is `webblock.grpc.port` (default 9090).
- Load limits: at most `webblock.grpc.max-calls-in-flight` calls run at once across every service (default 8, below
  the project database pool of 10 that REST shares). A call beyond it answers `UNAVAILABLE`, reason
  `DEPENDENCY_UNAVAILABLE`, at once and never reaches a service. A call takes its place only when its handler is about
  to run (at half-close), so a call that has started but not half-closed holds none; its place comes back when its
  work has ended. The cap covers unary calls, the only kind Project serves. Per connection, Netty allows 100 calls at
  once, closes a connection idle for 5 minutes or older than 30 minutes (10 s grace for its calls), accepts a client
  ping at most once a minute and only while calls are open, and closes a connection that sends more than 200 stream
  resets in 10 seconds. A call cancelled or past its deadline stops before the project lookup. A busy database answers
  `DEPENDENCY_UNAVAILABLE` in REST and gRPC with a warning and no stack trace: a lock wait, a statement timeout, or no
  free pooled connection within the pool's timeout (`common/presentation/error/DatabaseBusy`).
- Project serves `webblock.project.v1.AccessService/CheckAccess`. The proto request becomes the access feature's
  `AccessCheckRequest` (an empty string reads as missing), is checked with the same Bean Validation rules as REST,
  and goes to the same `AccessCheckService`. The answer is `allowed` and `reason` (empty when allowed); it is never
  gRPC `NOT_FOUND`. The subject is only the `user_id` field: `x-user-id` metadata and roles are not read, and an
  admin gets no other answer.
- Errors: each `ErrorCode` has one gRPC status, chosen by a `switch` over the enum so a new code without a status
  fails to compile. By category: `VALIDATION` `INVALID_ARGUMENT`; `UNAUTHENTICATED` `UNAUTHENTICATED`; `FORBIDDEN`
  `PERMISSION_DENIED`; `NOT_FOUND` `NOT_FOUND`; `CONFLICT` `ABORTED`, except `ALREADY_EXISTS` for
  `PROJECT_ID_CONFLICT`, `PAGE_PATH_TAKEN`, `HOSTNAME_TAKEN` and `FAILED_PRECONDITION` for `PROJECT_DELETED`,
  `PROJECT_SUSPENDED`; `BUSINESS_RULE` `FAILED_PRECONDITION`; `PAYLOAD_TOO_LARGE` `RESOURCE_EXHAUSTED`; `DEPENDENCY`
  `UNAVAILABLE`; `INTERNAL` and the HTTP-only categories `INTERNAL`. Each error carries one `ErrorInfo` (`reason` the
  code, `domain` `project.webblock`, `metadata.request_id`) and a `BadRequest` for field problems. `x-request-id` is
  read with the rule of `RequestIdFilter`, or made.
- A method the server does not serve answers `UNIMPLEMENTED` with reason `ROUTE_NOT_FOUND`, its fixed sentence, an
  `ErrorInfo` and the `x-request-id`; it never echoes the path the caller sent. This is the one answer whose status
  is not its code's category status. Refusals the transport makes before a call reaches the interceptors stay
  grpc-java's own, with no `ErrorInfo` and no request ID: a message over 64 KiB (`RESOURCE_EXHAUSTED`), metadata
  over 16 KiB (refused by HTTP/2; the client reads `INTERNAL`), bytes that do not parse as the proto (`INTERNAL`),
  and the Netty connection limits above (the connection is closed). Their texts hold sizes only, never content.
- `PROJECT_DELETED` and `PROJECT_SUSPENDED` are error codes (category `CONFLICT`) for callers. CheckAccess returns
  them as `reason` in an OK answer. Project's own REST keeps 422 `PROJECT_READ_ONLY`.
- The gRPC port reads no token. STRICT mTLS and the mesh `AuthorizationPolicy` are its only gate. The one allowed
  caller is `cluster.local/ns/webblock/sa/provisioning-api` on path
  `/webblock.project.v1.AccessService/CheckAccess`; the owner applies the port and the policy in `web-block-config`.
  A portal caller is added only when its identity is decided.
- Contracts: `contracts.lock` holds the full commit SHA of `web-block-contracts`. Only `scripts/contracts-sync.sh`
  writes `contracts/`, copying the files on its list; its `--check` fails on any difference. Nobody edits
  `contracts/` by hand.
- The protobuf Gradle plugin, `protoc` and `protoc-gen-grpc-java` generate the stubs in their own `contracts` source
  set; the boot jar ships them. JaCoCo, Spotless, PMD and CPD cover `main` only, and no exclusion is added. Every
  grpc-java artifact uses one version, the one `web-block-commons` pins.
- `AccessAction` is the one list of accepted actions for REST and gRPC, `manage_site` included. Project owns
  `access/actions.v1.json` and `access/actions.v1.schema.json` in `web-block-contracts` (each action's `name`,
  `write`, `admin_allowed`). `write` is true for every action that changes state and false for the get and list
  actions; `web-block-commons` maps `PROJECT_DELETED` and `PROJECT_SUSPENDED` to 409 only for write actions.
  `AccessActionListTest` proves the enum equals the pinned file (names and `write` flags) and that the file passes
  its schema. The file stays DRAFT until Provisioning confirms the `admin_allowed` flags.
- `contracts/` and `contracts.lock` count as code for the commit ID check. The guard hook and
  `.claude/rules/spec-driven.md` take them in a later small change.
- The REST check `POST /internal/v1/access-checks` stays until FR-34.

## Reason

Notion "Web Block architecture" §3 names gRPC `CheckAccess` as the call Provisioning and the portals use, and the
shared contracts repository is the one place both sides read the proto from. Pinning by SHA and copying by script is
how `web-block-commons` does it, so a contract never drifts silently. One request record, one rule set and one
service for REST and gRPC keep the two answers equal.

Options considered: a gRPC Spring starter (one more dependency and its defaults to review, against about 100 lines
of our own); generating the stubs into `main` (it needs a coverage exclusion, which Art. V forbids); copying protos by
hand (silent drift); gRPC's 4 MiB default message size (any mesh caller could send large bodies); calls without a
cap (a flood drains the pool REST shares); a queue for calls over the cap (it holds callers past their deadline).
The cost if wrong: a missed status answers `UNKNOWN`, and an action list that drifts from the shared file lets
Provisioning and Project disagree on what a deleted project refuses.

## Status

Accepted. Built and tested in tasks 5 and 6 of `.claude/docs/plans/j3-notion-alignment-plan.md`: the pinned
contracts, the stubs' source set, the server, the error statuses and CheckAccess (runbook
`.claude/docs/runbooks/grpc.md`). The load limits, the unknown-method answer and the service package check answer
the security review `ai/reviews/j3-grpc-security-review.md` (findings 1 to 3). Task 8 is built:
`manage_site` in `AccessAction` and `AccessActionListTest` against the shared action list, with `contracts.lock` at
`18dd301`. The mesh port and policy wait for the owner in
`web-block-config`. The commit ID check for `contracts/` waits for its own small change.
