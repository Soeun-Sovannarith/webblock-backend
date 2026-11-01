#!/usr/bin/env bash
# PostToolUse (Write|Edit|MultiEdit): static idempotency and schema-qualification checks for sql/*.sql.
# Alembic runs each file once per database, but a file must survive a re-run (stamp + upgrade, hand-applied production).
# Heuristic, not a parser: it catches the common slips. Exit 2 sends the problems back to Claude.
f=$(jq -r '.tool_input.file_path // .tool_response.filePath // empty')
case "$f" in */sql/*.sql) ;; *) exit 0 ;; esac
[ -f "$f" ] || exit 0
case "$(basename "$f")" in 000_reset_legacy.sql) exit 0 ;; esac

sql=$(sed 's/--.*$//' "$f")   # drop comments
problems=""
check() { [ -n "$1" ] && problems+="$2"$'\n'"$1"$'\n'; }

head -n1 "$f" | grep -q '^--' || problems+="Start the file with a -- comment saying what it changes and why it is idempotent."$'\n'
check "$(echo "$sql" | grep -niE 'create[[:space:]]+(table|schema|(unique[[:space:]]+)?index)[[:space:]]' | grep -viE 'if[[:space:]]+not[[:space:]]+exists')" \
  "CREATE TABLE/SCHEMA/INDEX without IF NOT EXISTS:"
check "$(echo "$sql" | grep -niE 'add[[:space:]]+column[[:space:]]' | grep -viE 'if[[:space:]]+not[[:space:]]+exists')" \
  "ADD COLUMN without IF NOT EXISTS:"
check "$(echo "$sql" | grep -niE 'add[[:space:]]+value[[:space:]]' | grep -viE 'if[[:space:]]+not[[:space:]]+exists')" \
  "ALTER TYPE ... ADD VALUE without IF NOT EXISTS:"
if echo "$sql" | grep -qiE 'create[[:space:]]+type[[:space:]]' && ! echo "$sql" | grep -qi 'duplicate_object'; then
  problems+="CREATE TYPE must sit in DO \$\$ BEGIN ... EXCEPTION WHEN duplicate_object THEN NULL; END \$\$;"$'\n'
fi
inserts=$(echo "$sql" | grep -ciE 'insert[[:space:]]+into')
conflicts=$(echo "$sql" | grep -ciE 'on[[:space:]]+conflict')
[ "$inserts" -gt "$conflicts" ] && problems+="$inserts INSERT(s) but $conflicts ON CONFLICT clause(s): every seed needs ON CONFLICT (...) DO NOTHING/UPDATE."$'\n'
check "$(echo "$sql" | grep -niE '(create[[:space:]]+table([[:space:]]+if[[:space:]]+not[[:space:]]+exists)?|insert[[:space:]]+into|alter[[:space:]]+table([[:space:]]+if[[:space:]]+exists)?|references|^[[:space:]]*update|delete[[:space:]]+from)[[:space:]]+[a-z_]+([[:space:]]|\(|$)' | grep -viE '(table|into|references|update|from)[[:space:]]+(if|only)[[:space:]]')" \
  "Unqualified table name (write <schema>.<table>):"

[ -z "$problems" ] && exit 0
printf 'sql-check (%s):\n%s' "$(basename "$f")" "$problems" >&2
exit 2
