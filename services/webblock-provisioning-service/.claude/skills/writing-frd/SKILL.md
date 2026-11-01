---
name: writing-frd
description: Use when the owner asks for the Provisioning FRD (FR IDs, acceptance rows) or FRD.md is missing or stale.
---

# Writing the Provisioning FRD

## Overview

The FRD says exactly how the Provisioning service behaves: who starts each action, what goes in, the steps, the
result, the errors, the states, and the acceptance checks that tests are written from. It is the spec of
spec-driven work: plans, tests and commits cite its IDs. Every FR-n links up to the PR-n it delivers (from
`.claude/docs/requirements/PRD.md`); the TRD links down to it.
Output: `.claude/docs/requirements/FRD.md`. Follow `recipe.md` and fill `template.md`, both in this folder.

## What an FR item looks like

One table row per behaviour: trigger (who, through REST, MCP, an event, a timer or the operator CLI),
input, what happens (short steps, then the result), errors, PR link, source. Real names are welcome here:
error codes with their HTTP status (`COMMAND_IN_PROGRESS`, 409), state values, event names.

- Take error codes from `src/contracts/openapi.yaml`, `src/provisioning/core/application/shared/errors.py` and
  `src/provisioning/core/domain/values/failure_codes.py`. A code a source assigns to Provisioning but the code lacks
  is Drift.
- Group rows into sections by area (for example: new releases and builds, deploy and go live, rollback and
  recovery, hosting on and off, admin actions, status reads, messages from other services, operator tools).
  Every section starts with one plain sentence saying what the area is for.
- The Errors column holds code names only; their meaning goes once in "Errors at a glance", whose Status is
  the HTTP status, `stored` for a failure code kept on a record, or `CLI exit n` for the operator CLI.
- Errors any request can get (401, 403, 404 for "not the owner", 503 in production) live once, in their own
  area with their own FRs and acceptance rows; other FRs do not repeat them.
- `wc -w` at most 6000, tables included. In "Errors at a glance", codes with the same status and meaning
  share one row.

## Acceptance checks

Under each area's FR table, an "Acceptance" table gives every FR its checks, one row each, so an implementer
turns each row into one test:

| ID | Given | When | Then |
| --- | --- | --- | --- |
| FR-7.1 | a built release, routing stable, hosting online | the owner asks to deploy it | 202; one `deploy` command stored |
| FR-7.2 | another command still running for the project | the owner asks to deploy | 409 `COMMAND_IN_PROGRESS`; nothing stored |

- IDs are `FR-<n>.<m>`, numbered from 1 under each FR, and follow the same stability rules as FR IDs.
- Each FR has one success row and one row per error it lists. A failing row names the real error code and
  status in "Then", plus what must not happen.
- "Then" says only what a test can observe: a status, a stored value, a state, an event sent, an object in
  the cluster.

**States.** For each thing with a status (release, command, hosting, and any other S1 defines) give a table:
Value | Meaning | Next. Take values from S1. The document's one Mermaid diagram sits in "Main flow"; it may
be the release state diagram.

## Checks special to the FRD

- Every section starts with a one-sentence summary.
- Every FR has at least one success row and one row per listed error in an Acceptance table.
- "Constitution check" marks each of Articles I to VIII pass, not applicable or conflict; a conflict goes to the
  owner before the FRD is approved.
- "Read next" links `TRD.md`.
