---
paths:
  - "**/*.md"
---

# Writing docs in this repo (owner rule)

- Plain, easy English. Short sentences. Say the fact once; link to the runbook or decision instead of copying it.
- No em dash (U+2014) and no en dash (U+2013). Use a comma, a colon, a full stop or "to" ("V1 to V5").
- Lines at most 120 characters in any text you write or change (table rows may be longer).
- Never write a secret value: no password, token, key, kubeconfig content, dev token or `.env` value. Name the file
  or the variable instead.
- Current facts only: git history is the record. No dates of changes, no change logs, no history summaries, no
  "Last updated", "Revised", "Done <date>" or "(owner, <date>)" notes, no "Lead decision (owner delegated)" tags.
  A retired ID says "Removed: <why>; see FR-n".
- Decision records keep each decision and its reason, stated as current truth.
- Each team doc page is one topic. A runbook says what it is for, what you need, the steps, and how to check.

Before you finish, check the files you changed:

```sh
grep -nP '[\x{2013}\x{2014}]' <files>                  # must print nothing
awk 'length > 120 && !/^\|/ {print FILENAME ":" FNR}' <files>   # must print nothing
```

Notion: the database design pages (platform-db-v6 and its children) are read-only. Only the lead edits "Web Block
architecture", and "2 · Project" and "6 · Shared catalog" on the owner's word. There, write a dollar sign as `\$`.
