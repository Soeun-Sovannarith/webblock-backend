---
name: adr-writer
description: Use when recording an architecture decision (technology choice, gateway vs Istio, messaging pattern, auth approach) as an ADR in docs/decisions/.
---

# Write an ADR

## Purpose
Record one significant architecture decision with its context, options and consequences so later readers know why, not just what. Index: `docs/decisions/README.md`.

## When to use / when NOT to use
- Use for decisions that are costly to reverse or affect several services: technology, gateway versus Istio, messaging pattern, auth approach, data ownership.
- Do NOT use for routine implementation choices, bug fixes or per-service conventions (those belong in the service `CLAUDE.md`).

## Inputs to confirm with the user
Decision topic, deciders and date, options already discussed, constraints (team, cost, deadline), affected services and contracts, current status (Proposed or Accepted).

## Procedure
1. List `docs/decisions/` and take the next number: `NNNN-short-kebab-title.md` (existing: 0001, 0002).
2. Gather facts from the repo (read `docs/architecture.md`, existing ADRs, contracts). Unknowns become `TODO(owner):`; never invent them.
3. Fill the template with at least two real options and honest cons.
4. Add a row to the table in `docs/decisions/README.md`.
5. If the decision replaces an Accepted ADR: do not edit its decision; create the new ADR and only change the old Status line to `Superseded by NNNN`.
6. Link affected contracts and services. Update `docs/architecture.md` only if the picture changes.
7. Run the `docs-reviewer` agent if available; keep the text short and secret-free.

## Templates
```
# NNNN. <Title>
- Status: Proposed | Accepted | Superseded by NNNN
- Date: YYYY-MM-DD
- Deciders: <names or roles>

## Context
Problem, constraints, forces.

## Decision
We will <choice>, because <main reason>.

## Options considered
| Option | Pros | Cons |
|---|---|---|
| A | | |
| B | | |

## Consequences
Good, bad, risks, impact on contracts and services, follow-up tasks (owner, date).
```

## Checklist
- [ ] Number unique and sequential; file name kebab-case
- [ ] Status, Date, Deciders present
- [ ] At least two options with cons
- [ ] Consequences name affected services and follow-ups
- [ ] README table updated; no accepted ADR rewritten
- [ ] No secrets, real hostnames or personal data

## Common mistakes
Writing the ADR after the fact with invented context; one-option ADRs; rewriting an Accepted decision; mixing several decisions in one file; copying schemas instead of linking contracts.

## Definition of done
ADR file created, index updated, links added, status honest (Proposed until the deciders agree).

## Related
`docs/decisions/0001-keycloak-auth.md`, `docs/decisions/0002-gateway-vs-istio.md`; rule `docs-style.md`; agent `docs-reviewer`.
