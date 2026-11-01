# Claude Code configuration: independent evaluation

Branch `cqrs-layout` at `c453a0a`, judged at HEAD (the working tree was clean when read). Grader: an independent
read-only agent. Rubric: "does your CLAUDE.md reflect your real project, are your skills and hooks functional, and
is your `.claude/` folder organized?" plus the owner rules (current facts only, plain English, no em or en dashes,
lines at most 120, spec-driven with a constitution, `spec.yaml`, clean organisation).

## Overall: 8.3 / 10

| Area | Score | One line |
| --- | --- | --- |
| 1. CLAUDE.md | 8.5 | Real, specific, every command and path exists; three rules disagree with other files |
| 2. Hooks | 9.0 | 263/263 and 192/192 pass, fail closed, valid matchers; two dated comments, two policy gaps |
| 3. Skills | 8.5 | 23 skills, valid frontmatter, paths resolve, vendored provenance; Phase C pointers are stale |
| 4. Agents | 9.5 | 12 agents, valid tools and skills, all 5 reviewers carry the write limit twice |
| 5. Rules, workflow, stack, constitution, spec | 7.5 | Stack exact, Mermaid valid, spec-check clean; only 5 of 51 FRs have a tagged test |
| 6. Boilerplate and staleness | 6.5 | Core files clean; decisions, runbooks and root README carry dashes, tags, stale facts |

## 1. CLAUDE.md (8.5)

Evidence of a real, current file:

- `.claude/CLAUDE.md` is 164 lines about this service only: the real cluster rule (section 0), commands, the
  folder layout, a real code sample, gates, git rules, boundaries.
- All 24 make targets named in section 1 exist (`Makefile`, `mk/*.mk`): py-tools, kind-tools, py-fmt-check,
  py-lint, py-typecheck, py-layers, spec-check, py-test, contracts-check, chart-check, py-test-integration,
  py-test-messaging, py-vuln, py-age, image-scan, py-cover, proto-up, proto-test-kind, proto-run, proto-e2e,
  proto-stop, proto-down, migrate-status, migrate-up.
- Every path in section 2 exists, including `core/application/shared/cqrs.py`, `hosts/devtools`,
  `src/contracts/shared`, `tests/unit/core/domain`. The sample at lines 93 to 107 matches
  `src/provisioning/core/application/operations/commands/suspend_website.py:19-40`.
- The import works: `.claude/CLAUDE.md:9` `@docs/CONSTITUTION.md` resolves to `.claude/docs/CONSTITUTION.md`.
- No em or en dash, no line over 120, no dated note.

Deductions:

- `.claude/CLAUDE.md:35-36` says never run `kubectl` against the real cluster, but `.claude/hooks/guard.py:36-41`
  sets `DOKS_READ = "ask"` "until the owner picks one", `.claude/README.md:25` says reads "ask you first", and
  `.claude/docs/CONSTITUTION.md:84` calls the real cluster "read-only for agents". Verified: `kubectl get pods -A`
  returns `ask`.
- `.claude/CLAUDE.md:128` says never `git add -A`; the guard allows it when no untracked file exists (verified:
  no output on a clean tree), and `settings.json:17` auto-allows `Bash(git add *)`.
- `.claude/CLAUDE.md:149` puts a Kind slot under "Ask first", but `settings.json:9` auto-allows `Bash(make proto-*)`
  and the guard returns nothing for `make proto-up` (verified).
- `.claude/CLAUDE.md:20-21` says the four Notion pages are read-only; `CONSTITUTION.md:89-90` (VI.4) and
  `WORKFLOW.md:135` let the lead edit "Web Block architecture", which is one of the four.
- `.claude/CLAUDE.md:19` lists the sources of truth without the constitution, which `CONSTITUTION.md:6` says wins.
- `.claude/CLAUDE.md:45` lists the `mk/` files without `mk/qa.mk`; `make qa-sweep` is not in section 1.
- `.claude/CLAUDE.md:113` "each test names its FR ID" is not true of the code yet (see area 5).

