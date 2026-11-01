---
description: Start the 5-stage spec workflow (audit, requirements, rules, spec and tests, build, verify) for one feature
argument-hint: <feature description>
disable-model-invocation: true
---

Use the `spec-workflow` skill for this feature: $ARGUMENTS

1. If no feature description was given, ask for it and stop.
2. Run Step 0 (read-only audit) and report the stack, test command, conventions, existing docs and gaps.
3. Continue stage by stage, stating "Stage 0X: <name>" at the start of each. Stop for approval after stages 1, 2, 3 and 5.
4. Stay in the current service folder. If the feature needs another service, write a note for its owner instead.

Stop conditions: no feature given; no test command can run (propose the smallest setup and wait); a request conflicts with an approved doc (ask which wins).
