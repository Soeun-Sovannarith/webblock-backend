# Payment service contract (what Payment must provide)

Status: **draft, proposed by Project, waiting for the Payment team's review**. This is the one contract page that a
live team, another team's Payment service, must review now. It fixes only the **interface** that Project and
Provisioning depend on. How Payment stores plans, bills and Webill data is Payment's own design (section 3 of the
platform database design). Until Payment implements it, Project runs a stand-in (profile `prototype` only) that
implements exactly this contract.

| Exchange | Section | Transport | State | IDs | Built in Project |
| --- | --- | --- | --- | --- | --- |
| Admit a website | 2.1 | REST | proposed by Project | FR-10, FR-11, PR-15 | yes |
| Release an admission | 2.2 | REST | proposed by Project | FR-12, PR-15 | yes |
| Admit a generation attempt | 2.3 | REST | proposed by Project | FR-26, PR-15 | yes |
| Project entitlement | 2.4 | REST; `entitlement.proto` is a draft | draft | PR-15 | no |
| Client plan summary | 2.5 | REST | proposed by Project | none | no |
| Hosting events | 3 | RabbitMQ | draft; Project is not a party | none | not applicable |
| Events Payment consumes | 4 | RabbitMQ | agreed for `publish_completed`; `project_deleted` draft, Project sends it | PR-20 | no |

Notion "Web Block architecture" 5.1 names gRPC for these calls (`AdmitWebsite`, `ReleaseWebsiteAdmission`,
`AdmitGenerationAttempt`, `GetEntitlement`). The code and this page use HTTP, and no admission proto exists. Which
transport holds is open for the owner and the Payment team (section 6, point 6).

The communication rules (internal HTTP, RabbitMQ envelope, outbox/inbox, service identity) are in
`.claude/docs/contracts/service-communication.md`, section 1. Service identity is the Istio mesh identity, with no
tokens
(`.claude/docs/decisions/0002-authentication-and-service-identity.md`); it is open for the Payment team's acceptance
(section 6).
HTTP bodies are camelCase inside the standard envelope; RabbitMQ events are snake_case.

## 1. Assumptions to confirm

| # | Assumption | If wrong |
| --- | --- | --- |
| A1 | **A plan is bought per client, not per website** (confirmed by the owner). A client has one current plan; its limits cover all of the client's websites (trial: 1 website, Pro: 3, Ultimate: unlimited). The team's Webill draft keys `payment.subscriptions` by `project_id`; the Payment team should key it by client instead (DBA note 1) | None |
| A2 | **A deleted project stops counting toward the website limit.** Payment learns this from `project.project_deleted.v1` | Payment ignores the event; a client must contact support to free a slot |
| A3 | The trial is 14 days from **Account signup** and allows 3 generation attempts. A failed generation is not credited back | None |
| A4 | Paid plans limit **publishes** (`publishes_per_period`, counted when a release goes live), not generations | Endpoint 2.3 also refuses on a paid plan's generation limit |
| A5 | No custom domains for now, so no domain bills in this contract | A later version adds them |

## 2. Synchronous API (Payment serves)

Common rules:

- **Base path and encoding.** `/internal/v1`. JSON in UTF-8 with **camelCase** field names. IDs are canonical lowercase
  UUIDs; times are RFC 3339 in UTC.
- **Envelope.** Every response uses the platform's standard envelope `{ success, data, error, meta }` (Payment's own
  shape, kept by the owner; it is not the Project service's error shape,
  `.claude/docs/decisions/0003-http-and-event-formats.md`).
  The bodies shown below are the content of `data`. `meta.requestId` echoes the caller's `X-Request-Id`.
- **Authentication: mesh identity, no bearer** (decision 0002; **open for the Payment team**, section 6). A call carries
  no `Authorization` header and needs no scope. Payment runs in the Istio mesh with STRICT mTLS, and its
  `AuthorizationPolicy` is the only gate: it admits `<project-sa>` on `/internal/v1/website-admissions*`,
  `/internal/v1/generation-admissions` and `/internal/v1/clients/*/entitlement`, and `<provisioning-api-sa>` and
  `<provisioning-worker-sa>` on `/internal/v1/projects/*/entitlement`. The sidecar refuses any other caller with `403`
  before Payment sees the request. Payment does not authenticate these calls itself, and ignores a bearer sent by
  mistake (never a `401`). The caller of each endpoint is named in its section.
- **Refusal versus error.** A refusal is a normal answer: `200` with `"decision": "REFUSED"` and a `reason`. HTTP errors
  mean the request could not be decided; they come as `success: false` with a stable `error.code` (UPPER_SNAKE_CASE).
- **Speed and failure.** Payment answers within 300 ms at p99. Callers time out after 2 seconds and **fail closed**:
  Project refuses the user's action with `503` with code `PAYMENT_UNAVAILABLE`; Provisioning does not deploy or promote.
