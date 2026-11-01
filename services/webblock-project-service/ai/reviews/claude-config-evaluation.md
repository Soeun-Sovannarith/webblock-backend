# Claude Code configuration: independent evaluation

Branch `claude-makeover` (from `main` at `ed5fcaf`), judged in the worktree. `ai/handoffs/next.md` was ignored (being
rewritten). Grader: an independent read-only agent. Rubric: "does your CLAUDE.md reflect your real project, are your
skills and hooks functional, and is your `.claude/` folder organized?" plus the owner rules (current facts only,
plain English, no em or en dashes, lines at most 120, spec-driven with a constitution and `spec.json`, clean
organisation). Scored the same way as Provisioning's `ai/reviews/claude-config-evaluation.md` at `37eda84`.

## Overall: 8.4 / 10

| Area | Score | One line |
| --- | --- | --- |
| 1. CLAUDE.md | 8.5 | Real and specific; every command and path exists; three rules disagree with the hooks |
| 2. Hooks | 9.0 | 402/402 and 215/215 pass, fail closed, valid matchers; one 2141 line file, cluster read still "ask" |
| 3. Skills | 9.0 | 14 skills, valid frontmatter, local ones start "Use when", paths resolve; vendored text has dashes |
| 4. Agents | 9.0 | 12 agents, valid tools and skills, all 5 reviewers carry the write limit; two loose ends |
| 5. Rules, workflow, stack, constitution, spec | 7.5 | Consistent and spec-check clean; 28 of 37 FRs (all Must) have no tagged test |
| 6. Boilerplate and staleness | 8.0 | Own docs free of dashes and dated notes; vendored files, one proposal note, finished design and plan remain |

## 1. CLAUDE.md (8.5)

Evidence:

- `.claude/CLAUDE.md` is 164 lines about this service only: cluster rule (section 0), commands, layout, a real code
  sample, gates, git rules, boundaries. It imports the constitution at line 9; `@docs/CONSTITUTION.md` resolves to
  `.claude/docs/CONSTITUTION.md` (Version 3).
- Section 1 commands: every Gradle task exists (`integrationTest` `build.gradle:184`, `cpd` `:331`,
  `resolveAndLockAll` `:352`, `jacocoTestReport` `:197`; `pmdMain`, `pmdTest`, `spotbugsMain` from the `pmd` and
  spotbugs plugins `build.gradle:4,9`). `scripts/osv-scan.sh`, `contracts-sync.sh`, `spec-check.py` exist;
  `python3 -I scripts/spec-check.py` ran: "0 Errors". `--self-test` and the Gradle tasks were not run.
- Section 2: all ten feature folders plus `common`, `presentation/{controller,grpc}`, `db/migration/{project,catalog}`
  (V1 to V7, so "next is V8" is true), `component-catalog/`, `site-templates/`, `contracts/proto`,
  `scripts/db`, `scripts/kind/j1-exit-check.sh`, `TYPE_FOLDERS` (`ArchitectureRulesTest.java:209`) exist.
- Section 3 sample matches `ProjectController.java:49-50` and `ProjectService.java:130-131` (checked line by line).
- Decisions 0001 to 0015, the four contracts and six runbooks named in "Start here" all exist.
- No em or en dash, no line over 120, no dated note.

Deductions:

- `CLAUDE.md:54-55` says never run `kubectl` against the real cluster, but `guard.py:43` sets `DOKS_READ = "ask"` and
  `guard.py:558-560` returns ask. Verified: `kubectl get pods -A` returns `ask`, not deny.
- `CLAUDE.md:157` (section 5) says never `git add -A`, but the guard allows it (verified, no output) and
  `settings.json:14` auto-allows `Bash(git add *)`.
- `CLAUDE.md:180` puts a Kind slot under "Ask first", but the guard says nothing for `make proto-up` (verified).
- `CLAUDE.md:25-27` lists sources of truth without the constitution, which the constitution page calls binding
  (`CONSTITUTION.md:4-5`); it is consistent with `CONSTITUTION.md:14` but easy to misread.
- `CLAUDE.md:62-85` does not mention `test_hooks.py` (line 77 lists only `test_guard.py`); both are allowed in
  `settings.json:7-8`.

## 2. Hooks (9.0)

