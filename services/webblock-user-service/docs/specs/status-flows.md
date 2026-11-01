# WebBlock — Status values (from the Data Dictionary)

Use these exact enum values (UPPER_CASE in DB and Java, mapped to friendly labels in the UI).
The arrows are the normal path read from the specs and workflow; anything not shown needs team approval.

| Table | Field | Values | Normal path |
|---|---|---|---|
| account_users | status | ACTIVE, SUSPENDED | ACTIVE ⇄ SUSPENDED (admin; reason ABUSE / FRAUD / OWNER_REQUEST / OTHER) |
| project_projects | status | DRAFT, ACTIVE, SUSPENDED | DRAFT → ACTIVE (first live) ⇄ SUSPENDED |
| project_generation_requests | status | CAPTURED, GENERATING, PUBLISHING, PUBLISHED, FAILED | CAPTURED → GENERATING → PUBLISHING → PUBLISHED; any → FAILED |
| ai_codegen_harness_runs | status | QUEUED, RUNNING, SUCCEEDED, FAILED | QUEUED → RUNNING → SUCCEEDED / FAILED |
| ai_codegen_harness_runs | strategy | PUCK_JSON, SOURCE_CODE, MIXED | — |
| ai_codegen_validation_runs | validation_type | PUCK_SCHEMA, TYPE_CHECK, BUILD, LINT, SMOKE_TEST, SECURITY | — |
| ai_codegen_validation_runs | status | PENDING, RUNNING, SUCCESS, FAILED | PENDING → RUNNING → SUCCESS / FAILED |
| ai_codegen_artifacts | artifact_type | SOURCE_ARCHIVE, CHECK_REPORT | — |
| ai_assistant_messages | role | USER, ASSISTANT | — |
| ai_assistant_change_sets | status | PROPOSED, APPLIED, DISCARDED, REJECTED | PROPOSED → APPLIED / DISCARDED; policy fail → REJECTED |
| ai_assistant_change_items | operation | ADD_COMPONENT, UPDATE_PROPS, DELETE_COMPONENT, MOVE_COMPONENT | — |
| provisioning_releases | status | ACCEPTED, BUILDING, BUILT, CANDIDATE_READY, PROMOTING, LIVE, SUPERSEDED, FAILED, ROLLED_BACK | ACCEPTED → BUILDING → BUILT → CANDIDATE_READY → PROMOTING → LIVE → SUPERSEDED (by next LIVE); any → FAILED; LIVE → ROLLED_BACK |
| provisioning_release_steps | step_name | BUILD, CANDIDATE, PROMOTION, ROLLBACK (ACCESS_SETUP, OBSERVATION unused) | — |
| provisioning_release_steps | status | PENDING, RUNNING, SUCCEEDED, FAILED | — |
| provisioning_commands | kind | DEPLOY, PROMOTE, ROLLBACK, SUSPEND_HOSTING, RESUME_HOSTING, REDEPLOY (SYNC_DOMAINS unused) | — |
| provisioning_commands | status | ACCEPTED, RUNNING, SUCCEEDED, FAILED, SKIPPED | ACCEPTED → RUNNING → SUCCEEDED / FAILED; stale → SKIPPED |
| provisioning_project_hosting | routing_status | STABLE, CHANGING, UNCERTAIN | — |
| provisioning_project_hosting | hosting_status | ONLINE, OFFLINE | OFFLINE needs ≥1 offline_reason: BILLING, USER_SUSPENDED, PROJECT_DELETED, ADMIN_SUSPENDED |
| catalog_websites | status | ACTIVE, SUSPENDED, ARCHIVED | — |
| payment_plans | billing_interval | MONTH | — |
| payment_subscriptions | status | PENDING, ACTIVE, PAST_DUE, CANCELED, EXPIRED | PENDING → ACTIVE → PAST_DUE (grace_until) → EXPIRED; ACTIVE → CANCELED |
| payment_bills | status | PENDING, PAID, EXPIRED, FAILED, VOID | PENDING → PAID / EXPIRED (QR timeout) / FAILED; VOID = cancelled by system |
| payment_usage_records | usage_type | PROJECT_CREATED, PUBLISH_COMPLETED, GENERATION_ATTEMPT | — |

## Publish flow (Admin › Monitoring › Pipeline & Generation)
Four phases: **Request → Generation → Release → Live**
- Request = `project_generation_requests` CAPTURED
- Generation = `ai_codegen_harness_runs` + `validation_runs`, then push to Git (GENERATING / PUBLISHING)
- Release = `provisioning_releases` BUILDING … PROMOTING (+ `release_steps`)
- Live = release `LIVE`, `project_hosting.live_release_id` set
