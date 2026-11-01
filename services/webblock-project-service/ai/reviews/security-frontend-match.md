# Security review: frontend-match (REST shapes, sub-domain at first deploy, V8)

Stage 5. Branch `frontend-match`, range `main..frontend-match` (50f78da, 327243e, 984c489, acc5a2d, b29c87d; 90
files), read in the worktree `.claude/worktrees/subdomain-at-first-deploy`. Reviewer model: claude-opus-5-5.
IDs in scope: FR-2, FR-3, FR-8, FR-9, FR-13, FR-14, FR-15, FR-16, FR-24, FR-38, FR-41; decision 0018. The commits
cite no TR. Rows the tests cite but the spec lacks: FR-13.18, FR-13.19, FR-13.20, FR-24.12, FR-38.11, FR-9.7.

## Method

- Skills: `differential-review` (diff triage, blast radius of the nullable sub-domain, test coverage of changed
  paths), `sharp-edges` (unchecked public methods, normalisation mismatches, config). `supply-chain-risk-auditor`
  not used: no dependency change.
- Commands:
  - `git log/diff --stat main..frontend-match`: 5 commits, 90 files. None touch `build.gradle`, `gradle/`, the
    lockfiles, `SecurityConfig`, `JwtDecoderConfig`, `common/infrastructure`, `application*.yml`, `contracts/` or
    `contracts.lock` (an empty `--stat` for those paths).
  - `git diff` for the V8 migration, the catalog R__ file, generation, project, page, wizard and template features.
  - `git grep` for every user of `subdomain()` and `platformHostname()` (blast radius of NULL), the constraint
    registry (`ProjectConfig`), `GlobalExceptionHandler` integrity mapping, `SecurityConfig` matchers.
  - Added lines scanned for secrets (`password|secret|token|bearer|apikey|private key|eyJ`): none. For new logs:
    one `LOG.warn` with no user data. For outbound HTTP, `@Profile`, method security and deletes: none.
  - `python3 -I scripts/spec-check.py`: 10 errors (the rows above do not exist in `spec.json`); Must FRs with no
    test: 24. Report only.
- Not run: `./gradlew check` (the implementer reports test 938, integrationTest 445, 0 failed; no finding needs a
  rerun), `scripts/osv-scan.sh` (no dependency change). Not checked: V8 against Notion "2 · Project" (the
  `db-notion-auditor` covers it; Notion not fetched here). The cluster mesh `AuthorizationPolicy` (not changed).

## Findings

No critical, high or medium finding.

1. **Low**: no test for two websites racing for one sub-domain at the first deploy.
   `src/main/java/org/kshrd/webblock/project/application/project/handler/ProjectService.java:121` (the conditional
   UPDATE). It breaks FR-13 (FR-13.2 "one sub-domain, the loser is HOSTNAME_TAKEN") and Art. V. The branch removed
   `ProjectCreationIntegrationTest` "FR-13.2: two creates racing for one sub-domain ... HOSTNAME_TAKEN" (a
   `CyclicBarrier` race) and `SubdomainFirstDeployIntegrationTest` has no replacement. The unique index
   `projects_subdomain_key` still guarantees one host per site, so no site can be served at another's host. What is
   unproven: that the unique violation now raised by a JPQL bulk UPDATE (not an insert) inside `capture` reaches
   `GlobalExceptionHandler` as 409 `HOSTNAME_TAKEN` and not 500 `INTERNAL_ERROR`. Fix: add an integration test
   where two owners' first captures with the same sub-domain pass `subdomainTaken` together (barrier) and assert one
   201 and one 409 `HOSTNAME_TAKEN`, and that the loser's project keeps a NULL sub-domain and no request.

2. **Low**: the availability check normalises the name and the deploy does not.
   `ProjectService.java:74` strips and lower-cases (`" Shop "` answers `name: "shop", available: true`), while
   `setSubdomain` at `ProjectService.java:111` and the equality test at
   `GenerationRequestService.java:134` use the raw value (`"Shop"` is 400 `VALIDATION_FAILED`, and on a later deploy
   `"Shop"` against `shop` is 409 `SUBDOMAIN_ALREADY_SET`). FR-13 keeps one rule in one place. Not exploitable: only
   `[a-z0-9-]` is ever stored, so no case or homoglyph twin can be registered. Fix: normalise once (strip and
   lower-case) in the capture path before validation, the hash and the comparison, or have the check refuse what the
   deploy refuses; pin it with a test.

