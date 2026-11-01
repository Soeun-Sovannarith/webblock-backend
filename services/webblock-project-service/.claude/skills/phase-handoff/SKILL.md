---
name: phase-handoff
description: Use when the owner says they stop, pause, sleep or close the session, when the context window is
  running low, when a phase or task ends, or when asked to write or update the handoff.
---

# Write or update the handoff before a pause

The next session (a person or an agent with no memory of this one) must be able to continue from the files
alone. There is one handoff file: `ai/handoffs/next.md`. Keep it short and true. Plain English, no em or en
dashes, lines at most 120 characters, no secret value (`.claude/rules/docs-style.md`).

## 1. Stop starting things

- Let the running agent or command finish, or stop it cleanly. Start nothing new.
- Free the machine slot you hold: a running Testcontainers suite ends with Gradle (check `docker ps` for left-over
  containers named `testcontainers-*` and tell the owner instead of removing other people's containers). A Kind
  stack belongs to Provisioning's `make proto-*` targets; say whether it is still up, never tear it down unasked.

## 2. Update `ai/handoffs/next.md`

Rewrite it as current state, not as a diary: no dates, no "Done (date)", no history of how you got here (git
history is the record). Keep its headings, adding the in-progress ones only while work is open:

1. **Title and first paragraph**: what is on `main`, the phase in progress or next, and where the requirements and
   design for it are (`.claude/docs/requirements/`, `.claude/docs/designs/`, `.claude/docs/plans/`).
2. **In progress** (only while a branch is open): branch and head commit (`git rev-parse --short HEAD`), not
   pushed, not merged; the plan file; a table of task, state (complete and reviewed, implemented not reviewed,
   not started), FR IDs, commits.
3. **Last gate results** (only while a branch is open): each command of the `project-gates` skill with its exact
   counts, and which suites have NOT run on this branch (for example "`integrationTest` not run", "Kind exit not
   run").
4. **For <next phase>** or **Next steps, in order**: numbered, each one concrete enough to start without asking,
   with the FR or TR IDs when they exist.
5. **Known limits (open)**: accepted gaps that still matter, one line each.
6. **Waiting on others**: per team or the owner, what they must do.
7. **Open for the owner**: questions with the options and your recommendation.

Remove items that are finished: a finished item lives in the code, the decision records and git history. Rules
set by the owner in this session go into a decision record (`decision-record` skill) or `.claude/CLAUDE.md`,
not into the handoff; the handoff links them.

## 3. Update `spec.json` at the end of a task

`.claude/docs/requirements/spec.json` lists, per FR, the tests that carry its ID. When a task ends, bring those
lists level with the code:

```sh
grep -rnE '@DisplayName\("(FR|TR)-[0-9]+|void fr[0-9]+_' src/test/java
python3 -I scripts/spec-check.py
```

- Add each new test to its FR's `tests` list: the `@DisplayName` text (`"FR-12.3: owner renames the project"`) or
  the method name (`fr12_3_ownerRenamesTheProject`). Remove a test that was deleted or renamed.
- Change only the `tests` lists here. A new, changed or removed ID is the spec-writer's (BR, PR, FR) or the
  designer's (TR) after an owner decision.
- The checker must print `Errors: 0` and no "Tests cite these FRs but spec.json lists no test" line. Put its
  "Errors" and "Must FRs with no test" lines in the handoff's "Last gate results".
- Commit `spec.json` by name with the handoff.

## 4. Update the ledger (git-ignored)

If a plan is in progress, `.superpowers/sdd/<plan name>/progress.md`: one line per finished task or ruling
("Ruling: ... cost if wrong: ..."), the same facts as the handoff, nothing contradicting it.

## 5. Update the owner's context folder (outside the repo)

`../context/` (next to the repo; read at the start of every session): keep `README.md` (current state only) and
`open-items.md` (things the owner must do, with exact commands where possible, such as a merge or push only the
owner runs) true for the Project service. Never put a secret value there.

## 6. Check and commit

```sh
grep -nP '[\x{2013}\x{2014}]' ai/handoffs/next.md
awk 'length > 120 && !/^\|/ {print FILENAME ":" FNR}' ai/handoffs/next.md
git add ai/handoffs/next.md .claude/docs/requirements/spec.json
git commit -m "Handoff: <one line>"
```

Both checks print nothing. Stage by name only, never `-A`, `.` or `commit -a`; end the message with the
Co-Authored-By line the harness gives. If git reports `index.lock`, wait a few seconds and retry. Never push; the
owner pushes.

## 7. Tell the owner

Three to five lines: where the work stopped, what is next, what only the owner can do.
