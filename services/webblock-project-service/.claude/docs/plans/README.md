# Task plans

One file per change, `<topic>.md`, written by the `planner` agent in the plan stage of `.claude/WORKFLOW.md` from an
approved design. It starts with a "Constitution check": one row per article of `../CONSTITUTION.md` with pass, not
applicable, or conflict; a conflict stops the work until the owner rules. Each task names its FR and TR IDs, whether
it can run in parallel, what it needs (security review, database check, Kind), its files, the failing tests to write
first, its gates (`./gradlew spotlessCheck test`, and `./gradlew integrationTest` for database and broker work) and
its commit message. The plan ends with a coverage table: every FR and TR of the design lands in a task. The owner
approves it at its gate.

When every task is built and reviewed and the decision record in `../decisions/` holds the rules, the plan is
finished and is deleted with `git rm` (git history keeps it). Work still owed moves to the current handoff in
`ai/handoffs/`. Only plans in progress live here.
