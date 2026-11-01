---
description: Draft a pull request title and description from the branch changes (does not push or open the PR)
disable-model-invocation: true
allowed-tools: Bash(git status *), Bash(git diff *), Bash(git log *), Bash(git branch --show-current)
---

Draft a PR from the current branch. Do not push and do not open the PR.

1. Get the branch name and compare with the base (`main` or `develop`) using `git log <base>..HEAD` and `git diff <base>...HEAD`.
2. Check the scope: one service per PR unless it is a contract change. If several services changed, recommend splitting.
3. Write the draft below from facts in the diff and from test output seen in this session. Never claim tests passed if they were not run.

Output format (print for the user to paste):

**Title**: `type(scope): summary` (max 72 characters)

**Description**
- What changed and why
- Contract impact: files in `contracts/`, additive or breaking, consumers notified (`contracts/CHANGELOG.md`)
- Database impact: new Flyway migrations, backward compatible?
- Security impact: auth, data exposure, new dependencies
- How it was tested: commands and real results
- Rollback plan

Reviewers: owners of touched contracts and migrations (see CODEOWNERS). Remind the user to run `/review` and `/security-review` first.

Stop conditions: on a protected branch, no commits ahead of base, uncommitted changes (ask the user to commit first).
