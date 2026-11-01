---
description: Review the current branch (or a given git range) with the contract-reviewer and security-reviewer subagents in parallel
argument-hint: "[git range, default main...HEAD + uncommitted]"
---

Review the changes in `$ARGUMENTS` (default: `git diff main...HEAD` plus uncommitted changes).

1. Run `git diff --stat` for the range and decide which reviewers apply:
   - `contract-reviewer`: always.
   - `security-reviewer`: only if the diff touches `glm.py`, `build_runner.py`, `scaffold.py`, `store.py`, `app/schemas/`, `drizzle_generator.py`, `business_layout.py` or `app/api/routes/`.
2. Launch the applicable subagents **in parallel in one message**, giving each the exact range.
3. Merge their findings, deduplicate, and rank by severity. Before reporting each finding, check its cited `file:line` yourself, and drop anything you can't confirm.
4. Output the confirmed findings (`file:line`, problem, failure scenario, suggested fix), then "checked, no issues" areas, then "not checked". Don't edit code.