- **Idempotency.** Every `POST` is idempotent on the key named in its section. A repeat with the same key and the same
  body returns the original decision, even if limits have changed since. The same key with a different body is `409`
  with code `IDEMPOTENCY_CONFLICT`.
- **Concurrency.** Admissions for one client are serialized: two concurrent requests never both take the last slot or
  the last attempt. The platform design's mechanism is to lock the client's trial row, count, and record the usage in
  the same transaction.

### 2.1 Admit a new website

`POST /internal/v1/website-admissions`. Caller: Project, before it creates the project.

Request:

```json
{ "clientUserId": "…", "projectId": "…" }
```

Idempotency key: `projectId` (Project generates it before calling).

Response `200`:

```json
{ "decision": "ADMITTED", "projectId": "…", "planCode": "trial", "websitesUsed": 1, "websitesLimit": 1 }
```

or

```json
{ "decision": "REFUSED", "reason": "WEBSITE_LIMIT_REACHED", "planCode": "pro", "websitesUsed": 3, "websitesLimit": 3 }
```

| `reason` | When |
| --- | --- |
| `WEBSITE_LIMIT_REACHED` | The plan's website limit is used up |
| `TRIAL_ENDED` | Trial over and no paid plan |
| `NO_ACTIVE_PLAN` | No trial and no active or grace-period subscription |
| `CLIENT_SUSPENDED` | The client's platform access is suspended |

On `ADMITTED`, Payment records `project_created` usage with the event key `project-created-<project_id>`. During the
trial it also records this project as the trial website. A refusal records nothing; a later repeat is decided again.

### 2.2 Release an unused website admission

`DELETE /internal/v1/website-admissions/{projectId}`. Caller: Project, **only** when it definitively did not create the
admitted project (the creation transaction failed for a reason a retry cannot fix).

- `204`: released. The slot is free again, and the trial website selection is cleared if it named this project.
- `404` with code `NOT_ADMITTED`: no admission exists.
- `409` with code `PROJECT_IN_USE`: Payment has seen any later usage for the project.

Without this endpoint, a trial client whose creation failed would be stuck with a website that does not exist.

### 2.3 Admit a generation attempt

`POST /internal/v1/generation-admissions`. Caller: Project, after it has captured a generation request and before it
sends the request to Codegen.

Request:

```json
{ "clientUserId": "…", "projectId": "…", "generationRequestId": "…" }
```

Idempotency key: `generationRequestId`, which is also the usage `event_key`.

Response `200`:

```json
{ "decision": "ADMITTED", "attemptsUsed": 2, "attemptsLimit": 3 }
```

`attemptsUsed` and `attemptsLimit` are `null` when the plan has no generation limit. A refusal has the same shape as
2.1, with these reasons:

| `reason` | When |
| --- | --- |
| `TRIAL_GENERATION_LIMIT_REACHED` | The trial's 3 attempts are used |
| `TRIAL_ENDED` | As 2.1 |
| `NO_ACTIVE_PLAN` | As 2.1 |
| `CLIENT_SUSPENDED` | As 2.1 |
| `PROJECT_NOT_ADMITTED` | The project was never admitted under 2.1 |

On `ADMITTED`, Payment records `generation_attempt` usage. It is never credited back, whatever the generation's outcome.

**What Project stores** (`.claude/docs/decisions/0004-generation-capture-and-admission.md`). A refused request becomes
`failed`,
and its `failure_code` is the `reason` in lower case: `trial_generation_limit_reached`, `trial_ended`, `no_active_plan`,
`client_suspended` or `project_not_admitted`. When Payment does not decide, Project stores a code of its own:
`admission_unavailable` (a timeout, a connection failure or a 5xx for the whole of Project's retry window; every retry
sends the same `generationRequestId`) or `admission_error` (any other answer, at once). A reason Payment adds later is
stored in lower case if that matches `[a-z_]{1,64}`, and as `admission_error` otherwise.

**Known limit (owner).** If Payment admits but its answer never reaches Project within the window, Project stores
`admission_unavailable` while Payment has counted the attempt; crediting it back is a later Payment reconciliation, and
this contract does not change now.

### 2.4 Hosting and publish entitlement of a project

Machine file for the gRPC form: `proto/webblock/payment/v1/entitlement.proto` on `web-block-contracts` `main`, header
"DRAFT, proposed by provisioning, waits for payment". Project does not call this today.

`GET /internal/v1/projects/{projectId}/entitlement`. Callers:

- Provisioning, when it accepts a `deploy` or `promote` command, and again when a `suspend_hosting` or `resume_hosting`
  command runs (so a stale event never wins over the current state);
- Project, for the UI.

Response `200`:

```json
{
  "projectId": "…",
  "hosting": "ENTITLED",
  "publish": "ALLOWED",
  "reason": null,
  "planCode": "pro",
  "publishesUsed": 4,
  "publishesLimit": 20,
  "periodEnd": "2026-11-13T00:00:00Z"
}
```

