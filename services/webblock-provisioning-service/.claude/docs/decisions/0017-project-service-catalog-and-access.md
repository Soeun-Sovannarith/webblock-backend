# 0017: The Project service owns the catalog migrations; `ProjectAccess` over HTTP

**Amended by decision [`0019`](0019-service-identity-is-the-mesh.md)**: the access check
carries no service token, `PROJECT_SERVICE_TOKEN_FILE` is removed, and production may use `project-service`. The
changed parts of section 2 are marked.

**Amended by decision [`0024`](0024-auth-and-telemetry-alignment.md) (G6, G7)**: the user's platform ID is the
`user_id` claim, read by web-block-commons; `IDENTITY_USER_ID_CLAIM` is no longer a setting and is refused by name. A
JWT without a valid `user_id` is refused 403 before any call; only a development token may lack one. The Project
service's HTTP check also carries `x-user-id`, `x-request-id` and `traceparent` (G3).

Status: **Current; decided by the owner** (the Project service's J1 rulings, given in the lead session). The source is
the Project service's handover `docs/handoffs/provisioning-j1-changes.md` (in the Project service's git history before
its commit `2782c64`); this record is Provisioning's own rule for it. The Kind stack's catalog setup
(`deploy/kind/proto-catalog.sh`) and the HTTP access check (`src/provisioning/infrastructure/project/access.py`,
`PROJECT_ACCESS_MODE=project-service`) implement it. The gRPC access check of decision `0022` E7 is the other mode.

## 1. Catalog migrations come from the Project service

- The Project service (Spring Boot, sibling repository `../project-service`) owns the catalog schema and its
  migrations (`src/main/resources/db/migration/catalog`, Flyway `V1` to `V4` plus the repeatable reference data
  `R__catalog_reference_data.sql`). This answers the migration part of OD-46.
- **The prototype's Kind stack switches in J1**. `deploy/kind/proto-catalog.sh`:
  1. creates the three NOLOGIN roles (`catalog_owner`, `catalog_platform`, `catalog_site`) with the Project service's
     `scripts/db/catalog-bootstrap-roles.sql`, and keeps creating Provisioning's login roles, the database and the
     `catalog_migrator` session role exactly as today;
  2. runs the Project service's catalog migrations with the Flyway CLI image `flyway/flyway:12.4.0-alpine`, **pinned by
     digest** in `deploy/prototype/catalog/versions.sh` (resolved with `bin/crane digest`), as `catalog_migrator` with
     `SET ROLE catalog_owner`, history in schema `catalog_flyway`, `placeholderReplacement=false`; the password reaches
     the container only through the environment;
  3. reads the Project service from `PROJECT_SERVICE_DIR` (default `../project-service`) and fails if the directory or
     `V1__catalog_tables.sql` is missing.
- Provisioning's own copies `deploy/prototype/catalog/migrations/00001` to `00005` are deleted; goose stays the
  tool for Provisioning's own migrations only. An existing stack carries goose's catalog history and must be recreated
  (`make proto-down proto-up`).
- **Business type codes use the `*_shop` form**: the demo shop's `clothing` becomes `clothing_shop`, with the
  same IDs and product type codes. Every seed, test and default that names `clothing` changes with it.
- `devtools/catalog_platform.py register` stays, for stacks run without the Project service.

## 2. `ProjectAccess` over HTTP

- A new `PROJECT_ACCESS_MODE` value **`project-service`** selects an adapter that calls
  `POST {PROJECT_SERVICE_URL}/internal/v1/access-checks` with `X-Request-Id` and the body
  `{"userId", "projectId", "action"}` (camelCase; `action` is one of Provisioning's ten action values in snake_case).
  *(Amended by `0019`: no `Authorization` header; the caller is its mTLS identity in the mesh. The first version sent
  a service bearer token.)*
- **Answers:** `200`, `success: true`, `data.allowed: true` → allowed; `data.allowed: false` with reason `NOT_FOUND`,
  `NOT_MANAGER`, `PROJECT_DELETED` or `PROJECT_SUSPENDED` → `ProjectAccessDenied`; **anything else** (timeout,
  connection error, non-200, `success: false`, malformed or oversized body) → `ProjectAccessUnavailable` (fail closed).
  The call is bounded by its own timeout of **2 s**; the response body read is bounded.
- **Rules the Project service applies**: only the owner is allowed; a deleted or suspended project allows the
  six reads and refuses the four changes. Operator actions are decided by Provisioning before any call; the Project
  service answers for owners only.
- **The user's platform ID.** `Principal` carries an optional platform user ID. With `IDENTITY_MODE=jwt` it comes from
  the claim named by the new required setting `IDENTITY_USER_ID_CLAIM` (the Project service uses `user_id`); dev-token
  entries get an optional `user_id`. A principal without a user ID is denied without a call.
- **Service token.** *Removed by `0019`:* there is no service token. The call is
  authenticated by the mesh (STRICT mTLS; the Project service's sidecar policy admits the caller's ServiceAccount),
  and **production may use `project-service`**; it still refuses the stand-ins and `PROJECT_ACCESS_FILE`. The end
  user's token is never relayed. *(History: the first version read a static prototype token from
  `PROJECT_SERVICE_TOKEN_FILE`, planned a Keycloak client-credentials token for production, and refused
  `project-service` in production until OD-05.)*
- **Settings**: `PROJECT_SERVICE_URL` and `IDENTITY_USER_ID_CLAIM` are required when the mode or identity
  mode that uses them is selected, with no defaults (OD-15); reasons never contain a value. *(Amended by `0019`:
  `PROJECT_SERVICE_TOKEN_FILE` is gone and refused by name; in production `PROJECT_SERVICE_URL` is plain `http` only
  to an in-cluster Service name, otherwise `https`.)*
- The stand-ins (`PrototypeAllowProjectAccess`, `PrototypeFileProjectAccess`) stay and remain the default of
  the prototype stack; `make proto-run` passes the new mode and settings through when they are set. The J1 exit run of
  the Project service selects `project-service`.

## 3. Records

- **OD-01:** the access part is *contract proposed, implementation pending* until the Project service's J1 is merged
  on its `main`, then *answered*; production may use it since `0019` (no token; the first version waited on OD-05);
  the repository-confirmation part follows the Project service's J3.
- **OD-46:** the migration part is answered here; the `catalog.websites` writer is the Project service's catalog
  registration job (its J1 Task 6); the admin credential rule is unchanged (`0011` section 5).
