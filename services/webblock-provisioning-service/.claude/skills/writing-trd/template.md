# Provisioning: Technical Requirements (TRD)

**Status:** <Draft | Approved at gate N>

## In short

<At most 5 plain sentences: what the service is made of, where its data lives, how others talk to it, how it is
kept safe, how it is deployed.>

**Read next:** [DIRECTION.md](DIRECTION.md), whether the work matches these requirements. Up: [FRD.md](FRD.md).

## Components

<One plain sentence. Optionally one small Mermaid diagram of the processes and who talks to whom.>

| ID | Requirement | FR | Decision | Source |
| --- | --- | --- | --- | --- |
| TR-1 | <how it is built, one sentence> | FR-<n>, FR-<m> | <0013> | <path or S2 §x> |

## Stack

<One plain sentence.>

<same table>

## Data

<One plain sentence. Tables named with one line each; fields live in S1.>

<same table>

## Interfaces

<One plain sentence. Rows for REST, gRPC, RabbitMQ events, MCP.>

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

<Each article of `.claude/docs/CONSTITUTION.md`, one row: pass, not applicable, or conflict, with one line why. A
conflict stops the work until the owner rules.>

| Article | Result | Why |
| --- | --- | --- |
| I Spec-driven flow | <pass, not applicable, conflict> | <one line> |
| II Tech stack | <...> | <...> |
| III Architecture limits | <...> | <...> |
| IV Coding conventions | <...> | <...> |
| V Testing and quality | <...> | <...> |
| VI Safety guardrails | <...> | <...> |
| VII Docs | <...> | <...> |
| VIII Agents | <...> | <...> |

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
| S1 | Notion "5 · Provisioning" (read-only) |
| S2 | Notion "Web Block architecture" (read-only) |
| <0013> | `.claude/docs/decisions/0013-...md` |
