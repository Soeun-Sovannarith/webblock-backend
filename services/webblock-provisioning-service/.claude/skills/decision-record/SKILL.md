---
name: decision-record
description: Use when an owner ruling, a lead decision or a new open question must be written down, and always
  before code changes a rule that a decision record or CLAUDE.md states.
---

# Record a decision

CLAUDE.md: change the decision record before changing a rule in code. Records live in `.claude/docs/decisions/`; the
index and the status words are in `.claude/docs/decisions/README.md`.

## Which file

- A ruling that belongs to the phase in progress: add it to that phase's record, in the section for its topic.
  The current handoff (listed under "Current" in `ai/README.md`) names that record.
- A new topic: a new record. The next free number is the one after the highest existing record
  (`ls .claude/docs/decisions`). Never fill a lower gap: `0020` lives only on branches `db-rev21` to `db-rev23`,
  and `0024` is reserved for the auth and telemetry record (`ai/handoffs/auth-telemetry-alignment.md` task 1).
  File name `NNNN-short-name.md`.
- A gap with no decision yet: an entry in `.claude/docs/decisions/open-decisions.md` (next free `OD-` number), status
  **open**, **draft** or **blocked-external**, with the owner and what it blocks.

## Shape of a new record

```
# NNNN: <title>

Status: <Proposed | Accepted | Superseded by NNNN>.

## 1. Context        why this needs deciding; links to Notion pages, findings, earlier records
## 2. Constraints    owner rules and earlier records that bind it
## 3. Decisions      numbered items (G1, G2, ...), each one testable
## 4. Consequences   what changes in code, tests, docs, other services; cost if wrong
## 5. What was built and tested   empty until built; commands and counts (Kind rows after the Kind run)
## 6. What stays open             OD- numbers
```

This is the layout of 0022 and 0023; other skills write gate and Kind results into section 5.

Write each decision as current truth with its reason. No dates, no "decided on" or "amended on" notes, no change
log: git history is the record.

- A routine design choice the owner delegated to the lead: add the options considered and the cost if wrong, one
  line each. The lead's report to the owner names it "Lead decision (owner delegated)"; the record does not.
- Product-level or irreversible questions are not lead decisions: write them as open and ask the owner.

## Rules

- Never invent another service's endpoints, scopes, event payloads, hostname ownership or catalog permissions
  (CLAUDE.md section 6). Write the gap as an open decision with that service as the owner.
- The Notion database design pages are read-only. A gap Notion does not cover becomes a labelled decision in the
  repo, never an edit of the Notion page.
- A later record that changes an earlier one says so at the top of the earlier one ("Amended by NNNN: <what>"),
  with no date. Never renumber or rename.
- Update the index table in `.claude/docs/decisions/README.md` (number, title, status) and the status of any record this
  one amends or supersedes.
- Plain English, no em or en dashes, lines at most 120 characters in new text.

## Then

- Close or update the matching items in `open-decisions.md`.
- Link the record from the plan (`.claude/docs/plans/...`) and the handoff `ai/handoffs/<phase>.md`.
- Commit the record, the index and `open-decisions.md` by name, before the code that depends on them.
