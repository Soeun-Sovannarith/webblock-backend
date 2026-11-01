# Change designs

One file per change, `<topic>-design.md`, written by the `designer` agent in stage 2 of `.claude/WORKFLOW.md`. A
design names the FR and TR IDs it covers, has numbered decisions (G1, G2, ...) that a test can check, a "Constitution
check" (each article of `../CONSTITUTION.md` pass, not applicable or conflict; a conflict stops the work for the
owner), what changes in code, tests, docs and other services, the cost if
wrong, and open questions for the owner. The owner approves it at gate 2.

When the change is built and its decision record in `../decisions/` holds the rules, the design is finished and is
deleted with `git rm` (git history keeps it). Only designs in progress live here.

In progress:

- [go-live-tries-design.md](go-live-tries-design.md): going live takes up to 3 tries (FR-19, FR-21, FR-22, FR-37.3;
  TR-57, TR-58; record `0027`).
- [k3s-site-cluster-spike.md](k3s-site-cluster-spike.md): spike, generated sites on a k3s cluster.
