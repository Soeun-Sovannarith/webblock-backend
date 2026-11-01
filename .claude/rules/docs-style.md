---
paths:
  - "docs/**"
  - "**/*.md"
---

# Documentation style

## Truthfulness
- Docs describe what is true now. If code or a contract disagrees, fix the doc or flag the mismatch; do not invent behavior.
- Unknowns go on a visible `TODO(owner):` line, not a guess.

## Form
- Short, concrete sentences. Tables for service, endpoint and event lists; Mermaid for diagrams.
- Link to contracts instead of copying schemas.
- Each `CLAUDE.md` stays under 120 lines: facts and rules needed every session; long text goes to `docs/` and is linked.

## Decisions
- One ADR per decision in `docs/decisions/NNNN-title.md` (skill `adr-writer`) with Context, Decision, Consequences, Status. Never rewrite an accepted ADR; supersede it.

## Safety
- Fake data and placeholders only: `<client-secret>`, `user@example.test`. No real tokens, passwords, production hostnames or personal data.

Example: write "Port: TODO(Vannda)" rather than "Port: 8081" when the owner has not confirmed it.
