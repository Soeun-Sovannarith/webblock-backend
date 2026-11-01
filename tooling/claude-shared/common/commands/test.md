---
description: Run the tests and summarize the result. No code changes.
argument-hint: [optional class, module or test name]
---

Run this service tests using the command in `CLAUDE.md` (Spring: `./gradlew test` or `./gradlew check`; Python: `pytest -q`, plus `ruff check .` when listed). If an argument is given, run only that: $ARGUMENTS

1. Read the test command from `CLAUDE.md`; do not invent one.
2. Run it from the service folder. Do not start servers or containers unless the test needs them and the user approves.
3. Report the real outcome. Do not edit any file and do not guess at causes.

Output format:
- **Command run**
- **Totals**: passed / failed / skipped, duration
- **Failures**: for each, test name, assertion or exception message, `file:line`
- **Next step**: if failures exist, offer `/fix-bug <test name>`

Stop conditions: command missing or build does not start (report the first error and stop); tests need secrets or external systems (say which, do not work around).
