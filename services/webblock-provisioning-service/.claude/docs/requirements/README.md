# Provisioning requirements: start here

**Status:** Approved at gate 1 (answers O1 to O4 are in `GATE1`; the owner delegated the other answers to the
lead). Site images are private, pulled with a Secret per site namespace (O3). Next: stage 2 design. Planned for the
next phase, not started: automatic deploy and go-live (PR-26, FR-47 to FR-49); private site images (FR-53, TR-52).
Still open: PRD Q-4, FRD Q-4, TRD Q-5.

The spec comes first and the code follows it (`.claude/rules/spec-driven.md`). Every document here is
checked against the constitution, [`../CONSTITUTION.md`](../CONSTITUTION.md). Notion stays the authority for the
database and the API contract; these files point to it and never copy it.

## Read in this order

| File | What it says | Items |
| --- | --- | --- |
| [BRD.md](BRD.md) | Why Provisioning exists and who it serves | BR-n |
| [PRD.md](PRD.md) | What each kind of user gets, and when an offer counts as done | PR-n |
| [FRD.md](FRD.md) | Exactly how the service behaves, with Given / When / Then acceptance rows | FR-n, FR-n.m |
| [TRD.md](TRD.md) | How it is built: components, data, interfaces, security, deployment | TR-n |
| [spec.yaml](spec.yaml) | The register: each ID's title, status, parents, priority, phase, FR rows and tests | all |

`DIRECTION.md` (whether the code matches the spec) is written by the direction check before a merge; it does not
exist yet.

## How IDs work

- Each item links up one level: PR to BR, FR to PR, TR to FR and to its decision record.
- An ID keeps one meaning forever. Plans, tests and commits cite it. Numbers are never reused or renumbered; a
  retired item says "Removed: <why>; see FR-n"; a changed meaning gets a new ID.
- Acceptance rows `FR-n.m` are one test each. A test names its ID in its name or docstring (`test_fr12_...`).
- `Q-n` is a question for the owner, numbered per file. A lower file writes "see BRD Q-n" instead of asking again.
- Source keys: `S1` = Notion "5 · Provisioning", `S2` = Notion "Web Block architecture", `AUTH` = the
  authentication contract, a number = a decision record in `.claude/docs/decisions/`, `OD-n` = an open decision.
  `GATE1` = the owner's gate 1 answers; `OWN` = the owner's decisions in the lead's brief; `LEAD` = answers the
  owner delegated to the lead; `DBRULE` = the database matches S1 100%.

`spec.yaml` is kept by hand: a change to an ID in a document changes its entry in the same commit, and the end
of each task updates statuses and test lists (`phase-handoff` skill). `make spec-check` compares it with the
documents, the test IDs, the branch's commits, the designs and the plans.

The skills that write these files are in `.claude/skills/writing-brd`, `writing-prd`, `writing-frd` and
`writing-trd`.
