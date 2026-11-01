---
name: writing-trd
description: Use when the owner asks for the Provisioning TRD (how it is built, TR IDs) or TRD.md is missing or stale.
---

# Writing the Provisioning TRD

## Overview

The TRD says how the Provisioning service is built so that the FRD behaviour happens. Every TR-n links up to the
FR-n it serves (from `.claude/docs/requirements/FRD.md`) and to the decision record (ADR) behind it.
Output: `.claude/docs/requirements/TRD.md`. Follow `recipe.md` and fill `template.md`, both in this folder.

## What a TR item looks like

One row: how it is built ("The worker applies each site's objects by server-side apply under one field
manager"), the FR IDs it serves (or `All FR` for cross-cutting needs like logs), the decision record, and the
source.

- Decision holds record numbers only (`0013`), from those marked Current in `.claude/docs/decisions/README.md`.
  Source says where the rule is written (`0013 §4`, S2, a contract, a path).
- No record covers the TR, or only part of it: add `none (Q-n)` and ask in Q-n whether a record is needed.
- A Current record says the opposite of Notion or of the authentication contract: write the TR as that newer
  source says and put `<number> (differs, Q-n)` in Decision (Q-n may be an upper one, `BRD Q-2`).
- No source gives a number (for example a response time): the row says `none (Q-n)`, and Source names the
  open item (`OD-15`) if there is one.
- The record agrees with Notion but the code does not: keep the number; the gap goes under "Drift seen".

**Layout.**

- Sections, each opening with one plain sentence: Components, Stack, Data, Interfaces (REST, gRPC, RabbitMQ
  events, MCP), Security, Non-functional needs (performance, availability, observability), Deployment.
- Read `deploy/` (Helm chart) for Deployment.
- `wc -w` at most 3500, tables included.

**Data.** A bullet list names each table with one line on what a row is, linking S1 for the fields; TR rows
hold only the rules on how data is stored and kept. The database design lives only in Notion on purpose,
so the TRD points to it instead of copying it.

**Security.** Use the final auth model: the authentication contract
(`../project-service/.claude/docs/contracts/authentication-contract.md`), S2, and the auth notes in `ai/handoffs/`.
This final model wins over older records such as
`0019`: the Istio gateway checks the token, the first service checks it again, inner calls carry `x-user-id`
over mTLS, and "not the owner" answers 404.

## Checks special to the TRD

- Every TR row has a decision (or `none (Q-n)`).
- "Constitution check" marks each of Articles I to VIII pass, not applicable or conflict; a conflict goes to the
  owner before the TRD is approved.
- "Read next" links `DIRECTION.md`.