Self-tests: `python3 -I .claude/hooks/test_guard.py` prints "402 of 402 passed";
`python3 -I .claude/hooks/test_hooks.py`
prints "215 of 215 passed". `.claude/README.md:34` names both as proof but states no counts, so no count can drift.

Piped dry probes (CLAUDE_PROJECT_DIR the worktree; nothing was run):

| Hook | Input | Result |
| --- | --- | --- |
| guard.py | Bash `git push origin main` | deny: "git push is the owner's job" |
| guard.py | Bash `git status`; `git merge x` on the feature branch | nothing; nothing (correct) |
| guard.py | Bash `cat .env`; Read `~/.kube/config` | deny; deny |
| guard.py | Bash `git reset --hard HEAD~1` | deny |
| guard.py | Bash `kubectl get pods -A` | ask (see area 1) |
| guard.py | Edit `.claude/docs/CONSTITUTION.md` | ask: only the owner amends it |
| guard.py | Notion `notion-update-page` on an unknown page | ask |
| guard.py | Bash `git add -A`; `make proto-up` | nothing; nothing |
| guard.py | stdin `not json` | deny, "Guard error ... not checked" (fails closed) |
| reviewer_writes.py | qa-reviewer Edit `src/a.java` | deny: "Reviewer limit" |
| reviewer_writes.py | qa-reviewer Write `ai/reviews/x.md` | nothing (allowed) |
| reviewer_writes.py | final-reviewer `git commit` | deny: reviewers are read-only |
| reviewer_writes.py | implementer Edit `src/a.java` | nothing (not a reviewer) |
| session_start.py | startup | branch, tree state, "37 FRs, 28 current with no test", constitution version 3 |

Registration (`settings.json:89-130`): guard on the Bash, Read, Edit, Write, Grep, Glob and `mcp__.*` tool names,
reviewer limit on the write tools, both `onFailure: "block"`, timeout 15;
SessionStart and PostToolUse (`^(Edit|Write|MultiEdit)$`) registered. All regexes are valid. No dated comment in
any hook (grep). No line over 120 in the hook files.

Deductions:

- `guard.py` is 2141 lines (95 KB) in one file with 84 top-level functions and classes: hard for the owner to audit.
- `DOKS_READ = "ask"` (`guard.py:43`) leaves the cluster read policy undecided against `CLAUDE.md:54`.
- `git add -A` and `make proto-*` gaps (area 1).
- `settings.json:68` `Read(!.env.example)` is a negated deny pattern with no documented effect; low risk, unclear.
- Not tested: a commit with staged `src/` files and no ID (needs a staged index; the test files cover it).

## 3. Skills (9.0)

- 14 skills, each with `SKILL.md`, `name` and `description`. `.claude/README.md:50-69` lists exactly these 14.
- The 12 local skills start the description with "Use when" and say when not to use them. Descriptions are 194 to 421
  characters in the frontmatter; none is blank.
- Paths named in the repo skills resolve (spot-checked `new-migration`, `spring-endpoint`, `db-notion-audit`,
  `project-gates`); templates and recipes exist for the four writing skills and `checking-requirements-direction`.
- Both vendored skills (`differential-review`, `sharp-edges`) carry `SOURCE.txt` and `LICENSE`; the README says
  "copied unchanged", with the commit in `SOURCE.txt`.
- `decision-record/SKILL.md:49` forbids dates and change logs.

Deductions:

- Vendored `SKILL.md` files and references carry em or en dashes (area 6), against the owner rule; also unlisted in
  `SOURCE.txt` as an accepted exception.
- `differential-review` and `sharp-edges` ship an `agents/` folder and `assets/` that no agent here uses (check
  `SOURCE.txt`; low impact).
- `phase-handoff/SKILL.md:63` points to `.superpowers/sdd/<plan>/progress.md`, a scratch folder (see removal list).

## 4. Agents (9.0)

- 12 agent files plus `agents/README.md` (no frontmatter, documented as such). Every file has `name`, `description`,
  `tools`, `model`; every `skills:` value is an existing skill; every tool is a real tool name.
- Reviewers: `db-notion-auditor`, `final-reviewer`, `project-validator`, `qa-reviewer`, `security-reviewer` each carry
  the `reviewer_writes.py` PreToolUse hook in frontmatter, and `settings.json:100` repeats it for `claude -p`.
  Behaviour proved by the probes above.
- The roster table in `agents/README.md` matches the files (stage, skills, model).
- `agents/README.md` and `WORKFLOW.md` agree on who edits what.

