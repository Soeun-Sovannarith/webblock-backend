# Project requirements: start here

The spec comes first and the code follows it (`.claude/rules/spec-driven.md`). Notion stays the authority for the
database and the API contract; these files point to it and never copy it.

**Status:** the constitution (`../CONSTITUTION.md`) is in force. `BRD.md`, `PRD.md` and `FRD.md` are approved at
gate 1 by the owner; code tasks cite their FR IDs. The designer writes the TRD in stage 2 (`.claude/WORKFLOW.md`).

## Read in this order

| File | What it says | Items | Written by |
| --- | --- | --- | --- |
| `BRD.md` | Why Project exists and who it serves | BR-n | `spec-writer`, skill `writing-brd` |
| `PRD.md` | What each kind of user gets, and when an offer counts as done | PR-n | `spec-writer`, skill `writing-prd` |
| `FRD.md` | Exactly how the service behaves, with Given / When / Then acceptance rows | FR-n, FR-n.m | `spec-writer`, skill `writing-frd` |
| `TRD.md` | How it is built: components, data, interfaces, security, deployment | TR-n | `designer`, skill `writing-trd` |
| `DIRECTION.md` | Whether the code matches the spec, written before a merge | verdict | `spec-writer`, skill `checking-requirements-direction` |
| `spec.json` | Every BR, PR, FR (with its acceptance rows and tests) and TR as data, for `scripts/spec-check.py` | all IDs | whoever changes the Markdown, in the same commit |

## How IDs work

- Each item links up one level: PR to BR, FR to PR, TR to FR and to its decision record.
- An ID keeps one meaning forever. Plans, tests and commits cite it. Numbers are never reused or renumbered; a
  retired item says "Removed: <why>; see FR-n"; a changed meaning gets a new ID.
- Acceptance rows `FR-n.m` are one test each. A test names its ID in `@DisplayName("FR-12.3: ...")` or its method
  name (`fr12_3_...`).
- `Q-n` is a question for the owner, numbered per file. A lower file writes "see BRD Q-n" instead of asking again.
- Source keys: `S1` = Notion "2 · Project", `S2` = Notion "Web Block architecture", `S3` = Notion "6 · Shared
  catalog", `AUTH` = `.claude/docs/contracts/authentication-contract.md`, `PAY` =
  `.claude/docs/contracts/payment-contract.md`, `PROV` = `provisioning-contract.md`, `COMM` =
  `service-communication.md`, `CMN` = `web-block-commons/DESIGN.md` (shared auth library), `CTR` = the
  `web-block-contracts` repository (protos, event schemas), a number = a decision record in
  `.claude/docs/decisions/`, `DBRULE` = the databases match S1 and S3 100%, `Art. N` = an article of
  `../CONSTITUTION.md`.
