# 0001: Domain rules

**See also decision [`0011`](0011-phase-4-go-tracks.md)**: a failed deploy leaves the release `built` unless the cluster
refuses the intent itself (section 3); new transition `candidate_ready → built` when a newer deploy replaces an idle
candidate (section 4); candidate attempts are `candidate` step rows (section 6). Where they differ, `0011` wins.

Status: **Current.** Decision `0007` adopts revision 16 and lists what it overturned, including two transitions the
owner had confirmed in the earlier version of this record. Unmarked lines restate `.claude/PLATFORM_DB_DESIGN.md`
section 5 (P) or `ai/reviews/provision-db-migration-note.md` (N).

The domain package (`src/provisioning/core/domain`) and its tests implement this record. The lead's rulings that
`0007` section 7 lists still wait for the owner's review (OD-16). Changing a rule means changing this record first.

## 1. Enum values

Go constants carry exactly the PostgreSQL labels, in the declaration order below. Each enum has a function returning all
values in this order; an integration test compares it with `pg_enum`.

| Go function | Values in order |
| --- | --- |
| `ReleaseStatuses()` | `accepted`, `building`, `built`, `candidate_ready`, `promoting`, `live`, `superseded`, `failed`, `rolled_back` |
| `ReleaseStepNames()` | `build`, `access_setup`, `candidate`, `promotion`, `observation`, `rollback` |
| `ReleaseStepStatuses()` | `pending`, `running`, `succeeded`, `failed` |
| `RouteStates()` | `stable`, `changing`, `uncertain` |
| `HostingStates()` | `serving`, `offline` |
| `CommandKinds()` | `deploy`, `promote`, `rollback`, `suspend_hosting`, `resume_hosting` |
| `CommandStatuses()` | `accepted`, `running`, `succeeded`, `failed`, `skipped` |

The resource state enums are removed with `tenant_resources`. A command is in flight while `accepted` or `running`.
"Unfinished" for a release (`accepted`, `building`, `built`, `candidate_ready`, `promoting`) is descriptive only: no
rule limits how many a project has.

## 2. Release transitions

Every pair not listed is illegal and returns `ErrIllegalTransition`. A transition to the same status is illegal.
`superseded`, `failed` and `rolled_back` are terminal.

| From | To | Conditions |
| --- | --- | --- |
| `accepted` | `building` | Build start |
| `accepted` | `failed` | Failure code required. The build could not start |
| `building` | `built` | Image digest required (section 5). Reached only through the build-result check of section 7 |
| `building` | `failed` | Failure code required |
| `built` | `candidate_ready` | Outcome of a deploy command |
| `built` | `failed` | Failure code required. Only when a candidate or `access_setup` step failed for good; the public route was never touched |
| `candidate_ready` | `promoting` | A promote command starts; in the same transaction `route_state` goes `stable → changing` |
| `candidate_ready` | `failed` | Failure code required. The candidate failed its recheck when the promote started |
| `promoting` | `live` | Route and external checks passed for this release; sets `promoted_at` |
| `promoting` | `rolled_back` | Failure code required. The project has an active release, it is not this release, and the restore was verified |
| `promoting` | `failed` | Failure code required. The project has no active release (first publish); the route was removed and verified |
| `live` | `superseded` | The caller names the release that became live, which is not this release |

Rules that go with the table:

- The caller supplies the project's active release ID with any transition out of `promoting`. With an active release,
  `promoting → failed` is illegal; without one, `promoting → rolled_back` is illegal.
- A release whose restore failed or is unknown stays `promoting` while the route is `uncertain`. No transition reports a
  recovery that was not verified.
- A command that fails before its work touched the release leaves the release unchanged. A deploy that fails with no
  failed candidate step leaves it `built`, and it can be deployed again. A promote refused at its start (for example
  entitlement lost) leaves it `candidate_ready`.
