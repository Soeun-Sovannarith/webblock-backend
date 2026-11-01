# Contracts with the other services

Every exchange between the Project service and another service or system, in one table. Each row says who owns it,
how far it is agreed, where the machine file lives and whether Project builds it today. The detail is in the page named
in the last column. A page links the schema or proto and never copies it in full.

## Where the process lives

The process for a new or changed contract is in the README of `web-block-contracts` (branch `main`):
its sections "What each service needs from the others" and "How to ask for a change". A team proposes a DRAFT file or a
change request there, the owning service reviews it, and `ACCEPTED` in the file header means agreed. Project's side of
every contract is decided there. This folder explains what Project needs and provides.

## Who exists

- Project, Provisioning and Payment exist today. Payment is another team's: its pages are the only ones a live team must
  review now ([`payment-contract.md`](payment-contract.md)).
- Account, AI Codegen and Notification are not built yet. Their pages are the spec those services will be built to, not
  agreements waiting for a live team.

## Words used

State:

- **agreed**: both sides use it, or the owner approved it.
- **draft**: written in `web-block-contracts` with a DRAFT header, not yet accepted by the other side.
- **proposed by Project**: Project wrote it for the other service to accept or build to.
- **spec for the service to be built**: the other service is not built; the page is what it will be built to.
- **missing**: nothing is written yet beyond a Notion row.

Files live in the repository `web-block-contracts` (`git@github.com:itme-roa15/webblock-contract.git`). Its `main`
(`18dd301`, the pin in `contracts.lock`) holds the action list, Project's drafts for AI Codegen, Account and
Notification, and Project's answers in the README there.

## Exchanges

