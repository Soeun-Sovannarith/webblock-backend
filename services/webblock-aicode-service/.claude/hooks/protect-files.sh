#!/usr/bin/env bash
# PreToolUse (Write|Edit|MultiEdit): keep Claude out of secrets, generated files and already-applied migrations.
f=$(jq -r '.tool_input.file_path // empty')
[ -n "$f" ] || exit 0
rel=${f#"$CLAUDE_PROJECT_DIR"/}

decide() {
  jq -n --arg d "$1" --arg r "$2" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: $d, permissionDecisionReason: $r}}'
  exit 0
}

case "$rel" in
  .env|.env.local)
    decide deny "$rel holds secrets. Document new variables in .env.example and ask the user to set the value." ;;
  Pipfile.lock)
    decide deny "Pipfile.lock is generated: add dependencies to pyproject.toml and run 'pipenv lock'." ;;
  .venv/*|*.egg-info/*|var/*|.pytest_cache/*|.ruff_cache/*)
    decide deny "$rel is generated or runtime state, not source." ;;
  app/templates/site/app/page.tsx)
    decide deny "app/page.tsx clashes with the catch-all route app/[[...slug]]/page.tsx (glm.RESERVED_PATHS)." ;;
  sql/*.sql)
    # A committed migration has already run on teammates' databases; changing it only affects fresh ones.
    if git -C "$CLAUDE_PROJECT_DIR" ls-files --error-unmatch "$rel" >/dev/null 2>&1; then
      decide ask "$rel is a committed migration that existing databases already applied. Prefer a new sql/00N_*.sql (see the new-migration skill); edit this one only to fix something that is broken on fresh databases."
    fi ;;
esac
exit 0