- `rolled_back` marks the failed replacement. It was never confirmed live and its `promoted_at` is null. `superseded` is
  a release that was live and was replaced; it is never a rollback target again. `live` means confirmed active, not
  receiving traffic: a suspended project keeps its `live` release.
- **Removed:** `live → rolled_back` and `superseded → live`, which the owner had confirmed before revision 16. The
  rollback target is always the project's `active_release_id`, so releases carry no predecessor.
- Every transition sets `updated_at`. `release_id`, `project_id`, `source_generation_request_id`,
  `source_repository_id`, `source_commit_sha` and `created_at` never change. `promoted_at` is written only by
  `promoting → live`.
- `image_digest` is write-once and recorded only on `building → built`. Supplying a digest on any other transition
  returns `ErrImmutableField`, even when it equals the recorded one. A non-nil pointer counts as supplied whatever it
  points at (validation finding V2-08, item 3). On `building → built` an omitted or empty digest is
  `ErrMissingImageDigest`. A non-empty digest that does not parse returns `ErrInvalidImageDigest` on every transition,
  before `ErrImmutableField`.

A new release is `accepted`, with non-nil release, project and generation-request IDs, a repository ID and a commit SHA
in the formats of section 5, and no digest, failure or `promoted_at`.

## 3. Project state

One row per project: the active release, the fencing counter, the route state and the hosting state.

| `route_state` from | To | Event |
| --- | --- | --- |
| `stable` | `changing` | A promote, `suspend_hosting` or `resume_hosting` starts |
| `changing` | `stable` | A verified outcome: confirmed live, restored, route removed after a failed first publish, offline page in place, serving again |
| `changing` | `uncertain` | A restore failed or its result is unknown; recovery found neither expected route |
| `uncertain` | `changing` | An operator rollback starts |

`stable → uncertain` and `uncertain → stable` are illegal. A deploy never changes `route_state`. `resume_hosting` moves
the route like `suspend_hosting` does; N states it only for suspend.

`hosting_state` goes `serving → offline` when a suspend is verified and `offline → serving` when a resume is verified,
each in the same transaction as `changing → stable`.

`active_release_id` changes only when a release is confirmed live. During a promotion it still names the old release, so
it is always the rollback target.

Invariants. The application and its tests hold them; decision `0004` section 9 says which the database also backs.

1. `route_state = 'changing'` implies exactly one command in flight, `running`, of kind promote, rollback, suspend or
   resume, whose `fencing_generation` equals `operation_generation`.
2. A `promoting` release implies `route_state` is `changing` or `uncertain`. At most one release per project is
   `promoting`.
3. `active_release_id` is set exactly when it names the project's `live` release.
4. `operation_generation` equals the highest `fencing_generation` of the project's commands, or 0. Only command
   acceptance moves it.
5. `uncertain` admits only `rollback`. `offline` admits no deploy and no promote.

## 4. Commands

Status: `accepted → running`, then `running → succeeded`, `running → failed` or `running → skipped`; and
`accepted → failed` for a command that was never claimed before its deadline. All other pairs are illegal. `succeeded`,
`failed` and `skipped` are terminal.

- `skipped` is reachable only from `running` and only for `suspend_hosting` and `resume_hosting`. A deploy or promote
  that loses entitlement when it runs ends `failed`.
- A failure code in the format of section 9 is required on `failed` and on `skipped`, and absent otherwise.
- A promote that ends with the route restored is a `failed` command. An operator rollback that restores the route is a
  `succeeded` command; when the project has no active release (the uncertain route came from a first publish), its
  success is the route removed and verified, with the release `failed`.

Preconditions at acceptance. A command belongs to the same project as its release; a mismatch returns
`ErrProjectMismatch`.

| Kind | `release_id` | Release status | `route_state` | `hosting_state` |
| --- | --- | --- | --- | --- |
| `deploy` | required | `built` | `stable` | `serving` |
| `promote` | required | `candidate_ready` | `stable` | `serving` |
| `rollback` | required | `promoting` | `uncertain` | `serving` |
| `suspend_hosting` | null | not applicable | `stable` | checked when it runs |
| `resume_hosting` | null | not applicable | `stable` | checked when it runs |