## 2. Hooks (9.0)

Self-tests at HEAD: `python3 -I .claude/hooks/test_guard.py` prints "263 of 263 passed", exit 0;
`python3 -I .claude/hooks/test_hooks.py` prints "192 of 192 passed", exit 0. The counts match
`.claude/README.md:30` and `:37`.

Piped PreToolUse inputs (cwd and `CLAUDE_PROJECT_DIR` the repo):

| Hook | Input | Result |
| --- | --- | --- |
| guard.py | Bash `git status`, `make py-test`, Read `README.md`, Edit `src/...` | nothing (normal flow) |
| guard.py | Bash `git push origin cqrs-layout` | deny: "git push is the owner's job" |
| guard.py | Bash `git checkout python && git merge cqrs-layout` | deny: merge into python needs the owner |
| guard.py | Bash `git reset --hard HEAD~1`; `rm -rf src` | deny; deny |
| guard.py | Bash `cat .env`; Read `.env`; Read `~/.kube/config` | deny; deny; deny |
| guard.py | Bash `kubectl get pods -A` (no Kind kubeconfig) | ask |
| guard.py | Bash `kubectl delete ns web` | deny |
| guard.py | Edit, Write `.claude/docs/CONSTITUTION.md`, `.claude/hooks/guard.py`, `.claude/settings.json` | ask |
| guard.py | Bash `sed -i ... .claude/hooks/guard.py` | ask |
| guard.py | Bash `make migrate-up PG_DATABASE=x`; `git commit --amend` | ask; ask |
| guard.py | Bash `git add -A` (clean tree); `make proto-up` | nothing; nothing (see fixes 6) |
| guard.py | stdin `not json` | deny (fail closed, "Guard error ... blocked") |
| reviewer_writes.py `--if-reviewer-agent` | qa-reviewer Write `ai/reviews/x.md` | nothing (allowed) |
| same | qa-reviewer Edit `src/...` | deny: outside `ai/reviews/` |
| same | final-reviewer `git commit`; security-reviewer `echo x > src/a.py`, `sed -i` | deny; deny; deny |
| same | security-reviewer `make py-test` | nothing (allowed) |
| same | implementer Edit `src/...`; main session (no agent_type) | nothing; nothing |
| reviewer_writes.py (no flag, frontmatter form) | Edit `src/...` | deny |
| reviewer_writes.py | stdin `{` | deny (fail closed) |
| session_start.py | SessionStart startup | branch, tree state, "FR 51 current, 46 with no test", read-first list |
| post_edit_check.py | Write `.md` with an em dash and a 130 character line | warns on both, exit 0 |
| post_edit_check.py | Edit Dockerfile `FROM python:3.14-slim`; Edit `pyproject.toml` | digest warning; 14-day warning |

Registration (`.claude/settings.json:98-146`): PreToolUse guard on
`^(Bash|Monitor|Read|Edit|Write|MultiEdit|NotebookEdit|Grep|Glob|mcp__.*)$`, reviewer limit on
`^(Bash|Monitor|Write|Edit|MultiEdit|NotebookEdit)$`, both `onFailure: "block"` (a real field: the installed
Claude Code binary handles it), timeouts 15 above the 10 second budget; SessionStart and PostToolUse registered.
The slow-git case in `test_guard.py:410-414` proves a hung `git` is denied on the guard's own budget.

Deductions: dated comments `guard.py:35` "(owner rules, 27 October 2026)" and `test_guard.py:274` "Review of
27 October 2026"; the unresolved `DOKS_READ` choice (`guard.py:36-41`); `git add -A` and `make proto-*` gaps.

## 3. Skills (8.5)

