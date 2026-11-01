New owner? Start with [`handover.md`](handover.md): what is built, what is open, where it lives.

# Next: work still owed

J0, J1, authentication, J2 and the J3 Notion alignment are on `main`: migration `V6` and `V7` with the sub-domain
(FR-13, FR-36), gRPC `CheckAccess` (FR-31 to FR-33) and the Java layout of Notion 6.1 (TR-5). Its design is
`.claude/docs/designs/j3-notion-alignment.md`, its plan `.claude/docs/plans/j3-notion-alignment-plan.md`. Still to
come: the rest of J3 (Git hosting on Gitea, repository creation, guarded publication, the
repository lookup over gRPC `GetRepository`, PR-18), then J4 (templates, scaffold, Codegen exchange, overlay checks,
`project.source_published.v1`, the `publish_completed` consumer) and J5 (production hardening,
observability; deletion is built, FR-38 to FR-40; the owner-status event is still open). The BRD, PRD, FRD
and TRD are in `.claude/docs/requirements/`; each later phase still needs its
design and plan (`.claude/WORKFLOW.md`).

Decisions: [0010](../../.claude/docs/decisions/0010-java-layout-notion-6-1.md) Java layout,
[0011](../../.claude/docs/decisions/0011-grpc-check-access-and-pinned-contracts.md) gRPC and pinned contracts,
[0012](../../.claude/docs/decisions/0012-checks-on-internal-tables.md) checks on internal tables,
[0013](../../.claude/docs/decisions/0013-grants-on-internal-tables.md) grants on internal tables,
[0014](../../.claude/docs/decisions/0014-catalog-websites-updated-at.md) `catalog.websites.updated_at`,
[0015](../../.claude/docs/decisions/0015-catalog-database-public-connect.md) catalog PUBLIC CONNECT.

Everything below is on `main`: the J3 alignment, the `.claude` makeover with task 8, and the lean flow
(constitution version 6).

## Merged and ruled

The final review and its three re-reviews are in `ai/reviews/final-project-notion-alignment*.md`: verdict ready, all
articles pass. Rulings in force:

- R-1: merged with FR-32.1 and FR-32.2 `manage_site` as known Drift; task 8 has built it since.
- R-2: constitution version 3, Art. III lets the internal check answer `NOT_MANAGER`; callers map it to 404. The
  owner confirmed it as their amendment (FR-31.2).
- R-3: the Art. II enforcement line and the gRPC pins are approved (`.claude/docs/dependency-versions.md`).
- R-4: the plan is approved by the owner.
- R-5: the FR-18 Drift is closed: the limit is the setting `webblock.pages.max-pages`.
- R-6: FR-37 matches Notion; nothing sets `suspended`. "First successful publish" means the release is live.

Guard: the merge and sed rules are fixed (`2bea88c`, `7fb9c09`, `7793638`). Known limit: writes through an
interpreter (`python3 -c`, a heredoc script, `node -e`) are not seen (`guard.py:8-9`), and a failed `cd` before `;`
is not tracked.

## Last gate results

Before the merge: `./gradlew check --rerun-tasks` passed at `2bee190` (`test` 848, `integrationTest` 301,
0 failed, 0 skipped, coverage rules passed). Hook tests 693 of 693 and 215 of 215.
`python3 -I scripts/spec-check.py`: Errors 0, Must FRs with no test: 28. `scripts/contracts-sync.sh --check` clean.
`scripts/osv-scan.sh` clean at `546ceeb` (no dependency change since). The Kind exit check is not run.

## Scope until the push (owner)

