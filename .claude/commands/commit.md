---
description: Propose a conventional commit message for the staged/working changes (you confirm before committing)
disable-model-invocation: true
allowed-tools: Bash(git status *), Bash(git diff *), Bash(git log *), Bash(git branch --show-current)
---

Prepare a commit following `.claude/rules/git-workflow.md`. Nothing is committed until the user confirms.

1. Run `git branch --show-current`. On `main`, `master` or `develop`, stop and ask for a feature branch (`feature/<service>-<short-desc>`, `fix/<service>-<short-desc>` or `chore/<short-desc>`).
2. Run `git status`, `git diff` and `git diff --staged`. Flag secrets, build output, IDE files, `CLAUDE.local.md` or unrelated changes and exclude them.
3. Check that tests passed in this session. If not, ask whether to run `/test` first.
4. If the changes mix concerns or services, propose splitting into several commits.
5. Propose the message: `type(scope): summary` with type in `feat|fix|refactor|test|docs|chore|build|ci`, scope = service (for example `user`), imperative mood, max 72 characters. Add a short body explaining why when it is not obvious.
6. List the exact files to stage by name. Never stage everything blindly.
7. Wait for explicit confirmation, then run `git add <files>` and `git commit`. No `--no-verify`, no amending pushed commits, never push.

Output format: branch, files to stage, proposed message, test status.

Stop conditions: protected branch, a possible secret in the diff, failing tests, no changes.
