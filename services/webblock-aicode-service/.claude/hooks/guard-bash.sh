#!/usr/bin/env bash
# PreToolUse (Bash): stop shell commands that destroy shared state, leak secrets or bypass the dependency workflow.
# "deny" blocks the command and tells Claude why; "ask" hands the decision to the user even if the command is allowed.
cmd=$(jq -r '.tool_input.command // empty')
[ -n "$cmd" ] || exit 0

decide() {
  jq -n --arg d "$1" --arg r "$2" \
    '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: $d, permissionDecisionReason: $r}}'
  exit 0
}

# Postgres volume holds every project's baseline; artifacts hold the SOURCE_ARCHIVE that Flow 2 starts from.
echo "$cmd" | grep -qE 'docker[ -]compose[^|;&]*down[^|;&]*(-v|--volumes)' &&
  decide deny "This deletes the Postgres volume (all projects, runs and baselines). Ask the user to run it themselves if they really want a reset."
echo "$cmd" | grep -qE 'rm[[:space:]]+(-[a-zA-Z]*[rR][a-zA-Z]*|--recursive)[[:space:]]+([^|;&]*[[:space:]])?(\./)?((var|\.venv|app|sql|tests|\.claude)/?([[:space:]]|$)|var/artifacts)' &&
  decide deny "Recursive delete of a project directory (var/artifacts holds the source snapshots later updates start from). Delete specific files instead."

# Git: history on main is shared.
echo "$cmd" | grep -qE 'git[[:space:]]+push[^|;&]*(--force|[[:space:]]-f([[:space:]]|$)|--force-with-lease)' &&
  decide deny "Force-push rewrites shared history. Push normally or ask the user."
echo "$cmd" | grep -qE 'git[[:space:]]+(reset[[:space:]]+--hard|clean[[:space:]]+-[a-zA-Z]*f)' &&
  decide ask "This discards uncommitted work. Confirm with the user first."

# Secrets: .env holds GLM/GitHub tokens. .env.example is fine.
echo "$cmd" | grep -qE '(cat|less|more|head|tail|bat|grep|rg|sed|awk|cp|scp|curl)[^|;&]*([[:space:]]|/|^)\.env([[:space:]]|$|[^.a-zA-Z_])' &&
  decide deny "Reading or copying .env would expose secrets. Use .env.example for variable names, or ask the user for a specific non-secret value."
echo "$cmd" | grep -qE '(^|[[:space:]])(printenv|env)([[:space:]]*$|[[:space:]]*\|)' &&
  decide deny "Dumping the environment can expose tokens. Print the one variable you need instead."

# Dependencies are declared in pyproject.toml (Pipfile installs the package editable).
if echo "$cmd" | grep -qE '(^|[[:space:]]|/)pip3?[[:space:]]+install' && ! echo "$cmd" | grep -qE 'install[[:space:]]+(-e|--editable)[[:space:]]'; then
  decide deny "Add the dependency to pyproject.toml, then run 'pip install -e \".[dev]\"' (and 'pipenv lock'). Ad-hoc installs drift from the declared environment."
fi

# Destructive SQL against the dev database.
echo "$cmd" | grep -qiE 'psql[^|]*(drop[[:space:]]+(schema|table|type|database)|truncate[[:space:]])' &&
  decide ask "Destructive SQL against the shared dev database. Confirm with the user; schema changes belong in a new sql/00N_*.sql."

exit 0
