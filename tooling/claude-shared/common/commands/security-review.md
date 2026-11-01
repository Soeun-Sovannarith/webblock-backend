---
description: Security review of the current changes (secrets, authz, injection, data exposure)
allowed-tools: Bash(git status *), Bash(git diff *), Bash(git log *)
---

Use the `security-auditor` subagent on the current changes. Do not edit files and never print secret values.

1. Run `git status`, `git diff` and `git diff --staged`. If there are no changes, say so and stop.
2. Delegate to `security-auditor` with the changed files and the service name.
3. Report findings ordered BLOCKER, MAJOR, MINOR, NIT with `file:line`, the realistic attack or failure scenario, and the fix.
4. State which categories were checked and found clean (secrets, authn/authz and ownership, injection, validation, data exposure, messaging, dependencies, transport/config).

Output format:
- **Verdict**: no blocking issue / fix before merge
- **Findings** by severity
- **Checked and clean**
- **Could not verify** (what is missing)

Stop conditions: a live secret is found (report file:line only, tell the user to rotate it, and stop); no diff.
