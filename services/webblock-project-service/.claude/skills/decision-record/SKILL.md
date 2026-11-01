---
name: decision-record
description: Use when an owner ruling, a lead decision or a gap Notion does not cover must be written down, and
  always before code changes a rule that a decision record or .claude/CLAUDE.md states.
---

# Record a decision

The decision record changes before the rule in code (`.claude/CLAUDE.md` section 6). Records live in
`.claude/docs/decisions/`; the index and the status words are in `.claude/docs/decisions/README.md`.

## What belongs in a record

- A binding choice the owner approved: a format, a flow, a boundary, an operational rule.
- A gap the Notion database pages do not cover (for example how a backfill treats old rows). The Notion
  database pages are read-only: the gap becomes a record here, never an edit of the page.
- Not in a record: tables, columns, indexes and grants (Notion platform-db-v6 decides them); another service's
  endpoints, payloads, events or audiences (ask the owner; that service owns them).

A routine design choice the owner delegated to the lead: write it as a normal record with the options considered
and the cost if wrong, one line each. Product-level or irreversible questions are not lead decisions: write the
record as Proposed and ask the owner.

## Which file

- The topic already has a record (`ls .claude/docs/decisions`): a small change of the same rule is a new record
  that supersedes it; never edit an Accepted record's decision in place.
- A new topic: the next free number (`ls .claude/docs/decisions | sort | tail -2`), file name
  `NNNN-short-name.md` with lower case words and dashes. Never reuse or renumber a number.

## Shape (the same as `0007-project-access-rules.md`)

```
# NNNN Short title

## Decision

- <one bullet per rule, each one testable, stated as current truth>

## Reason

<why, in a few plain sentences; the options considered and the cost if wrong when the choice was delegated>

## Status

<Proposed | Accepted | Superseded by NNNN>. <Built and tested, or what is still to build.>
```

No dates, no "decided on" or "amended on" notes, no "(owner, <date>)", no change log: git history is the record.
Plain English, no em or en dashes, lines at most 120 characters (`.claude/rules/docs-style.md`).

## Steps

1. Write the record as Proposed, unless the owner already approved it in this session (then Accepted, and say in
   your report where the owner said so).
2. Superseding an older record: set the older one's status to `Superseded by NNNN` and nothing else in it.
3. Add the row to the index table in `.claude/docs/decisions/README.md` (number with link, short decision, status)
   and update the status of any record it supersedes.
4. If the decision changes a requirement, tell the spec-writer: the BRD, PRD, FRD or TRD cites the record by
   number in its Decision or Source column.
5. Link the record from the plan (`.claude/docs/plans/<topic>.md`) and the handoff (`ai/handoffs/next.md`).
6. Check the files you changed:
   `grep -nP '[\x{2013}\x{2014}]' <files>` and `awk 'length > 120 && !/^\|/ {print FILENAME ":" FNR}' <files>`
   print nothing.
7. Commit the record and the index by name (`git add .claude/docs/decisions/NNNN-short-name.md
   .claude/docs/decisions/README.md`), before the code that depends on them. Never push.
