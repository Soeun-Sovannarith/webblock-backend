---
description: Check (and optionally apply) shared .claude files in every service
argument-hint: [check | apply]
disable-model-invocation: true
allowed-tools: Bash(tooling/sync-claude.sh --check)
---

Report drift between `tooling/claude-shared/` (master files) and the copies in the repo root `.claude/` and every `services/*/.claude/`.

1. Run `tooling/sync-claude.sh --check`. Exit code 1 means drift.
2. Summarize: for each line, status (`DRIFT` = copy differs, `MISSING` = copy absent), file path and service.
3. If the argument is `apply`, do NOT run `tooling/sync-claude.sh` yourself. It overwrites shared files in every service, so tell the user to review the drift and run it manually.
4. Remind the user: edit the master files in `tooling/claude-shared/`, never the copies in services; root-only files live in `.claude/` and are not synced.
5. If a drift is a hand edit made in a service copy, say the edit will be lost on sync and should be moved to the master.

Output format:
- **Result**: in sync, or N files drifted
- Table: Service | File | Status
- **Next step**: the exact command for the user (`tooling/sync-claude.sh`) or "nothing to do"

Stop conditions: the script is missing or fails (report the error, do not work around); never edit files.
