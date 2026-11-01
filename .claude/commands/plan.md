---
description: Read-only plan for a task. No files are edited until you approve.
argument-hint: [task description]
allowed-tools: Read, Grep, Glob, Bash(git status *), Bash(git diff *), Bash(git log *), Bash(git branch --show-current)
---

Plan this task without editing any file: $ARGUMENTS

1. Restate the goal and acceptance criteria in 2-3 lines. If something is ambiguous, ask ONE specific question and stop.
2. Read only what you need: this service CLAUDE.md, the closest existing code, and the relevant contract in `../../contracts/` (repo root: `contracts/`).
3. Confirm the task fits this service only. If another service must change, say so and plan only a handoff note for its owner (`.claude/rules/cross-service-changes.md`).
4. Contracts: does an API or event change? If yes, name the `contracts/` file and whether the change is additive or breaking. Contract goes first (skill `contract-change`).
5. Database: is a new Flyway migration `V<n>__*.sql` needed? Is it backward compatible? (Never edit a committed one.)
6. Security: auth, role and ownership checks, input validation, PII, new dependencies.
7. Tests: list the tests to add and the test command from `CLAUDE.md`.
8. Branch and commit: suggest a branch name (`feature/<service>-<short-desc>`) and the conventional commit scope.

Output format:
- **Goal / acceptance criteria**
- **Files to change** (path, create or edit, why)
- **Contract and migration impact**
- **Risks and out of scope**
- **Tests**
- **Open question** (only if any)

End with: "Reply `go` to implement, or tell me what to change." Then stop.
Stop conditions: ambiguity (ask), a needed change in another service (hand off), a request that touches secrets or guardrail files (refuse and explain).
