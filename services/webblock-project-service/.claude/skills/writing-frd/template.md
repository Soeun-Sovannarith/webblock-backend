# Project service: Functional Requirements (FRD)

**Status:** <Draft | Approved at gate N>

## In short

<At most 5 plain sentences: the main flow from a new website to a published generation, how conflicts and
refusals are handled, how deletion works, what the owner must decide.>

**Read next:** [TRD.md](TRD.md), how it is built. Up: [PRD.md](PRD.md).

## Main flow

<One sentence, then the document's one small Mermaid diagram: the main flow or the generation request states.>

## <Area name, e.g. "Pages and the builder">

<One plain sentence: what this area is for.>

| ID | Trigger | Input | What happens | Errors | PR | Source |
| --- | --- | --- | --- | --- | --- | --- |
| FR-1 | <who, how> | <what goes in> | <short steps, then the result> | <`CODE`, `CODE`> | PR-<n> | <S1 heading> |

**Acceptance**

| ID | Given | When | Then |
| --- | --- | --- | --- |
| FR-1.1 | <starting state> | <the trigger> | <what a test observes on success> |
| FR-1.2 | <starting state that fails> | <the trigger> | <status and `CODE`; what must not happen> |

<repeat one section per area>

## States

<One plain sentence: which things have a status and why it matters.>

### <Project | Generation request>

| Value | Meaning | Next |
| --- | --- | --- |
| <value from S1> | <plain meaning> | <values it can move to, and what moves it> |

## Errors at a glance

<One plain sentence.>

| Code | Status | Meaning for the user | Source |
| --- | --- | --- | --- |
| <`CODE`> | <409 or stored> | <plain> | <path or S2 §x> |

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
| AUTH | `.claude/docs/contracts/authentication-contract.md` |
| PAY | `.claude/docs/contracts/payment-contract.md` |
| <0004> | `.claude/docs/decisions/0004-generation-capture-and-admission.md` |
