# Agent handoffs and reviews

Files written by the agents that build this service. The service docs start at
[.claude/docs/README.md](../.claude/docs/README.md).

## Current

- [handoffs/after-phase-c.md](handoffs/after-phase-c.md): START HERE. Phase C and cqrs-layout are merged into
  `python`; the work owed, the gates still owed and the owner's steps.
- [handoffs/auth-telemetry-alignment.md](handoffs/auth-telemetry-alignment.md): auth and telemetry work to do next.

## Reviews still in use

- [reviews/pk-validation.md](reviews/pk-validation.md): memory limits of the chart (cited by the chart and its test).
- [reviews/wave6-validation.md](reviews/wave6-validation.md): wave 6 findings (cited by
  `scripts/prototype/reset-sites.sh`).
- [reviews/ph-validation.md](reviews/ph-validation.md): Phase P-H validation (cited by decision `0016`).
- [reviews/devops-overseer-review.md](reviews/devops-overseer-review.md): review behind the open proposal `0015`.
- [reviews/qa-cqrs-layout.md](reviews/qa-cqrs-layout.md): the QA sweep of branch `cqrs-layout` (constitution V.3).
- [reviews/qa-cqrs-layout-final.md](reviews/qa-cqrs-layout-final.md): the final QA sweep of `cqrs-layout`, before
  the final review.
- [reviews/qa-duplicates-cqrs-layout.md](reviews/qa-duplicates-cqrs-layout.md): the QA cleanup of duplicates and
  PyCharm warnings that answers both sweeps.
- [reviews/db-notion-audit-cqrs-layout.md](reviews/db-notion-audit-cqrs-layout.md): the database audit against
  Notion "5 · Provisioning" (constitution III.3).
- [reviews/cqrs-layout-final-review.md](reviews/cqrs-layout-final-review.md): the final review of `cqrs-layout`,
  its fix wave and the owner rulings owed before the merge.
- [reviews/claude-config-evaluation.md](reviews/claude-config-evaluation.md): the grader's evaluation of the
  `.claude/` configuration and its ranked fixes.

## Closed reviews

Phase C task 12 (sub-domain hostnames, migration `00023`, admission policies). The last security re-review
approved it and Phase C is merged; they stay as the record behind decision `0023` G18 to G20.

- [reviews/phase-c-task-12-review.md](reviews/phase-c-task-12-review.md): the task review.
- [reviews/phase-c-task-12-security-review.md](reviews/phase-c-task-12-security-review.md): the security review.
- [reviews/phase-c-task-12-security-rereview.md](reviews/phase-c-task-12-security-rereview.md): re-review of fix
  round 2.
- [reviews/phase-c-task-12-security-rereview-2.md](reviews/phase-c-task-12-security-rereview-2.md): re-review of fix
  round 3.

Removed documents stay in git tag `docs-before-cleanup` (`git checkout docs-before-cleanup -- <path>`).
