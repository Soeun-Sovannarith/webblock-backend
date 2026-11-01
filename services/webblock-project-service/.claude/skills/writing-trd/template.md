# Project service: Technical Requirements (TRD)

**Status:** <Draft | Approved at gate N>

## In short

<At most 5 plain sentences: what the service is made of, where its data lives, how others talk to it, how it is
kept safe, how it is deployed.>

**Read next:** [DIRECTION.md](DIRECTION.md), whether the work matches these requirements. Up: [FRD.md](FRD.md).

## Modules and layers

<One plain sentence. Optionally one small Mermaid diagram of the modules and who talks to whom.>

| ID | Requirement | FR | Decision | Source |
| --- | --- | --- | --- | --- |
| TR-1 | <how it is built, one sentence> | FR-<n>, FR-<m> | <0008> | <path or S2 §x> |

## Stack

<One plain sentence.>

<same table>

## Data

<One plain sentence. Tables named with one line each; fields live in S1 and S3.>

<same table>

## Interfaces

<One plain sentence. Rows for REST, internal REST, gRPC and RabbitMQ events.>

<same table>

## Security

<One plain sentence on the auth model.>

<same table>

## Non-functional needs

<One plain sentence. Rows for performance, availability, observability.>

<same table>

## Deployment

<One plain sentence.>

<same table>

## Constitution check

<One row per article of `.claude/docs/CONSTITUTION.md`: pass, not applicable, or conflict. A conflict stops the work:
name it under "Questions for the owner" and wait for the owner's ruling.>

| Article | Result | Note |
| --- | --- | --- |
| I Spec-driven flow | <pass, not applicable or conflict> | <one short phrase: why> |
| II Tech stack | <result> | <why> |
| III Architecture limits | <result> | <why> |
| IV Coding conventions | <result> | <why> |
| V Testing and quality | <result> | <why> |
| VI Safety guardrails | <result> | <why> |
| VII Docs | <result> | <why> |
| VIII Agents | <result> | <why> |

## Drift seen

<One bullet per difference: what the source says, what the code does, path. Or "None seen.">

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
| S3 | Notion "6 · Shared catalog" (read-only) |
| AUTH | `.claude/docs/contracts/authentication-contract.md` |
| <0001> | `.claude/docs/decisions/0001-outbox-with-modulith-and-namastack.md` |
