# Account contract (what Account must provide to Project)

**Account is not built yet. This page is the spec the service will be built to**, not an agreement with a live team. It
covers the one event Project needs from Account and how Project passes it on. Sign-in, tokens and the Keycloak realm
are in [`authentication-contract.md`](authentication-contract.md). The index of every exchange is
[`README.md`](README.md).

## 1. `account.user_status_changed.v1`

- **Direction:** Account publishes; Project consumes (Payment consumes it too).
- **Transport:** RabbitMQ, exchange `account.events`, routing key `user_status_changed.v1`. Project's queue is
  `project.user_status_changed.v1` (Notion "Web Block architecture" 4.4 and 5.2). The envelope, outbox and inbox rules
  are in [`service-communication.md`](service-communication.md), section 1.
- **File:** `events/account/user_status_changed.v1.schema.json` on `web-block-contracts` branch `main`
  (`5708a31`). Header: DRAFT, proposed by Project, waits for Account. Owner: Account. The fields come from Notion
  "1 · Account" (table `account.user_status_changes`). Valid and invalid examples sit beside it.
- **Payload beyond the envelope:**
  - `event_key`: the `user_status_change_id` (UUID).
  - `user_id`: the Account user.
  - `status_version`: the user's version after this change; it grows by one with each change. A consumer drops an
    event whose version is at or below the one it holds.
  - `from_status`, `to_status`: `active` or `suspended`; they differ.
  - `reason_code`: set exactly when `to_status` is `suspended` (`abuse`, `fraud`, `owner_request`, `other`), else null.
  - `changed_by`: the admin's Keycloak subject, null when the system made the change. The admin's note is never sent.
  - `changed_at`.
- **Errors:** a body that fails the schema goes to the dead-letter queue. Project records `event_key` once, so a repeat
  has no effect.
- **State:** proposed by Project; spec for the service to be built. **Serves** PR-21. **Built in Project:** no. No FR
  covers it yet.

### Open point: `project_id` in the envelope

`envelope.v1` requires `project_id`. A user event is about no project, so the draft has none. Notion 4.1 says
`project_id` is sent "when there is one". The owners of the envelope decide which rule holds: make `project_id` optional
in the envelope, or give user events their own envelope. Until then this event does not validate against the common
envelope.

## 2. What Project does with it

For each website of that user (deleted ones included), Project publishes `project.owner_status_changed.v1` with the
user's `status`, the same `status_version`, and the project's `subdomain`. The shape and Provisioning's rules are in
[`provisioning-contract.md`](provisioning-contract.md), section F. Project does not change `projects.status` for this:
nothing sets `suspended` today (Notion "2 · Project": no writer, no hosting effect).

Project cannot invent a version: it relays Account's. So Account's counter must be monotonic per user.

## 3. What Account never receives from Project

Project sends Account nothing. A user's status is Account's own; Payment reports a suspended client to Project only as
the refusal reason `CLIENT_SUSPENDED` ([`payment-contract.md`](payment-contract.md), section 2.1).
