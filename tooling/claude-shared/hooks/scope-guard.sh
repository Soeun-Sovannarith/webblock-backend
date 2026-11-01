#!/usr/bin/env bash
# PreToolUse(Edit|Write|MultiEdit|NotebookEdit): keep edits inside the folder where the
# session started ($CLAUDE_PROJECT_DIR). Inside a service this means "this service only";
# a service may also touch its OWN contract files under <repo>/contracts/.
# Exit 2 = block.
set -u
command -v jq >/dev/null 2>&1 || { echo "BLOCKED: jq is required by the .claude hooks (install jq)." >&2; exit 2; }

FILE=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')
[ -z "$FILE" ] && exit 0

rp() {
  realpath -m -- "$1" 2>/dev/null || python3 -I -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$1"
}

ROOT=$(rp "${CLAUDE_PROJECT_DIR:-$PWD}")
TARGET=$(rp "$FILE")

case "$TARGET" in
  "$ROOT"|"$ROOT"/*) exit 0 ;;
  /tmp/claude-*|"${TMPDIR:-/tmp}"/claude-*) exit 0 ;;   # Claude's scratchpad
esac

REPO=$(git -C "$ROOT" rev-parse --show-toplevel 2>/dev/null || true)
NAME=${ROOT##*/}
SLUG=${NAME#webblock-}
SLUG=${SLUG%-service}

if [ -n "$REPO" ] && [ "$ROOT" != "$REPO" ]; then
  case "$TARGET" in
    "$REPO"/contracts/*/"$SLUG"*) exit 0 ;;   # this service's own contract files
  esac
fi

echo "BLOCKED by scope-guard: $FILE is outside $NAME." >&2
echo "Work only inside this folder. If another service or shared file must change," >&2
echo "stop and describe the change for its owner instead of editing it." >&2
exit 2
