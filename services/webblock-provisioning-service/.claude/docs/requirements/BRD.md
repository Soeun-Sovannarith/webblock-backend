# Provisioning: Business Requirements (BRD)

**Status:** Approved at gate 1 (answers O1 to O4 are in `GATE1`).

## In short

Provisioning turns each change a website owner publishes into a live website, and takes sites offline and back
online when the plan, the owner's account or the project says so. It serves website owners, visitors, platform
staff, and the Project and Payment services. The main goal: a published change goes live safely, and a failed
update never takes a working site down. After a build, the new version goes live by itself; only an admin rolls
back. The biggest risk: other teams' contracts are not final, so production stays read-only.

**Read next:** [PRD.md](PRD.md), what the product offers each user.

## Who it serves

| Who | What they need from Provisioning | Source |
| --- | --- | --- |
| Platform owner | Sites go live without staff work | 0014 §2 |
| Website owners | Changes go live; progress and failures shown | S1 status questions |
| Site visitors | The live version, or an offline page | S1 `project_hosting` |
| Platform admins | Act on any site; see who did what | S1 `commands`, S2 §2.4 |
| Project | Published commits become releases | S2 §5.2 |
| Payment | Sites follow the plan; publishes counted | S2 §5.2 |
| Notification | Hears "publish completed" | S2 §5.2 |
| Team agents (MCP) | Same actions as REST | S2 §6.2 |

## Goals

| ID | Goal | Sign of success | Source |
| --- | --- | --- | --- |
| BR-1 | A published change becomes a live site with no staff work | No click on Kind; 95% live within 15 min | 0014 §2, S1 steps 1 to 4, GATE1 |
| BR-2 | A failed update never takes a working site down | Kind run: failed go-live, old version served (yes/no) | S1 `releases`, 0014 §2 |
| BR-3 | A site is offline exactly while a reason holds | Each reason added and removed alone (yes/no) | S1 `offline_reasons` |
| BR-4 | An owner acts only on their own sites and learns nothing of others | Not-owner answer equals unknown-site answer (yes/no) | AUTH §2 |
| BR-5 | Staff can act on any site, and each staff action names the admin | Activity log shows the admin (yes/no) | S1 `commands` |
| BR-6 | Payment hears of each publish gone live, once | One message per live release (yes/no) | S1 `usage_outbox` |
| BR-7 | Owners and staff see where a version is and why it failed | Safe status read; API up 99.5% a month | S1 status questions, GATE1 |

## Scope

**In:** release intake, build, private start, go-live, recovery, offline and online, admin actions, the
"publish completed" message, REST and MCP (S1 callout, S1 `commands`, S2 §5.3, §6.2, 0023 G9).

**Out (owned by others):**

- Generating code and committing it to Gitea: AI Codegen and Project (S2 §5.1, OWN).
- Choosing the sub-domain: Project (S1 callout).
- Plans and whether hosting is paid: Payment (S1 callout).
- User status and sign-in: Account and Keycloak (AUTH §1).
- Messages to people: Notification with Knock (S2 §5.2).
- The site portal while a site is offline: closed; the owner edits in the web app (LEAD).
- Custom domains: not offered (0014 §23).

## Business rules

| ID | Rule | Source |
| --- | --- | --- |
| BR-8 | One published commit is one version; a failed version is never retried | S1 `releases` |
| BR-9 | One action per site at a time; a stuck route blocks new work until recovered | S1 `commands` |
| BR-10 | Going offline never deletes code, content or the live version | S1 `hosting_status` |
| BR-11 | A deleted project never comes back online | S1 `offline_reasons` |

## Constraints

| ID | Constraint | Source |
| --- | --- | --- |
| BR-12 | Follows the platform's sign-in, answer, event and library rules | AUTH, S2 §1 to §4, §8 |
| BR-13 | Shared cluster and data stores; open-source licences; images pinned, 14 days old | S2 §7, OWN |
| BR-14 | Jenkins builds site images into the Gitea registry; platform images go to Docker Hub | S2 §5.4, OWN, GATE1 |
| BR-15 | Production refuses every change until Project's and Payment's contracts are agreed | 0016 §6, 0023 §6 |

## Risks

| ID | Risk | What would happen | What reduces it | Source |
| --- | --- | --- | --- | --- |
| R-1 | Other contracts not final | Production read-only | Draft protos | OD-01, OD-04 |
| R-2 | Route stuck | Site waits | Rollback, alert | S1 `routing_status` |
| R-3 | Shared library not approved | Auth code done twice | Stop-gaps | S2 §8, HO-AUTH |
| R-4 | Jenkins or Gitea down | No new builds | Build retries | S1 `release_steps` |
| R-5 | Removed: answered, site images are private with a pull Secret (GATE1 O3, replaced) | | | GATE1 |

## Drift seen

- Registry: S2 §5.4 and OWN say Gitea for site images; `0016` §2 says Docker Hub.
- Gateway: AUTH has the Istio gateway check the token first; OD-55 says no gateway is in front of `/v1/*`.

## Questions for the owner

None open. Answered: rollback admin only (O1); deploy and go-live run by themselves after a
build (O2); site images private, a pull Secret per site (O3); targets 95% live in 15 min,
API 99.5% a month (O4). Lead: portal closed while offline; rows kept forever, images of live and waiting releases
kept; production opens when Project's and Payment's gRPC calls pass on Kind.

## Glossary

| Word | Meaning here |
| --- | --- |
| Release | One version of a site, from one commit |
| Go live | Move visitors to a candidate |
| Stuck route | A route change that could not be confirmed (`uncertain`) |
| Offline reason | Why a site is offline (four kinds, S1) |

## Sources

| Key | Source |
| --- | --- |
| S1 | Notion "5 · Provisioning" (read-only) |
| S2 | Notion "Web Block architecture" (read-only) |
| AUTH | `../project-service/.claude/docs/contracts/authentication-contract.md` |
| OWN | Owner decisions, in the lead's brief |
| GATE1 | Owner: gate 1 answers O1 to O4 |
| LEAD | Answers the owner delegated to the lead |
| HO-AUTH | `ai/handoffs/auth-telemetry-alignment.md` |
| 0014, 0016, 0019, 0023 | `.claude/docs/decisions/` records by number |
| OD-n | `.claude/docs/decisions/open-decisions.md` |
