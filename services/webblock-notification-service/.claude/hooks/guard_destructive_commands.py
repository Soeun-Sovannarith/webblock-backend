#!/usr/bin/env python3
"""Claude Code PreToolUse hook: block a few high-risk shell commands.

This hook is intentionally narrow. It does not replace code review or backups.
"""
import json
import re
import sys

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)

tool_input = payload.get("tool_input") or {}
command = str(tool_input.get("command") or "")

blocked = [
    (r"\bgit\s+reset\s+--hard\b", "git reset --hard can discard uncommitted work"),
    (r"\bgit\s+push\b.*\s--force(?:-with-lease)?\b", "force-pushing can rewrite shared history"),
    (r"\brm\s+-rf\s+/(?:\s|$)", "recursive deletion from filesystem root is blocked"),
    (r"\bdocker\s+compose\s+down\b.*\s-v\b", "docker compose down -v can delete persistent volumes"),
    (r"\bDROP\s+DATABASE\b", "dropping a database is destructive"),
    (r"\bTRUNCATE\s+TABLE\b", "truncating a table deletes all rows"),
]

for pattern, reason in blocked:
    if re.search(pattern, command, re.IGNORECASE):
        print(f"Blocked by project safety hook: {reason}. Ask the user before proceeding.", file=sys.stderr)
        sys.exit(2)

sys.exit(0)
