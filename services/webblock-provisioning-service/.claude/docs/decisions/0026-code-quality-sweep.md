# 0026: Code quality sweep and stricter lint rules

Status: Accepted (owner approved). TRD: TR-8 and TR-9 (the tool set and the dependency rules), TR-2 (layers).
Applies to every FR through the gates of `.claude/CLAUDE.md` section 4.

## 1. Context

The blocking gates (ruff, `mypy --strict`, import-linter, the test suites) prove that code is correct and in the
right layer. They do not find code that nothing calls, logic copied into two places, functions that grow too
complex, or calls to deprecated APIs. `.claude/rules/python-code.md` ("No dead or duplicated code") asks for all of
these, but nothing checked them, so a reviewer had to find them by reading.

## 2. Constraints

- Never lower a gate (CLAUDE.md section 4); new checks only add.
- A new dependency needs the owner. Every dependency is open source, at least 14 days old, pinned exactly, and
  passes `make py-age` and `make py-vuln` (TR-9).
- Nothing new enters the runtime image (`deploy/image/Dockerfile` installs only `[project] dependencies`).
- Agents that review never fix the code they review (`.claude/WORKFLOW.md`, constitution VIII.2).

## 3. Decisions

- **Q1. Two development tools.** `pylint==4.1.2` and `vulture==2.16` join the `dev` dependency group. pylint runs
  only its duplicate-code check (R0801); every other pylint message stays off, because ruff and mypy already cover
  them. pylint is GPL-2.0-or-later: allowed as a development tool that never enters the image or links into the
  service. vulture is MIT. Their own dependencies (astroid, dill, isort, mccabe, platformdirs, tomlkit) are open
  source, pinned by `uv.lock`, and pass the age and advisory gates.
- **Q2. mypy reports deprecated APIs.** `enable_error_code = ["deprecated"]`: a call to anything marked
  `@deprecated` (PEP 702) fails `make py-typecheck`, the static twin of `filterwarnings = ["error"]`.
- **Q3. More blocking ruff rule sets.** `ERA` (commented-out code), `PIE` (unnecessary statements), `RET` (return
  statements), `FAST` (FastAPI usage) and `DTZ` (datetimes without a time zone) join `make py-lint`. Their
  findings were in tests only and are fixed.
- **Q4. A complexity ceiling.** `C90` with `max-complexity = 18`, the score of the most complex function today
  (`_dispatch` in `presentation/operations/`). It stops growth; lowering it is a later, separate change.
- **Q5. A sweep target that does not block.** `make qa-sweep` runs pylint duplicate-code on `src/` (at least 8
  similar lines), vulture on `src/` with the whitelist `scripts/py/vulture_whitelist.py` for framework entry
  points it cannot see, ruff with the Q3 and Q4 rules, and mypy with Q2. It always prints every finding; it is not
  one of the gates, because duplicates and dead code need a person to judge them.
- **Q6. A QA reviewer.** The agent `qa-reviewer` (stage 5 Verify) runs the `qa-sweep` skill once per phase before
  `final-reviewer`, and before any dependency upgrade. It triages each finding as a real problem, a false positive,
  or a question for the owner, and writes `ai/reviews/qa-<branch>.md`. Real problems become implementer tasks; the
  reviewer never edits code.
- **Q7. No unused runtime extra.** The runtime dependency is `psycopg[binary]`, without the `pool` extra:
  SQLAlchemy's engine pools the connections (`infrastructure/postgres/engine.py`, `hosts/shared/runtime.py`) and no
  module imports `psycopg_pool`. The import-linter contract that keeps the core free of `psycopg_pool` stays.

## 4. Consequences

- `pyproject.toml` and `uv.lock` change; the image loses `psycopg-pool`, nothing else changes at runtime.
- A function above complexity 18, a commented-out code line, a naive datetime or a deprecated call now fails the
  fast gates. The fix is in the code; a `noqa` needs a named rule and a reason (`.claude/rules/python-code.md`).
- The vulture whitelist is reviewed like code: each name has a comment saying which framework calls it.
- Cost if wrong: a rule set that proves noisy can be narrowed by a later record; the sweep tools are dev only and
  can be removed with their lock entries.

## 5. What was built and tested

- `make py-fmt-check py-lint py-typecheck py-layers py-test py-test-integration py-test-messaging chart-check
  contracts-check py-age py-vuln` pass with Q1 to Q7 in place.
- The first sweep report: `ai/reviews/qa-cqrs-layout.md`.

## 6. What stays open

None.
