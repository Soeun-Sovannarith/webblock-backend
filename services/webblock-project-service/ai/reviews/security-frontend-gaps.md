# Security review: frontend gaps, page versions, one-call deploy

Stage 5. Worktree `.claude/worktrees/one-call-deploy`, range `54a477b..0fd2205` (de1efe7, 6fc2088, 0fd2205).
Reviewer model: claude-opus-5-5. In scope: FR-9.5, FR-9.6, FR-10.12, FR-10.13, FR-14.10, FR-14.11, FR-22, FR-24.7 to
FR-24.10, FR-41.1 to FR-41.9; TR-19, TR-21, TR-22, TR-32, TR-33, TR-35.

## Method

- Skills: `differential-review` (diff, callers, tests), `sharp-edges` (defaults, optional fields, error paths).
- `git diff --stat` and full `src/main` diff; read at 0fd2205 `ProjectCreationService`, `GenerationContractService`,
  `GenerationRequestService.capture`, `PageService.update`, `PageRules.checkedTree`, `RequestCursor`,
  `CatalogReference`, `CatalogDataSourceConfig`, `SecurityConfig`, `application.yml`, decision `0007`.
- Grep of the diff for logging, tokens and `Authorization`: no new log line. New `@DisplayName` tests listed.
- Not run: Gradle (brief: another check is running), `scripts/osv-scan.sh` (no dependency, lockfile or version change).

## Findings

**Medium. Catalog read and contract freeze under the project row lock, reachable on every refused retry.**
`GenerationRequestService.java:101,122,151,172`. FR-24.7, TR-19, TR-21 (and the class comment of
`GenerationContractService.java:36-37`, "never inside a transaction"). After `lockOwned` the ID-less path reads the
catalog (`allActiveProductTypeSchemas`), then `contractService.create` reads it again and freezes rows, all before the
insert that fails on `generation_requests_one_in_flight`. There is no in-flight check first, so an owner sending
captures with fresh `Idempotency-Key`s while a request is in flight runs two cross-database reads plus a rolled-back
contract each time. Each catalog read may wait 2 s for a connection plus 5 s statement time, so the row lock can be
held about 14 s; page saves on that project then fail at the 5 s `lock_timeout` (503), and parallel captures across a
user's projects take the shared 5-connection catalog pool from the wizard. Fix: read the active schemas before taking
the lock (a non-transactional entry, then a `TransactionTemplate` for the locked part, as `GenerationContractService`
does), add an in-flight check right after the read-only check, and freeze with the already-read schemas instead of
calling `create` (which reads the catalog a second time). Then update the class comment.

**Low. The name rule can be bypassed and raced.** `ProjectService.java:221`, `ProjectRepository.java:70`,
`ProjectCreationService.java:115`. FR-10.12. Java `strip()` removes all Unicode whitespace, SQL `trim()` removes only
spaces, and the stored name is not stripped, so a name saved with a leading tab or NBSP never matches. The check runs
in its own read transaction with no index, so two concurrent creates both pass. Data integrity only, no cross-owner
effect. Fix: store `name.strip()` and compare it as stored, or accept the race and note it in FR-10.12.

**Low. N+1 queries on the versions list.** `GenerationRequestService.java:262`. FR-41.1, TR-35. One `count` query per
row, up to 100 per call. Fix: only the first row of the first page can be `current`; compute it once.

## Checked and clean

- Not the owner is 404 (Art. III, TR-32): `list` and `restore` call `getOwned` (`PageVersionService.java:44,57`)
  before reading the version; capture calls `lockOwned` before the idempotency lookup. Tests FR-41.3, FR-24.10.
- IDOR on versionId: `findByProjectIdAndGenerationRequestId` plus a `published` filter; another project's request is
  `VERSION_NOT_FOUND` (FR-41.8 test).
- Deleted is 404, suspended reads allowed and writes 422 (decision `0007`): `getOwned` filters `deletedAt`; restore
  goes through `PageService.update`, which calls `writable` (FR-41.7 test).
- Restore validation: the frozen tree goes through `checkedTree` with the project's current catalog version, the
  1 MiB size limit and `checkSortOrder`; `captured_input` is written only by the server (FR-41.9 test).
- Idempotency: the hash of `{}` and the lookup run before any contract work under the row lock; an ID-less replay
  returns the same request with no second contract (FR-24.9 test); a replay that adds a contract ID is 409
  `IDEMPOTENCY_KEY_REUSED`; old hashes with an ID are unchanged. A failed capture rolls back the contract it made.
- Enumeration: counts use only the token's user ID; `PROJECT_NAME_TAKEN` is per owner and comes after the existing-ID
  check, so `PROJECT_ID_CONFLICT` and the identical repeat keep precedence (FR-10.12 repeat test).
- Error bodies (TR-33): new codes carry fixed messages; no name, ID or tree echoed.
- Auth (TR-30): `/api/**` still needs a user token; the template detail and counts have 401 tests. No change to
  `SecurityConfig`, `JwtDecoderConfig`, `/internal/**`, profiles, stand-ins or method security.
- Secrets and supply chain (Art. VI, Art. II): no secret, config, dependency, migration or grant in the diff.
- Defaults: the business name falls back to the website name (both at most 255); the time zone default is
  `Asia/Phnom_Penh`, and a sent value is still checked.

Verdict: approved (one Medium for the backlog or the one fix round; no High)
