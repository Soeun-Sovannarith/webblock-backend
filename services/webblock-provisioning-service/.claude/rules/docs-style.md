---
paths:
  - "**/*.md"
---

# Writing docs in this repo (owner rule)

- Plain, easy English. Short sentences. Say the fact once; link to the runbook or decision instead of copying it.
- No em dash (U+2014) and no en dash (U+2013). Use a comma, a colon, a full stop or "to" ("00018 to 00021").
- Lines at most 120 characters in any text you write or change.
- Never write a secret value: no password, token, key, kubeconfig content or `.env` value. Name the file or the
  variable instead.
- Current facts only: no dates of changes, no change logs; git history is the record. No "updated on", "Last
  updated", "Revised", "Done <date>" or "Removed on <date>" notes. A retired ID says "Removed: <why>; see FR-n".
- Decision records keep each decision and its reason, stated as current truth, with no dated provenance.
- In a report to the owner, label a call the lead made "Lead decision (owner delegated)"; docs state it as fact.

Before you finish, check the files you changed:

```sh
grep -nP '[\x{2013}\x{2014}]' <files>          # must print nothing
awk 'length > 120 {print FILENAME ":" FNR}' <files>   # must print nothing for new lines
```

Notion pages (only "Web Block architecture" and "Authentication Process Webblock" may be edited; the database
design pages are read-only): write a dollar sign as `\$`, and after a code span write "plus", not "+".
