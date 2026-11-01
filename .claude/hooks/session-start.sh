#!/usr/bin/env bash
# SessionStart: stdout is added to Claude's context. Keep it short.
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
cat <<EOF
Session reminders (from .claude/hooks/session-start.sh):
- Branch: ${BRANCH}. Never commit directly to main/master/develop.
- Plan first: use /plan and wait for approval before editing files.
- Stay in scope: edit only this folder; other services are read-only references.
- Contracts first: API/event changes go through contracts/ (contract-change skill) before code.
- Bugs: follow the bug-investigation skill (evidence, reproduce, hypothesis, isolate, then fix).
- Never read or write .env, keys, application-prod*, or put secrets in code, logs, or docs.
EOF
exit 0
