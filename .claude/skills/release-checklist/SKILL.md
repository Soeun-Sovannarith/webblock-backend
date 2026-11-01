---
name: release-checklist
description: Use before tagging or releasing a service version. Verifies contracts, migrations, tests, security and rollback readiness, and produces a go/no-go report.
---

# Release checklist (per service)

## Purpose
Decide GO or NO-GO for one service version using evidence (command output, file:line), not assumption.

## When to use / when NOT to use
- Use before tagging or deploying a version of one service.
- Do NOT use to deploy, tag or push (the user does that), or for several services at once; run it once per service.

## Inputs to confirm with the user
Service folder, version or tag, last released tag, target environment, whether contract or migration changes are included, the service test command from its `CLAUDE.md`.

## Procedure
Mark each item Pass / Fail / N/A with evidence.
1. **Branch and diff.** `git branch --show-current`, `git log <last-tag>..HEAD --oneline`; only changes for this release, none unrelated.
2. **Tests.** Full suite green (Spring: `./gradlew check`; Python: `pytest -q` and `ruff check .`). Paste the summary line.
3. **Contracts.** Code matches `contracts/` (run agent `contract-checker`); breaking changes use a new version; consumers notified in `contracts/CHANGELOG.md`.
4. **Database.** Only new `V<n>__*.sql`; none edited (`git diff <last-tag> -- '*/db/migration/*'`); backward compatible so the previous app version still works (expand/contract); migration applied on a clean database in tests.
5. **Security.** `/security-review` has no BLOCKER or MAJOR; no secrets in the diff; dependency changes reviewed and justified.
6. **Config.** Every new property exists in config-server for each environment, with no secret in the repo.
7. **Observability.** Health endpoint responds; errors logged with correlation id; no PII or tokens in logs.
8. **Rollback.** Previous version runs on the new schema; rollback steps written; events and contracts keep the old version until consumers migrate.
9. **Docs.** `docs/service-map.md`, event catalog and CHANGELOG updated; ADR if a decision changed.

## Templates
```
Release: <service> <version>   Date: <YYYY-MM-DD>
| # | Item | Status | Evidence |
|---|------|--------|----------|
| 2 | Tests | Pass | BUILD SUCCESSFUL, 142 tests |
Blockers: <list or none>
Rollback: <steps>
Verdict: GO | NO-GO
```

## Checklist
- [ ] All 9 items have a status and evidence
- [ ] Any Fail is listed under Blockers
- [ ] Rollback steps are concrete
- [ ] Verdict is stated in one line

## Common mistakes
Marking Pass from memory; skipping the migration diff; releasing a breaking contract without a v2; forgetting config-server properties; no rollback plan.

## Definition of done
Report delivered with evidence per item and a clear GO or NO-GO. No tag, push or deploy performed.

## Related
Commands `/test`, `/security-review`, `/pr`; agents `contract-checker`, `security-auditor`; rule `git-workflow.md`.
