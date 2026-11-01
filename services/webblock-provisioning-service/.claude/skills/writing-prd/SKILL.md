---
name: writing-prd
description: Use when the owner asks for the Provisioning PRD (what each kind of user gets) or PRD.md is stale.
---

# Writing the Provisioning PRD

## Overview

The PRD says what the Provisioning service offers each kind of user, and when an offer counts as done.
Every PR-n links up to the BR-n it serves (from `.claude/docs/requirements/BRD.md`); the FRD links down to it.
Output: `.claude/docs/requirements/PRD.md`. Follow `recipe.md` and fill `template.md`, both in this folder.

## What a PR item looks like

One offer a user can see or use, in plain words: "A website owner sees why a version failed." Then the BR it
serves, "Done when ...", priority, phase and source. Endpoint paths, table names and error codes belong in the
FRD.

- Group by user type: website owner, admin, other services, then visitors, operators and team agents (MCP).
- 10 to 25 PR items; `wc -w` at most 2000, tables included.

**Done when** is a check the owner could watch happen. If it waits on an open BRD question, write the check
for the suggested answer and add "see BRD Q-n".

**Priority** is Must when Notion, or a Current decision requires it, or when a BRD goal cannot be met
without it. Otherwise choose Must, Should or Could yourself, add `*` ("lead suggestion"), and list all starred
items in one owner question.

**Phase** is the phase that first delivered or will deliver the item: `Prototype` (0014), `Phase A` (0021),
`Phase B` (0022), `Phase C` (0023), `Production` (0016 gates) or `Later` (no plan names it). When parts of an
offer land in different phases, split it into two items.

## Checks special to the PRD

- Every PR row has a BR, a "Done when", a priority, a phase and a source.
- "Read next" links `FRD.md`.