- 23 skills; every `SKILL.md` has `name` and `description`. All 13 local skills start their description with
  "Use when" and are written for this repo (`fastapi-endpoint/SKILL.md:9-12` even says why generic FastAPI
  advice is wrong here).
- Every path in the local skills resolves (checked by script), for example every file in the
  `fastapi-endpoint/SKILL.md:16-31` table, `new-migration` helpers `tests/integration/_pg.py`, `_rows.py`,
  `test_schema_00023.py`, `test_schema_mirror.py`. Every make target named exists.
- Facts checked true: `new-migration/SKILL.md:26-28` (last file `00023`, next `00025`);
  `security-exceptions/SKILL.md:68-70` (2 certifi entries to 2026-11-03, 55 image entries to 2026-11-17, no
  age waiver).
- All 10 vendored skills have `SOURCE.txt` (repo, path, commit, licence, local notes) and `LICENSE`;
  `.claude/rules/vendored-skills.md` makes repo rules win.
- No skill tells agents to add dates or change notes; `decision-record/SKILL.md:40-41` and
  `phase-handoff/SKILL.md:57` forbid them.
- `kind-slot-run` has `disable-model-invocation: true`, and `agents/kind-runner.md:13-14` handles that.

Deductions:

- Stale "Phase C is in progress" pointers: `decision-record/SKILL.md:14-15`, `kind-slot-run/SKILL.md:54`,
  `phase-handoff/SKILL.md:27`, `db-notion-audit/SKILL.md:65`. Phase C is merged; this branch's records are 0025
  and 0026.
- `writing-brd`, `writing-prd`, `writing-frd`, `writing-trd`, `checking-requirements-direction`: one-line
  descriptions over 120 characters (`SKILL.md:3`).
- Vendored descriptions do not lead with "Use when" (`python-anti-patterns/SKILL.md:3` "Use this skill when");
  `claude-md-improver/SKILL.md:4` uses `tools:` (noted in its SOURCE.txt); `differential-review` and
  `sharp-edges` carry `agents/openai.yaml` that nothing here uses. Low impact.

## 4. Agents (9.5)

- 12 agents, each with `name`, `description`, `tools`, `model`; `agents/README.md` has no frontmatter and says why.
- Every tool exists (Read, Grep, Glob, Bash, Write, Edit, Skill, WebFetch, WebSearch, the two Notion tools, the
  three PyCharm tools of `qa-reviewer.md:14-16`). Every preloaded skill exists.
- The five reviewers (provisioning-validator, final-reviewer, security-reviewer, db-notion-auditor, qa-reviewer)
  run `reviewer_writes.py` from frontmatter with `onFailure: block`, and settings.json runs it again by
  `agent_type` so the limit holds in `claude -p`. `test_hooks.py` asserts this for each one.
- Writers have narrow scopes (spec-writer only `.claude/docs/requirements/`, planner only plans).

Deduction: `final-reviewer.md:3-4` says "once ... Kind has run" but not "QA sweep triaged", which
`CONSTITUTION.md:79-80` (V.3) and `WORKFLOW.md:71` require before it.

## 5. Rules, workflow, README, stack, constitution, spec (7.5)

- `TECH_STACK.md`: all 29 library versions equal `uv.lock` and `pyproject.toml`; `requires-python`, `uv_build`
  0.12.18, osv-scanner v2.6.0, goose v3.28.0, grype v0.119.0, protoc v36.2, kind v0.33.0, kubectl v1.36.5,
  helm v3.22.0, node `v1.36.4`, base `python:3.14.7-slim-trixie`, chart 0.1.0, `rabbitmq:4.3.6-management-alpine`
  all match their sources.
- `WORKFLOW.md:10-55` Mermaid: valid. `flowchart TD`; node shapes `([..])`, `{".."}`, `[".."]` well formed;
  labels with commas, parentheses or semicolons are quoted; every referenced node is defined; no reserved
  word as an ID.
