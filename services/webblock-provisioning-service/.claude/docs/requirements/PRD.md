# Provisioning: Product Requirements (PRD)

**Status:** Approved at gate 1 (answers O1 to O4 are in `GATE1`).

## In short

Website owners get each published change built, started privately and made live with no click, with automatic
recovery when going live fails, and a clear view of each version. Admins can suspend, resume, restart, read and
recover any site; only an admin rolls back. Project and Payment drive releases and hosting by events, and Payment
hears of every publish that went live. Phase C (in progress) adds the answer shape, admin actions and sub-domain
addresses; automatic deploy and go-live (PR-26) is planned for the next phase and not started.

**Read next:** [FRD.md](FRD.md), exactly how each offer behaves. Up: [BRD.md](BRD.md).

## What each user gets

### Website owner

| ID | Offer | BR | Done when | Priority | Phase | Source |
| --- | --- | --- | --- | --- | --- | --- |
| PR-1 | Each published change is built into a new version by itself | BR-1, BR-8 | Publish on Kind: a release reaches `built` with no click | Must | Prototype | S1 steps 1 to 2 |
| PR-2 | Removed: the owner no longer starts deploy or go-live (GATE1 O2); see PR-26 | BR-1 | none | none | Prototype | GATE1 |
| PR-3 | A failed go-live brings the previous version back by itself | BR-2 | Break a candidate's checks: old version still served | Must | Prototype | S1 `releases` |
| PR-4 | The owner sees versions, their steps, actions and whether the site is live and online | BR-7 | Each read shows status and a safe failure reason | Must | Prototype | S1 status questions |
| PR-5 | Removed: only an admin rolls back (GATE1 O1); see PR-13 | BR-2, BR-9 | none | none | Phase C | GATE1 |
| PR-6 | The owner never sees or touches another owner's site | BR-4 | Another owner's site answers exactly like an unknown one | Must | Prototype | AUTH §2, S2 §2.3 |
| PR-7 | The owner retries a failed build attempt while attempts remain | BR-1 | Retry starts the next attempt | Could* | Prototype | 0014 §3 |
| PR-8 | Every answer has the platform's shape (envelope, problem errors, paging) | BR-12 | A REST answer matches S2 §2.1 and §2.2 | Must | Phase C | S2 §2, 0023 G2 to G6 |
| PR-9 | Changes are refused while the plan does not cover the site or it is offline | BR-3 | Deploy with plan ended or site offline: refused, nothing stored | Must | Prototype | S1 `commands` notes |
| PR-26 | Each built version starts privately and goes live by itself, with no click | BR-1, BR-2 | Publish on Kind: the site serves the new version, nobody clicks | Must | Later | GATE1 O2, S1 steps 3 to 4 |

### Admin

| ID | Offer | BR | Done when | Priority | Phase | Source |
| --- | --- | --- | --- | --- | --- | --- |
| PR-10 | Suspend one website; the owner can still edit | BR-5, BR-3 | Site offline with reason admin suspended | Must | Phase C | S1 planned migration |
| PR-11 | Resume a website the admin suspended; other reasons stay | BR-5, BR-3 | Online only when no other reason holds | Must | Phase C | S1 `offline_reasons` |
| PR-12 | Restart a live site with the same image | BR-5 | Pods restart; no new release, no publish counted | Must | Phase C | S1 `redeploy` |
| PR-13 | Roll back any site's stuck route | BR-5, BR-9 | Admin rollback on another owner's site is accepted | Must | Prototype | S1 `commands`, 0019 §7 |
| PR-14 | See a site's actions with who asked | BR-5 | Admin list shows the requester on admin actions | Must | Phase C | S1 activity log |
| PR-28 | Read any site's versions, actions and status | BR-5 | An admin who is not the owner reads them: answered | Must | Later | LEAD, S2 §2.4 |

### Other services (Project, Payment, Notification)

| ID | Offer | BR | Done when | Priority | Phase | Source |
| --- | --- | --- | --- | --- | --- | --- |
| PR-15 | Project: each "source published" becomes exactly one release after Project confirms it | BR-8 | Same event twice: one release | Must | Prototype | S1 `source_published_events` |
| PR-16 | Project: a deleted project goes offline for good | BR-11 | After deletion no resume brings it back | Must | Prototype | S1 `offline_reasons`, 0014 |
| PR-17 | Project: a suspended owner's sites go offline; reactivation lifts only that reason | BR-3 | Owner suspended then reactivated: billing reason stays | Must | Phase B | S1 planned 00018 |
| PR-18 | Payment: an ended plan takes the site offline; a restored plan brings it back | BR-3 | Ended then restored: offline then online | Must | Prototype | S1 `offline_reasons` |
| PR-19 | Payment and Notification: one "publish completed" per release gone live | BR-6 | Go live once: one message, Payment dedupes on its key | Must | Prototype | S1 `usage_outbox` |