- A hosting command that names a release, or any other command without one, returns `ErrInvalidCommandTarget`.
- A route that is not `stable` returns `ErrRouteNotStable` for every kind except rollback. A rollback when the route is
  not `uncertain` returns `ErrIllegalTransition`.
- A deploy, promote or rollback while hosting is `offline` returns `ErrHostingOffline`. For rollback this fails closed:
  no state that the other rules allow has a `promoting` release while hosting is `offline`, and whether an operator may
  ever restore a route for a suspended project is OD-45. Widening it needs a decision.
- A release in the wrong status returns `ErrIllegalTransition`.
- **Older releases.** A deploy or promote is refused with `ErrReleaseOutdated` when the project has an active release
  and the target was created before it. "Before" compares `created_at`, then `release_id`. The order is Provisioning's
  acceptance order; whether Project's event carries its own order is OD-02.
- Precedence when several apply: project mismatch, command target, consistency of the active release supplied by the
  caller (it must be the release the project state names, and `live`), route state, hosting state, release status, older
  release. An empty or unknown route or hosting state is refused, never read as `stable` or `serving` (validation
  finding VR-01).
- A rollback names the project's `promoting` release. Which release it names when the route is `uncertain` and no
  release is `promoting` (possible only after a failed hosting command) is OD-45; until then such a rollback is
  `ErrIllegalTransition`.

(OD-53) **Requester.** A deploy, promote or rollback command records who issued it: `requested_by`, the subject of the
verified token, 1 to 255 bytes under the same character rule as the key. A hosting command has none, because it comes
from a Payment event. A command of a client kind without a requester, a hosting command with one, or a malformed value
returns `ErrInvalidRequester`. The value never changes, is not part of `request_hash`, and a replay by another principal
returns the stored command with its original requester. It attributes a command; it is not the audit trail (OD-14).

**Idempotency.** Keys are scoped to a project. A key is 1 to 255 bytes with no control characters, no line or paragraph
separators, no bidirectional controls (the same characters the failure-summary rule of section 9 refuses), and no
leading or trailing whitespace (validation finding VR-06). An incoming command whose key exists is a replay when its
`request_hash` equals the stored one, and returns the stored command whatever its status. A different hash returns
`ErrIdempotencyConflict` and changes nothing.

 **`request_hash`** is the lowercase hexadecimal SHA-256 of these UTF-8 bytes, each line ended by a line feed:

```
provisioning.command.v1
<kind label>
<project ID>
<release ID, or for a hosting kind the Payment event key>
```

IDs are canonical lowercase UUID text. The key of a hosting command is the Payment event key, which has no control
characters, so the framing is unambiguous. The idempotency key of a deploy, promote or rollback, the principal,
timestamps and the generation are not hashed. Every hashed value is also a column, so a loaded row can be checked by
recomputing the hash. The revision's documents describe the hash as "of the request body"; decision `0007` section 5
records why this differs. Any field a request gains later (a body, a reason, a flag) must enter the hashed text, and the
first line must then change to a new version label, so that an old and a new form can never be equal.

## 5. Image digest and source identity

- `image_digest` is the bare digest `sha256:` followed by 64 lowercase hexadecimal characters (71 characters). The
  registry host and repository path are derived from trusted configuration and the project ID, never from a request. The
  domain type rejects tags, other algorithms, uppercase and full image references.
- `source_commit_sha` is 40 or 64 lowercase hexadecimal characters (SHA-1 or SHA-256). Anything else, including a branch
  name or an abbreviated SHA, returns `ErrInvalidCommitSHA`.
