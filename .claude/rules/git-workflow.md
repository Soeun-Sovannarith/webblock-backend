# Git workflow rules

## Branches
- Work on a branch: `feature/<service>-<short-desc>`, `fix/<service>-<short-desc>` or `chore/<short-desc>`.
- Never commit to `main`, `master` or `develop`.

## Commits
- One concern per commit. Conventional commits: `type(scope): summary`.
  - type: `feat|fix|refactor|test|docs|chore|build|ci`
  - scope: the service (for example `feat(user): add OTP resend`)
  - summary: imperative mood, max 72 characters.
- Commit only after the service tests pass.
- Never use `--no-verify`, force push, `reset --hard` or history rewrites.
- Only commit or push when the user asks. Propose the message first.

## Staging
- Never run `git add .` blindly. Review `git status` and `git diff` first and stage files by name.
- Do not commit generated files, build output, `.env*`, IDE settings or `CLAUDE.local.md`.

## Pull requests
- One service per PR. A PR that touches several services is split unless it is a contract change.
- The description covers: what changed, why, how it was tested, contract/migration impact, rollback plan.

Example: `fix(payment): reject duplicate idempotency key` on branch `fix/payment-duplicate-key`, staged with `git add src/main/.../PaymentService.java src/test/.../PaymentServiceTest.java`.
