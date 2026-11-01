# Git workflow (always on)

The repo has several people working in it at once, and a dirty working tree is normal. Protect other people's work.

## Before you start
- Run `git status` first. If there are changes you did not make, **leave them alone**. Never `git checkout -- .`, `git reset --hard`,
  `git clean -fd` or `git stash` over them (`guard-bash` blocks the first three). If they are in your way, tell the user.
- Work on a branch, not on `main`: `feat/<service>-<what>`, `fix/<service>-<what>`, `chore/<what>`, `docs/<what>`.
  One service per branch when you can; a contract change and its consumers may share a branch (see `cross-service-change.md`).

## Commits
- Only commit when the user asks. Small commits, one idea each.
- Message: `type(scope): short summary` — types `feat`, `fix`, `refactor`, `test`, `docs`, `chore`, `build`; scope = the service
  or `contracts`, `commons`, `claude`. Example: `feat(contracts): add project CheckAccess v1`.
- Body (when needed): why, not what; mention the open question or business rule number it follows.
- Do not mix a folder move/rename with a behaviour change in the same commit (reviewers cannot see the change).
- Never commit: `.env*`, keys, `*.pem`, realm exports, `build/`, `.gradle/`, `.venv/`, `node_modules/`, `*.iml`, IDE folders.
  The `commit-guard` hook checks the staged file list.

## Pushing and pull requests
- Never push to `main`/`master`/`develop`; never force push (`guard-bash` blocks both). Open a PR.
- A PR description answers: what changed · which service(s) · is it a contract change (additive/breaking) · how I tested it ·
  what I did NOT do. Link the business rule or spec section when behaviour changes.
- A PR that changes `contracts/` is reviewed by the owner service's team and by each consumer's team.
- Do not skip hooks (`--no-verify`) or signing. If a hook fails, fix the cause.

## Renames and moves
- Use `git mv` (or move then `git add -A <folder>`) so history follows. Move files in a commit of their own.
- After a move, run that service's build and tests before saying done.

## Migrations
Applied Flyway/Alembic files are never edited (hard rule 2). A branch that adds a migration must not be merged after another
branch added a migration with a LATER version unless the order is checked: Flyway versions here are `V<yyyyMMddHHmm>__name.sql`.

## Nested repository
`services/webblock-aicode-service/` has its own `.git`. Commits in there are separate from this repo. Never `git add` it from the root.