- `source_repository_id` is 1 to 2^53−1 in the domain and in every contract, so the value survives JSON number handling;
  the column is `BIGINT` with a check that it is positive. A value outside the range returns `ErrInvalidRepositoryID`.

## 6. Generations and the fence

- `project_release_state.operation_generation` is the only fencing counter. It starts at 0 and ranges from 0 to 2^53−1.
  A command's `fencing_generation` is the counter's value after the acceptance that created the command, so it ranges
  from 1 to 2^53−1. The columns stay `BIGINT`; the domain and every contract enforce the upper bound.
- Accepting a command advances the counter by exactly one in the acceptance transaction. Nothing else moves it: not
  release acceptance, not a build, not a result. A rejected acceptance leaves it unchanged; a command that later fails
  or is skipped leaves it advanced.
- Advancing past the maximum returns `ErrGenerationOverflow`. A value outside its range from any source returns
  `ErrInvalidGeneration`.
- Clients send no generation. What a client can no longer say is "only if nothing changed since I looked"; the state
  preconditions under the project row lock, one command in flight, and the older-release rule replace it.
- A worker write carries its command's fencing generation. `CheckFence` returns `ErrStaleFence` when it differs from the
  project's counter. The fence alone is not sufficient; decision `0004` section 7 lists the three checks every worker
  write makes.

## 7. Results

- **Build result.** One entry point applies it. It accepts the result only when the release is `building` and the
  reported project, release ID and commit SHA equal the row (and the repository ID, when the result carries one).
  Otherwise it returns `ErrResultMismatch` and changes nothing; the caller records the mismatch and ignores the result.
  A build is not a command and carries no fence. The same entry point applies a failed build (`building → failed`) after
  the same match, and it is the only way to `built`: a direct transition `building → built` is refused, so the match
  cannot be skipped.
- (decision `0008` section 4) **Amended for Phase 3.** The build result also names its step and attempt, and its
  repository ID is required; the entry point takes the release and its `running` `build` step and refuses a result for
  any other step or attempt with `ErrResultMismatch`. A failed attempt or a passed deadline fails the step and either
  queues the next attempt (release stays `building`) or, at `BUILD_MAX_ATTEMPTS`, moves the release `building → failed`
  with `build_attempts_exhausted`.
- **Command outcome.** A worker reports an outcome for its command; the domain checks the fence, that the command is
  `running`, and that the outcome fits the kind, the release status and the route state. The active release handed to a
  precondition or an outcome must be the release the project state names and must be `live`; anything else is
  `ErrIllegalTransition`. A failure code or summary supplied where the resulting status does not carry one is refused,
  not ignored. A command has no summary column, so an outcome that writes no release refuses a summary (validation
  finding VR-02). The sets that must commit together:

| Event | Commits together |
| --- | --- |
| Deploy outcome | Release `→ candidate_ready` or `→ failed`; command terminal; route untouched |
| Promote starts | Release `→ promoting`; `route_state` `stable → changing` |
| Confirmed live | Old active release `→ superseded`, then this release `→ live` with `promoted_at`; `active_release_id` set to it; `route_state → stable`; command `succeeded` |
| Restore verified | Release `→ rolled_back`; `route_state → stable`; `active_release_id` unchanged; a promote ends `failed`, an operator rollback `succeeded` |
| No active release, route removed and verified | Release `→ failed`; `route_state → stable`; a promote ends `failed`, an operator rollback `succeeded` |
| Restore failed or unknown | `route_state → uncertain`; command `failed`; release stays `promoting` |

The results of promotion, recovery and hosting are executed from Phase 5 and Phase 6; the rules are fixed here so that
the domain and the schema carry them from Phase 1R.

## 8. Release steps

A step loaded from storage is validated like the other rows: a `running` step has a start and a deadline, a finished
step has a start and a finish that is not earlier, and a `failed` step has a reason code.

