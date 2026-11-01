#!/usr/bin/env bash
# PreToolUse(Edit|Write|MultiEdit) - ROOT only: released contract files (…vN.yaml/json that are
# already committed) need explicit human approval for every edit. Returns an "ask" decision.
set -u
command -v jq >/dev/null 2>&1 || { echo "BLOCKED: jq is required by the .claude hooks (install jq)." >&2; exit 2; }

FILE=$(jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0
case "$FILE" in */contracts/*) ;; *) exit 0 ;; esac

BASE=${FILE##*/}
printf '%s' "$BASE" | grep -Eq '\.v[0-9]+\.' || exit 0
[ -e "$FILE" ] || exit 0
git -C "$(dirname "$FILE")" ls-files --error-unmatch -- "$FILE" >/dev/null 2>&1 || exit 0

jq -n --arg r "Released contract $BASE. Only additive, backward-compatible changes are allowed. A breaking change needs a NEW version file (e.g. v2), not an edit. Approve only if this edit is additive." \
  '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$r}}'
exit 0
