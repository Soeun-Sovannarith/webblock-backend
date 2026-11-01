# Provisioning contract (what Provisioning and the Project service rely on)

Between the Project service and Provisioning: the catalog migrations Provisioning's Kind stack runs, the access
check, the repository lookup, the events in both directions and the settings the Project service needs in production.
Each side changes its own repository. A change to this contract is a new version of this document, agreed by both
leads. The index of every exchange is `.claude/docs/contracts/README.md`; the shared rules for events and HTTP are in
`.claude/docs/contracts/service-communication.md`, section 1.

Machine files are in `web-block-contracts`, pinned in `contracts.lock` (branch `main`, `18dd301`). Provisioning pins the
same commit. Each file carries a status header; `ACCEPTED` means both sides agreed.

| Exchange | Section | State | IDs | Built in Project |
| --- | --- | --- | --- | --- |
| Catalog database | A | agreed | FR-35, FR-36, PR-17 | yes |
| Access check, REST | B | agreed, goes when Provisioning switches | FR-30, FR-34, PR-12 | yes |
| Access check, gRPC | B | agreed | FR-31, FR-33, PR-13 | yes |
| Action list | D | proposed by Project | FR-32, PR-14 | no |
| `GetRepository` | E | draft, Project accepts with open points | PR-18 | no |
| `source_published`, `project_deleted`, `owner_status_changed` | F | draft, Project accepts; `project_deleted` built | PR-19 to PR-21 | no |
| `publish_completed` consumed by Project | G | owned by Provisioning; Project's queue needs owner items | FR-37 | no |

## A. Catalog database on Kind (`deploy/kind/proto-catalog.sh` in Provisioning)

The catalog schema belongs to the Project service (`project-service/src/main/resources/db/migration/catalog`).

1. **Roles.** Provisioning creates the three NOLOGIN roles with this repository's
   `scripts/db/catalog-bootstrap-roles.sql` (`catalog_owner`, `catalog_platform`, `catalog_site`), and its own login
   roles: `catalog_migrator` with `INHERIT FALSE, SET TRUE` on `catalog_owner`; `project_catalog_platform` with
   `INHERIT TRUE, SET FALSE` on `catalog_platform`; `provisioning_catalog_admin`, `CREATEROLE` with `ADMIN` on
   `catalog_site`. Then the database (`CREATE DATABASE catalog OWNER catalog_owner`) and
   `ALTER ROLE catalog_migrator IN DATABASE catalog SET role = 'catalog_owner'`.
2. **Migrations.** The Flyway CLI image, pinned by digest, at the Flyway version the Project service's Spring Boot BOM
   manages (12.4.0, image `flyway/flyway:12.4.0-alpine`):
   ```sh
   docker run --rm --network kind -e FLYWAY_PASSWORD \
     -v "$PROJECT_SERVICE_DIR/src/main/resources/db/migration/catalog:/flyway/sql:ro" "$PROTO_FLYWAY_IMAGE" \
     -url="jdbc:postgresql://$PROTO_CATALOG_NAME:5432/$PROTO_CATALOG_DATABASE" -user=catalog_migrator \
     -initSql="SET ROLE catalog_owner" -schemas=catalog_flyway -defaultSchema=catalog_flyway \
     -locations=filesystem:/flyway/sql -placeholderReplacement=false migrate
   ```
   `FLYWAY_PASSWORD` comes from `CATALOG_MIGRATOR_PASSWORD` (environment only, never the command line).
   `PROJECT_SERVICE_DIR` defaults to `../project-service`; the script fails if the directory or
   `V1__catalog_tables.sql` is missing.
3. **Reference data.** The repeatable `R__catalog_reference_data.sql`. Business type codes use the `*_shop` form
   (`clothing_shop`), with the product type codes `top`, `outerwear`, `trousers`, `accessory`.
4. The Project service registers websites itself (`catalog.websites`). Provisioning's
   `devtools/catalog_platform.py register` is only for stacks run without the Project service.

## B. The access check over HTTP

**Precondition for production:** the mesh is the only gate. STRICT mTLS, no port exclusions or `hostNetwork`, and
the Project service's Istio `AuthorizationPolicy` allowing `POST /internal/v1/access-checks` only from Provisioning's
API ServiceAccount and the tenant namespaces (`.claude/docs/decisions/0002-authentication-and-service-identity.md`).
Without that policy the endpoint is open to every pod in the mesh.