3. **Low**: a nullable sub-domain meets contract fields that require one.
   `.claude/docs/contracts/provisioning-contract.md:160` (`owner_status_changed.v1`: `subdomain` required, "one event
   per project of the owner") and `:130` (`GetRepository` answers `subdomain`, 1 to 63), with
   `.claude/docs/contracts/account-contract.md:41`. It breaks Art. III (another service's contract is not changed
   without the owner). Both are unbuilt (PR-19, PR-21, PR-18), so nothing ships wrong today; a never-deployed website
   now has no sub-domain. Abuse when built carelessly: a `null` or `"null"` label sent to Provisioning, or an event
   that fails its schema and dead-letters. Fix: record in decision 0018 (or the contract's open points) what
   `owner_status_changed` and `GetRepository` send for a website without a sub-domain (skip it, or make the field
   optional), and agree it with Provisioning before PR-21 and PR-18 are built.

4. **Low**: public read paths with no owner check widen.
   `WebsitePageService.java:63` (`of(projectId, write)`, public, readOnly transaction) and
   `GenerationRequestService.java:287` (`latestPublished(projectId)`, "No owner check") return the published Puck
   tree of any project ID. Every current caller checks the owner first (`PageService.list`/`writable` call
   `getOwned`; `PageVersionService.restore` calls `getOwned`), so Art. III holds today. A future controller that
   calls either directly would leak another client's page with no 404. Fix: make `of` package-private (its callers
   are in the same package) and keep `latestPublished` documented as caller-checked, or take the owner and call
   `getOwned` inside.

## Checked and clean

- Tokens: `SecurityConfig` and `JwtDecoderConfig` unchanged; `/api/**` still needs the user role
  (`SecurityConfig.java:97`), so `/page`, `/versions`, `/subdomains/{name}` and counts all need a token.
- Not the owner is 404: `capture` calls `lockOwned` before the idempotency read and before any sub-domain answer, so
  `SUBDOMAIN_REQUIRED`, `SUBDOMAIN_ALREADY_SET` and `HOSTNAME_TAKEN` reach only the owner; `setSubdomain` re-checks
  with `lockOwned` and `whyNotChanged`; `GET/PUT /page` go through `PageService.list`/`writable` (`getOwned`);
  versions list and restore call `getOwned` before `versions`/`versionInput`, which filter on `projectId`;
  suspend/reactivate use `lockOwned` and owner-filtered UPDATEs; tests cover the stranger case
  (`SubdomainFirstDeployIntegrationTest` "a stranger is 404 PROJECT_NOT_FOUND and no sub-domain is set").
- Published copy: `latestPublished` reads `generation_requests` where `r.projectId = :projectId and status =
  published` (`GenerationRequestRepository.java:68`), only the home page's `puckTree` is returned, only after the
  owner check.
- List search: the owner and `deletedAt is null` stay ANDed outside the parenthesised OR
  (`ProjectRepository.java:114-118`); business type IDs come from the catalog, not the caller; the LIKE pattern
  keeps its escaping; the catalog-down path logs no user input.
- Counts: same owner-scoped query; only JSON keys change.
- Races on one project: two first deploys serialise on `lockOwned` (FOR UPDATE, READ COMMITTED), the second sees
  the set value (`SUBDOMAIN_ALREADY_SET`, or `GENERATION_IN_PROGRESS` for the same value); the UPDATE carries
  `subdomain is null`, and the V8 trigger refuses value to value and value to NULL. Any later failure in `capture`
  rolls the sub-domain back with the request (one transaction).
- Idempotency: the hash adds `subdomain` only when sent, so pre-change hashes of `{}` and of a contract ID still
  match; a replay with the same body answers the stored request before any sub-domain logic; a refused capture
  stores nothing, so a retry with another name is free.
- DomainCheck: static English problem texts, no project data, no upstream reason; "taken" was already answered as
  `TAKEN`; reserved names come from public config; the length is checked first. The echoed `name`/`host` is JSON.
- Errors: `SUBDOMAIN_ALREADY_SET` and `SUBDOMAIN_REQUIRED` are fixed texts (409), gRPC maps them to
  `ALREADY_EXISTS`/`FAILED_PRECONDITION`; unmapped integrity errors log the constraint name only.
- Database: V8 is new (V1 to V7 untouched), forward-only; `project_app` gains column UPDATE on `subdomain`,
  `suspended_at`, `reactivated_at` only (`GrantsTest` pins the full list); `template_code` has no UPDATE grant and is
  `updatable = false`; the guard keeps `SET search_path = pg_catalog` and no `SECURITY DEFINER`; no DELETE added.
  The catalog R__ change is reference data only (upsert, no grant, no delete).
- Create body: unknown fields are 400 (`MALFORMED_REQUEST` test); `category`, `thumbnailUrl`, `details.logoUrl`,
  `details.businessType` are size-bounded, never stored, never fetched (no outbound HTTP added).
- Profiles and stand-ins: no `@Profile` or stand-in change; `ProfileGuard` untouched.
- Method security: none added (`@PreAuthorize`, `@Secured`, `@RolesAllowed`, `EnableMethodSecurity` absent).
- Secrets: none in added code, tests, fixtures or docs.
- Supply chain: no change to `libs.versions.toml`, `gradle.lockfile` or `verification-metadata.xml`; OSV not
  needed.
- Sub-domain squatting: one name per website is reserved for ever, as before (it was taken at creation); a deleted
  website still keeps its name. Not widened by this change.

Verdict: approved (four Low findings for the backlog; none blocks the merge)
