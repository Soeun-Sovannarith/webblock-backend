---
name: qa-sweep
description: Use when running the code quality sweep of the Provisioning service (duplicate code, dead code,
  complexity, deprecated APIs, PyCharm inspections): once per phase before the final review, before any
  dependency upgrade, or when the lead asks for a QA report. Triages findings; never fixes code.
---

# Code quality sweep

The sweep finds what the blocking gates do not: copied logic, code nothing calls, functions near the complexity
ceiling, deprecated calls, and the owner's PyCharm inspections. It is not a gate (decision `0026` Q5): a person
judges each finding. You write a report and turn real problems into tasks. You never edit code, tests, docs or
configuration, and you never commit.

## 1. Run the tools

From the repo root:

```sh
make qa-sweep
```

It runs four steps and fails only when a tool could not run (`mk/qa.mk`):

1. pylint duplicate-code (R0801), at least 8 similar lines, on `src/provisioning` (generated `_gen/` skipped);
2. vulture on `src/provisioning` plus `scripts/py/vulture_whitelist.py` (framework callers it cannot see);
3. ruff with `ERA,PIE,RET,FAST,DTZ,C90` (these also block in `make py-lint`);
4. mypy with `enable_error_code = ["deprecated"]` (also blocks in `make py-typecheck`).

To see which vulture findings only tests use (step 4 below), run vulture again with the tests:

```sh
.venv/bin/vulture src/provisioning tests scripts/py/vulture_whitelist.py \
  --exclude src/provisioning/infrastructure/grpc/_gen
```

A name in the first run but not in this one is used only by tests.

## 2. PyCharm inspections (the owner's IDE)

The PyCharm MCP server is configured at user level (`~/.claude.json`). Never add a project `.mcp.json` for it.

1. Call `mcp__pycharm__get_python_environment` with `filePath` a file under `src/` and `projectPath` the repo
   root. If it reports `noInterpreterConfigured`, or an interpreter other than the repo's `.venv` (Python 3.14),
   stop this part and tell the lead: "PyCharm has no interpreter for this project; please set it to `.venv`".
   Without it PyCharm reports false errors (`str | None` as an invalid annotation, "`__init__` should return
   None", "Class has no `__init__`" on dataclasses).
2. Call `mcp__pycharm__lint_files` with `min_severity` `warning`, a timeout of at least 120000 ms, and batches of
   about 40 project-relative paths: the files the branch changed (`git diff --name-only <base>...HEAD -- src`) or,
   for a full sweep, every `src/provisioning/**/*.py` except `_gen/` (`git ls-files 'src/provisioning/*.py'`).
   Retry a batch that returns `more: true` or files with `timedOut: true`, with fewer files.
3. If the PyCharm tools are not available or the server does not answer, say so in the report ("PyCharm MCP not
   connected; IDE inspections not run") and continue with `make qa-sweep` only. Never report them as passed.

## 3. Triage every finding

Put each finding in exactly one class:

- **Real problem**: the code breaks a repo rule (`.claude/rules/python-code.md`, "No dead or duplicated code")
  or a constitution rule. It becomes an implementer task (section 5).
- **False positive**: a framework, the language or a protocol uses it, or the tool is wrong. Say why; for
  vulture, propose a whitelist line.
- **Owner ruling**: fixing it changes a decision, a contract, or which code is kept. Ask the owner, with your
  recommendation.

Rules of thumb for this repo:

- **Duplicates.** Copied validation, SQL or parsing is real (one copy of each rule). Similar wiring in the host
  `__main__` files, or similar dataclass field lists, is usually a false positive unless a rule is repeated.
- **Dead code.** Code that nothing in `src/` calls is real and is deleted. Code only tests call: keep it only when
  the test proves a rule the running service relies on through another path; otherwise delete the code and its
  test together. A config `*_keys` helper or a test-only property is not a reason to keep code.
- **Enum members** that no code names may still be stored values (Notion "5 · Provisioning" enums). Check the
  migrations in `src/migrations/` and `infrastructure/postgres/schema.py` before calling one dead; a stored
  value is a false positive.
- **Complexity.** Anything at or within 2 of the ceiling (18) is a real finding when a planned change will add
  branches to it.
- **Docstrings.** A docstring that names a Go file (`*.go`) or a retired file (`ARCHITECTURE.md`,
  `REQUIREMENT.md`) is a real, small finding: the Go reference lives only on branch `go-reference`.
- **PyCharm.** Triage IDE findings with the rest; a finding that ruff or mypy already reports is a duplicate.
  "Type parameter is not used" on `cqrs.Command[R]` and `cqrs.Query[R]` is a phantom type for the bus and a false
  positive. A finding the interpreter setting causes is not a finding (step 2.1).

Find the IDs each finding touches: the FR in the docstring or the test of that code, else the TR of the layer
(TR-2 layers, TR-3 one operation table, TR-54 buses), else the rule number from `.claude/docs/CONSTITUTION.md`.

## 4. Write the report

`ai/reviews/qa-<branch>.md` (the branch name from `git branch --show-current`). Plain English, no em or en
dashes, lines at most 120 characters, no dates.

```
# QA sweep: <branch> at <short sha>
Stage: 5 Verify. IDs: <FR, TR and P IDs the findings touch>
## Tools: table of step, command, exit, findings (pylint, vulture, ruff, mypy, PyCharm or "not run")
## Summary: counts per class (real problem, false positive, owner ruling)
## Findings: table of #, tool, file:line, what, class, IDs, reason
## Code used only by tests: table of name, file:line, test, decision (delete with test | keep), reason
## Tasks: numbered implementer tasks for the real problems (section 5)
## Owner questions: numbered, each with a recommendation
## Not checked: what the tools cannot see
```

## 5. Turn real problems into tasks

Each real problem becomes a task under the spec-driven flow (`.claude/WORKFLOW.md`): most go through the small
change lane (an existing FR or TR, no new rule). A task names its FR or TR IDs, the files, the failing test or
the test to delete, and the acceptance check (for example "vulture no longer reports X; `make py-test` passes").
Group small findings of one module into one task. A change that alters a rule goes to stage 1 instead.

## Never

- Edit `src/`, `tests/`, `deploy/`, the whitelist or any config; commit; push.
- Lower a gate, add a `noqa`, or whitelist code that only tests use.
- Touch a cluster, read `~/.kube` or `.env`, or print a secret value.
