#!/usr/bin/env bash
# Notification: desktop alert when Claude is waiting for input. Personal: wired up in settings.local.json, not settings.json.
msg=$(jq -r '.message // "Claude Code needs your attention"')
if command -v osascript >/dev/null; then
  osascript -e "display notification \"${msg//\"/\'}\" with title \"Claude Code: webblock harness\"" >/dev/null 2>&1
elif command -v notify-send >/dev/null; then
  notify-send "Claude Code: webblock harness" "$msg"
fi
exit 0
