#!/usr/bin/env bash
# PreToolUse(Edit|Write|MultiEdit): applied Flyway migrations are immutable.
# Blocks edits to a migration file that is already tracked by git. New files are allowed,
# repeatable migrations (R__*) are allowed. Exit 2 = block.
set -u
command -v jq >/dev/null 2>&1 || { echo "BLOCKED: jq is required by the .claude hooks (install jq)." >&2; exit 2; }

FILE=$(jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0

case "$FILE" in
  */db/migration/*|*/db/migrations/*) ;;
  *) exit 0 ;;
esac

BASE=${FILE##*/}
case "$BASE" in R__*) exit 0 ;; esac
[ -e "$FILE" ] || exit 0                      # new migration: allowed

if git -C "$(dirname "$FILE")" ls-files --error-unmatch -- "$FILE" >/dev/null 2>&1; then
  echo "BLOCKED by protect-migrations: $BASE is already committed and may be applied." >&2
  echo "Never edit an applied migration (Flyway checksum mismatch). Add a NEW versioned file:" >&2
  echo "V<next>__<description>.sql, and use the flyway-migration skill." >&2
  exit 2
fi
exit 0