| Exchange | Direction | Transport | Owner | State | File in web-block-contracts | IDs | Built | Page |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Access check, REST `POST /internal/v1/access-checks` | Provisioning calls Project | REST, no token | Project | agreed; goes when Provisioning switches | none | FR-30, FR-34, PR-12 | yes | [provisioning](provisioning-contract.md) |
| Access check, gRPC `AccessService/CheckAccess` | Provisioning, site portals call Project | gRPC 9090 | Project | agreed | `proto/webblock/project/v1/access.proto` on `main` | FR-31, FR-33, PR-13 | yes | [provisioning](provisioning-contract.md) |
| Action list (11 actions, adds `manage_site`) | Project reads at build time | data file | Project | proposed by Project, Provisioning to agree | `access/actions.v1.json` on `main` | FR-32, PR-14 | yes | [provisioning](provisioning-contract.md) |
| Site portal ownership check | Website portal calls Project | gRPC `CheckAccess` over the mesh (public REST is open) | Project | draft | none | FR-32, PR-14 | no | [provisioning](provisioning-contract.md) |
| `GetRepository` | Provisioning build worker calls Project | gRPC | Project | draft, Project accepts with open points | `proto/webblock/project/v1/repository.proto` on `main` (answer header on `main`) | PR-18 | no | [provisioning](provisioning-contract.md) |
| `project.source_published.v1` | Project publishes; Provisioning, Notification consume | RabbitMQ `project.events` | Project | draft, Project accepts | `events/project/source_published.v1.schema.json` on `main` | PR-19 | no | [provisioning](provisioning-contract.md), [notification](notification-contract.md) |
| `project.project_deleted.v1` | Project publishes; Provisioning, Payment consume | RabbitMQ | Project | draft, Project accepts, built | `events/project/project_deleted.v1.schema.json` on `main` | PR-20 | no | [provisioning](provisioning-contract.md) |
| `project.owner_status_changed.v1` | Project publishes; Provisioning consumes | RabbitMQ | Project | draft, Project accepts | `events/project/owner_status_changed.v1.schema.json` on `main` | PR-21 | no | [provisioning](provisioning-contract.md) |
| `provisioning.publish_completed.v1` | Provisioning publishes; Payment and Project consume | RabbitMQ `provisioning.events` | Provisioning | agreed with Payment; Project is a new consumer (owner items open) | `events/provisioning/publish_completed.v1.schema.json` on `main` | FR-37 | no | [provisioning](provisioning-contract.md) |
| Shared catalog database | Project writes; Provisioning and sites read | PostgreSQL roles | Project | agreed | none (Notion "6 · Shared catalog") | FR-35, FR-36, PR-17 | yes | [provisioning](provisioning-contract.md) |
| Admit a website, `POST /internal/v1/website-admissions` | Project calls Payment | REST (Notion says gRPC) | Payment | proposed by Project, Payment to accept | none | FR-10, FR-11, PR-15 | yes | [payment](payment-contract.md) |
| Release an admission, `DELETE .../website-admissions/{projectId}` | Project calls Payment | REST | Payment | proposed by Project | none | FR-12, PR-15 | yes | [payment](payment-contract.md) |
| Admit a generation attempt, `POST /internal/v1/generation-admissions` | Project calls Payment | REST | Payment | proposed by Project | none | FR-26, PR-15 | yes | [payment](payment-contract.md) |
| Project entitlement | Provisioning (and Project for the UI) call Payment | REST; gRPC proto exists | Payment | draft | `proto/webblock/payment/v1/entitlement.proto` on `main` | PR-15 | no | [payment](payment-contract.md) |
| Client plan summary | Project calls Payment | REST | Payment | proposed by Project; not in Notion | none | none | no | [payment](payment-contract.md) |
| `payment.hosting_entitlement_*` | Payment publishes; Provisioning, Notification consume | RabbitMQ `payment.events` | Payment | draft; Project is not a party | `events/payment/*.v1.schema.json` on `main` | none | not applicable | [payment](payment-contract.md) |
| `project.generation_requested.v1` | Project publishes; AI Codegen consumes | RabbitMQ | Project | proposed by Project, spec for the service to be built | `events/project/generation_requested.v1.schema.json` on `main` | FR-27, FR-26, PR-16 | yes | [AI Codegen](service-communication.md) |
| `project.generation_cancelled.v1` | Project publishes; AI Codegen consumes | RabbitMQ | Project | proposed by Project, spec for the service to be built | `events/project/generation_cancelled.v1.schema.json` on `main` | PR-16 | no | [AI Codegen](service-communication.md) |
| `ai_codegen.generation_completed.v1` | AI Codegen publishes; Project consumes | RabbitMQ | AI Codegen | proposed by Project, spec for the service to be built | `events/ai_codegen/generation_completed.v1.schema.json` on `main` | PR-9, PR-16 | no | [AI Codegen](service-communication.md) |
| Codegen input, `GET .../codegen-input` | AI Codegen calls Project | REST (Notion says gRPC) | Project | agreed in the note; AI side not built | none | FR-29, PR-16, PR-22 | yes | [AI Codegen](service-communication.md) |
| Scaffold, `GET .../scaffold` | AI Codegen calls Project | REST (Notion says gRPC) | Project | spec for the service to be built | none | PR-22 | no | [AI Codegen](service-communication.md) |
| Read a run and its runs | Project calls AI Codegen | REST (Notion says gRPC) | AI Codegen | spec for the service to be built | none | PR-9, PR-16 | no | [AI Codegen](service-communication.md) |
| Download generated source | Project reads AI Codegen storage | HTTPS presigned URL, SHA-256 | AI Codegen | spec for the service to be built | none | PR-9 | no | [AI Codegen](service-communication.md) |
| `CheckThreadOwner` | AI Codegen calls Project | gRPC (Notion) | Project | missing | none | PR-22 | no | [AI Codegen](service-communication.md) |
| Git hosting (Gitea) | Project calls Gitea | HTTP, bot account | Project | missing | none | PR-8, PR-9 | no | [AI Codegen](service-communication.md) |
| `account.user_status_changed.v1` | Account publishes; Project consumes | RabbitMQ `account.events` | Account | proposed by Project, spec for the service to be built | `events/account/user_status_changed.v1.schema.json` on `main` | PR-21 | no | [account](account-contract.md) |
| `GetProjectOwner` | Notification calls Project | gRPC | Project | proposed by Project, spec for the service to be built | `proto/webblock/project/v1/project_owner.proto` on `main` | PR-22 | no | [notification](notification-contract.md) |
| Keycloak token check | Project checks tokens against Keycloak's key set | HTTPS (JWKS) | Account and Keycloak | agreed (owner approved) | none | FR-1, FR-2, PR-23 | yes | [authentication](authentication-contract.md) |
| Frontend and mobile REST `/api/v1` | Next.js server and mobile app call Project | REST | Project | agreed in prose; no OpenAPI file in `web-block-contracts`. In the `prototype` profile only, the service serves an OpenAPI description generated from its code at `/v3/api-docs` and Swagger UI (TR-40); Notion stays the authority | none | FR-8 to FR-25 | yes | Notion "Web Block architecture" section 2 |
| Admin pages | Admin pages call Project | REST, role `ADMIN` | Project | missing | none | PR-11 | no | none yet |
| Mesh policies for Project's callers | Owner writes `AuthorizationPolicy` | Istio | Owner | draft | none | TR-31, TR-38 | not applicable | [provisioning](provisioning-contract.md), `.claude/docs/runbooks/grpc.md` |

