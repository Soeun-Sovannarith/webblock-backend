# J1 exit on Kind (lead only)

Proves on Kind that Provisioning's `ProjectAccess` calls the Project service. Provisioning runs this repository's
catalog migrations and the HTTP `ProjectAccess` adapter (`.claude/docs/contracts/provisioning-contract.md`); its
access check carries no token. Never open or use a kubeconfig yourself;
Provisioning's `make proto-*` targets check their own local Kind context. The Kind slot rule holds: no Kind stack
while this repository's Testcontainers suites run.

Ports: the Project service listens on `127.0.0.1:8080` (management `8081`); Provisioning's API on `127.0.0.1:18080`
(`PROV_API_PORT`); the two never clash.

1. **Provisioning stack.** In `provision-service`: `PROJECT_SERVICE_DIR=<this repository's checkout> make proto-up`.
   Its catalog database carries this repository's migrations (check: `catalog_flyway.flyway_schema_history`
   lists 1 to 5 and the repeatable seed). In a fresh Provisioning worktree run `make kind-tools` first (kind, kubectl,
   helm and crane go into that worktree's `bin/`). `proto-up` refuses to start below 7000 MB of available memory
   (`KIND_MIN_AVAILABLE_MB`); wait for memory rather than lowering it.
2. **Project database and broker** (containers this runbook creates and later removes):
   `docker run -d --name webblock-project-db -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=<generated> postgres:18`;
   then as its superuser run `scripts/db/bootstrap-roles.sql`, create `project_migrator_login` and
   `project_app_login` and database `webblock_project` as `.claude/docs/runbooks/database.md` says;
   `docker run -d --name webblock-project-mq -p 127.0.0.1:55672:5672 rabbitmq:4.3.6-management-alpine`.
3. **Tokens.** Generate one random token (32 to 512 characters) and two fresh UUIDs, A and B. Write
   `bin/j1-dev-tokens.json` (git-ignored, mode 0600) in the form
   `{"tokens": [{"token", "subject", "userId", "scopes"}]}` with one entry: user A, `userId` A. Every entry needs a
   `userId`: the Project service refuses the whole file at start-up otherwise. There is no service token: Provisioning's
   access check carries none (the mesh
   policy is the gate; on Kind, `/internal` is open).
4. **Run the Project service** in the prototype profile, exporting the passwords from files, never typing them on the
   command line:
   ```sh
   SPRING_PROFILES_ACTIVE=prototype PROJECT_DEV_TOKENS_FILE=bin/j1-dev-tokens.json \
   PROJECT_DB_URL=jdbc:postgresql://127.0.0.1:55432/webblock_project \
   PROJECT_DB_APP_PASSWORD=... PROJECT_DB_MIGRATOR_PASSWORD=... \
   RABBITMQ_PORT=55672 RABBITMQ_USER=guest RABBITMQ_PASSWORD=guest \
   CATALOG_DB_URL=jdbc:postgresql://127.0.0.1:<PROTO_CATALOG_PORT_ON_HOST>/catalog \
   CATALOG_DB_PLATFORM_USER=project_catalog_platform CATALOG_DB_PLATFORM_PASSWORD=<from catalog-admin.env> \
   CATALOG_MIGRATIONS_ENABLED=false PROJECT_SITE_DOMAIN=<Provisioning's SITE_DOMAIN> ./gradlew bootRun
   ```
   The Payment stand-in admits everything when `PROJECT_PAYMENT_STAND_IN_FILE` is unset. Readiness:
   `curl -s 127.0.0.1:8081/actuator/health/readiness` shows `UP` (both databases and RabbitMQ).
5. **Provisioning in `project-service` mode.** In `provision-service`:
   ```sh
   make proto-run PROJECT_ACCESS_MODE=project-service PROJECT_SERVICE_URL=http://127.0.0.1:8080 \
     PROTO_TENANT_USER_ID=<A> PROTO_OPERATOR_USER_ID=<B>
   ```
   Do not set `PROJECT_SERVICE_TOKEN_FILE`: Provisioning's API refuses it at start-up. Its tenant token then acts as
   user A, its operator token as user B. Not
   the reader token: its scope is `provisioning:none`, so Provisioning refuses it (403 `action_not_permitted`) before
   any access check. In a fresh Provisioning worktree run `make py-sync` first.
6. **Check.** Export `PROJECT_URL=http://127.0.0.1:8080`, `PROVISIONING_URL=http://127.0.0.1:18080`,
   `PROJECT_TOKEN_A` (user A's Project token), `PROVISIONING_TOKEN_A` (Provisioning's tenant token),
   `PROVISIONING_TOKEN_B` (its operator token, `TOKEN_PASSWORD` in Provisioning's `provisioning.env`) and
   `BUSINESS_TYPE=clothing_shop` (while the seed is the placeholder),
   then run `scripts/kind/j1-exit-check.sh`: five `PASS` lines. Then confirm the catalog row:
   `SELECT status FROM catalog.websites WHERE website_id = '<PROJECT_ID>'` as `project_catalog_platform` shows `active`.
7. **Fail closed.** Stop the Project service; run `PROJECT_ID=<id> scripts/kind/j1-exit-check.sh --expect-unavailable`:
   `PASS` (503 `project_access_unavailable`). Start it again; step 6's owner check passes again.
8. **Clean up.** `docker rm -f webblock-project-db webblock-project-mq`; `make proto-stop proto-down` in Provisioning
   when done. Paste the command outputs (no tokens) into the current handoff in `ai/handoffs/`.
