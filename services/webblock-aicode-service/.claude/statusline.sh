#!/usr/bin/env bash
# Status line: model · branch(+dirty) · Postgres up/down · build runner and codegen mode from .env (non-secret keys only).
input=$(cat)
model=$(echo "$input" | jq -r '.model.display_name // "Claude"')
dir=$(echo "$input" | jq -r '.workspace.project_dir // .workspace.current_dir // "."')
cd "$dir" 2>/dev/null || exit 0
branch=$(git branch --show-current 2>/dev/null)
[ -n "$(git status --porcelain 2>/dev/null | head -n1)" ] && branch="$branch*"
(exec 3<>/dev/tcp/127.0.0.1/5433) 2>/dev/null && db="db ●" || db="db ○"
val() { [ -f .env ] && grep -E "^$1=" .env | tail -n1 | cut -d= -f2-; }
printf '%s · %s · %s · runner=%s mode=%s' "$model" "${branch:-no-git}" "$db" "$(val BUILD_RUNNER || echo '?')" "$(val CODEGEN_MODE || echo '?')"