Deductions:

- `researcher.md` has `Write` with no path limit; "writes one report file" is only a promise. `kind-runner.md` has
  `Edit` with no path limit, same issue.
- Tool lists mix two forms (comma string in `kind-runner`, `qa-reviewer`, `security-reviewer`; YAML list in the others).
- `planner` has no skills and relies on `superpowers:writing-plans` "if installed" (`agents/README.md`), a plugin
  enabled in `settings.json:127` but not vendored.

## 5. Rules, workflow, stack, constitution, spec (7.5)

Evidence:

- Rules: 5 files with `paths:` globs (`spec-driven.md` without, so always loaded). Every ArchUnit rule name quoted in
  `java-code.md` exists in `src/test` (9 of 9 checked).
- `TECH_STACK.md` matches `gradle/libs.versions.toml` (grpc 1.83.1, protobuf-java 4.35.1, Modulith 2.1.1, Namastack
  1.7.3, Error Prone 2.50.0, Guava override 33.7.2-android).
- `decisions/README.md` lists 0001 to 0015, all 15 files present, statuses Accepted.
- Constitution Version 3 (`CONSTITUTION.md:3`); the Version 3 commit matches `FR-30`/`TR-10` and `NOT_MANAGER` in
  `provisioning-contract.md:51`, `java-code.md`, and `ProjectService` answers.
- `spec.json` has keys `br, pr, fr, tr`; `spec-check.py` reports 15 BR, 24 PR, 37 FR (125 acceptance rows), 39 TR,
  0 errors. `session_start.py` and `spec-driven.md` describe it correctly.
- Relative links in `README.md`, `.claude/*.md`, `docs/`, `agents/` all resolve (script check, 0 broken).

Deductions:

- 28 of 37 FRs, all Must, have no tagged test (FR-1 to FR-4, FR-6 to FR-12, FR-14, FR-16 to FR-29, FR-34, FR-37):
  64 `@DisplayName("FR-` tags in total. The rule "each test names its ID" (`testing.md`, `CLAUDE.md:143`) holds only for
  new tests, so traceability is thin. Largest single deduction.
- `requirements/DIRECTION.md:3-5` is "Status: Draft", names a commit hash and a branch, and carries Drift on four
  Must items; it is a snapshot, not a current-facts doc.
- `designs/j3-notion-alignment.md` and `plans/j3-notion-alignment-plan.md` are merged work, yet `designs/README.md`
  and `plans/README.md` say finished ones are deleted with `git rm`. The plan still says "Approved by the owner at
  gate 3" and names a branch (`plans/j3-notion-alignment-plan.md:3,7`). FR-30 is still owed, so keep until it ships.
- `dependency-versions.md:4-6` states "the plan date, 2026-10-13" and "newer eligible releases exist (grpc-java 1.84.3
  of 2026-10-13)": a dated snapshot, not a stable fact.
- `authentication-contract.md:323-363` has 8 non-table lines over 120 characters (323, 325, 327, 329, 331, 333, 349,
  363).
- `WORKFLOW.md` Mermaid block (`:10`) renders, but its first check was not run in a Mermaid parser (not checked).

## 6. Boilerplate and staleness (8.0)

- Dashes: no em or en dash in any file of `.claude/` that this repo wrote, in `ai/`, in the root `README.md` or in
  `scripts/`. Hits are only in vendored text:
  `skills/sharp-edges/references/case-studies.md:3,190`, `language-specific.md:3,168`, `lang-c.md:18`,
  `skills/differential-review/SKILL.md:96,101`.
- Lines over 120 outside tables: 22 in vendored `differential-review/methodology.md`, `sharp-edges/references/*`, and 8
  in `authentication-contract.md` (area 5).
- Dated or provenance notes: none in agents, rules, skills, hooks, WORKFLOW, README, CLAUDE.md. `docs/notes/
  admin-dashboard-monitoring.md:3` says "18 October 2026" and "draft for review".
- `DIRECTION.md` header and `dependency-versions.md:4-6` carry dated or commit snapshots (area 5).
- Root `README.md:7-9` says "Next: Git hosting (J3)" while the J3 alignment is merged and the handoff says J3 to J5;
  the line is a plan, not a fact, and will go stale.
- Reviews that no doc links stay as an unreferenced pile (removal list).