## Open owner questions (Notion against the repo)

Notion "Web Block architecture" (called S2 below) and this repo disagree in these places. Nothing here is decided.

1. **Payment transport.** S2 5.1 names gRPC (`AdmitWebsite`, `ReleaseWebsiteAdmission`, `AdmitGenerationAttempt`,
   `GetEntitlement`). The code and `payment-contract.md` use HTTP, and only `entitlement.proto` exists. Suggestion:
   keep HTTP until Payment ships protos, and mark the gRPC form as the target.
2. **AI Codegen reads Project.** S2 5.1 names gRPC `GetCodegenInput` and `GetScaffold`; S2 3.7 lists a
   `codegen_input.proto` that does not exist; Notion "4 · AI" says HTTP. REST is built. Suggestion: keep REST.
3. **Project reads AI runs.** S2 5.1 names gRPC `GetGenerationRun` and `ListGenerationRuns`; the note uses REST.
4. **How accepted code reaches Project.** S2 5.1 has a push method ("a new method to hand over the tested code") and a
   pull (read the result after the completion event). The note and the drafts use pull only.
5. **Git host.** S2 and the database design say Gitea. The pages here say Gitea. Which address goes
   into `clone_url` is open ([provisioning](provisioning-contract.md)).
6. **`source_published` receivers.** S2 5.2 sends it to Provisioning, Payment and Notification. No page says what
   Payment does with it.
7. **Project as a consumer.** S2 4.4 gives Project two queues only (`project.generation_completed.v1`,
   `project.user_status_changed.v1`). FR-37 needs `project.publish_completed.v1` on `provisioning.events` and a read
   right for Project's broker user. Both need rows in S2 4.4 and 5.2.
8. **Site portal check.** S2 section 9 point 3 proposes a small public REST endpoint, because sites run on another
   cluster. The repo uses `CheckAccess` over the mesh.
9. **Who may call.** S2 3.8 admits `provisioning-api` on `CheckAccess` and `provisioning-build-worker` on
   `GetRepository`. `grpc.md` admits `provisioning-api` on `CheckAccess` only.
10. **Contract tree.** S2 3.7 lists `codegen_input.proto`, `admission.proto`, `users.proto` and `runs.proto`;
    `web-block-contracts` has none of them.
11. **Envelope `project_id`.** `envelope.v1` requires it; a user event has none ([account](account-contract.md)).

Other owner questions are on the pages: the `clone_url` host and commit confirmation
([provisioning](provisioning-contract.md)), the events Notification needs ([notification](notification-contract.md)),
and marking accepted files ACCEPTED in `web-block-contracts`.
