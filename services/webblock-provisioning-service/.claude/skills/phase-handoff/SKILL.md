---
name: phase-handoff
description: Use when the owner says they stop, pause, sleep or close the session, when the context window is
  running low, when a task or phase ends, or when asked to write or update the handoff.
---

# Write or update the handoff before a pause

The next session (a person or an agent with no memory of this one) must be able to continue from the files
alone. Keep it short and true. Plain English, no em or en dashes, lines at most 120 characters, no secret value.

## 1. Stop starting things

- Let the running agent or command finish, or stop it cleanly. Start nothing new.
- Note any machine slot in use (Kind stack, Testcontainers) and free it (`make proto-stop proto-down`) unless the
  owner said to keep it.

## 2. Update the phase handoff in the repo

File: `ai/handoffs/<phase>.md` (today `ai/handoffs/after-phase-c.md`). Keep its layout:

1. Title and one line: what the handoff covers; read this first.
2. **Where everything is**: branch and head commit (not pushed, not merged), spec, plan, decision record, ledger
   path, the Notion sources of truth (read-only).
3. **Owner rules set in this session (binding)**.
4. **Task status**: table of task, state (complete and reviewed, implemented not reviewed, not started), commits.
5. **Last gate results**: exact counts and which suites have NOT run (for example "Kind not run on this branch").
6. **Next steps, in order**: numbered, each one concrete enough to start without asking.
7. **Open for the owner**: questions with the options and the lead's recommendation.
8. **Rulings in force for this phase**: one line each, as current rules; full text stays in the ledger.

If a new handoff file is created, add it under "Current" in `ai/README.md` and remove the closed one with
`git rm` (git history keeps it).

## 3. Update the spec register

At the end of every task, and before the handoff is committed, bring `.claude/docs/requirements/spec.yaml` up to
date by hand:

- `status`: an ID the spec retired in this task becomes `removed`; a new BR, PR, FR or TR gets its entry (id,
  title, status, parents, priority, phase; an FR also `rows` and `tests`).
- `tests`: each FR lists the test node IDs that carry its ID (name `test_fr12_...` or a docstring naming
  `FR-12`). Add the tests the task wrote; drop the ones it removed or renamed.
- `no_test_yet`: remove each Must FR that has a test now.

Then run `make spec-check`: it must print `0 problems`, or the handoff names each remaining problem. Commit
`spec.yaml` with the handoff (step 6).

## 4. Update the ledger (git-ignored)

`.superpowers/sdd/<plan name>/progress.md`: one line per finished task or ruling ("Ruling: ... cost if
wrong: ..."), the same facts as the handoff, nothing contradicting it.

## 5. Update the owner's context folder (outside the repo)

`../context/` (the owner's context folder next to the repo, if it exists):
`README.md` (current state only, no dates) and `open-items.md` (things the owner
must do, with exact commands where possible, such as a push only the owner runs). Touch `provisioning.md` when the
Provisioning state changed. Never put a secret value there.

## 6. Commit

`git add ai/handoffs/<phase>.md .claude/docs/requirements/spec.yaml` (and `ai/README.md` if changed), by name
only, never `-A` or `.`. Commit message "Handoff: <one line>". Never push; the owner pushes.

## 7. Tell the owner

Three to five lines: where the work stopped, what is next, what only the owner can do.
