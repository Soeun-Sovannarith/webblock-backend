# Provisioning agents

The subagents of this repo. This file has no frontmatter, so Claude Code reads it as documentation, not as an
agent. The lead (main session) picks an agent, writes its brief, keeps the ledger and makes the rulings; the
owner approves at each gate. How the agents work together, stage by stage: `.claude/WORKFLOW.md`.

| Agent | Stage | Job | May edit | Model |
| --- | --- | --- | --- | --- |
| spec-writer | 1, 5 | BRD, PRD, FRD; DIRECTION.md before a merge | `.claude/docs/requirements/` (not TRD) | opus |
| designer | 2 | TRD and the change design | `TRD.md`, `.claude/docs/designs/` | opus |
| planner | 3 | Design to tasks, each with FR and TR IDs | `.claude/docs/plans/` | opus |
| implementer | 4 | One task, tests first from FR criteria | `src/`, `tests/`, `deploy/` (brief) | opus |
| provisioning-validator | 5 | Diff against the cited FR and TR rows | its report in `ai/reviews/` | opus |
| security-reviewer | 5 | Auth, tokens, RBAC, policies, secrets, dependencies | its report in `ai/reviews/` | opus |
| db-notion-auditor | 5 | Database against Notion (III.3, merge gate) | its report | opus |
| kind-runner | 5 | Full local Kind run in an owner-given slot | result lines the brief names | sonnet |
| qa-reviewer | 5 | Dead and duplicate code, complexity, IDE checks | its report in `ai/reviews/` | sonnet |
| final-reviewer | 5 | Whole branch, coverage per ID, deferred minors | its report in `ai/reviews/` | opus |
| docs-keeper | 4, 5 | Runbooks, guides, records, handoffs | `.claude/docs/` (not specs), `ai/` | sonnet |
| researcher | 1, 2 | Read-only facts: repo, sibling repos, web, Notion | one report file | sonnet |

The implementer covers both implementation roles (Python, and Kubernetes manifests).

## Rules for every agent

- Every report names its stage and the IDs it covers, and cites a broken constitution rule by number (`III.3`).
- One implementer at a time in the shared tree. Reviewers may run alongside it.
- Never push, never merge, never switch branches in the shared tree, never `git add -A` or `git add .`.
- No cluster except the project's local Kind cluster, and only kind-runner runs it. Never read `~/.kube`.
- Notion database design pages are read-only for every agent. Only the lead edits the two allowed pages.
- Never write a secret value. Plain English docs, no em or en dashes, lines at most 120 characters.
- Agents do not start other agents.
