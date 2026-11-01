# Task plans

One file per change, `<topic>.md`, written by the `planner` agent in stage 3 of `.claude/WORKFLOW.md` from an
approved design. Each task names its FR and TR IDs, whether it can run in parallel, what it needs (security review,
DB audit, Kind), its files, the failing tests to write first, its gates and its commit message (the task block in
`.claude/agents/planner.md`). It opens with a "Constitution check" (each article of `../CONSTITUTION.md` pass, not
applicable or conflict; a conflict stops the plan for the owner) and ends with a coverage table: every FR and TR of
the design lands in a task. The owner approves it at gate 3.

When every task is built and reviewed and the decision record in `../decisions/` holds the rules, the plan is
finished and is deleted with `git rm` (git history keeps it). Work still owed moves to the current handoff in
`ai/handoffs/`. Only plans in progress live here.

Plans in progress:

- [`go-live-tries.md`](go-live-tries.md): going live takes up to 3 tries (FR-19, FR-21, FR-22, FR-37.3; TR-57,
  TR-58; design `go-live-tries-design.md`; record `0027`).
