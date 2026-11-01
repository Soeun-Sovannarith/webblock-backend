# Prototype demo runbook (task G, decision 0014)

Scope: running the Provisioning service's four processes against the local prototype stack, the end-to-end suite, and
the clothing-shop demo. Local test infrastructure only; it proves nothing about DigitalOcean Kubernetes, the owner's
Jenkins or a production database.

The real-cluster rule of `.claude/CLAUDE.md` section 0 applies unchanged. The worker and bootstrap processes get their
project-local kubeconfig by explicit path (`bin/kind-state/proto/worker.kubeconfig`, `bootstrap.kubeconfig`), and the
client refuses any kubeconfig whose context is not `kind-webblock-*` on `https://127.0.0.1:<port>`. The processes run
with a cleared environment, so no `KUBECONFIG` reaches them. Nothing reads `.env`, and the owner's `postgres` and
`rabbitmq` containers are never touched.

## Commands

From the module root, in this order:

| Command | What it does | Time |
| --- | --- | --- |
| `make proto-up` | the stack (`.claude/docs/runbooks/prototype-stack.md`; the catalog schema comes from the Project service at `PROJECT_SERVICE_DIR`, decision 0017) | 84 s with warm caches |
| `make proto-run` | Provisioning's own containers, roles, migrations, logins, configuration, the four processes; waits until the API is ready | 5 to 8 s (first time: image start, about 2 min if the RabbitMQ image must start cold) |
| `make proto-reset-sites` | prototype only: clears the sites that finished tests left behind (see "Repeated runs" below); `proto-e2e` and `proto-demo` run it first | about 30-60 s |
| `make proto-e2e` | the flows and edge cases on the sample site (`tests/e2e`, without the demo) | about 2.5 min |
| `make proto-demo` | the clothing shop from commit to live, then five sites at once (`tests/e2e/demo`) | 80 s on a cold BuildKit cache (npm), 15 s warm |
| `make proto-stop` | stops the processes, removes `webblock-provisioning-db` and `webblock-rabbitmq` and the generated secrets; logs stay | a few seconds |
| `make proto-down` | removes the stack | 2 to 3 s |

`bash scripts/prototype/run-provisioning.sh status|restart|start-process NAME|stop-process NAME|kill-process NAME`
manages single processes (`api`, `worker`, `build_worker`, `bootstrap`); `restart` reloads all four after a code change.

## What runs

| Process | Logins (each a member of one role) | Cluster identity | Does |
| --- | --- | --- | --- |
| `api` (`python -m provisioning.hosts.api`) | `prov_api_login` (`provisioning_api`), `prov_build_login` (`provisioning_build`, for `build_release`) | none | REST on `127.0.0.1:18080`, MCP at `/mcp`, `/healthz` and `/readyz` on `127.0.0.1:18081` |
| `worker` | `prov_worker_login` (`provisioning_worker`), `prov_hosting_login` (`provisioning_hosting_intake`) | worker kubeconfig | dispatcher; command consumer for five kinds (`sync_domains`, kept in the schema, fails `operation_unavailable`: no custom domains, decision 0014 section 23); Payment-event consumer with the in-process entitlement record; the promotion probe over the Gateway's HTTPS listener, verified against `local-ca.crt`, with the preview token |
| `build_worker` | `prov_intake_login` (`provisioning_intake`), `prov_build_login` | none | "source published" consumer, inbox processor and release intake, builds on Jenkins, poller and sweeper |
| `bootstrap` | `prov_bootstrap_login` (`provisioning_api`, reads only) | bootstrap kubeconfig | every second: projects with a release on its way to a deploy → tenant (namespace, quota, limits, ReferenceGrant, RoleBinding) → `access_setup` (catalog role `site_<hex>` and Secret `catalog-runtime`) |

## Files

`bin/kind-state/proto/provisioning/` (folder 0700, git-ignored):
- `provisioning.env`: generated passwords (database superuser and logins, broker, the development token) and
  `PREVIEW_TOKEN`, the worker's preview probe token (task T, recheck N1), 0600, never printed;
- `env/<process>.env`: each process's whole configuration, 0600; the keys are listed by the process when started with an
  empty environment (exit 2);
- `tokens.json`: the development token file (one operator with scope `provisioning:all`);
- `repositories.json`: the prototype repository locator; the processes read it again when it changes, so a test or demo
  registers its project by rewriting it atomically;
- `logs/<process>.log`: JSON, no secrets, no SQL (client loggers are kept at WARNING); `logs/migrations.log`;
- `timings/*.json`: the durations the e2e suite and the demo measured.

## Driving it by hand

- A sample commit: `bash scripts/prototype/sample-repo.sh NAME site v1` (variants: `site`, `never-ready`,
  `broken-build`, `unready` with `/readyz` 503, `slow-start` that listens after 20 s, `preview-only` with `/readyz` 200
  only on a preview hostname, a test fixture for the restore after a failed switch).
- Register the repository: add
  `{"repository_id": N, "project_id": "<uuid>", "subdomain": "NAME", "git_url": "http://webblock-git:8080/NAME.git"}` to
  `repositories.json` (write a new file and rename it). The sub-domain is a DNS label (lower-case letters, digits and
  `-`), one per entry; the first release copies it into `project_hosting.subdomain` (decision 0021). A file with the old
  key `github_repository_id` is refused.
- After this change, delete or rewrite an old `repositories.json` that still uses the key `github_repository_id`:
  `run-provisioning.sh` keeps an existing file (only its `stop` removes it) and writes an empty one only when none is
  there.