Provisioning selects it with `PROJECT_ACCESS_MODE=project-service`.

- **Call:** `POST {PROJECT_SERVICE_URL}/internal/v1/access-checks`, `Content-Type: application/json`,
  `X-Request-Id`, no `Authorization` header, body (camelCase):
  `{"userId": "<account.users UUID>", "projectId": "<uuid>", "action": "<action>"}`.
  Accepted actions: `deploy_release`, `promote_release`, `rollback_release`, `build_release` (changes);
  `get_release`, `get_command`, `get_deployment`, `get_release_diagnostics`, `list_releases`, `list_commands` (reads).
  Any other value is answered `400`.
- **Answer:** `200` with the standard success envelope: `success: true` and `data: {"allowed": true}`, or
  `data: {"allowed": false, "reason": "NOT_FOUND" | "NOT_MANAGER" | "PROJECT_DELETED" | "PROJECT_SUSPENDED"}`
  (Provisioning raises `ProjectAccessDenied`). **Anything else** (timeout, connection error, any non-200, malformed
  body) means unavailable: Provisioning fails closed with `ProjectAccessUnavailable`. Every error body is RFC 9457
  Problem Details (`.claude/docs/decisions/0003-http-and-event-formats.md`). Log `code` and `requestId` if you log
  anything; never branch on `title`.
- **Rules the Project service applies:** only the owner (`owner_user_id`) is allowed; a deleted or suspended project
  allows the six reads and refuses the four changes.
- **Timeout:** the caller bounds the call (2 s).
- **Identity:** Provisioning reads the platform user ID from the token claim `user_id`. A principal without a user ID
  is denied without calling.
- **Operators:** Provisioning decides operator actions (realm role `ADMIN`) before calling; the Project service
  answers for owners only.
- **No service token.** There is no `PROJECT_SERVICE_TOKEN_FILE`, no allow-list, no service role and no scope. In the
  cluster, `PROJECT_SERVICE_URL` is a plain `http://` Service URL; the sidecar adds mTLS. A bearer sent by mistake is
  ignored, never answered `401` or `403`. Never relay the end user's token.
- **Tests:** Provisioning tests the answer mapping above against a stub server.
- **gRPC:** the same check is served as `webblock.project.v1.AccessService/CheckAccess` on port 9090 (FR-31, decision
  0011, `.claude/docs/runbooks/grpc.md`; proto `proto/webblock/project/v1/access.proto`, status ACCEPTED). The request
  is `user_id`, `project_id`, `action`; the answer is `allowed` and `reason` with no envelope. An unknown project is
  an OK answer with `allowed` false and reason `NOT_FOUND`; bad input is `INVALID_ARGUMENT` with `ErrorInfo`; a busy
  database is `UNAVAILABLE`. It reads no token. This REST endpoint stays until the owner confirms Provisioning calls
  gRPC everywhere (FR-34); then it, decision 0002 and this section change in one change.
- **IDs:** FR-30, FR-31, FR-33, FR-34, PR-12, PR-13. Built: yes, both.

## C. Settings the Project service needs in production

The production profile (`SPRING_PROFILES_ACTIVE=production`) refuses to start unless each of these holds. The
prototype profile accepts `application.yml`'s defaults.

| Environment variable | Property | Production requirement |
| --- | --- | --- |
| `PAYMENT_BASE_URL` | `webblock.payment.base-url` | a plain `http` URL of an in-cluster Service name (`.svc` or `.svc.cluster.local`; the sidecar adds mTLS), or an `https` URL of any other real host; no user info, no port above 65535, no loopback host, and no `invalid`, `test`, `.invalid` or `.test` name (the default `http://payment.invalid` is refused) |
| `PROJECT_SITE_DOMAIN` | `webblock.platform.site-domain` | a real domain, the suffix Provisioning serves sites under (`SITE_DOMAIN`); the default `sites.webblock.test` and any `invalid`, `test`, `.test` or `.invalid` name is refused |
| `PROJECT_JWT_ISSUER_URI` | `spring.security.oauth2.resourceserver.jwt.issuer-uri` | an `https` URL |

Calls to Payment carry no token; Payment's sidecar admits the Project service by its mesh identity.

