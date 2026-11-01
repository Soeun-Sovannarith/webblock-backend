---
name: checking-requirements-direction
description: Use when asked if Provisioning work is on track or ready to merge, or DIRECTION.md is missing or old.
---

# Checking the direction of the Provisioning work

## Overview

The direction check traces every requirement down to code and tests, and tells the owner in plain words whether
the work is on track. Its base is the output of `make spec-check`: the register
`.claude/docs/requirements/spec.yaml` (every ID with its status, parents, priority, phase, FR rows and tests) and
the problems the checker finds. Other inputs: `BRD.md`, `PRD.md`, `FRD.md`, `TRD.md`, the code, the tests, and the
plans and handoffs (`ai/handoffs/`, `.claude/docs/plans/`). Output: `.claude/docs/requirements/DIRECTION.md`,
built from `template.md` in this folder. A check only reads and reports.

If one of the four documents is missing, say so in the first sentence of "In short" and trace that level
against its sources instead (Notion "5 · Provisioning" and "Web Block architecture", then Current decisions).

## Status words

| Status | Use it when | Evidence |
| --- | --- | --- |
| Done | code does it and every acceptance row (`FR-n.m`) has a passing test | `path:line`, test names |
| In progress | code or a plan task exists, but some acceptance row has no passing test | path or plan task |
| Not started | nothing in code or plans | the search you ran |
| Drift | code differs from the requirement | both sides, `path:line`; plan task if any |
| Orphan | code, a test file or feature work that no requirement asks for | path or commit |

- A test belongs to an FR when its docstring or name carries the ID (`"""FR-7.2: ..."""`, `test_fr7_...`), or,
  as "closest test, no ID yet", when it (alone or with one other test) asserts all of that row's "Then".
- A test is passing when you ran it, or when a handoff reports its suite passing on this branch; say which.
  Suites never run on this branch (for example Kind, e2e) are not passing.
- An FR or TR takes the priority and phase of its PR; a TR serving `All FR` is Must. A BR, PR or FR takes
  its children's status (an FR's children are its TRs): Drift if any is Drift, Done if all are Done, Not
  started if none started, else In progress.
- Process files (rules, skills, agents, diagrams, handoffs, docs) are never Orphans.
- Check the committed code at one commit; mention uncommitted work in "How I checked" without judging it.
  When the code looks right and the spec looks wrong, it is still Drift; Note says "spec may need the fix".

## Verdict

**Open drift** is Drift on a Must item with no plan task (a task in `.claude/docs/plans/` or
`ai/handoffs/`) that fixes it.

- **On track:** no Drift on Must items, and every Must item of the current phase is Done or In progress.
- **At risk:** any Must Drift, planned or open; a Must item of the current phase Not started; or the phase
  waits on another team.
- **Off track:** open drift makes the "Done when" of three or more Must PRs fail today, or there are more
  Orphans than In progress items.

## Steps

1. Run `make spec-check` and keep its output. Its register is the ID list (acceptance rows included) with
   priority, phase and the tagged tests of each FR; quote its counts in "How I checked". Each problem it
   prints is a finding: a citation of an unknown or removed ID, a test list that differs from the tests, or a
   Must FR with no test, each with its ID.
2. For each FR and its TRs, start from the register's test list, search the code and tests, open the line you
   cite, and pick a status. `rg -n -i "fr-?[0-9]+" tests` also finds tagged tests the register missed.
3. Find Orphans: compare `git log --oneline -30`, plan tasks, top-level modules and test files with the trace.
4. Run `make py-test` once in the repo (it needs `../web-block-contracts` beside it) and quote the count.
   For suites you did not run, give the latest handoff count and the commit it ran on.
5. Fill `template.md` in order; its placeholders say how to group the trace. Write "Top 5" last: what the
   owner should look at, most urgent first, each in plain words with the IDs and the decision or action needed.

## Writing rules

Plain English, short sentences, no em or en dashes (`grep -nP '[\x{2013}\x{2014}]'` prints nothing), prose
lines at most 120 characters, table cells are short phrases, `wc -w` at most 4000. "In short" has at most 5
sentences and starts with the verdict. Project words go in the Glossary.
