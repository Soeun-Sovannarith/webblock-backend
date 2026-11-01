#!/usr/bin/env bash
# PostToolUse (Write|Edit): lint the edited Python file with the project's ruff.
# Exit 2 sends ruff's findings back to Claude so it fixes them; anything else is silent.
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
case "$f" in
  *.py) ;;
  *) exit 0 ;;
esac
ruff="$CLAUDE_PROJECT_DIR/.venv/bin/ruff"
[ -x "$ruff" ] || exit 0
"$ruff" check --quiet "$f" >&2 || exit 2
