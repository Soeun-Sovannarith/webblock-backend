# Runbook: the production profile and the build/API split (tasks H2 and H3, phase P-H)

## The profile key

Every process requires `PROVISIONING_PROFILE`: the API, the worker, the build worker, the bootstrap process and the
operator CLI (both its inbox and its dead-letter commands).

- The key accepts exactly two values: `prototype` or `production`.
- The key has no default. Unset or empty, the process exits 2 and prints `PROVISIONING_PROFILE: is required`.
- `scripts/prototype/run-provisioning.sh` sets `prototype` for every process.

## What `production` does today

Real implementations of these contracts with other services do not exist yet:

- Project's repository confirmation (OD-01). Its access check exists and production may use it
  (`PROJECT_ACCESS_MODE=project-service`, [0019](../decisions/0019-service-identity-is-the-mesh.md));
- Payment's entitlement and events (OD-04);
- Project's "source published" event (OD-02);
- the catalog owner's access setup (OD-46);
- identity: answered (OD-05, [0019](../decisions/0019-service-identity-is-the-mesh.md) section 3); task H3 built the JWT
  verifier, so the API starts in production, and [identity.md](identity.md) has the production example.

So a production process fails closed. It never falls back to a placeholder success.

| Process | In production |
| --- | --- |
| api | Starts with `IDENTITY_MODE=jwt`, the only identity production accepts (task H3, [identity.md](identity.md)). Every bearer token is verified against the issuer's JWKS endpoint. Every mutation (`deploy_release`, `promote_release`, `rollback_release`, `build_release`) is refused with HTTP 503 `operation_unavailable`. The typed error `OpenContract` names the open contracts, and the audit log records `refused` with `open_contract`. Reads keep their permission check. Project access denies every request (404) unless `PROJECT_ACCESS_MODE=project-service` is set; then the Project service decides, without a token, the mesh being the gate ([0019](../decisions/0019-service-identity-is-the-mesh.md), [identity.md](identity.md)). |
| worker | Starts, but enables no command kind. A command that appears anyway is claimed and fails `operation_unavailable`, with no executor run. The dispatcher and the reconciler run. Not built: the Payment-event consumer, the project-deleted consumer, the hosting retry and the usage publisher (OD-02, OD-04, OD-42, OD-52). Routing's entitlement and the hosting entitlement are closed (`ContractNotDecided`, OD-04). |
| build_worker | Starts the build poller alone: it polls running builds and runs the deadline sweep. It starts no build, because a build needs Project's repository URL (OD-01). Not run: the "source published" consumer, the inbox processor (OD-02, OD-01) and the hint listener. It opens no broker connection. |
| bootstrap | **Refused at configuration**, naming `PROVISIONING_PROFILE`. Every tenant it creates ends with access_setup, whose production contract with the catalog owner is open (OD-46). |
| operator | Accepted as is. The inbox re-drive and the dead-letter commands use only Provisioning's own storage. |

### Keys refused in production

Each key below is refused by name when it is set (exit 2). The reason begins "must be unset in production:", except
where the table says otherwise.

| Process | Keys |
| --- | --- |
| api | `AUTH_DEV_TOKENS_FILE`, `PROJECT_ACCESS_FILE`, `ENTITLEMENT_MODE`, `ENTITLEMENT_FILE`; `PROJECT_ACCESS_MODE` other than `project-service` ("must be project-service or unset in production"); `IDENTITY_MODE=dev-tokens` ("must be jwt in production"); `PROJECT_SERVICE_URL` without `PROJECT_ACCESS_MODE=project-service`, and in that mode a plain `http` URL whose host is not an in-cluster Service (`.svc`, `.svc.cluster.local`), or, for any scheme, a non-ASCII, loopback, unspecified, local-name, non-canonical numeric, zoned IPv6 or placeholder host ([identity.md](identity.md)). `PROJECT_SERVICE_TOKEN_FILE` is refused in every profile ([0019](../decisions/0019-service-identity-is-the-mesh.md)) |
| worker | `ENTITLEMENT_MODE`, `ENTITLEMENT_FILE`, `HOSTING_ENTITLEMENT_MODE`; `KUBE_CONNECTION=kubeconfig` is refused with "must be in-cluster in production": production connects only with the projected token and its SelfSubjectReview (decision 0016 section 9 ruling 2) |
| build_worker | `REPOSITORY_MODE`, `PROTOTYPE_REPOSITORIES_FILE` |

