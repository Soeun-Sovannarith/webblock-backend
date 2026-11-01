# Notification contract (what Notification needs from Project)

**Notification is not built yet. This page is the spec the service will be built to**, not an agreement with a live
team. Notification only reports: it decides nothing for Project (Notion "Web Block architecture" 5.5), and Project never
calls it. The index of every exchange is [`README.md`](README.md).

## 1. `GetProjectOwner`

- **Direction:** Notification calls Project.
- **Transport:** gRPC, deadline 2 s, safe to retry on `UNAVAILABLE` at most 2 times. The Istio policy is the only gate;
  the call carries no token ([`service-communication.md`](service-communication.md), section 1). The mesh policy must
  admit Notification's service account on this method. The owner writes it.
- **File:** `proto/webblock/project/v1/project_owner.proto` on `web-block-contracts` branch `main`
  (`5708a31`). Header: DRAFT, proposed by Project, waits for Notification. Owner: Project.
- **Request:** `project_id` (UUID).
- **Answer:** `owner_user_id` (the Account user who owns the project; it never changes) and `deleted` (true when the
  client deleted the project).
- **Errors:** an unknown project is `NOT_FOUND` with `ErrorInfo.reason` `PROJECT_NOT_FOUND`. A deleted project is found
  and answers its owner with `deleted` true, so Notification can still tell a client about a deleted site.
- **State:** proposed by Project; spec for the service to be built. **Serves** PR-22. **Built in Project:** no. No FR
  covers it yet.

## 2. `project.source_published.v1` as a consumer

- **Direction:** Project publishes; Notification consumes.
- **Transport:** RabbitMQ, exchange `project.events`, routing key `source_published.v1`. Notification's queue is
  `notification.source_published.v1` (Notion 4.4 and 5.2).
- **File and payload:** `events/project/source_published.v1.schema.json` on `main`. The fields are listed in
  [`provisioning-contract.md`](provisioning-contract.md), section F. Notification needs only `project_id` and
  `generation_request_id`; it asks `GetProjectOwner` for the person to tell.
- **State:** draft (the same file Provisioning reads). **Serves** PR-19. **Built in Project:** no.

## 3. Open: which other events Notification needs

Notion 5.2 gives Notification the one queue above and says more will come "when their events are defined". Candidates
the owner can rule on: a failed generation (today a state of the request, no event), and a deleted project
(`project.project_deleted.v1` exists but Notification is not a listed receiver). Notification also consumes Payment's
hosting events and `provisioning.publish_completed.v1` (Notion 5.2); those are not Project's exchanges. Until the owner
rules, Project publishes nothing new for Notification.
