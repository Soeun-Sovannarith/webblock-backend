# web-block: the Project service

Backend for clients' websites: projects, pages (Puck JSON), generation requests, publication to Gitea,
and the events Provisioning consumes. Agents and developers start at [`.claude/CLAUDE.md`](.claude/CLAUDE.md).

Built: projects and the wizard, pages, the catalog registration, Payment admission, the access check for Provisioning,
generation contracts, capture and admission, `codegen-input`, the sub-domain and gRPC `CheckAccess`. Next: Git
hosting (rest of J3); work still owed is in [`ai/handoffs/next.md`](ai/handoffs/next.md); a new owner reads
[`ai/handoffs/handover.md`](ai/handoffs/handover.md) first. Decisions:
[`.claude/docs/decisions/`](.claude/docs/decisions/README.md).
Contracts with other services: [`.claude/docs/contracts/`](.claude/docs/contracts/).

## Requirements

JDK 25 and Docker (for the integration and messaging tests). Gradle comes from the wrapper.

## Commands

```sh
./gradlew spotlessApply       # format
./gradlew test                # unit and slice tests
./gradlew integrationTest     # PostgreSQL 18 and RabbitMQ through Testcontainers
./gradlew check               # every gate: format, Error Prone, all tests, coverage
scripts/osv-scan.sh           # known-vulnerability scan of gradle.lockfile (needs network; not part of check)
```

`scripts/osv-scan.sh` downloads a pinned osv-scanner into the git-ignored `bin/`, verifies its SHA-256 (fails closed
on a mismatch), scans `gradle.lockfile` and exits non-zero when it finds vulnerabilities.

Dependency changes: `./gradlew resolveAndLockAll --write-locks` and
`./gradlew --write-verification-metadata sha256 check`, committed with the change.

Running the service locally:

1. Start PostgreSQL 18 and RabbitMQ, for example
   `docker run -d --name webblock-project-db -p 127.0.0.1:5432:5432 -e POSTGRES_PASSWORD=<generated> postgres:18` and
   `docker run -d --name webblock-project-mq -p 127.0.0.1:5672:5672 rabbitmq:4.3.6-management-alpine`.
2. Create the roles and databases as [`database.md`](.claude/docs/runbooks/database.md) and
   [`catalog-database.md`](.claude/docs/runbooks/catalog-database.md) say (`scripts/db/` holds the role SQL).
3. Copy [`.env.example`](.env.example) to `.env` (git-ignored), fill the empty values, and load it:
   `set -a; . ./.env; set +a`. It names every variable the service reads, with its default.
4. `./gradlew bootRun`, then `curl -s 127.0.0.1:8081/actuator/health/readiness` shows `UP`.

`SPRING_PROFILES_ACTIVE=prototype|production` is required. The prototype profile reads its dev
tokens from `PROJECT_DEV_TOKENS_FILE` (mode 0600, git-ignored); dev tokens stand for users only, so every entry needs a
`userId`. There are no service tokens: `/internal/**` takes no token (the Istio mesh policy is its gate; Kind has no
Istio yet, so it is open there) and the Payment client sends none
([decision 0002](.claude/docs/decisions/0002-authentication-and-service-identity.md)). The catalog database comes from
`CATALOG_DB_URL`, `CATALOG_DB_PLATFORM_USER` and `CATALOG_DB_PLATFORM_PASSWORD`
([`.claude/docs/runbooks/catalog-database.md`](.claude/docs/runbooks/catalog-database.md)). The J1 exit on Kind, with
Provisioning: [`.claude/docs/runbooks/j1-kind-exit.md`](.claude/docs/runbooks/j1-kind-exit.md) and
`scripts/kind/j1-exit-check.sh`.

API description, prototype profile only (TR-40): Swagger UI is at `http://127.0.0.1:8080/swagger-ui/index.html` and
the OpenAPI JSON at `/v3/api-docs`; both open without a token and describe `/api/v1/**` only, never `/internal/**`.
Press Authorize and paste a dev token from `PROJECT_DEV_TOKENS_FILE` (or a Keycloak access token in jwt mode) to try a
call. The description is generated from the code; Notion "Web Block architecture" section 2 stays the authority for the
API contract. The production profile serves neither: `ProfileGuard` refuses `springdoc.api-docs.enabled` or
`springdoc.swagger-ui.enabled` set to true there.

Database roles, migrations and the test fixture:
[`.claude/docs/runbooks/database.md`](.claude/docs/runbooks/database.md).

Event delivery (the outbox): [`.claude/docs/runbooks/outbox.md`](.claude/docs/runbooks/outbox.md) for operating it,
[decision 0001](.claude/docs/decisions/0001-outbox-with-modulith-and-namastack.md) for why it is built that way.

The gRPC server (port 9090, `CheckAccess` for Provisioning):
[`.claude/docs/runbooks/grpc.md`](.claude/docs/runbooks/grpc.md).
