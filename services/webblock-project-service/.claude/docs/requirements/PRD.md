# Project service: Product Requirements (PRD)

**Status:** Approved at gate 1 by the owner

## In short

Website owners create a website from a template with its own sub-domain, edit its pages safely, freeze generation
rules and ask to generate; all of this works today. Other services already get Payment admission, the catalog
registration, the generation event, the generation input and an access check over REST and gRPC. J3 continues with
Git hosting and publication; J4 brings the AI Codegen hand-over and the "source published" event; J5 brings deletion
and the admin search.

**Read next:** [FRD.md](FRD.md), exactly how each offer behaves. Up: [BRD.md](BRD.md).

## What each user gets

### Website owner

| ID | Offer | BR | Done when | Priority | Phase | Source |
| --- | --- | --- | --- | --- | --- | --- |
| PR-1 | Picks a business type and template and creates the website in one step, if the plan allows; a retry gives the same website | BR-1, BR-11 | A new website opens with its template pages; a full plan gets a clear refusal and no website | Must | J1 | S1 How the tables work, PAY §2.1 |
| PR-2 | Chooses the website's sub-domain at creation; it never changes and is never reused | BR-5 | The site's address is `<subdomain>.<site domain>`; a taken name is refused, deleted sites included; so are `preview`, a name ending in `-preview` (another site's preview address) and one starting with `xn--` (FRD FR-13) | Must | J3 | S1 Site hostname, FRD Q-2 |
| PR-3 | Sees their own websites (the name cannot be changed); a deleted or suspended one is read-only | BR-8, BR-9 | The list shows only their live websites; a read-only one refuses edits | Must | J1 | S1 projects, 0007 |
| PR-4 | Edits the one page of a website (home, `/`), without losing a save; no page is added or deleted | BR-2 | A save from an old screen is refused and nothing changes | Must | J1 | S1 pages |
| PR-5 | Loads the builder's component catalog | BR-2 | The builder shows the blocks of the website's catalog version | Must | J1 | S1 projects |
| PR-6 | Freezes a generation contract for chosen product types | BR-3 | A new contract version lists each type with its newest active schema | Must | J2 | S1 generation_contracts, 0004 |
| PR-7 | Asks to generate, one request at a time, safe to retry, and sees its status and failure reason | BR-3, BR-11 | A second request while one runs is refused; a refused attempt shows its reason; none stays `captured` | Must | J2 | S1 generation_requests, 0004 |
| PR-8 | Gets a Git repository for the site and the code committed safely | BR-4 | Each site has one repository; a commit never overwrites another | Must | J3 | S1 Site hostname, `ai/handoffs/next.md` |
| PR-9 | Gets tested code from AI Codegen accepted and published | BR-4 | A request ends `published` with its commit; the website turns `active` when its first release is live, and a failed publish leaves it `draft` (FRD FR-37) | Must | J4 | S1 generation_requests, 0006, CTR |
| PR-10 | Deletes a website; suspends or reactivates it (FR-38, FR-39, FR-40) | BR-7, BR-10 | The deleted website is unknown to every call, archived in the catalog, its row kept; a suspended one is read-only; taking hosting offline is a later round | Must | J5 | S1 How the tables work |

### Admin

| ID | Offer | BR | Done when | Priority | Phase | Source |
| --- | --- | --- | --- | --- | --- | --- |
| PR-11 | Finds a website by its sub-domain (exact or prefix) | BR-5 | An admin search for `kiln` lists `kilncoffee`; a non-admin is refused | Should | J5 | S1 projects |

### Other services (Provisioning, Payment, AI Codegen, Catalog, Notification)

| ID | Offer | BR | Done when | Priority | Phase | Source |
| --- | --- | --- | --- | --- | --- | --- |
| PR-12 | Provisioning asks over REST whether a user may act on a project | BR-6, BR-8, BR-9 | Owner allowed; anyone else refused without learning the project exists | Must | J1 | PROV §B, 0007 |
| PR-13 | Provisioning, website portals and the auth library ask over gRPC `CheckAccess`; the REST check then goes | BR-6, BR-8, BR-9 | Same answers as PR-12 over gRPC; admins pass only where the caller's per-action rule allows; REST gone after Provisioning switches | Must | J3 | S2 §3.2, CMN §7, 0002 |
| PR-14 | A website portal checks that the signed-in user owns the site (`manage_site`) | BR-6 | The owner opens the portal; anyone else is refused | Must | J4 | S2 §2.4, PROV §D |
| PR-15 | Payment is asked before each new website, and an unused admission is released | BR-11 | No website exists without an admission; a failed creation frees its slot | Must | J1 | PAY §2.1, §2.2 |
| PR-16 | AI Codegen hears that a request was admitted and reads its frozen input | BR-3 | The event arrives once per admitted request; the input hash matches | Must | J2 | COMM §2.1, §2.2 |
| PR-17 | Catalog gets every new website registered in the background | BR-1, BR-12 | A website and its business profile appear in the catalog | Must | J1 | S3 How the tables work |
| PR-18 | Provisioning finds a site's repository and sub-domain over gRPC `GetRepository` (no REST version) | BR-4, BR-5 | It gets the repository ID, clone URL and sub-domain, or `NOT_FOUND` | Must | J3 | S2 §5.1, CTR `repository.proto` |
| PR-19 | Provisioning, Payment and Notification hear "source published" | BR-4 | The event comes from the same transaction that marks the request `published` | Must | J4 | S2 §5.2, CTR |
| PR-20 | Provisioning and Payment hear "project deleted" | BR-7 | The event comes from the transaction that sets the deletion time (FR-38) | Must | J5 | S2 §5.2, PAY §4 |
| PR-21 | Provisioning hears when a website owner is suspended or active again | BR-6 | An Account user status change gives one event per website of that user | Must | Later | S2 §4.4, CTR |
| PR-22 | AI Codegen and Notification ask Project over gRPC: input, scaffold, thread owner, project owner | BR-3, BR-6 | Each method answers per S2 §5.1; `codegen-input` stays REST until AI Codegen's team is ready | Must | Later | S2 §5.1, §9 |

### Visitors and operators

Visitors get no offer from Project: they see only the generated site.

| ID | Offer | BR | Done when | Priority | Phase | Source |
| --- | --- | --- | --- | --- | --- | --- |
| PR-23 | Operators: a token is checked at the gateway and by Project; inner calls carry only the user ID | BR-13 | A bad token gets 401; an internal call never needs a token | Must | Auth | AUTH §5, 0002 |
| PR-24 | Operators: the next migration brings the database level with Notion | BR-12 | The database audit against S1 shows zero differences | Must | J3 | S1 Planned, Art. III |

## Not offered

- Custom domains: none (S1 Site hostname, PAY A5).
- Release status and "is it live?": read from Provisioning (COMM §3).
- Editing products, categories and images: the site's CMS (S3).
- Restoring a deleted website: never offered.
- Page history: only the latest design is kept (S1 pages).

## Drift seen

The owner carries the PR-4 Drift as known Drift.

- PR-4: a website has exactly one page by owner decision; there is no page limit setting (FRD FR-18, FR-20 withdrawn).
- PR-16, PR-22: AI Codegen reads REST `codegen-input` (COMM §2.2, `CodegenInputController.java`); S2 §5.1 says gRPC
  `GetCodegenInput`; REST stays until AI Codegen's team is ready.

## Questions for the owner

Open, with a proposal, in FRD "Questions for the owner": Q-3 who commits to Gitea (PR-8, PR-9). Closed there: Q-2
and Q-5, the sub-domain label rule and length (PR-2).

## Glossary

| Word | Meaning here |
| --- | --- |
| Builder | The page editor (Puck) in the frontend |
| Component catalog | The block definitions a page tree is checked against |
| Website portal | A generated site's admin area, signed in with a `site` token |
| Per-action admin rule | Each action says whether an admin may skip the owner check (CMN §7.1) |

## Sources

| Key | Source |
| --- | --- |
| S1 | Notion "2 · Project" (read-only) |
| S2 | Notion "Web Block architecture" |
| S3 | Notion "6 · Shared catalog" (read-only) |
| AUTH, PAY, PROV, COMM | `.claude/docs/contracts/` |
| CMN | `web-block-commons/DESIGN.md` |
| CTR | `web-block-contracts` (protos, event schemas) |
| 0002, 0004, 0006, 0007 | `.claude/docs/decisions/` |
| Art. III | `.claude/docs/CONSTITUTION.md` |
