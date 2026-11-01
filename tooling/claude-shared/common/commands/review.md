---
description: Review the current uncommitted changes for bugs, conventions and missing tests
allowed-tools: Bash(git status *), Bash(git diff *), Bash(git log *)
---

Use the `code-reviewer` subagent to review the current changes. Do not edit files.

1. Run `git status`, `git diff` and `git diff --staged`. If there are no changes, say so and stop.
2. Delegate to `code-reviewer` with the list of changed files and the service name.
3. Return its findings ordered BLOCKER, MAJOR, MINOR, NIT. Each: `file:line`, evidence, concrete fix.
4. If the diff touches auth, payments, input handling, config or dependencies, recommend `/security-review` too. If it touches `contracts/` or an API or event, recommend the `contract-checker` agent (root session).

Output format:
- **Verdict**: approve / approve with comments / request changes
- **Findings** by severity
- **Tests**: what is covered, what is missing
- **Suggested next step**

Stop conditions: no diff; diff includes a secret (report location only, never the value, and stop).
