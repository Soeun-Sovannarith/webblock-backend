---
name: writing-trd
description: Use when the owner asks for a TRD, technical requirements, or "how is the Project service built"
  (modules, stack, tables, REST, gRPC, RabbitMQ outbox, auth, performance, deployment), or when
  .claude/docs/requirements/TRD.md is missing or out of date.
---

# Writing the Project TRD

## Overview

The TRD says how the Project service is built so that the FRD behaviour happens. Every TR-n links up to the
FR-n it serves (from `.claude/docs/requirements/FRD.md`) and to the decision record behind it.
Output: `.claude/docs/requirements/TRD.md`. Follow `recipe.md` and fill `template.md`, both in this folder.

## What a TR item looks like

One row: how it is built ("Events leave through the Namastack outbox in the same transaction as the change"), the
FR IDs it serves (or `All FR` for cross-cutting needs like logs), the decision record, and the source.

- Decision holds record numbers only (`0001`), from those marked Accepted in `.claude/docs/decisions/README.md`.
  Source says where the rule is written (`0001`, S2 §4.3, a contract, a path).
- No record covers the TR, or only part of it: add `none (Q-n)` and ask in Q-n whether a record is needed.
- An Accepted record says the opposite of Notion or of the authentication contract: write the TR as that newer
  source says and put `<number> (differs, Q-n)` in Decision (Q-n may be an upper one, `BRD Q-2`).
- No source gives a number (for example a response time): the row says `none (Q-n)`.
- The record agrees with Notion but the code does not: keep the number; the gap goes under "Drift seen".

**Layout.**

- Sections, each opening with one plain sentence: Modules and layers, Stack, Data, Interfaces (REST, gRPC,
  RabbitMQ events), Security, Non-functional needs (performance, availability, observability), Deployment.
- Modules and layers: read `.claude/CLAUDE.md` section 2, S2 §6.1 and the rules the build enforces
  (`src/test/java/org/kshrd/webblock/project/architecture/ArchitectureRulesTest.java`, `LayoutRuleTest.java`).
- Stack: `gradle/libs.versions.toml`, `build.gradle` and `.claude/TECH_STACK.md`; never copy a version from memory.
- Deployment: `.claude/docs/runbooks/` (migrations, database, catalog database, outbox, Kind exit) and S2 §7. This
  repo has no Helm chart; say where the deployment is defined, or ask.
- `wc -w` at most 3500, tables included.

**Data.** A bullet list names each table with one line on what a row is, linking S1 (and S3 for the catalog) for
the fields; TR rows hold only the rules on how data is stored and kept (roles, frozen columns, inbox and outbox,
migrations as a separate job). The database design lives only in Notion on purpose, so the TRD points to it
instead of copying it.

**Security.** Use the final auth model: the authentication contract, S2 §2.4 and decision `0002`. The Istio
gateway checks the token, Project checks it again (`common/infrastructure/security/`), `/internal/**` takes no
token (the mesh mTLS policy is its gate), inner calls carry the user ID, and "not the owner" answers 404.

## Checks special to the TRD

- Every TR row has a decision (or `none (Q-n)`).
- "Read next" links `DIRECTION.md`.
