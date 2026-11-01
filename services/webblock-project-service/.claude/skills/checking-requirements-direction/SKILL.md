---
name: checking-requirements-direction
description: Use when the owner asks whether Project service work is going in the right direction, is on track, is
  ready to merge, matches the requirements or Notion, or wants a trace from BRD, PRD, FRD and TRD to code and tests,
  and always before a merge (.claude/rules/spec-driven.md), or when .claude/docs/requirements/DIRECTION.md is
  missing or older than the latest work.
---

# Checking the direction of the Project work

## Overview

The direction check traces every requirement down to code and tests, and tells the owner in plain words whether
the work is on track. Inputs: `.claude/docs/requirements/BRD.md`, `PRD.md`, `FRD.md`, `TRD.md`, the code, the tests,
and the plans and handoffs (`.claude/docs/plans/`, `ai/handoffs/`). Output: `.claude/docs/requirements/DIRECTION.md`,
built from `template.md` in this folder. A check only reads and reports.

If one of the four documents is missing, say so in the first sentence of "In short" and trace that level
against its sources instead (Notion "2 · Project" and "Web Block architecture", the contracts in
`.claude/docs/contracts/`, then the Accepted decisions in `.claude/docs/decisions/`).

## Status words

| Status | Use it when | Evidence |
| --- | --- | --- |
| Done | code does it and every acceptance row (`FR-n.m`) has a passing test | `path:line`, test names |
| In progress | code or a plan task exists, but some acceptance row has no passing test | path or plan task |
| Not started | nothing in code or plans | the search you ran |
| Drift | code differs from the requirement | both sides, `path:line`; plan task if any |
| Orphan | code, a test class or feature work that no requirement asks for | path or commit |

- A test belongs to an FR when its `@DisplayName` or method name carries the ID (`@DisplayName("FR-7.2: ...")`,
  `fr7_2_...`), or, as "closest test, no ID yet", when it (alone or with one other test) asserts all of that
  row's "Then". Tests written before the FRD carry no ID; that is not an Orphan.
- A test is passing when you ran it, or when a handoff reports its suite passing on this branch at this commit;
  say which. Suites never run on this branch (for example `integrationTest`, the Kind exit) are not passing.
- An FR or TR takes the priority and phase of its PR; a TR serving `All FR` is Must. A BR, PR or FR takes
  its children's status (an FR's children are its TRs): Drift if any is Drift, Done if all are Done, Not
  started if none started, else In progress.
- Process files (`.claude/`, `ai/`, docs, scripts that run gates) are never Orphans.
- Check the committed code at one commit; mention uncommitted work in "How I checked" without judging it.
  When the code looks right and the spec looks wrong, it is still Drift; Note says "spec may need the fix".

## Verdict

**Open drift** is Drift on a Must item with no plan task (a task in `.claude/docs/plans/` or `ai/handoffs/`)
that fixes it.

- **On track:** no Drift on Must items, and every Must item of the current phase is Done or In progress.
- **At risk:** any Must Drift, planned or open; a Must item of the current phase Not started; or the phase
  waits on another team.
- **Off track:** open drift makes the "Done when" of three or more Must PRs fail today, or there are more
  Orphans than In progress items.

## Steps

1. Run `python3 -I scripts/spec-check.py` and keep its output. Take the list of IDs, acceptance rows and tests
   from `spec.json` (its counts are the first line), and check it against the four documents: an ID in the
   Markdown but not in `spec.json`, or the other way round, is a finding.
2. Use the checker's lines: each `ERROR` (an ID cited in a test, commit, design or plan that does not exist or is
   removed, a bad parent) is a finding, usually Orphan or Drift. An FR in "Must FRs with no test" cannot be Done.
   "Tests cite these FRs but spec.json lists no test" means `spec.json` is behind: say so. Then find tagged tests
   (`grep -rnE 'FR-[0-9]+|fr[0-9]+_' src/test/java`); for each FR and its TRs, search the code and tests, open the
   line you cite, and pick a status.
3. Find Orphans: compare `git log --oneline -30`, plan tasks, the feature packages under
   `src/main/java/org/kshrd/webblock/project/` and the test classes with the trace.
4. Run `./gradlew test` once and quote the count:
   `awk -F'tests="' '/<testsuite /{split($2,a,"\""); s+=a[1]} END{print s+0}' build/test-results/test/*.xml`
   (failures: the same with `failures="`). `./gradlew integrationTest` needs Docker and a free slot (the
   `project-gates` skill); if you do not run it, give the latest handoff count and the commit it ran on.
5. Fill `template.md` in order; its placeholders say how to group the trace. Write "Top 5" last: what the
   owner should look at, most urgent first, each in plain words with the IDs and the decision or action needed.

## Writing rules

Plain English, short sentences, no em or en dashes (`grep -nP '[\x{2013}\x{2014}]'` prints nothing), prose
lines at most 120 characters, table cells are short phrases, `wc -w` at most 4000. "In short" has at most 5
sentences and starts with the verdict. Project words go in the Glossary. Current facts only, no dates of changes.
