---
name: service-bootstrap
description: Use when adding a NEW service to the monorepo. Creates its CLAUDE.md and .claude/ from the shared packs and registers it in docs, contracts and CODEOWNERS.
---

# Bootstrap a new service

## Purpose
Add a service with the same guardrails as the others: synced `.claude/`, a short `CLAUDE.md`, a contract skeleton and registration in docs.

## When to use / when NOT to use
- Use only when a NEW folder under `services/` is being created.
- Do NOT use to change an existing service, to write source or build files (only if asked), or to edit hooks or settings.

## Inputs to confirm with the user
Service name and folder (`services/webblock-<name>-service`), stack (`spring` or `python`; gateway, discovery and config are `infra`), owner, responsibility, database, events published or consumed, REST consumers.

## Procedure
1. Create the folder and decide the stack.
2. Add the service to the `stack_of` function in `tooling/sync-claude.sh`. This is a script change: show the diff and get approval first.
3. Ask the user to run `tooling/sync-claude.sh` (it overwrites shared files in every service; Claude does not run it). Then run `tooling/sync-claude.sh --check`.
4. Write `services/<name>/CLAUDE.md` from a sibling of the same stack: owner, responsibility, commands, layout, contracts, events, scope, don'ts. Facts only; unknowns as `TODO(owner):`. Keep under 120 lines.
5. Add service-specific rules or skills only for real needs, with unique names (never edit synced shared files).
6. Create `contracts/openapi/<name>.v1.yaml` with the Keycloak bearer scheme and a `$ref` to the shared `Problem` schema; add events under `contracts/events/` if any (skill `contract-change`).
7. Update `docs/service-map.md`, the root `CLAUDE.md` service table, and `CODEOWNERS`; Claude cannot edit CODEOWNERS, so write the exact line for the user.
8. Report the `--check` result and the list of TODOs.

## Templates
CODEOWNERS line for the user to add:
```
/services/webblock-<name>-service/   @<github-user>
/contracts/openapi/<name>.v1.yaml    @<github-user>
```
Service-map row:
```
| <Name> | `services/webblock-<name>-service` | <stack> | <owner> | PostgreSQL `TODO(owner)` | TODO | yes |
```

## Checklist
- [ ] `stack_of` updated; sync run by the user; `--check` clean
- [ ] `CLAUDE.md` short and factual
- [ ] Contract skeleton has security scheme and Problem schema
- [ ] Docs and root table updated; CODEOWNERS line handed to the user
- [ ] No secrets, no source code unless asked

## Common mistakes
Hand-copying `.claude/` instead of syncing; editing synced files in a service; guessing ports or database names; forgetting CODEOWNERS; running the sync script from Claude.

## Definition of done
New service is in sync with shared packs, registered in docs and contracts, and open TODOs are listed for the owner.

## Related
`tooling/sync-claude.sh`, command `/sync-claude`, skill `contract-change`, `docs/service-map.md`, rule `docs-style.md`.