Keycloak's access tokens must carry `typ: Bearer`, Keycloak's default. The Project service refuses any other token
type, so an ID token (`typ: ID`) is answered `401` on `/api`.

In every profile, `PROJECT_DB_URL` and `CATALOG_DB_URL` must not carry the JDBC parameters `logServerErrorDetail`,
`socketTimeout` or `options` (any case): they would replace the pools' own settings, so the service refuses to start
and the migration job exits 2 before connecting.

## D. The action list

`access/actions.v1.json` (and its schema) on `main` names the 11 actions `CheckAccess`
accepts, each with `name`, `write` and `admin_allowed`. It adds `manage_site` to the ten in section B. `write` is true
for a change and false for a get or list. Only `rollback_release` has `admin_allowed` true. Header: DRAFT, proposed by
Project, waits for Provisioning. Project reads the list at build time; an action not on it is refused (400 on REST,
`INVALID_ARGUMENT` on gRPC).

Serves FR-32, PR-14. Built: yes. Project's `AccessAction` has the 11 actions, `AccessActionListTest` compares it
with the pinned file. The site portal's check uses `manage_site` with the owner's `user_id`
from a token with audience `site`, cached for at most 60 seconds. Over the mesh it is the same `CheckAccess`. A public
REST endpoint for sites on another cluster (Notion "Web Block architecture" section 9 point 3) is an open owner
question.

## E. Repository lookup `GetRepository`

- **Direction and transport:** Provisioning's build worker calls Project over gRPC, deadline 2 s, retry on
  `UNAVAILABLE` at most 2 times. There is no REST version.
- **File:** `proto/webblock/project/v1/repository.proto` on `main`, header "DRAFT, proposed by provisioning, waits for
  project". Project accepts it as written. The copy on `main` records Project's answers.
- **Request:** `project_id`. **Answer:** `repository_id` (1 to 2^53-1), `clone_url`, `full_name` (logs only),
  `subdomain` (a DNS label, 1 to 63).
- **Errors:** `NOT_FOUND` with `ErrorInfo.reason` `REPOSITORY_NOT_FOUND` when the project has no repository. Only this
  reason is a permanent reject on Provisioning's side (`repository_not_registered`). `NOT_FOUND` with
  `PROJECT_NOT_FOUND` is treated as an unavailable dependency and retried until `repository_unconfirmed`. Any other
  failure is retried.
- **Provisioning's answers:**
  - `clone_url` must come from Project and must not be empty. Provisioning passes it to Jenkins as `REPOSITORY_URL`, so
    Jenkins must reach it. That is likely the Cloudflare Access Git address; this is an inference and the owner decides
    the host. Project stores `repository_id` and `repository_full_name` and no URL, so it builds the URL from a
    configured Gitea base and the full name.
  - The answer needs no commit SHA. Intake compares `repository_id` with the event's, and the SHA comes from the event.
  - The only caller is `provisioning-build-worker`.
- **Open owner points:** the `clone_url` host; one rule for a deleted project (proposed: the same
  `REPOSITORY_NOT_FOUND`); the mesh rule for the build worker (`grpc.md` admits `provisioning-api` on `CheckAccess`
  only).
- **Serves** PR-18. **Built:** no. Provisioning keeps the call refused in production while the proto is a draft. It is
  meaningful only after Project writes `repository_id` and `repository_full_name`, both or neither, and before it
  publishes `source_published`.

## F. Events Project publishes to Provisioning

Exchange `project.events`; the rules for envelope, outbox and queues are in `service-communication.md`, section 1. All
three files are on `main`, header "DRAFT, proposed by provisioning, waits for project". Project accepts the shapes as
written. They stay DRAFT until Project emits them and both sides move their pinned commit. Valid and invalid examples
sit beside the schemas.

| Event | Routing key | Fields beyond the envelope | Consumer rule | IDs |
| --- | --- | --- | --- | --- |
| `project.source_published.v1` | `source_published.v1` | `generation_request_id`, `repository_id`, `git_commit_sha` (40 or 64 hex), optional `subdomain` | Queue `provisioning.source_published.v1`; bad body to the dead-letter queue after 5 tries | PR-19 |
| `project.project_deleted.v1` | `project_deleted.v1` | none | Provisioning sends `suspend_hosting` with the fixed key `project-deleted`; repeats are harmless | PR-20 |
| `project.owner_status_changed.v1` | `owner_status_changed.v1` | `status` (`active` or `suspended`), `status_version` (at least 1), `subdomain` (required) | Drops a version at or below the stored one; command key `owner-status:<version>` | PR-21 |

