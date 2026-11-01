# Project service: Business Requirements (BRD)

**Status:** Approved at gate 1 by the owner

## In short

The Project service keeps every client's website: its pages, the frozen rules for generating its code, and each
request to generate and publish it. It serves website owners through the builder, and the platform services that
need to know who owns a site and when new code is ready. The main goal: a client goes from a new website to published
code without losing work and within their plan. The biggest risk is that Payment, AI Codegen, Keycloak and the mesh
are not finished, so parts run on stand-ins. Each website now has its sub-domain and Project answers access checks
over gRPC; Git hosting and publication come next.

**Read next:** [PRD.md](PRD.md), what the product offers each user.

## Who it serves

| Who | What they need from Project | Source |
| --- | --- | --- |
| Platform owner | A service equal to the design | Art. III |
| Website owners | Create, edit, generate, publish | S1 |
| Site visitors | Only the generated site | S1 Site hostname |
| Platform admins | Find and act on any website | S2 §2.4 |
| Provisioning | Who manages a site, where its code is | S2 §5.1 |
| Payment | Asked before each website or generation | PAY §2 |
| AI Codegen | A generation's frozen input | S2 §5.1 |
| Catalog | Each new website registered | S3 |
| Notification | Who owns a project | S2 §5.1 |
| Account | Its `user_id` is the owner | AUTH §3 |

## Goals

| ID | Goal | Sign of success | Source |
| --- | --- | --- | --- |
| BR-1 | A client creates a website from a template in one step, within the plan's website limit | Yes/no: the website and its template pages exist, or a plan refusal and nothing | S1 How the tables work, PAY §2.1 |
| BR-2 | Two saves of one website never silently overwrite each other | Yes/no: every save with an old revision is refused; one page per website | S1 pages |
| BR-3 | A generation request freezes the pages, is counted once by Payment and reaches AI Codegen | Yes/no: one request in flight per website; each ends `published` or `failed` | S1 generation_requests |
| BR-4 | Accepted code is committed to the site's Gitea repository and Provisioning can deploy it | Yes/no: a `published` request has a commit and a "source published" event | S1 How the tables work |
| BR-5 | Each website has a fixed visitor-facing name that is never reused | Yes/no: the sub-domain is unique over all websites, deleted ones too, never changes, never equals another site's preview name and is never a punycode name | S1 Site hostname, FRD Q-2 |
| BR-6 | Other services learn reliably who owns a website and what changed | Yes/no: each access check answers inside the caller's 2 s deadline; no event is lost | S2 §3.6, §4.3 |
| BR-7 | A deleted website goes offline, its catalog is archived and its plan slot is freed | Yes/no: deletion time set once and a "project deleted" event sent | S1 How the tables work |

## Scope

**In:** websites, pages, generation contracts and requests (S1, 0004); the catalog schema and registration (S3);
access checks and events (S2 §5); committing code to Gitea, which Project alone does (S2 §9).

**Out (owned by others):**

- Hosting, releases and "is it live?": Provisioning (S1).
- Catalog content (products, categories, images): the site's CMS (S1, S3).
- Building and testing generated code: AI Codegen (S1, 0006).
- Plans, limits, bills and usage counts: Payment (PAY §1).
- Sign-in, accounts and user suspension: Account and Keycloak (AUTH §1).

## Business rules

| ID | Rule | Source |
| --- | --- | --- |
| BR-8 | Only the owner sees or changes a website; anyone else is told it does not exist | AUTH §2, S2 §2.3 |
| BR-9 | A deleted or suspended website stays readable to its owner but cannot be changed | 0007 |
| BR-10 | Nothing is purged: websites and requests stay forever; pages are only marked deleted | S1 Tables |
| BR-11 | Payment alone decides plan limits; Project refuses when Payment cannot answer | PAY §2, §5 |
| BR-12 | The databases match the Notion design exactly, nothing more | Art. III, S1, S3 |

## Constraints

| ID | Constraint | Source |
| --- | --- | --- |
| BR-13 | Sign-in is checked at the gateway, then by Project; between services only the user ID travels, over the mesh | AUTH §1, 0002 |
| BR-14 | Payment, AI Codegen, Keycloak and the mesh are built by others; stand-ins run only in the prototype profile | `ai/handoffs/next.md`, Art. VI |
| BR-15 | Every dependency is open source, at least 14 days old and pinned | Art. II |

## Risks

| ID | Risk | What would happen | What reduces it | Source |
| --- | --- | --- | --- | --- |
| R-1 | Payment's answer is lost | A failed request still uses a trial attempt | Accepted; Payment reconciles later | PAY §2.3 |
| R-2 | Removed: gRPC `CheckAccess` is built; see R-5 | | | |
| R-3 | Removed: V6 and V7 bring the database level with Notion; see R-6 | | | |
| R-4 | A request stuck in `captured` | The website can never generate again | Deadline job (FRD) | `ai/handoffs/next.md` |
| R-5 | Nobody calls gRPC `CheckAccess` yet | REST check stays; the auth library waits | Owner adds the mesh port and policy; Provisioning adds its client | 0011, CMN §7.2 |
| R-6 | V6 goes live before Provisioning routes by sub-domain | Sites unreachable at their new names | Rollout waits for Provisioning's routing | `.claude/docs/runbooks/migrations.md` |

## Drift seen

- None left in the database: after V6 and V7 it matches S1 (`subdomain`, `repository_id`, `repository_full_name`,
  no `project.domains`, no PUBLIC access).
- None left on the Git host: S1, S2, CTR, COMM and the database (`repository_id` since V6) say Gitea, and
  Provisioning reads the repository over gRPC `GetRepository` only (PR-18).
- S1 "Open" lists who commits (FRD Q-3), who sets `active` and `suspended` (FR-37: the first live release; nobody)
  and restore (never). S2 §4.4 gives Project no queue for "publish completed", which FR-37 needs.

## Questions for the owner

Open, with a proposal: FRD Q-3 (Gitea committer). Closed: FRD Q-2 and Q-5 (sub-domain labels and length).

## Glossary

| Word | Meaning here |
| --- | --- |
| Website, project | One client site, keyed by its project ID |
| Sub-domain | The visitor-facing name, `<subdomain>.<site domain>` |
| Preview name | A site's preview address, `<subdomain>-preview.<site domain>` |
| Generation request | One attempt to turn saved pages into published code |
| Admission | Payment's yes or no for a website or a generation |

## Sources

| Key | Source |
| --- | --- |
| S1 | Notion "2 · Project" (read-only) |
| S2 | Notion "Web Block architecture" |
| S3 | Notion "6 · Shared catalog" (read-only) |
| AUTH, PAY, COMM | `.claude/docs/contracts/` (authentication, payment, service-communication) |
| CMN | `web-block-commons/DESIGN.md` (shared auth library, approved) |
| CTR | `web-block-contracts` (protos, event schemas) |
| 0002, 0004, 0006, 0007, 0011 | `.claude/docs/decisions/` |
| Art. II, Art. III, Art. VI | `.claude/docs/CONSTITUTION.md` |
