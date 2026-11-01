# Lean flow, code first

Code and tests come first; documents follow only when a fact people rely on changes. The flow (change note,
implement, a security review when the change is security-sensitive, merge), its agents and its two owner gates
are explained once in `.claude/WORKFLOW.md`. These rules bind every change (Art. I):

- Small batches: one change per branch, merged within a day where it can be.
- The change note is a few lines in the chat: the closest existing FR or TR ID, what changes, the tests, and a risk
  or article only when one is at stake. The owner's yes (one line is enough) comes before code. A design file is
  written only for a new service, a new or changed contract between services, or a schema change; a decision
  record only for a rule later work must follow.
- Every commit that changes code (`src/main/`, `src/test/`, Flyway migrations, `build.gradle`, `gradle/`,
  `scripts/`) cites the closest existing FR or TR ID. A new ID is added to the spec only for a new user-visible
  feature or a new contract between services; then `.claude/docs/requirements/spec.json` changes in the same
  commit and `python3 -I scripts/spec-check.py` reports what is off.
- Tests are written with the code, in the same commit. Each new test names its ID in `@DisplayName("FR-12: ...")`
  or in its method name (`fr12_ownerRenamesTheProject`). A bug fix has a test that was seen failing first.
- Docs change only when a runbook step, a README command, a contract between services or a rule later work must
  follow changes. No TRD, design or spec bookkeeping for a technical change.
- Drift: if the code must differ from a contract or the database design, the implementer stops and reports Drift
  with both sides; the owner decides.
- Notion stays the authority for the database and the API contract; the spec points to it and never copies it.
- A review runs only for a security-sensitive change (tokens, `SecurityConfig`, ownership answers, `/internal` or
  mesh rules, secrets, the guard, the cluster, dependencies, migrations, grants): one `security-reviewer` pass, plus
  the `db-notion-auditor` for a migration or grant. One fix round; a High finding still open goes to the owner,
  anything else becomes a backlog item. Every other change merges on green gates after the owner reads the diff.