| Field | Values |
| --- | --- |
| `hosting` | `ENTITLED` or `ENDED` |
| `publish` | `ALLOWED`, or `LIMIT_REACHED` (the plan's `publishes_per_period` is used up; hosting continues) |
| `reason` | When `hosting = ENDED`: `TRIAL_ENDED`, `GRACE_EXPIRED`, `PLAN_CANCELED`, `OVER_WEBSITE_LIMIT` |

Unknown project: `200` with `hosting: ENDED` and `reason: NOT_ADMITTED`.

### 2.5 A client's plan summary (for the UI)

`GET /internal/v1/clients/{clientUserId}/entitlement`. Caller: Project.

Answers `planCode`, `trialEndsAt` (`null` if not on the trial), `websitesUsed` and `websitesLimit`, `attemptsUsed` and
`attemptsLimit`, and `periodEnd`. It is read-only and never used to admit anything.

## 3. Events Payment publishes

Exchange `payment.events`. Envelope as in the communication note. Schemas:
`events/payment/hosting_entitlement_ended.v1.schema.json` and `hosting_entitlement_restored.v1.schema.json` on
`web-block-contracts` `main`, header DRAFT, proposed by provisioning.

| `message_type` | Fields beyond the envelope | Meaning |
| --- | --- | --- |
| `payment.hosting_entitlement_ended.v1` | `event_key`, `reason` (lowercase in events: `trial_ended`, `grace_expired`, `plan_canceled`, `over_website_limit`) | This project must go offline |
| `payment.hosting_entitlement_restored.v1` | `event_key` | This project may serve again |

Rules:

- **One event per project.** When a client's trial or plan ends, Payment publishes one `ended` event for each of the
  client's projects. Provisioning acts per project.
- **`event_key`:**
  - stable for one transition and reused unchanged on every redelivery;
  - unique per project;
  - 1 to 255 bytes of UTF-8, with no control characters and no surrounding white space;
  - format `payment:<ended|restored>:<uuidv7>`, generated once when Payment records the transition in its outbox.

  Provisioning uses it as the hosting command's idempotency key.
- **No ordering guarantee.** Consumers do not rely on order; Provisioning re-checks 2.4 when it acts.
- **Outbox and publisher confirms, at least once.**
- **No personal data:** no names, emails or bank details in any event.

## 4. Events Payment consumes

| `message_type` (producer) | Fields beyond the envelope | Payment must |
| --- | --- | --- |
| `provisioning.publish_completed.v1` (Provisioning) | `event_key` = `publish-completed-<release_id>`, `release_id` | Record `publish_completed` usage (`release_id` set), deduplicated on `event_key`. Count it against `publishes_per_period` |
| `project.project_deleted.v1` (Project, built, FR-38) | None | Stop counting the project toward the website limit (A2), deduplicated on `project_id`. Never delete history |

Schemas: `events/provisioning/publish_completed.v1.schema.json` (owned by Provisioning) and
`events/project/project_deleted.v1.schema.json` (DRAFT; [provisioning](provisioning-contract.md), section F). Notion
"Web Block architecture" 5.2 also sends `project.source_published.v1` to Payment; no page says what Payment does with
it. Project itself consumes `publish_completed` for FR-37 (see [provisioning](provisioning-contract.md), section G).

Payment also needs the signup time from Account to create the trial. That is Account's contract, not part of this one.

## 5. What the callers guarantee

- Project calls 2.1 before it creates a project and 2.3 before it sends a generation to Codegen. It retries with the
  same key after a timeout.
- Project calls 2.2 only for an admission it will never use.
- Provisioning publishes `publish_completed` exactly for releases confirmed live, at least once, with a stable
  `event_key`.
- No caller keeps its own usage counters or resets a trial.

## 6. Open questions for the owner and the Payment team

1. A2 above (A1 is confirmed).
2. Whether a client downgrading below its number of websites is blocked, or chooses which websites go offline (the
   `OVER_WEBSITE_LIMIT` reason assumes the second).
3. `publishes_per_period` and the grace period for each plan.
4. While a site is offline for non-payment, whether its CMS stays reachable (this affects Provisioning's offline route,
   not this contract).
5. **Open for the Payment team: accept "mesh identity, no bearer"** (section 2, *Authentication*; decision 0002).
   Project's Payment client already sends no token, and Provisioning's calls (section 2.4) carry none either. Until
   Payment accepts, this point stays open; a Payment that demanded a client-credentials token would refuse every call,
   and Project would fail closed (`PAYMENT_UNAVAILABLE`). Payment's mesh policy admits `<project-sa>` on
   `/internal/v1/projects/*/entitlement` as well, since section 2.4 names Project as a caller (for the UI).
6. **Open for the owner and the Payment team: HTTP or gRPC.** Notion 5.1 names gRPC; this page and Project's client use
   HTTP. Suggestion: keep HTTP until Payment ships protos, and then move each call by a new version of this page.
7. **Open for the owner: `source_published` to Payment.** Notion 5.2 lists Payment as a receiver; say what it does with
   the event, or drop it from the list.

