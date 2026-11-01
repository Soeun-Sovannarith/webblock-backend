---
description: Check README.md, .env.example and .claude/ instructions against the code and fix drift
argument-hint: "[--report-only]"
---

Use the `docs-syncer` subagent to compare the docs with the code: routes, models, events, settings, the pipeline graph, SQL enums, and the names referenced in `.claude/`.

- If `$ARGUMENTS` contains `--report-only`, ask it only to report.
- Otherwise let it fix the docs (never the code), then show `git diff --stat` and summarise what changed.

List any conflict where the code looks wrong separately, for the user to decide.
