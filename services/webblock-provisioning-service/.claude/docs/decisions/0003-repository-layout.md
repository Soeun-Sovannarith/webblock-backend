# 0003: Repository layout: AI workflow folder and unit test folders


**Amended by decision [`0010`](0010-src-layout.md)** (owner): service code moves under `src/` in Clean Architecture
folders; `test/` subfolders and the rules of section 2 stay.
Status: **decided by the project owner**. Both points depart from the folder structure sketched in ARCHITECTURE.md
section 3, which that document describes as a guide.

## 1. AI workflow documents live in `ai/`

Documents that exist because the work is done by coding agents are kept apart from platform code and platform
documentation:

| Path | Holds |
| --- | --- |
| `ai/handoffs/` | The current handoff for each phase or task, written before a session or sub-agent runs out of context |
| `ai/reviews/` | Independent validation reports |

ARCHITECTURE.md section 3, REQUIREMENT.md section 8, and CLAUDE.md section 6 name `docs/reviews/`; read those as
`ai/reviews/`. The validation report written before the move still mentions the old paths in its text; it is a record of
what the validator saw and is not rewritten. `.claude/docs/decisions/` and `.claude/docs/runbooks/` stay where they are:
they are platform documentation, and the XRDs, the Crossplane README, and migration `00001` refer to those paths.
`.claude/` stays because Claude Code loads `CLAUDE.md` from it.

## 2. Unit tests live in a `test/` subfolder of the package

ARCHITECTURE.md section 3 says unit tests live beside the code they test. The project owner asked for them to be
separated because mixed source and test files are hard to read. For `internal/domain`, tests live in
`internal/domain/test/`.

Consequences, which follow from how Go works:

- A subfolder is a different package, so these tests can use only the exported API of the package they test. A rule that
  cannot be reached through the exported API is either exposed deliberately, with a reason, or reported; it is not left
  untested and it is not exported only to satisfy a test.
- Coverage of the tested package has to be measured across packages (`-coverpkg`). The coverage gate still measures
  statements of `internal/domain`, at the same thresholds.

The project owner confirmed that every `internal/` package keeps its unit tests in a `test/` subfolder, not only
`internal/domain`. `tests/integration` and `tests/contracts` are already separate folders and do not change.

Rules that follow:

- Tests import the package under test with its normal qualified name (`domain.Release`), never with a dot import. The
  owner chose this over the dot import the first move used (validation finding V3-01).
- A `test/` folder holds `_test.go` files only. A helper that tests need lives in a `_test.go` file, so nothing under
  `test/` is ever counted as production code (V3-04).
- `go test` prints `[no test files]` and `coverage: 0.0%` for a source package whose tests live in its `test/` folder.
  That line is expected and does not mean the package is uncovered (V3-03).
- The 80% gate for the core packages is measured from each package's own `test/` folder, so tests elsewhere cannot make
  up a shortfall (V3-02).
- Test doubles for ports live in `_test.go` files only. No type that stands in for an external service exists in a
  production package.
