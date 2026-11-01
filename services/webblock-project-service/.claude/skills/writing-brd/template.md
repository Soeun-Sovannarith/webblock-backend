# Project service: Business Requirements (BRD)

**Status:** <Draft | Approved at gate N>

## In short

<At most 5 plain sentences: what the Project service is for, who it serves, the main goal, the biggest risk, what
the owner must decide.>

**Read next:** [PRD.md](PRD.md), what the product offers each user.

## Who it serves

| Who | What they need from Project | Source |
| --- | --- | --- |
| <platform owner, website owners, visitors, admins, Provisioning, Payment, AI Codegen, ...> | <one phrase> | <S1> |

## Goals

| ID | Goal | Sign of success | Source |
| --- | --- | --- | --- |
| BR-1 | <outcome, one plain sentence> | <number, time or yes/no check> | <S1 heading> |

## Scope

**In:** <bullets, each with its source key>

**Out (owned by others):** <bullets: what, which service owns it, source key>

## Business rules

| ID | Rule | Source |
| --- | --- | --- |
| BR-<next> | <a rule the business holds, e.g. "only the owner sees or changes a website"> | <S1 heading> |

## Constraints

| ID | Constraint | Source |
| --- | --- | --- |
| BR-<next> | <a limit the service must live with: plan limits, licence, cluster, other teams> | <PAY §2> |

## Risks

| ID | Risk | What would happen | What reduces it | Source |
| --- | --- | --- | --- | --- |
| R-1 | <plain risk> | <effect on customers or the owner> | <mitigation or "none yet"> | <path or S2 §x> |

## Drift seen

<One bullet per difference: what the source says, what the code or older doc does, path. Or "None seen.">

## Questions for the owner

| ID | Question | Why it matters | Suggested answer |
| --- | --- | --- | --- |
| Q-1 | <question> | <effect> | <your best option, labelled "suggestion"> |

## Glossary

| Word | Meaning here |
| --- | --- |
| <project word> | <one plain line> |

## Sources

| Key | Source |
| --- | --- |
| S1 | Notion "2 · Project" (read-only) |
| S2 | Notion "Web Block architecture" |
| AUTH | `.claude/docs/contracts/authentication-contract.md` |
| PAY | `.claude/docs/contracts/payment-contract.md` |
| <0007> | `.claude/docs/decisions/0007-project-access-rules.md` |