### Visitors, operators and team agents

| ID | Offer | BR | Done when | Priority | Phase | Source |
| --- | --- | --- | --- | --- | --- | --- |
| PR-20 | Visitors reach the live version at `<subdomain>.<site domain>` | BR-1 | The host answers with the live release | Must | Phase C | S1 `subdomain`, 0023 G18 |
| PR-21 | A candidate is viewable at `<subdomain>-preview.<site domain>` before go-live | BR-2 | The preview host answers with the candidate | Should* | Phase C | PREVIEW1 |
| PR-22 | Visitors see an offline page while the site is offline | BR-3, BR-10 | Offline site answers the offline page | Must | Prototype | S1 `rollback` kind, 0014 §13 |
| PR-23 | Operators re-drive failed source events and parked messages | BR-1 | A failed event re-driven becomes a release | Should* | Prototype | S1 manual retry, S2 §4.3 |
| PR-24 | Team agents use the same actions through MCP with the same checks | BR-4, BR-5 | An MCP call is refused exactly as REST | Should* | Prototype | S2 §6.2, 0023 G7 |
| PR-25 | Operators follow one request across services by its request ID and trace | BR-7 | A log line and its trace share IDs | Must | Phase B | S2 §7.3, 0022 |
| PR-27 | Production stays read-only until the contracts are agreed | BR-15 | Production: a change is refused, a read works; opens when Project and Payment gRPC pass on Kind | Must | Production | 0016 §6, LEAD |
| PR-29 | Only the owner opens a candidate's preview host, later behind the site-portal sign-in | BR-2, BR-4 | A preview request without the project's token never reaches the candidate | Should | Later | LEAD, 0023 G18c |

## Not offered

- Generating or committing code, or choosing the sub-domain: Project and AI Codegen (S1 callout).
- Custom domains: not offered for now (0014 §23).
- Reverting a healthy live version to an older one: rollback is recovery only (S1 `rollback` kind).
- Owners starting deploy, go-live or rollback: deploy and go-live run by themselves, rollback is for admins (GATE1).
- Plan limits, bills and trials: Payment (S1 callout).
- Messages to owners: Notification (S2 §5.2).
- An admin list across all sites: the activity log is per site (0023 G9).

## Drift seen

- Admin reads (PR-14 area): `get_command` and `get_deployment` answer 404 to an admin who is not the owner
  (OD-45, security review M-4), while S2 §2.4 lets `ADMIN` act on any site.
- Deploy and promote (PR-26): the code takes them from the owner with role `USER`
  (`.claude/docs/runbooks/identity.md:49`); the automatic flow is planned work for the next phase, not started.

## Questions for the owner

| ID | Question | Why it matters | Suggested answer |
| --- | --- | --- | --- |
| Q-4 | Keep deploy and promote in the API for admins? S1 lists admin actions as rollback, suspend, resume and redeploy only | Decides whether the routes stay | suggestion: no; remove the owner routes when PR-26 ships |

Answered (LEAD): Q-1 priorities as marked; Q-2 preview host for the owner only (PR-29);
Q-3 an admin reads any site (PR-28).

## Glossary

| Word | Meaning here |
| --- | --- |
| Release | One version of a site, from one published commit |
| Candidate | A release running privately, with no visitors |
| Stuck route | A route change not confirmed (`uncertain`) |
| Activity log | A site's commands with the admin who asked |
| Re-drive | Put a failed message or event back to work |

## Sources

| Key | Source |
| --- | --- |
| S1 | Notion "5 · Provisioning" (read-only) |
| S2 | Notion "Web Block architecture" (read-only) |
| AUTH | `../project-service/.claude/docs/contracts/authentication-contract.md` |
| 0014, 0016, 0019, 0021, 0022, 0023 | `.claude/docs/decisions/` records by number |
| OD-45 | `.claude/docs/decisions/open-decisions.md` |
| PREVIEW1 | Owner: the preview host is `<subdomain>-preview.<site domain>`, one label under the site domain; a sub-domain ending in `-preview`, or over 55 characters, is refused |
| BRD | [BRD.md](BRD.md) |
| GATE1 | Owner: gate 1 answers O1 to O4 |
| LEAD | Answers the owner delegated to the lead |
