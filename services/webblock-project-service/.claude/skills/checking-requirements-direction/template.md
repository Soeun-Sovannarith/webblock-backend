# Project service: Direction check

**Status:** <Draft | Approved at gate N>
**Checked:** branch <name> at commit <short sha>
**Verdict:** <On track | At risk | Off track>, <one plain reason>

## In short

<At most 5 plain sentences, starting with the verdict: what is done, what drifts, what blocks, what to decide.>

**Read next:** [BRD.md](BRD.md) to start the chain again; the items below link into each document.

## Top 5 to look at

1. **<plain title>** (<IDs>): <what is wrong or waiting, why it matters, what you need to decide or do>.
2. ...

## Count

| Level | Done | In progress | Not started | Drift | Orphan |
| --- | --- | --- | --- | --- | --- |
| BR | <n> | <n> | <n> | <n> | |
| PR | | | | | |
| FR | | | | | |
| TR | | | | | |
| Code and work | | | | | <n> |

## Trace

<One plain sentence. One row per FR under the BR of its first PR, then the groups "No PR" (FRs with no PR)
and "Cross-cutting" (TRs serving All FR). The TR column lists every TR that serves the FR. A BR with no FR
keeps its heading with one line saying so.>

### BR-<n>: <title>, <status>

| FR | PR | TR | Evidence | Status | Note |
| --- | --- | --- | --- | --- | --- |
| FR-<n> | PR-<n> | TR-<n> | <`path:line`, test name> | <Done> | <short> |

### TRs not Done

| TR | FR | Status | Evidence |
| --- | --- | --- | --- |
| TR-<n> | FR-<n> | <In progress, Not started or Drift> | <`path:line` or search> |

## FRs with no test

<One plain sentence: how many FRs have every acceptance row tested. The table lists rows with neither a
tagged nor a closest test; one line after it gives the count of rows covered only by closest tests.>

| FR | Acceptance rows without a test | Closest test, no ID yet |
| --- | --- | --- |
| FR-<n> | FR-<n>.<m> | <`ClassNameTest.methodName` or "none"> |

## Drift

| Item | Requirement says | Code does | Where |
| --- | --- | --- | --- |
| <FR-n> | <plain> | <plain> | <`path:line`> |

## Orphans

| Work, code or test file | Where | Suggested home |
| --- | --- | --- |
| <plain> | <path or commit> | <an FR it could belong to, or "ask owner"> |

## How I checked

<Commit, documents read, the `scripts/spec-check.py` summary (counts, errors, Must FRs with no test), searches,
tests run with counts, suites not run and the latest known counts.>

## Questions for the owner

| ID | Question | Why it matters | Suggested answer |
| --- | --- | --- | --- |
| Q-1 | <question> | <effect> | <your best option, labelled "suggestion"> |

## Glossary

| Word | Meaning here |
| --- | --- |
| <project word> | <one plain line> |
