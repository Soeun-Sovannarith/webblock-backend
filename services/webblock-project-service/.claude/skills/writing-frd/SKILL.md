---
name: writing-frd
description: Use when the owner asks for an FRD, functional requirements, acceptance criteria, or "exactly what
  happens when a client creates a website, saves a page or asks to generate" in the Project service, or when
  .claude/docs/requirements/FRD.md is missing or out of date.
---

# Writing the Project FRD

## Overview

The FRD says exactly how the Project service behaves: who starts each action, what goes in, the steps, the
result, the errors, the states, and the acceptance checks that tests are written from. It is the spec of
spec-driven work: plans, tests and commits cite its IDs. Every FR-n links up to the PR-n it delivers (from
`.claude/docs/requirements/PRD.md`); the TRD links down to it.
Output: `.claude/docs/requirements/FRD.md`. Follow `recipe.md` and fill `template.md`, both in this folder.

## What an FR item looks like

One table row per behaviour: trigger (who, through REST `/api/v1`, internal REST `/internal/v1`, gRPC, an event,
or a durable job), input, what happens (short steps, then the result), errors, PR link, source. Real names are
welcome here: error codes with their HTTP status (`STALE_EDIT_REVISION`, 409), state values, event names
(`project.generation_requested.v1`).

- Take error codes from `src/main/java/org/kshrd/webblock/project/common/domain/error/ErrorCode.java` (the status
  comes from its `ErrorCategory`) and stored failure codes from
  `src/main/java/org/kshrd/webblock/project/domain/generation/model/FailureCodes.java`. A code a source assigns to
  Project but the enum lacks is Drift.
- Group rows into sections by area (for example: creating a website, pages and the builder, generation contracts,
  generation requests and admission, access checks for other services, catalog registration, deletion, admin).
  Every section starts with one plain sentence saying what the area is for.
- The Errors column holds code names only; their meaning goes once in "Errors at a glance", whose Status is the
  HTTP status or `stored` for a failure code kept on a record.
- Errors any request can get (401, 403, 404 for "not the owner", 400 `VALIDATION_FAILED`, 503
  `DEPENDENCY_UNAVAILABLE`) live once, in their own area with their own FRs and acceptance rows; other FRs do not
  repeat them. "Not the owner" answers the same 404 as "does not exist" (S2 §2.3, AUTH).
- `wc -w` at most 6000, tables included. In "Errors at a glance", codes with the same status and meaning share
  one row.

## Acceptance checks

Under each area's FR table, an "Acceptance" table gives every FR its checks, one row each, so an implementer
turns each row into one test (`@DisplayName("FR-7.2: ...")`):

| ID | Given | When | Then |
| --- | --- | --- | --- |
| FR-7.1 | a draft project the caller owns, edit revision 3 | the owner saves a page with revision 3 | 200; revision 4 |
| FR-7.2 | the same project, edit revision 4 | the owner saves a page with revision 3 | 409 `STALE_EDIT_REVISION`; page unchanged |

- IDs are `FR-<n>.<m>`, numbered from 1 under each FR, and follow the same stability rules as FR IDs.
- Each FR has one success row and one row per error it lists. A failing row names the real error code and
  status in "Then", plus what must not happen.
- "Then" says only what a test can observe: a status, a body field, a stored value, a state, an event in the
  outbox.

**States.** For each thing with a status give a table: Value | Meaning | Next. Take values from S1
(`project.project_status`, `project.generation_request_status`) and check them against
`domain/project/model/ProjectStatus.java` and `domain/generation/model/GenerationRequestStatus.java`. The
document's one Mermaid diagram sits in "Main flow"; it may be the generation request state diagram.

## Checks special to the FRD

- Every section starts with a one-sentence summary.
- Every FR has at least one success row and one row per listed error in an Acceptance table.
- "Read next" links `TRD.md`.