The owner pushes this repository soon and another team member takes it over. Until then: no new service logic.
Only the structure, the `.claude` setup, removing unwanted files and folders, the written contracts with the
other services (no implementation), and keeping the code on the latest API contract (Notion "Web Block
architecture", the pinned `web-block-contracts`) and database design (Notion "2 · Project", "6 · Shared catalog").
The rest of J3, J4 and J5 is for the next owner of the service.

## Next steps, in order

The makeover, the contract pages, task 8 and the handover are done. What is open, in order, is section 4 of
[`handover.md`](handover.md): the FR-13 code first. The contract facts that used to sit here (GetRepository,
`publish_completed`, the sub-domain rule) are in `.claude/docs/contracts/provisioning-contract.md`, sections E, G
and H. Keep the handover page true when this list changes.

## For the next owner of the service (not in scope now)

- The older QA items of `qa-project-notion-alignment.md` (Payment HttpClient not closed, two cursor parsers, the
  `"20"` page-size literal, ProfileGuard brackets, OutboxMetrics null check, deprecated test calls, two Javadoc
  warnings, copying List and Map in `application/model` records) and an IntelliJ inspection pass on `src/main`.
- FR-18, carried to the next owner (ruling R-5): `max_pages` is a constant, Notion says a setting. See
  `.claude/docs/requirements/DIRECTION.md`.
- FR-37 (J4): a website becomes `active` when its first release is live. Project consumes
  `provisioning.publish_completed.v1`; a failed first publish stays `draft`. Nothing sets `suspended` (it matches
  Notion, ruling R-6). Not built.
- The Kind check (`.claude/docs/runbooks/j1-kind-exit.md`).
- The rest of J3:
   1. **Two guards.** `GenerationTransitions.move` checks `(failureCode != null) == (to == failed)` once
      `generating -> failed` exists. Add a test for the publish-only-if-moved race once cancellation or deadlines
      can move a captured request.
   3. **Project state at publish.** The project state is not re-checked at the move to `generating`; publication
      must handle a project that became read-only.
   4. **Exact `fieldSchema`.** `fieldSchema` is parsed with a default mapper; a raw pass-through would make it
      exact, as `capturedInput` is.
   5. **Timestamp precision** differs between `created_at`, `updated_at` and `occurred_at`.
   6. **FR-34**: remove the REST check once the owner confirms Provisioning calls FR-31 everywhere.

## Known limits (open)

- If scheduling the admission release itself fails, an abandoned wizard keeps its Payment admission (logged only).
- A timed-out `DELETE` still running at Payment could free a fresh admission. Fixing it needs a Payment contract
  change: release a specific admission, not a project ID.
- The catalog trigger `products_schema_matches_business_type` (SECURITY DEFINER, before RLS) lets a site learn whether
  another website ID exists and its business type. Closing it needs a new catalog migration.
- The catalog `socketTimeout` can still be overridden by a `CATALOG_DB_URL` parameter.
- Non-canonical path UUIDs are canonicalized silently (the same in every controller).
- The seed reference data and the component catalog are placeholders.
- Completed outbox records are never purged. A retention job needs an owner decision and must keep `payment:` records.
- The gRPC port is open on Kind, as `/internal/**` is: Kind has no Istio yet.

## Waiting on others

- **Owner, mesh:** port 9090 named `grpc` on Project's Deployment and Service, and one `AuthorizationPolicy` rule
  for `provisioning-api` on `CheckAccess`, in `web-block-config` (`.claude/docs/runbooks/grpc.md`).
- **Owner, V6 rollout:** the read-only collision count (O-6), a backup with its name written down, stop the old
  version, the migration job, start the new version with Provisioning's routing by sub-domain
  (`.claude/docs/runbooks/migrations.md`, "Rolling out V6").
- **Owner, J4 broker:** the Project queue `project.publish_completed.v1` on `provisioning.events` and a read right
  for Project's user (Notion "2 · Project" 4.4 and 5.2).
- **Owner:** the push of this repository; a Kind slot for the Kind exit check.
- **Provisioning:** routing by sub-domain for `V6` (O-4); confirm the `admin_allowed` flags of the action list, which
  stays DRAFT until then; its gRPC client for CheckAccess; apply catalog `V5` to `V7` on its Kind stack (its Flyway
  run of this repository's catalog migrations).
- **Frontend:** the wizard sends `subdomain` on create.
- **Payment team:** accept "mesh identity, no bearer" (payment contract section 6, point 5); decide whether a
  deleted project frees a website slot (assumption A2) and the other open questions in section 6.
- **AI Codegen team:** accept `.claude/docs/contracts/service-communication.md`, section 2.
- **Keycloak team:** set up the realm as `.claude/docs/contracts/authentication-contract.md` says.
- **Owner as DevOps:** Istio on Kind (until then `/internal/**` and port 9090 are open there); Istio and Keycloak on
  the real cluster, with the placeholders filled (Istio version, `<domain>`, namespaces and ServiceAccounts, tool
  hostnames, `<jenkins-host>`, callback and post-logout URLs) and a complete `REGISTRY_ONLY` `ServiceEntry` list.
- **Owner:** CI engine; broker permissions for a restricted `project` user (whether declaring `project.events` with
  its alternate exchange needs read permission); template and component catalog versioning.
- **Shared auth library** (Java starter and Python package) for inner calls carrying `user_id`: to be planned.

## Open for the owner

- **Sub-domain labels:** ruled by the owner (FR-13.5 to FR-13.12 in the FRD); the code is open, see the handover.