- **`source_published`:** Provisioning reads four fields (`project_id`, `generation_request_id`, `repository_id`,
  `git_commit_sha`) and ignores `subdomain`; the routing sub-domain comes from `GetRepository`. Project still sends it.
  The AMQP `message_id` must equal the body's, and `type` must equal `message_type`. The inbox key is `project_id` plus
  `generation_request_id`; a second event with the same key and another repository or SHA is refused. So Project
  publishes one source per request, set once, and a re-publish after a crash carries the same values. It is written in
  the transaction that sets the request `published`. Notification also consumes it (queue
  `notification.source_published.v1`; [notification](notification-contract.md)).
- **`project_deleted`:** written in the transaction that sets `deleted_at`. Payment also consumes it
  (`payment-contract.md`, section 4). A project with no hosting row is acknowledged. Built (FR-38): the owner's
  `DELETE /api/v1/projects/{projectId}` sets `deleted_at`, publishes the event with the envelope only and schedules
  the catalog archive in one transaction. A deleted project is `NOT_FOUND` to the access check, so Provisioning no
  longer reads a deleted project's releases through it.
- **`owner_status_changed`:** one event per project of the owner, deleted projects included. `status_version` is
  Account's counter, relayed from `account.user_status_changed.v1` ([account](account-contract.md)).
- **Built:** `project_deleted` (FR-38, PR-20). Not built: `source_published` and `owner_status_changed`
  (PR-19, PR-21).

## G. `publish_completed`, consumed by Project (FR-37)

- **File:** `events/provisioning/publish_completed.v1.schema.json` on `main`, OWNED BY provisioning. Fields beyond the
  envelope: `event_key` (`publish-completed-<release_id>`) and `release_id`. Payment consumes it today.
- **When it is sent:** only when a release is confirmed live after promotion, in the same transaction as the live
  write. Never for a failed release. Once per release that goes live, not once per project. Each send has a new
  `message_id`.
- **What Project does:** the first event for a project moves a `draft` website to `active`. Later events change
  nothing, and a project not in `draft` is left alone. Project de-duplicates on `event_key`. A failed first publish
  sends nothing, so the website stays `draft`. Nothing sets `suspended`.
- **What the owner must add:** Project's queue `project.publish_completed.v1`, bound to `provisioning.events` (topic,
  key `publish_completed.v1`), and read permission on `provisioning.events` for Project's broker user. Notion "Web
  Block architecture" 4.4 and 5.2 list no Project consumer for it yet.
- **Serves** FR-37, PR-9. **Built:** no. Project must not rely on a field Payment may still change; the file is
  Provisioning's.

## H. Sub-domain rule

Project accepts a sub-domain that matches `^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$` (3 to 63 characters) and is not on its
reserved list (FR-13). Provisioning's check `project_hosting_subdomain_check` accepts
`^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`. Project's rule is a strict subset, so a sub-domain Project accepts never fails
the hosting insert. Both accept labels that start with `xn--`. Sub-domains are unique in Project's database
(`projects_subdomain_key`; a deleted project keeps its sub-domain, FR-13.2).

Provisioning serves previews at `<subdomain>.preview.<site domain>`, so it asks Project to reserve the label
`preview`. Project's reserved list (`webblock.platform.reserved-subdomains` in `application.yml`) is `www`, `app`,
`api`, `admin`, `git`, `mail`, `status`, `docs`, `auth`: `preview` is not on it yet. Adding it is an FR-13 change,
open with FRD Q-2. State: asked by Provisioning, waiting for Project.

## I. Mesh policy

The owner writes the `AuthorizationPolicy` for Project's callers in `web-block-config`. The callers this contract
needs: `provisioning-api` on `CheckAccess` and the REST check, `provisioning-build-worker` on `GetRepository`, and the
tenant namespaces `wb-*` for the site portal check. Today `grpc.md` states one rule only. Serves TR-31, TR-38. Not
built by Project; the policy is the owner's.