## Removal list for the owner

| Path | Why unwanted | Linked by a doc (grep) |
| --- | --- | --- |
| `.superpowers/` (shared tree, 5.3 MB, git-ignored) | Tool scratch: old plan briefs (`sdd/2026-10-*`), research drafts, db-drift, archive-worktrees | Yes: `phase-handoff/SKILL.md:63`, `implementer.md`, `guard.py`, `test_guard.py`, `next.md` name `.superpowers/sdd/` as a progress folder. Delete the contents; keep the folder name only if those mentions stay |
| `docs/notes/` (shared tree, untracked) | Holds one proposal, `admin-dashboard-monitoring.md` (dated, 16 lines over 120). Move to `.claude/docs/proposals/` (new folder, needs a README line) or delete | No doc links it |
| `ai/reviews/final-project-notion-alignment.md` | Whole-branch review of merged work; its re-review is linked, this one is not | Not linked (only the other review names it) |
| `ai/reviews/j3-tasks-3-4-review.md` | Task review of merged work | Not linked |
| `ai/reviews/qa-claude-setup.md` | QA of an older Claude setup; names the old layout | Not linked |
| `ai/reviews/qa-project-notion-alignment.md` | QA sweep of merged work | Linked by `next.md` only; goes when the handoff is rewritten |
| `ai/reviews/db-notion-audit-project-notion-alignment.md` | DB audit of merged work | Linked by `FRD.md` |
| `ai/reviews/j3-grpc-security-review.md` | Security review of merged work | Linked by decision `0011` |
| `ai/reviews/contracts-inventory.md` | Contracts inventory | Linked by `DIRECTION.md`, `next.md` |
| `.claude/docs/designs/j3-notion-alignment.md`, `plans/j3-notion-alignment-plan.md` | Merged; README rules say delete when done (keep until FR-30 ships) | Linked by decision `0011`, `TRD.md`, `next.md` |
| `bin/osv-scanner` (shared tree) | Downloaded binary, git-ignored; fine, listed for completeness | `README.md` |

Checked and clean: no empty folder in the worktree, no stray file at the root, no backup or `.orig` file, no
`settings.local.json` in git, `.claude/worktrees/` git-ignored.

## Top fixes (priority order)

1. Add tagged tests for the 28 Must FRs with none (`@DisplayName("FR-n: ...")`), starting with FR-1 to FR-4 and
   FR-6 to FR-12. Area 5 gains the most.
2. Settle the cluster read policy: set `DOKS_READ` to deny in `guard.py:43` and update `test_guard.py`, or reword
   `CLAUDE.md:54-55` (owner decision; it edits a hook).
3. Make `CLAUDE.md:157` true: have the guard deny `git add -A`, `git add .` and `commit -a` always, or soften the line.
4. Align `CLAUDE.md:180` (Kind slot is ask-first) with the guard: make the guard ask on `make proto-*`, or reword.
5. Delete or archive the unlinked `ai/reviews/` files and `.superpowers/` contents; fix the `.superpowers/sdd`
   mentions in `phase-handoff/SKILL.md:63`, `implementer.md`, `next.md` to a stated, current place.
6. Move `docs/notes/admin-dashboard-monitoring.md` to `.claude/docs/proposals/`, strip the date at line 3, wrap long
   lines; or delete it.
7. Replace dashes in vendored files (`sharp-edges`, `differential-review`) with commas or colons and record the
   change in each `SOURCE.txt`, or add `.claude/rules/vendored-skills.md` stating the exception.
8. Rewrite `dependency-versions.md:4-6` without a plan date and "newer eligible releases" snapshot; wrap the 8 long
   lines of `authentication-contract.md:323-363`.
9. Restrict `researcher.md` and `kind-runner.md` writes (a path hook or a narrower tool list); use one tool list form.
10. Split `guard.py` (2141 lines) into small modules, and add test counts nowhere (keep the README count-free); also
    add `test_hooks.py` to `CLAUDE.md` section 1 and explain or remove `Read(!.env.example)` in `settings.json:68`.

## Not checked, confidence

- Gradle tasks (`check`, `integrationTest`, `spotlessCheck`), `spec-check.py --self-test`, Mermaid rendering, Notion
  pages: not run or fetched. Confidence in areas 1 to 4: high; area 5: medium; area 6 dash and date greps: high,
  "no other stale fact" claims: medium.
