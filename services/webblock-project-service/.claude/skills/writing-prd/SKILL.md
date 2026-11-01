---
name: writing-prd
description: Use when the owner asks for a PRD, product requirements, or "what does the Project service offer each
  kind of user", or when .claude/docs/requirements/PRD.md is missing or out of date.
---

# Writing the Project PRD

## Overview

The PRD says what the Project service offers each kind of user, and when an offer counts as done.
Every PR-n links up to the BR-n it serves (from `.claude/docs/requirements/BRD.md`); the FRD links down to it.
Output: `.claude/docs/requirements/PRD.md`. Follow `recipe.md` and fill `template.md`, both in this folder.

## What a PR item looks like

One offer a user can see or use, in plain words: "A website owner adds a page to the site, up to the page limit."
Then the BR it serves, "Done when ...", priority, phase and source. Endpoint paths, table names and error codes
belong in the FRD.

- Group by user type: website owner, admin, other services (Provisioning, Payment, AI Codegen, Catalog,
  Notification), then visitors and operators.
- 10 to 25 PR items; `wc -w` at most 2000, tables included.

**Done when** is a check the owner could watch happen. If it waits on an open BRD question, write the check
for the suggested answer and add "see BRD Q-n".

**Priority** is Must when Notion, a contract or an Accepted decision record requires it, or when a BRD goal
cannot be met without it. Otherwise choose Must, Should or Could yourself, add `*` ("lead suggestion"), and list
all starred items in one owner question.

**Phase** is the phase that first delivered or will deliver the item: `J0` (foundation), `J1` (wizard, projects,
pages, catalog, access check), `Auth`, `J2` (contracts, capture, admission, codegen input), `J3` (Git hosting and
publication), `J4` (templates, scaffold, Codegen exchange, `source_published`), `J5` (deletion, production
hardening, observability) or `Later` (no plan names it). Take the phase from the handoffs in `ai/handoffs/` and
the plans in `.claude/docs/plans/`. When parts of an offer land in different phases, split it into two items.

## Checks special to the PRD

- Every PR row has a BR, a "Done when", a priority, a phase and a source.
- "Read next" links `FRD.md`.
