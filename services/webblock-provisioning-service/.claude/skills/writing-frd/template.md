# Provisioning: Functional Requirements (FRD)

**Status:** <Draft | Approved at gate N>

## In short

<At most 5 plain sentences: the main flow from published change to live site, how failures are handled, how
hosting goes off and on, what the owner must decide.>

**Read next:** [TRD.md](TRD.md), how it is built. Up: [PRD.md](PRD.md).

## Main flow

<One sentence, then the document's one small Mermaid diagram: the main flow or the release state diagram.>

## <Area name, e.g. "New releases and builds">

<One plain sentence: what this area is for.>

| ID | Trigger | Input | What happens | Errors | PR | Source |
| --- | --- | --- | --- | --- | --- | --- |
| FR-1 | <who, how> | <what goes in> | <short steps, then the result> | <`CODE`, `CODE`> | PR-<n> | <S2 §x> |

**Acceptance**

| ID | Given | When | Then |
| --- | --- | --- | --- |
| FR-1.1 | <starting state> | <the trigger> | <what a test observes on success> |
| FR-1.2 | <starting state that fails> | <the trigger> | <status and `CODE`; what must not happen> |

<repeat one section per area>

## States

<One plain sentence: which things have a status and why it matters.>

### <Release | Command | Hosting | ...>

| Value | Meaning | Next |
| --- | --- | --- |
| <value from S1> | <plain meaning> | <values it can move to, and what moves it> |

## Errors at a glance

<One plain sentence.>

| Code | Status | Meaning for the user | Source |
| --- | --- | --- | --- |
| <`CODE`> | <409 or stored> | <plain> | <path or S2 §x> |

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
| <0023> | `.claude/docs/decisions/0023-...md` |
