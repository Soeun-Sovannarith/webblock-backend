#!/usr/bin/env bash
# Stop: before Claude ends a turn that changed Python, run ruff and the offline test suite.
# On failure, block the stop once and hand Claude the output; stop_hook_active prevents a retry loop.
input=$(cat)
[ "$(echo "$input" | jq -r '.stop_hook_active // false')" = true ] && exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
git status --porcelain -- '*.py' 'pyproject.toml' | grep -q . || exit 0
[ -x .venv/bin/pytest ] || exit 0

out=""
lint=$(.venv/bin/ruff check --quiet . 2>&1) || out+="ruff check . failed:"$'\n'"$lint"$'\n'
# not_found tests need Postgres; they are the documented exception and are skipped here.
tests=$(.venv/bin/pytest -q -x -p no:cacheprovider -k "not not_found" 2>&1) || out+="pytest failed:"$'\n'"$(echo "$tests" | tail -n 40)"$'\n'
[ -z "$out" ] && exit 0
jq -n --arg r "Python changed and the checks fail. Fix them before finishing, or tell the user why they can't pass:"$'\n'"$out" \
  '{decision: "block", reason: $r}'