Step status: `pending → running → succeeded`, `pending → running → failed`, and `pending → failed` (deadline passed
before start). `succeeded` and `failed` are terminal. `attempt` ranges from 1 to 2147483647 (the `INTEGER` column); a
value outside it, or a next attempt that would exceed it, returns `ErrInvalidAttempt`. A retry is a new row with the
next attempt number, not a reset of a finished row. `failed` requires a reason code in the format of section 9, and a
reason code supplied on any other transition is refused.

(decision `0008` sections 4 and 6) Build steps: at most one `pending` or `running` attempt per release and step name;
their timestamps come from the database clock; `details_ref` is an opaque identifier, never a URL.

 Timing: `→ running` sets `started_at` and requires a deadline (`ErrMissingDeadline`). A finish sets `finished_at`,
 never earlier than `started_at`. `pending → failed` sets `started_at` and `finished_at` to the same instant, so a
 finished step always has both.

## 9. Identifiers and failure text

- The nil UUID (all zeros) is not a valid identity. Parsing it returns `ErrInvalidID`, and every rule that takes an ID
  rejects the zero value. The REST, MCP and event contracts exclude it in their schemas too (validation finding V2-01).
- Functions that take more than one ID take them as named fields of a struct, not as adjacent positional parameters of
  the same type.
- `failure_code` and `reason_code` are machine-readable: a lowercase letter followed by up to 99 lowercase letters,
  digits or underscores. An absent code where one is required returns `ErrMissingFailureCode`; a present but malformed
  or over-long code returns `ErrInvalidFailureCode`.
- `failure_summary` is optional, at most 500 characters. It must not contain control characters (Unicode category Cc),
  line or paragraph separators (U+2028, U+2029), or bidirectional control characters (U+061C, U+200E, U+200F, U+202A to
  U+202E, U+2066 to U+2069). Other format characters stay legal: the zero-width space (U+200B) separates words in Khmer,
  and the zero-width joiner and non-joiner are needed by Persian, Indic scripts and emoji sequences. A summary that is
  present must not be blank, meaning it must not consist only of Unicode white space. A summary made only of legal
  format characters such as U+200B is not blank and is accepted: summaries are written by platform code, not by callers,
  and a rule about "visible" text would need a definition this record does not want. A violation returns
  `ErrInvalidFailureSummary`. The domain rejects rather than silently rewrites.

Struct fields stay exported. Validation finding V1-08, item 4 is closed at the store, not by hiding fields; decision
`0004` section 7 holds the detail.

## 10. Typed errors

Kept: `ErrIllegalTransition`, `ErrIdempotencyConflict`, `ErrMissingImageDigest`, `ErrInvalidImageDigest`,
`ErrMissingFailureCode`, `ErrImmutableField`, `ErrProjectMismatch`, `ErrInvalidIdempotencyKey`, `ErrGenerationOverflow`,
`ErrInvalidGeneration`, `ErrInvalidAttempt`, `ErrInvalidFailureCode`, `ErrInvalidFailureSummary`, `ErrInvalidID`,
`ErrUnknownEnumValue`.

Added: `ErrStaleFence`, `ErrRouteNotStable`, `ErrHostingOffline`, `ErrReleaseOutdated`, `ErrInvalidCommandTarget`,
`ErrResultMismatch`, `ErrInvalidCommitSHA`, `ErrInvalidRepositoryID`, `ErrInvalidRequestHash`, `ErrInvalidRequester`,
`ErrMissingDeadline`.

Removed: `ErrStaleGeneration`, `ErrNoRollbackTarget`, `ErrInvalidPredecessor`.

Callers match with `errors.Is`. Error text contains statuses, kinds and IDs only, never request content.

## Not decided here

Entitlement gates and where they run (decision `0004`); lease fencing (decision `0005`); observation windows; what
happens to older unfinished releases when a newer one goes live (OD-47); which images are kept (OD-48); the rollback
target when hosting is `offline` and the release a rollback names after a failed hosting command (OD-45); hosting events
for a project with no state row or no active release (OD-52).
