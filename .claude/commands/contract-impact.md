---
description: Show which services are affected if a contract changes
argument-hint: [contract file or event name, e.g. user.v1.yaml or user.registered]
allowed-tools: Read, Grep, Glob, Bash(git status *), Bash(git diff *), Bash(git log *)
---

Analyze the impact of changing: $ARGUMENTS

1. Open the contract in `contracts/` (`openapi/` or `events/`) and `docs/service-map.md` to identify the provider and every listed consumer. If the contract is not found, list `contracts/` and ask which one.
2. Search every service under `services/` read-only for Feign clients, `httpx` calls, DTOs or Pydantic models, listeners and publishers that use the affected paths, fields or routing keys. Do not rely on the service map alone.
3. Classify the planned change as additive or breaking and say why (rules in `contracts/README.md`). If the change is not yet described, ask for it.
4. Check `contracts/CHANGELOG.md` for related recent changes.
5. Propose the safe rollout order: contract, provider, consumers, cleanup of the old version (expand/contract).

Output format:
- **Verdict**: additive or breaking, with reason
- Table: Consumer | Where used (file:line) | Impact | Needed change | Owner
- **Rollout order** and rollback note
- **Not found**: consumers in the map with no usage found, and usages not in the map

Stop conditions: contract or change unclear (ask one question); never edit any file.