A second line of defence stands behind the configuration:

- `hosts/shared/stand_ins.py` is the only module allowed to import `infrastructure.standins`. The import-linter contract
  `standins-only-through-hosts-stand-ins` enforces this.
- Every builder in that module raises `StandinNotAvailable` in production, naming the key.
- The closed adapters live in `hosts/shared/open_contracts.py`.

### Worker timing for the go-live tries (TR-58)

One promote makes up to 3 go-live tries (decision `0027`). The worker refuses to start (exit 2, naming the key, never
its value) when its timing cannot hold them. Below, S is `ROUTE_SWITCH_DEADLINE`, R `ROUTE_RESTORE_DEADLINE`, L
`WORKER_LEASE_TTL` and B `WORKER_RETRY_BACKOFF_MAX`.

| Key | Must be above | Prototype value and bound |
| --- | --- | --- |
| `WORKER_ATTEMPT_TIMEOUT` | 2 x S plus 2 x R: one claim puts the route back, runs the preview and the switch, and restores | 10m against 160 s |
| `COMMAND_DEADLINE` | 3 x (2 x S plus R), plus the 90 s of waits, plus 3 x (the longer of L and B, plus R) for an interruption on each try | 15m against 615 s |
| `WORKER_MAX_ATTEMPTS` | 3, the go-live tries, so the claim after an interrupted try 3 still ends the promote | 20 |

- The API writes each command's `deadline_at` from its own `COMMAND_DEADLINE`; the worker checks its own. One process
  cannot check the other's value, so the API's and the worker's `COMMAND_DEADLINE` must be equal.
- Production values are set outside this repository (OD-15). Check them against this table before a deploy.

### Site domain

In production `SITE_DOMAIN` is `roaa15.xyz` (decision 0016 section 10).

## The build/API split (decision 0016 section 5 item 2)

- **The API holds no build credentials.** It holds neither the Jenkins token nor a `provisioning_build` login. It
  refuses these keys when they are set, in both profiles, so a stale environment file is caught at start-up:
  - `PG_BUILD_USER`, `PG_BUILD_PASS`;
  - `BUILD_*`;
  - `JENKINS_*`;
  - `REPOSITORY_MODE`, `PROTOTYPE_REPOSITORIES_FILE`.
- **What `build_release` does now.** The release's pending build step, written by the intake or by a failed attempt, is
  the durable record that a build is wanted. `build_release` then:
  1. checks that step with the API's read grant;
  2. refuses as before: `release_not_buildable`, `build_in_progress`, not found;
  3. writes nothing;
  4. sends `pg_notify('provisioning_build_wanted', '<project_id>/<release_id>')`, which needs no grant.

  The 202 body is the release as read, so its build step usually still says `pending`.
- **The build worker's half.**
  - It listens on that channel through one connection of its own login (`PgBuildHintListener`).
  - It validates each payload as two canonical UUIDs. Anything else is dropped with a log line that never quotes the
    payload.
  - It queues at most `BUILD_HINT_QUEUE_SIZE` hints, drops the rest with a log line, and starts each queued release
    through `StartBuild`.
  - A dead connection is noticed within `BUILD_HINT_LIVENESS` by `SELECT 1`. The listener then reconnects with backoff,
    from `BUILD_HINT_RECONNECT_BASE` up to `BUILD_HINT_RECONNECT_MAX`.
- **A lost hint only delays the start.** This covers a listener that is down and a dropped hint. The build poller's
  grace scan (`BUILD_PENDING_GRACE`, 30 s by default) starts the attempt anyway. SQL stays the authority.

### Connection pooling caveat (production, P-P)

`LISTEN` needs a session of its own. It does not work through PgBouncer in transaction-pooling mode. In production, the
build worker must therefore reach the Provisioning database in one of two ways:

- directly;
- through a session-mode pool.

The API's `NOTIFY` runs inside an ordinary transaction and works through either mode.

## Checking a deployment

- `make py-layers`: the contract `standins-only-through-hosts-stand-ins` is KEPT.
- Start a process with `PROVISIONING_PROFILE=production` and a stand-in key set. It exits 2, naming the key and never
  its value.
- On the prototype stack, call `build_release` while an attempt is pending. The build worker's log shows
  `hinted build started` within a second, and the attempt leaves `pending` long before the grace period.
