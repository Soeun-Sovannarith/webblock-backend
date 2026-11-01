# Team testing guide: Provisioning REST and MCP on the prototype stack

For the UI and agent teams who want to call Provisioning's REST API and MCP tools by hand (the contract is a draft
its consumers review, OD-25). Everything here runs on one machine against the local prototype stack: a Kind
cluster, a local Jenkins and Git server, and Provisioning's own PostgreSQL and RabbitMQ. It proves nothing about a
production cluster or database.

Every table below comes from a file of this repository, named with it. If a table and the file disagree, the file
wins; tell the Provisioning team.

Rules for you as a tester (`.claude/CLAUDE.md` section 0): never point `kubectl`, `helm` or `kind` at any cluster but
the project's own Kind cluster, never read `~/.kube`, and never use another team's kubeconfig. This guide needs no
cluster client.

## 1. Start the stack

Full description: [prototype-stack.md](../runbooks/prototype-stack.md) (the stack) and
[prototype-demo.md](../runbooks/prototype-demo.md) (Provisioning's processes). From the repository root:

```sh
make kind-tools      # once per machine: installs bin/kind, bin/kubectl, bin/helm, bin/crane
make proto-up        # the Kind stack; about 90 s with warm caches, longer the first time
make proto-run       # Provisioning's database, broker and four processes; waits until the API is ready
make proto-stop      # when finished: stops the processes and removes Provisioning's containers
make proto-down      # removes the stack
```

`make proto-up` needs a checkout of the Project service at `../project-service` (or `PROJECT_SERVICE_DIR`) for the
catalog schema, and refuses to start when another Kind cluster exists or memory is short. One stack at a time.

After `make proto-run`, check the stack with `curl -s http://127.0.0.1:18081/readyz`. Addresses
(`prototype-demo.md`, "What runs"):

| What | Address |
| --- | --- |
| REST and MCP | `http://127.0.0.1:18080` (`/v1/...`, `/mcp`) |
| Health and readiness | `http://127.0.0.1:18081/healthz` and `/readyz` |

If the machine is crowded after many test sites, `make proto-reset-sites` clears them.

## 2. Tokens and who may do what

`make proto-run` writes four development bearer tokens to `bin/kind-state/proto/provisioning/tokens.json` (mode
0600, git-ignored). `provisioning.env` in the same folder holds the same values and the database and broker
passwords. Take tokens from `tokens.json`. Source: `scripts/prototype/run-provisioning.sh`.

**Never print, paste, log, commit or share a token or either file.** Put one in a shell variable without echoing it:

```sh
export TOKEN=$(python3 -I -c 'import json,sys; print(next(t["token"] for t in json.load(open(sys.argv[1]))["tokens"]
  if t["subject"]==sys.argv[2]))' bin/kind-state/proto/provisioning/tokens.json prototype-operator)
```

Use `prototype-tenant`, `prototype-reader` or `prototype-admin` as the last argument for the others. `make
proto-stop` removes the generated secrets, so read the tokens again after the next `make proto-run`.

What each token may do (`run-provisioning.sh`: `AUTH_ACTION_SCOPES`, `write_files`; the gate is
`core/application/operations/gate.py`):

| Subject | Scopes | Actions (403 `ACCESS_DENIED` if missing) | Project access (404 `PROJECT_NOT_FOUND` if missing) |
| --- | --- | --- | --- |
| `prototype-operator` | `provisioning:all` | the client operations but rollback | every project (`access.json` grants `"*"`) |
| `prototype-tenant` | `provisioning:all` | the client operations but rollback | none until you add a project to `access.json` |
| `prototype-reader` | `provisioning:none` | none: every operation answers 403 | none |
| `prototype-admin` | `provisioning:all`, `ADMIN` | the ten client operations and the four admin ones | any project for `rollback_release` and the admin operations; none for the other client reads |

In the prototype every client operation but rollback needs the one scope `provisioning:all`, so operator and tenant
differ only in project access. Rollback is `ADMIN` only in every profile (FR-50,
[identity.md](../runbooks/identity.md)), like the four admin operations. Production maps the other client operations to
the role `USER`.

To let the tenant act on a project, rewrite `bin/kind-state/proto/provisioning/access.json` atomically (write a new
file, then rename; it is read again when it changes):

```json
{"subjects": [{"subject": "prototype-operator", "projects": "*"},
              {"subject": "prototype-tenant", "projects": ["<project uuid>"]}]}
```

A subject the file does not name has no project; an unreadable file fails closed with 503
`PROJECT_ACCESS_UNAVAILABLE`. A project the caller may not see answers 404, the same as one that does not exist.

## 3. The REST reference

Swagger UI is built into the API **in the prototype profile only** (`presentation/http/docs.py`,
[api-testing-with-swagger.md](../runbooks/api-testing-with-swagger.md)): `http://127.0.0.1:18080/docs` (loads Swagger
UI 5.33.0 from `cdn.jsdelivr.net`; click **Authorize** and paste the token without `Bearer `) and
`http://127.0.0.1:18080/openapi.yaml` (the contract file). In the production profile neither route exists.

The contract is [`src/contracts/openapi.yaml`](../../../src/contracts/openapi.yaml) (version 0.2.0, draft). The 14
operations:

| Method and path | operationId | Purpose | Success | Needs |
| --- | --- | --- | --- | --- |
| `POST /v1/projects/{projectId}/releases/{releaseId}/build` | `build_release` | Start or retry the build of a release | 202 | client |
| `POST /v1/projects/{projectId}/releases/{releaseId}/deploy` | `deploy_release` | Accept a private candidate deployment | 202 | client, key |
| `POST /v1/projects/{projectId}/releases/{releaseId}/promote` | `promote_release` | Accept route promotion (go live) | 202 | client, key |
| `POST /v1/projects/{projectId}/releases/{releaseId}/rollback` | `rollback_release` | Recover an uncertain route to the live release or the offline page | 202 | `ADMIN`, key |
| `GET /v1/projects/{projectId}/releases` | `list_releases` | Releases, newest first (`limit`, `cursor`) | 200 | client |
| `GET /v1/projects/{projectId}/releases/{releaseId}` | `get_release` | Release and step status | 200 | client |
| `GET /v1/projects/{projectId}/releases/{releaseId}/diagnostics` | `get_release_diagnostics` | Redacted, bounded diagnostics | 200 | client |
| `GET /v1/projects/{projectId}/commands` | `list_commands` | Commands, newest first (`limit`, `cursor`) | 200 | client |
| `GET /v1/projects/{projectId}/commands/{commandId}` | `get_command` | Progress of an accepted command | 200 | client |
| `GET /v1/projects/{projectId}/deployment` | `get_deployment` | Live release, routing and hosting status | 200 | client |
| `POST /v1/admin/projects/{projectId}/hosting/suspend` | `suspend_website` | Take the site offline (`admin_suspended`) | 202 | `ADMIN`, admin key |
| `POST /v1/admin/projects/{projectId}/hosting/resume` | `resume_website` | Remove only `admin_suspended` | 202 | `ADMIN`, admin key |
| `POST /v1/admin/projects/{projectId}/redeploy` | `redeploy_release` | Restart the live release's pods, same image | 202 | `ADMIN`, key |
| `GET /v1/admin/projects/{projectId}/commands` | `admin_list_commands` | The site's commands with `requestedBySubject` | 200 | `ADMIN` |

"client" is `provisioning:all` in the prototype and `USER` with project access in production. "key" is the
`Idempotency-Key` header.

**Deploy and promote are requested by a caller today** (the owner or an admin). This is the current behaviour
until automatic deploy and go-live after a successful build (FR-47 to FR-49) is built: a release that reaches
`built` waits for a `deploy` request, and a `candidate_ready` release waits for a `promote` request.

Rules for every call (`openapi.yaml`; `presentation/http/app.py`):

- Send `Authorization: Bearer <token>`. IDs are canonical lowercase UUIDs, not nil. Names are camelCase.
- A success is the envelope `{"success": true, "data": ..., "meta": {...}}`; a list puts the next page in
  `meta.page.nextCursor` (null on the last page). `limit` is 1 to 50, 20 by default.
- `deploy`, `promote`, `rollback` and `redeploy` need an `Idempotency-Key` header of 1 to 255 bytes; the admin
  `suspend` and `resume` take 1 to 249 bytes (stored as `admin:<key>`). The same key with the same request returns the
  original command; the same key with a different request is 422 `IDEMPOTENCY_KEY_REUSED`. `build` takes no key.
- A mutation takes no body. If you send one it must be `{}` with `Content-Type: application/json`.
- No call creates a project or a release: a release appears when Project's "source published" event arrives
  (section 6). Hosting goes off and on from Payment's and Project's events and the admin operations.
- An optional `X-Request-Id` header is echoed; otherwise the service makes one. Quote it when you report a problem.

Values (`openapi.yaml`, schemas): release `status` is `accepted, building, built, candidate_ready, promoting, live,
superseded, failed, rolled_back`; `routingStatus` is `stable, changing, uncertain`; `hostingStatus` is `online,
offline`; command `status` is `accepted, running, succeeded, failed, skipped`; command `kind` is `deploy, promote,
rollback, suspend_hosting, resume_hosting, redeploy`.

### Bruno collection

[`bruno/`](bruno/) holds a [Bruno](https://www.usebruno.com/) collection: plain-text `.bru` files, one per operation
above, with an example answer in each. Open the folder in Bruno (Open Collection), pick the `local` environment and
give the secret variable `token` your token in Bruno's UI. The environment file
[`bruno/environments/local.bru`](bruno/environments/local.bru) sets `baseUrl` to `http://127.0.0.1:18080` and declares
`token` as a secret with no value, so Bruno keeps it outside the repository. Fill the path parameters (`projectId`,
`releaseId`, `commandId`) on each request, and change each request's `Idempotency-Key` for a new attempt.

## 4. The MCP tools reference

The tool list is [`src/contracts/mcp/tools.json`](../../../src/contracts/mcp/tools.json). The 14 tools have the
names of the 14 operations, run the same handlers with the same checks, and return the REST `data`; a list returns
`{items, page}` (FR-46). The endpoint is `presentation/mcp/endpoint.py`. Inputs are camelCase:

| Tool | Inputs (required in bold) |
| --- | --- |
| `deploy_release`, `promote_release`, `rollback_release` | **projectId**, **releaseId**, **idempotencyKey** |
| `build_release`, `get_release`, `get_release_diagnostics` | **projectId**, **releaseId** |
| `get_command` | **projectId**, **commandId** |
| `get_deployment` | **projectId** |
| `list_releases`, `list_commands`, `admin_list_commands` | **projectId**, limit, cursor |
| `suspend_website`, `resume_website`, `redeploy_release` | **projectId**, **idempotencyKey** |

A mutation tool takes its identifiers and `idempotencyKey` only; the server assigns the command number. No tool
creates a release.

### Connecting a client

The transport is Streamable HTTP, stateless, with JSON answers, at `POST http://127.0.0.1:18080/mcp`. Headers
(`endpoint.py`; the prototype's allow-lists in `run-provisioning.sh`):

| Header | Rule |
| --- | --- |
| `Authorization` | `Bearer <token>`, as for REST. Without it the endpoint answers 401 |
| `Content-Type` | `application/json` (exactly one) |
| `Accept` | must cover `application/json` and `text/event-stream`: send `application/json, text/event-stream` |
| `Host` | `127.0.0.1:18080` or `localhost:18080` (`MCP_ALLOWED_HOSTS`); normal clients send it correctly |
| `Origin` | optional; the prototype allows none (`MCP_ALLOWED_ORIGINS=none`), so a browser page is refused with 400 |

Only `POST` (a `GET` or `DELETE` is 405), one JSON-RPC message per request (no batch). With an MCP SDK, give it the
URL and the headers; the end-to-end tests use the official Python SDK (`tests/e2e/_stack.py`, `Stack.mcp`). With
`curl` (the token comes from the environment, never typed):

```sh
curl -sS http://127.0.0.1:18080/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

curl -sS http://127.0.0.1:18080/mcp \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call",
       "params":{"name":"get_deployment","arguments":{"projectId":"<uuid>"}}}'
```

If a `tools/call` is refused, send an `initialize` request first, as an SDK client does. A failed tool call is a
normal result with `isError: true` whose structured content is the Problem of section 5, without `instance`.
HTTP-level refusals (401, 400, 413) are REST answers.

## 5. Errors

Every REST error and every MCP error result is one RFC 9457 Problem Details object (decisions
[`0018`](../decisions/0018-problem-details-errors.md) and `0023`; schema `Problem` in `openapi.yaml`). Members, in
this order:

| Member | Meaning |
| --- | --- |
| `type` | `urn:webblock:problem:` plus the code in lower case with `_` as `-`; an identifier, not a page |
| `title` | The code's fixed message |
| `status` | The HTTP status (the same number on MCP) |
| `instance` | REST only: the request path without the query |
| `code` | The stable code in UPPER_SNAKE, for example `PROJECT_NOT_FOUND`, `ACCESS_DENIED`, `COMMAND_IN_PROGRESS` |
| `requestId` | The request ID; quote it when you report a problem |
| `traceId` | Only when the request is traced |
| `errors` | Always present: field errors (`field`, `code`, `message`) for 400 `VALIDATION_FAILED`, else empty |
| `commandId` | Only with `COMMAND_IN_PROGRESS`: the command in flight |

There is no `detail` member; the body never echoes what you sent. Every code with its status:
[FRD "Errors at a glance"](../requirements/FRD.md#errors-at-a-glance); the stored failure codes of releases and
commands: [failure-codes.md](../runbooks/failure-codes.md).

**Content type follows `Accept`** (decision 0018 section 3): `application/problem+json` whenever `Accept` allows it
(absent, `*/*`, `application/*`, `application/problem+json`); a client that accepts only `application/json` gets the
same body as `application/json`. An error is never a 406.

The body pinned by `tests/contracts/test_http_conformance.py` for `GET /v1/projects/{projectId}/deployment` on a
project with no deployment (`p` stands for the project UUID; a real answer has a real `requestId`):

```json
{
  "type": "urn:webblock:problem:deployment-not-found",
  "title": "The project has no deployment.",
  "status": 404,
  "instance": "/v1/projects/p/deployment",
  "code": "DEPLOYMENT_NOT_FOUND",
  "requestId": "r-1",
  "errors": []
}
```

A 401 also carries `WWW-Authenticate: Bearer`.

## 6. The simulator and the stand-in files

Provisioning never creates a release or changes hosting on its own: the prototype stands in for the other services
with simulated events and files ([prototype-stand-ins.md](../architecture/prototype-stand-ins.md);
`hosts/devtools/simulate.py`; `prototype-demo.md`, "Driving it by hand").

**Events.** The simulator publishes to the prototype's RabbitMQ. It reads the broker URL from
`WEBBLOCK_SIMULATOR_AMQP_URL` and never prints it. This builds the URL in a subshell without echoing it (the Python
environment is `.venv`; `make py-sync` creates it):

```sh
( PW=$(sed -n "s/^MQ_PASSWORD=//p" bin/kind-state/proto/provisioning/provisioning.env | tr -d "'")
  PORT=$(docker port webblock-rabbitmq 5672 | head -1 | sed 's/.*://')
  WEBBLOCK_SIMULATOR_AMQP_URL="amqp://provisioning:${PW}@127.0.0.1:${PORT}/" \
    .venv/bin/python -m provisioning.hosts.devtools.simulate entitlement-ended --project "$P" )
```

Replace the subcommand with any row below. Do not run `make proto-e2e` while you test by hand; it clears sites.

| Subcommand (after `python -m provisioning.hosts.devtools.simulate`) | Stands in for | Effect |
| --- | --- | --- |
| `source-published --project P --repository-id N --commit SHA [--subdomain NAME]` | Project | Creates a release and starts its build |
| `entitlement-ended --project P [--reason R] [--event-key K]` | Payment | Suspends hosting (the offline page) |
| `entitlement-restored --project P [--event-key K]` | Payment | Resumes hosting |
| `project-deleted --project P` | Project | Suspends hosting for good |
| `owner-status --project P --status S --version V --subdomain NAME` | Project | Suspends or resumes for the owner's account status (FR-29) |

`--reason` is one of `trial_ended` (default), `grace_expired`, `plan_canceled`, `over_website_limit`. The simulator
prints `event_key=...` when the broker confirmed the event, and exits 1 if it could not publish.

**Stand-in files**, in `bin/kind-state/proto/provisioning/` (mode 0600, read again when they change; replace them by
write and rename):

| File | Stands in for | Format |
| --- | --- | --- |
| `repositories.json` | Project's repository lookup | `{"repositories": [{"repository_id": N, "project_id": "P", "subdomain": "NAME", "git_url": "http://webblock-git:8080/NAME.git"}]}` |
| `access.json` | Project's access check | section 2 (`standins/access.py`) |
| `entitlement.json` | Payment's entitlement | `{"default_entitled": true, "projects": {"<uuid>": false}}`: a project set to `false` gets 422 `PUBLISH_LIMIT_REACHED` on deploy and promote |

**A sample commit** to publish: `bash scripts/prototype/sample-repo.sh NAME site v1` prints the commit SHA on its
last line. Variants for failures: `never-ready`, `broken-build`, `unready` (`/readyz` answers 503), `slow-start`,
`preview-only` (`prototype-demo.md`).

## 7. Common scenarios

Use `BASE=http://127.0.0.1:18080`, `P` (project UUID), `R` (release UUID) and the header
`Authorization: Bearer $TOKEN`. A `202` answer's `data` holds `commandId` and `statusUrl`; poll
`GET $BASE/v1/projects/$P/commands/<commandId>` until `status` is `succeeded` or `failed`. The expected states are
those of `tests/e2e/test_flows.py`.

**Publish a new release.**
1. `bash scripts/prototype/sample-repo.sh myshop site v1`; note the SHA.
2. Add the repository to `repositories.json` with `"subdomain": "myshop"` (section 6).
3. `simulate source-published --project $P --repository-id N --commit SHA`.
4. `GET /v1/projects/$P/releases` until the newest release is `built` (`building` before; a build takes minutes).
   Its `releaseId` is `R`. If it fails, `GET .../releases/$R/diagnostics`.

**Deploy a private candidate** (a caller requests it today; FR-47 will make it automatic).
1. `POST /v1/projects/$P/releases/$R/deploy` with `Idempotency-Key: deploy-1`. Expect 202.
2. Poll the command until `succeeded`; the release becomes `candidate_ready`. Public traffic has not moved.
3. Repeat step 1 with the same key: you get the original command. Another release with the same key: 422
   `IDEMPOTENCY_KEY_REUSED`.

**Go live** (a caller requests it today; FR-48 will make it automatic).
1. `POST .../releases/$R/promote` with `Idempotency-Key: promote-1`. Expect 202.
2. Poll until `succeeded`. `GET /v1/projects/$P/deployment` shows `liveReleaseId` = `R`, `routingStatus` `stable`,
   `hostingStatus` `online`. The site answers with `Host: myshop.sites.webblock.test` at the Gateway
   (`prototype-demo.md`).
3. Another command for the same project while one runs: 409 `COMMAND_IN_PROGRESS` with `commandId`.

**Failed go-live.**
- Publish a commit of variant `unready`, deploy it, then promote. The candidate fails its preview check on each of its
  3 tries, about 30 s and then 60 s apart (decision `0027`). The command ends `failed` and the release `rolled_back`,
  both with `failureCode` `dependency_unavailable`, and the site keeps serving the old live release
  (`test_4_a_candidate_failing_its_preview_never_receives_the_sites_traffic`).
- A candidate that fails after the switch is restored after each try and ends `rolled_back` with
  `site_not_answering` after try 3 (test 4b).

**Admin rollback.** `POST .../releases/$R/rollback` with the `prototype-admin` token and an `Idempotency-Key` is
accepted only while `routingStatus` is `uncertain`; otherwise expect 422 `RELEASE_STATE_INVALID`. The prototype has
no simple way to make a route uncertain, so you will mostly see the refusal.

**Suspend and resume.**
1. Payment: `simulate entitlement-ended --project $P`. Within a minute or two `GET .../deployment` shows
   `hostingStatus` `offline` and the site answers with the offline page (`test_5_...`). `GET .../commands` lists a
   `suspend_hosting` command.
2. While offline, `deploy` and `promote` are refused with 422 `HOSTING_OFFLINE`.
3. `simulate entitlement-restored --project $P`: `hostingStatus` returns to `online`.
4. Admin: `POST /v1/admin/projects/$P/hosting/suspend` with the `prototype-admin` token and
   `Idempotency-Key: admin-suspend-1`, then `.../hosting/resume`. The resume removes only `admin_suspended`: a site
   that Payment also took offline stays offline and the resume ends `skipped`. Follow both with
   `GET /v1/admin/projects/$P/commands`.

**Refusals to try.** `prototype-reader` on any call: 403 `ACCESS_DENIED`. No token: 401 `UNAUTHENTICATED`.
`prototype-tenant` on a project not in `access.json`: 404 `PROJECT_NOT_FOUND`. Any token but `prototype-admin` on an
admin path: 403 `ACCESS_DENIED`. A project set to `false` in `entitlement.json`: 422 `PUBLISH_LIMIT_REACHED` on
deploy or promote.

## 8. What this guide does not cover

- Reaching a site over HTTPS with the local CA: `prototype-demo.md` "Driving it by hand" and `prototype-stack.md`
  "Addresses and credentials".
- The operator CLI (listing and re-driving failed work): [operator.md](../runbooks/operator.md).
- Logs: `make proto-logs Q='<LogQL>'` and `bin/kind-state/proto/provisioning/logs/`
  ([observability.md](../runbooks/observability.md)).

## Keeping this guide true

No test pins this guide. When `src/contracts/openapi.yaml` or `src/contracts/mcp/tools.json` change, update sections
3 and 4 and the files in `bruno/`.
