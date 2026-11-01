# Open questions (found while reading the specs)

These are places where the documents disagree or are not finished. Claude must **ask** instead of guessing on these.

| # | Topic | What the docs say | Status |
|---|---|---|---|
| 1 | Git server | Data Dictionary uses **Gitea** (`repository_id`, `repository_full_name`). Earlier plans said GitHub organization. | Confirm: Gitea |
| 2 | Settings › email | Feature Detail §3.2: user can update full name **and email**. Workflow board Settings: "Edit profile Photo, Edit Full Name" only. | Decide which |
| 3 | Subscription statuses | DD has `PAST_DUE`; Admin Portal design uses only Pending / Active / Expired / Canceled. | Decide UI mapping for PAST_DUE |
| 4 | Subscription per project | `subscriptions.project_id` and `bills.project_id` say "redesign pending". | Open |
| 5 | Bill payment term | `bills.payment_term`: "time unit not confirmed". KHQR order lives 10 minutes in the design. | Confirm unit |
| 6 | Catalog tables | `catalog_categories`, `catalog_products`, `catalog_product_images`, `catalog_product_types`, `catalog_product_type_schemas` exist on the ERD but not in the Data Dictionary. | Add to DD |
| 7 | Admin Monitoring | Feature Detail lists Pipeline & Generation, Sites & Routing Health, User Diagnostic Lookup, Audit & System Logs. Workflow board shows only Pipeline & Generation + Activity Logs. | Confirm list |
| 8 | Deploy retry limit | "Max retry is 2 times but it can change." | Keep as config value |
| 9 | Catalog ownership | ERD Project page says "Shared catalog (owned by Project)", but Catalog has its own ERD page. | Decided: part of **project-service** (no catalog-service) |
| 10 | Token claims | Notion page (27 Oct 2026): `user_id` + top-level `roles` + per-service `aud`. `security.md` and existing code: `jwt.sub` + Keycloak `admin` realm role. | Decide which; until then do not change existing SecurityConfig |
| 11 | REST response shape | Notion: `{success,data,meta}` envelope, cursor paging, RFC 9457 errors. `api-contracts.md`: `ProblemDetail`, `{items,page,size,totalItems}` paging. | Decide; new endpoints follow `rest-response-format.md`, old ones stay |
| 12 | Service-to-service transport | Notion: gRPC (open point 5, start with `CheckAccess`). Repo rule: OpenFeign. | Proposed: gRPC for new calls, migrate Feign one call at a time |
| 13 | Event exchanges | Notion: one exchange per sender (`project.events`), queue `<consumer>.<event>.v<N>`. `payment-service` today: shared `webblock.events`, queue `payment.account-events`. | Decide when to migrate payment-service |
| 14 | Outbox / inbox tables | Notion 4.3 requires them. Not in the Data Dictionary, no service has them yet. | Add to DD, then `adopt-outbox-inbox` per service |
| 15 | Contracts / commons location | Notion: separate repos `web-block-contracts`, `web-block-commons` (GitLab registry). This repo: `contracts/` and `commons/` folders. | Proposed: folders now, split later |
| 16 | AI service name and place | Specs say `ai-generator-service` / AI Codegen; the code is `services/webblock-aicode-service`, its own git repo. | Confirm the name and whether it joins this repo |