- `CONSTITUTION.md`: Version 1, Articles I to VIII, numbered rules, amendment rule with no dates; imported by
  CLAUDE.md; the guard asks before a change (verified).
- Rules: six files, path-scoped frontmatter where it fits, none over 120, no dash.
- `make spec-check`: "Problems: 0 (none)", exit 0. With `SPEC_CHECK_FLAGS=--strict`: same, exit 0. Register:
  BR 15, PR 29, FR 54 (51 current), TR 55, 125 acceptance rows.

Deductions:

- Traceability is thin: "Current FRs with a test: 5 of 51; Must FRs 45: 5 with a test, 40 listed as no test yet".
  Strict passes only because `spec.yaml:1139` `no_test_yet` waives 40 Must FRs. Only 32 test files carry any
  FR ID. CLAUDE.md:113, `rules/testing.md:12` and CONSTITUTION V.1 describe a rule the suite does not meet yet.
- `README.md:120-122` "Database roles" says it "still describes the roles as of migration 00010 (Go reference)";
  `README.md:122-132` name `src/hosts/api`, `src/hosts/build-worker` that are not here. 22 lines over 120.
- `.claude/docs/runbooks/python-p0.md:186-188` says `age-waivers.toml` holds 14 waivers expiring 2026-10-19 with
  `exclude-newer = 2026-10-05`. False: the file has no entry and `exclude-newer` is `2026-10-10T00:00:00Z`.
- `.claude/docs/runbooks/phase-1-database.md:5` and `:80` are Go-era (`make test-integration`, `src/hosts/...`).
- The flowchart has no QA sweep node though the table (`WORKFLOW.md:71`) and V.3 require one.
- `CONSTITUTION.md:7-9` "Reports written before this file cite P-n" is a history note.

## 6. Boilerplate and staleness (6.5)

Clean: CLAUDE.md, WORKFLOW.md, `.claude/README.md`, TECH_STACK.md, CONSTITUTION.md, all rules, all agents and
both handoffs have zero em or en dashes and zero prose lines over 120. No TODO or placeholder text anywhere
outside rules that forbid it.

Found (`git grep` over `.claude/`, `ai/handoffs/`, `README.md`):

- Dated notes: `guard.py:35`, `test_guard.py:274`. Stale dated fact: `python-p0.md:188`.
- Provenance tags `*(owner, ...)*`: about 30, in decisions `0001:107,139`, `0007:76`, `0008:44-70`,
  `0009:14-70`, `0011:7-64`, `0019:14,55`. `docs-style.md:15` and `decision-record/SKILL.md:44` forbid them.
- Em or en dashes: 60 outside vendored skills, in 18 decision records (every H1 of 0001 to 0014 is
  an em dash (`# 000N <U+2014> Title`)) and 3 runbooks (`identity.md` 3, `prototype-demo.md` 2, `prototype-stack.md` 5).
- Lines over 120 outside tables: about 1000 in decisions and runbooks (`0014` 123, `prototype-stack.md` 87,
  `operator.md` 78, `phase-1-database.md` 62), 22 in `README.md`.
- Missing files referenced as current: `ai/handoffs/auth-telemetry-alignment.md:47` admits "paths below are from
  before the folder move" and keeps them (`workers/messages.py` at `:218`). `DIRECTION.md` and decision `0024` are
  missing by design and say so (`requirements/README.md:22-23`, `decisions/README.md:52`): not a defect.
- Organisation: `ai/README.md:12-18` indexes 4 of the 8 files in `ai/reviews/`; `qa-cqrs-layout.md` (current) and
  four closed `phase-c-task-12-*` reviews are not listed.

## Top 10 fixes, ranked by grade impact

1. **Spec traceability** (`spec.yaml:1139`, CLAUDE.md:113, `rules/testing.md:12`). Only 5 of 51 FRs have a tagged
   test. Fix: one small-lane task per FR group adds `"""FR-n: ..."""` to the existing tests that prove it, fills
   `tests:` in `spec.yaml`, empties `no_test_yet`; then make `--strict` the default of `make spec-check`.