- Publish: `WEBBLOCK_SIMULATOR_AMQP_URL=... python -m provisioning.hosts.devtools.simulate source-published --project P
  --repository-id N --commit SHA`. The broker URL is in `provisioning.env` (`MQ_PASSWORD`, port from
  `docker port webblock-rabbitmq 5672`); do not print it.
- Queue names changed in Phase B (decision `0022`): recreate the stack with `make proto-down; make proto-up` before the
  first run. Before the first publish the simulator command `declare-producers` must have run (the run script does it);
  it declares `project.events`, `payment.events` and Payment's usage queue.
- Every REST answer carries `X-Request-Id`. Send your own (`^[A-Za-z0-9._:-]{1,128}$`) to find the request in the logs;
  a bad or missing one is replaced by a new UUIDv7.
- Tracing is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` names an OTLP/HTTP collector (see
  [prototype-stack.md](prototype-stack.md)).
- Owner status: `... simulate owner-status --project P --status suspended --version 1 --subdomain NAME` publishes
  Project's "owner status changed" event; the worker turns it into a suspend (or, with `active` and a higher version, a
  resume) with the key `owner-status:<version>`.
- Then `POST /v1/projects/P/releases/R/deploy` and `.../promote` with `Idempotency-Key` and the bearer token from
  `tokens.json`. The site answers at the Gateway (`PROTO_GATEWAY_ADDRESS` in `stack.env`) with
  `Host: NAME.sites.webblock.test`, where `NAME` is the sub-domain from `repositories.json` (decision 0023 G18), and
  over HTTPS at `PROTO_GATEWAY_HTTPS_URL` with the wildcard certificate of the local CA: `curl --resolve
  NAME.sites.webblock.test:<https port>:<gateway address> --cacert bin/kind-state/proto/local-ca.crt
  https://NAME.sites.webblock.test:<https port>/`. The promotion probes the candidate at
  `NAME-preview.sites.webblock.test` first (decision 0023 G18b). A site has no other hostname (decision 0014 section
  23).
- The shop: `python -m provisioning.hosts.devtools.catalog_platform register --project P --business-type clothing_shop`,
  `bash scripts/prototype/publish-demo-site.sh clothing-shop`, publish, wait for `built`,
  `catalog_platform seed --project P --file deploy/prototype/demo-sites/clothing-shop/seed/seed.json` (retried until
  access_setup has written the Secret), deploy, promote.

## Repeated runs on one stack (`make proto-reset-sites`)

Every test site stays running at 256 MiB, and the tests do not delete it. After about 60 tenant pods the Kind worker
node has no memory left (`FailedScheduling ... Insufficient memory`) and deploys fail with `progress_deadline_exceeded`
(finding M-2 of wave 6's validation). `make proto-e2e` and `make proto-demo` therefore start with
`make proto-reset-sites` (`scripts/prototype/reset-sites.sh`), so they run again and again on one stack without
`proto-down`. It:
1. stops the four Provisioning processes, so nothing recreates a site while it is cleared;
2. deletes the tenant namespaces `wb-*` through the project's admin kubeconfig (context `kind-webblock-proto` on
   `127.0.0.1` only) and waits until they are gone;
3. truncates every table of the schemas `provisioning` and `provisioning_ops` in the prototype's own container
   `webblock-provisioning-db`, as its superuser. The schema, the migration table and the roles stay. The owner's
   `postgres` container is never addressed;
4. purges the queues of the prototype's own `webblock-rabbitmq`;
5. restarts the processes, waits `RESET_SETTLE_SECONDS` (12 s; the bootstrap loop runs every second and the dispatcher
   every 250 ms) and fails if a namespace `wb-*` or a row has come back.

It prints the worker node's memory requests before and after. It does not touch the catalog database, the Git server,
Jenkins or the registry. Running `pytest` by hand does not reset; run `make proto-reset-sites` yourself first when the
node is crowded.

## The shop's outage page

When the shop cannot read its catalog it answers `503` with a `Retry-After`, never `200` (`src/proxy.ts` and
`src/lib/outage.ts` in the shop; a page cannot choose its status once it renders). A `200` page without product cards
looks like a shop with no products and was the signature of the one flaky demo run (0 cards). `tests/e2e/demo` therefore
waits up to 60 s while the shop answers 503 (`Stack.visit_when_available`) and, on failure, prints the status and the
start of the body. The shop's own checks: `npm test` (`tests/outage.test.ts`) and `scripts/docker-check.sh` (503 on `/`
and on a product page with the catalog unreachable). A read that fails after the gate passed still renders the friendly
page with 200 (rare).

## Known limits

- The entitlement record of hosting commands lives in the worker's memory and keeps each project's Payment events in
  order, by event key (decision 0014 section 17 item 2). A `suspend_hosting` ends `skipped` (`entitlement_restored`)
  only when the record holds an "entitlement restored" event that arrived after the suspend's own event; a
  `resume_hosting` ends `skipped` (`entitlement_ended`) only when an "entitlement ended" event arrived after its own.
  When the record does not know (a restarted worker, another replica, or more than 32 events of one project or 100 000
  projects, oldest dropped first) the command runs: a shutdown is never dropped. Production asks Payment instead
  (OD-04).
- The bootstrap loop remembers ready tenants in memory and ensures them once more after a restart (idempotent).
- The Kubernetes readiness probe of a release is TCP, not `/healthz` (D's renderer); `/readyz` is only the promotion
  probe (decision 0014 section 15).
- One worker, one build worker, one bootstrap process; no replicas were run.
