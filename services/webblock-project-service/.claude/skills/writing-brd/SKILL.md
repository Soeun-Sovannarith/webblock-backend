---
name: writing-brd
description: Use when the owner asks for a BRD, business requirements, or "why does the Project service exist and
  who is it for", or when .claude/docs/requirements/BRD.md is missing or out of date.
---

# Writing the Project BRD

## Overview

The BRD says why the Project service exists and who it serves, in words a website owner understands.
It is the top of the chain BRD -> PRD -> FRD -> TRD: its rows have no upward link, and every lower item links
up to a BR-n.
Output: `.claude/docs/requirements/BRD.md`. Follow `recipe.md` and fill `template.md`, both in this folder.

## What a BR item looks like

One plain sentence about an outcome for a person or team: "Two people editing the same site never silently
overwrite each other's work." Use business words: website, page, design, generate, publish, plan, owner, admin.
Endpoints, table names, status codes and class names belong in the FRD and TRD.

- 8 to 15 BR items, numbered in one run across Goals, Business rules and Constraints.
- About one page: `wc -w` at most 1300, tables included.

## Sign of success

Each goal gets a measurable sign: a number, a time, or a yes/no check someone can run (yes/no is fine). Take
it from the source (S1 limits such as "at most 4 live pages", PAY limits). When the source gives none, write
`<your proposal> (proposed, Q-n)` and ask in Q-n.

## Who it serves

The "Who it serves" table covers the platform owner, website owners (clients), site visitors (only through the
generated site), platform admins, and each service S2 section 5 says talks to Project: Provisioning, Payment,
AI Codegen, Catalog, Notification, Account.

## Scope boundaries

S1 says what Project does not own: hosting, releases and "is it live?" (Provisioning), catalog content
(Catalog), the generated code (AI Codegen). Put these under "Out (owned by others)".

## Checks special to the BRD

- Every Goals row has a sign of success.
- "Read next" links `PRD.md`.