2. **One real-cluster rule** (`CLAUDE.md:35-36`, `guard.py:36-41`, `CONSTITUTION.md:84`, `.claude/README.md:25`).
   Fix: owner picks; recommended `DOKS_READ = "deny"` to match section 0, delete "until the owner picks one",
   change README line 25 to "blocks", VI.1 to "The real cluster is off limits for agents", update test rows.
3. **Dated comments in hooks.** `guard.py:35` becomes `# Settings (owner rules). Change here, then run
   test_guard.py.`; `test_guard.py:274` becomes `# Regression rows: one per bug fixed (fail-open, bypass, false
   positive)`. Hook edits ask the owner.
4. **Root README** (`README.md:118-132`). Replace "Database roles" with three lines: roles come from
   `scripts/db/bootstrap-roles.sql`, grants from the migrations, the design is Notion "5 · Provisioning". Wrap
   the 22 long lines. Add `deploy/image`, `deploy/helm` to the `deploy/` row.
5. **False runbook fact** (`python-p0.md:186-188`). Replace with "The file holds no waiver." Rewrite the Go-era
   lines of `phase-1-database.md:5,80` to name `make py-test-integration` and `src/provisioning/hosts/`.
6. **Permissions that skip "ask first".** `settings.json:9`: replace `Bash(make proto-*)` by allow entries for
   `proto-logs`, `proto-stop`, `proto-down` and ask entries for `proto-up`, `proto-run*`, `proto-e2e*`,
   `proto-demo`, `proto-test-kind*`. `guard.py` `check_git_add`: deny `-A`, `--all`, `.`, `:/` always; add rows to
   `test_guard.py`.
7. **Stale Phase C pointers in skills.** `decision-record/SKILL.md:14-15`, `kind-slot-run/SKILL.md:54`,
   `phase-handoff/SKILL.md:27`, `db-notion-audit/SKILL.md:65`, `rules/spec-driven.md:21`: say "the current
   phase's record, named in the handoff" and drop the Phase C example.
8. **Dashes and provenance tags in records.** `sed` the H1s of 0001 to 0014 em dash (U+2014) to `: `, en dash ranges
   (U+2013) to ` to `; remove the `*(owner, ...)*` tags (keep an OD-n ID as plain text); same for the 3 runbooks.
9. **Rule disagreements.** `CLAUDE.md:20-21`: "The database pages are read-only; only the lead edits Web Block
   architecture (VI.4)". `CLAUDE.md:19`: put the constitution first. `CONSTITUTION.md:7-9`: drop the P-n note.
   `CLAUDE.md:45`: add `mk/qa.mk` and `make qa-sweep`. `final-reviewer.md:3-4`: add "the QA sweep is triaged".
10. **Organisation.** `ai/README.md`: list `qa-cqrs-layout.md`; `git rm` the closed `phase-c-task-12-*` reviews.
    `auth-telemetry-alignment.md`: replace the pre-move paths. `WORKFLOW.md`: add a QA sweep node before
    `FR["Final review ..."]`. Wrap the 120+ description lines of the `writing-*` skills.

## Already excellent

- The guard and reviewer hooks: 455 self-tests, fail-closed on bad input and on a hung `git`, no shell, owner
  "ask" on constitution, hook and settings edits, and a reviewer limit that also holds in `claude -p`.
- `session_start.py` puts the true spec state ("46 current FRs with no test") in front of every session.
- `TECH_STACK.md` matches every pin exactly.
- CLAUDE.md is specific to this service and every command, target and path in it works.
- Vendored skills with commit, licence and local overrides; local skills written against this codebase.
- An honest decisions index (`decisions/README.md`) that names reserved and branch-only numbers.
- A spec register with a self-tested checker that reads commits, tests, designs and plans.
