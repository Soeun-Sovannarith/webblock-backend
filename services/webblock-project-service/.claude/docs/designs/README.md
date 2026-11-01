# Change designs

One file per change, `<topic>-design.md`, written by the `designer` agent in the design stage of
`.claude/WORKFLOW.md` from the approved requirements in `../requirements/`. A design names the FR and TR IDs it
covers, has numbered decisions (G1, G2, ...) that a test can check, a "Constitution check" (one row per article of
`../CONSTITUTION.md`: pass, not applicable, or conflict; a conflict stops the work for the owner), what changes in
code, tests, docs and other services, the cost if wrong, and open questions for the owner. Tables, columns, indexes
and grants follow the Notion platform-db-v6 pages exactly. The owner approves the design at its gate.

When the change is built and its decision record in `../decisions/` holds the rules, the design is finished and is
deleted with `git rm` (git history keeps it). Only designs in progress live here.
