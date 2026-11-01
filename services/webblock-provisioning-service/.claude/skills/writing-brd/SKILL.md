---
name: writing-brd
description: Use when the owner asks for the Provisioning BRD (business needs, why and for whom) or BRD.md is stale.
---

# Writing the Provisioning BRD

## Overview

The BRD says why the Provisioning service exists and who it serves, in words a website owner understands.
It is the top of the chain BRD -> PRD -> FRD -> TRD: its rows have no upward link, and every lower item links
up to a BR-n.
Output: `.claude/docs/requirements/BRD.md`. Follow `recipe.md` and fill `template.md`, both in this folder.

## What a BR item looks like

One plain sentence about an outcome for a person or team: "A failed update never takes a working site down."
Use business words: publish, go live, offline, plan, owner, admin. Endpoints, table names, status codes and code
names belong in the FRD and TRD.

- 8 to 15 BR items, numbered in one run across Goals, Business rules and Constraints.
- About one page: `wc -w` at most 1300, tables included.

## Sign of success

Each goal gets a measurable sign: a number, a time, or a yes/no check someone can run (yes/no is fine). Take
it from the source. When the source gives none, write `<your proposal> (proposed, Q-n)` and ask in Q-n.

## Who it serves

The "Who it serves" table covers the platform owner, website owners, site visitors, platform admins and each
other service S2 says Provisioning talks to.

## Checks special to the BRD

- Every Goals row has a sign of success.
- "Read next" links `PRD.md`.
