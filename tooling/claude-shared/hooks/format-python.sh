#!/usr/bin/env bash
# PostToolUse(Edit|Write|MultiEdit): format Python files with ruff if it is installed.
# Never blocks; formatting is best-effort.
command -v jq >/dev/null 2>&1 || exit 0
FILE=$(jq -r '.tool_input.file_path // empty')
case "$FILE" in *.py) ;; *) exit 0 ;; esac
[ -f "$FILE" ] || exit 0
if command -v ruff >/dev/null 2>&1; then
  ruff format -q "$FILE" >/dev/null 2>&1 || true
fi
exit 0
