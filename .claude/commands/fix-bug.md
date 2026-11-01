---
description: Investigate and fix a bug with the evidence-first protocol
argument-hint: [error message, issue number, or description]
---

Investigate this bug using the `bug-investigation` skill: $ARGUMENTS

1. Collect evidence: error, stack trace, logs, correlation id, input, versions, recent changes. Ask the user for anything missing.
2. Reproduce with the smallest trigger (a failing test is preferred). Show the failing output.
3. State one hypothesis with `file:line` and what would disprove it.
4. Rule out other suspects one at a time and record each result.
5. Only then propose the smallest fix; wait for approval unless it is a one-line change.
6. Add a regression test that fails before and passes after. Run the service test command from `CLAUDE.md` and report the real result.

Output format: Symptom, Evidence, Root cause (file:line), Ruled out, Fix, Verification, Follow-ups (as defined in the skill).

Stop conditions: no reproduction and no way to get one (list what is needed); the root cause is in another service (write a note for its owner, do not edit it); the fix needs a protected file or an edit to a committed migration.
