# Project agents

The subagents of this repo. This file has no frontmatter, so Claude Code reads it as documentation, not as an
agent. The lead (main session) picks an agent, writes its brief, keeps the ledger and makes the rulings; the owner
says yes to the change note and merges (two gates). How the agents work together, stage by stage: `.claude/WORKFLOW.md`.

| Agent | Stage | Job | May edit | Skills | Model |
| --- | --- | --- | --- | --- | --- |
| spec-writer | spec edit, per phase | BRD, PRD, FRD; DIRECTION.md before a merge | `.claude/docs/requirements/` (not TRD) | `writing-brd`, `writing-prd`, `writing-frd`, `checking-requirements-direction` | opus |
| designer (optional) | design file | TRD and the change design | `TRD.md`, `.claude/docs/designs/`, decision records | `writing-trd`, `decision-record` | opus |
| planner (optional) | design file | Design to tasks, each with FR and TR IDs | `.claude/docs/plans/` | none (`superpowers:writing-plans` if installed) | opus |
| implementer | implement | One task, tests first from FR criteria | `src/`, build files the brief names | `project-gates`; loads `spring-endpoint`, `new-migration` | opus |
| project-validator | review | The one review per change: diff against the cited FR and TR rows, layers, gates | its report in `ai/reviews/` | `project-gates` | opus |
| security-reviewer | review | Tokens, ownership, internal calls, profiles, secrets, dependencies | its report in `ai/reviews/` | `project-gates`, `differential-review`, `sharp-edges` | opus |
| db-notion-auditor | review | Databases against Notion (Art. III, merge gate) | its report in `ai/reviews/` | `db-notion-audit` | opus |
| kind-runner | before merge | Cross-service Kind check in an owner-given slot | result lines the brief names | none (follows the J1 exit runbook) | sonnet |
| qa-reviewer | per phase | Dead and duplicate code, complexity, deprecations, Error Prone warnings | its report in `ai/reviews/` | `qa-sweep` | sonnet |
| final-reviewer | per phase | Once per phase or before a large merge: whole branch, coverage per ID, deferred minors | its report in `ai/reviews/` | `project-gates` | opus |
| docs-keeper | implement | Runbooks, contracts, records, handoffs | `.claude/docs/` (not the constitution, specs, designs, plans; only the `tests` lists of `spec.json`), `ai/handoffs/` | `decision-record`, `phase-handoff` | sonnet |
| researcher | any | Read-only facts: repo, sibling repos, web, Notion | one report file | none | sonnet |

The five reviewers (project-validator, security-reviewer, db-notion-auditor, qa-reviewer, final-reviewer) carry a
`PreToolUse` hook in their frontmatter, `.claude/hooks/reviewer_writes.py`: they may write only under `ai/reviews/`
and may not change git state.

## Rules for every agent

- Every report names its stage and the IDs it covers, and cites a broken article of the constitution
  (`.claude/docs/CONSTITUTION.md`) as `Art. N`.
- One implementer at a time in the shared tree. Reviewers may run alongside it; nobody reviews their own work.
- Never push, never merge, never switch branches in the shared tree, never `git add -A`, `git add .` or
  `git commit -a`.
- Reads of the real cluster are allowed; every change to it is a command written for the owner (`.claude/CLAUDE.md`
  section 0). Only kind-runner starts Provisioning's local Kind stack, in an owner-given slot.
- Notion database design pages are read-only for every agent. Only the lead edits "Web Block architecture", and
  "2 · Project" and "6 · Shared catalog" on the owner's word.
- Never write a secret value. Plain English docs, no em or en dashes, lines at most 120 characters.
- Agents do not start other agents.

The planner and the designer run only when a design file is needed (a new service, a new or changed contract between
services, or a schema change). A change gets one review and one fix round (Art. I).
