---
name: contracts-pin
description: Use when the shared web-block-contracts repository changed (events, protos, OpenAPI shared parts)
  and Provisioning must move its pinned commit, or when make contracts-check fails.
---

# Move the pinned web-block-contracts commit

How the pin works (decision 0022 E3, `mk/contracts.mk`, `scripts/contracts/sync.sh`):

- `contracts.lock` at the repo root holds one full commit SHA (40 lower-case hex characters).
- The clone is `../web-block-contracts` (or `CONTRACTS_REPO`). Only `git archive` reads it, so its branch and
  working tree do not matter, but the commit must exist in it.
- `make contracts-sync` copies the files Provisioning reads into `src/contracts/shared/` and generates the Python
  protobuf code into `src/provisioning/infrastructure/grpc/_gen/` with the pinned `bin/protoc` (`make py-tools`).
  Only `--python_out` and `--pyi_out`; no gRPC stubs. Generated code is excluded from lint and coverage.
- `make contracts-check` fails when either folder differs from what the pinned commit gives.

## Steps

1. In `../web-block-contracts`, find the commit to pin: `git -C ../web-block-contracts log --oneline`. It has no
   remote yet; never push it. Never change another service's contract on your own (CLAUDE.md section 6): a
   change you need there goes to the lead first.
2. Read what changed between the old and the new pin:
   `git -C ../web-block-contracts diff <old>..<new> --stat` and the diff of every file Provisioning reads.
3. Write the new SHA into `contracts.lock` (one line, nothing else).
4. `make contracts-sync`, then `make contracts-check` (must pass).
5. Fast gates (`provisioning-gates` skill), at least `make py-fmt-check py-lint py-typecheck py-layers py-test`.
   `tests/contracts/test_shared_contracts.py` checks the shared files. Fix the code that uses changed messages
   or events; never edit generated files by hand.
6. If events or message handling changed: `make py-test-messaging` in a free slot.
7. Commit `contracts.lock`, `src/contracts/shared/`, `src/provisioning/infrastructure/grpc/_gen/` and your code
   fixes, by name, in one commit that says old and new SHA.

## Tell others

A breaking wire change (renamed field, new required field, changed enum, changed REST shape) goes into the
handoff `ai/handoffs/<phase>.md` with who must act: the frontend, the Project service, the Payment service.
Draft protos and events stay drafts until their owning service accepts them (handoff "Open for the owner").

## Report

Old SHA, new SHA, the contract files that changed, gate results, and the breaking changes (or "none").
