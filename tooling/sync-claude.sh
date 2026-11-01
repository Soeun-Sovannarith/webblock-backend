#!/usr/bin/env bash
# Copies the shared Claude files from tooling/claude-shared/ into:
#   - the repo root .claude/ (common + root-only hooks)
#   - services/<service>/.claude/ (common + stack pack + hooks + settings.json)
#
# Usage:
#   tooling/sync-claude.sh            copy (overwrites shared files in every service)
#   tooling/sync-claude.sh --check    report drift only, change nothing (exit 1 if drift) - use in CI
#
# Rule: edit the MASTER files in tooling/claude-shared/, never the copies in services.
# Service-specific files (different names) are never touched. Needs bash 3.2+.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
SHARED="$ROOT/tooling/claude-shared"
MODE=${1:-sync}
DRIFT=0

stack_of() {   # service folder name -> spring | infra | python | unknown
  case "$1" in
    webblock-user-service|webblock-payment-service|webblock-notification-service|webblock-project-service) echo spring ;;
    api-gateway|discovery-service|config-server) echo infra ;;
    webblock-aicode-service|webblock-provissioning-service) echo python ;;
    *) echo unknown ;;
  esac
}

copy_file() {  # src dest
  local src=$1 dest=$2
  if [ "$MODE" = "--check" ]; then
    if [ ! -f "$dest" ]; then echo "MISSING $dest"; DRIFT=1
    elif ! cmp -s "$src" "$dest"; then echo "DRIFT   $dest"; DRIFT=1
    fi
  else
    mkdir -p "$(dirname "$dest")"
    cp "$src" "$dest"
  fi
}

copy_tree() {  # src_dir dest_dir
  [ -d "$1" ] || return 0
  local f
  while IFS= read -r f; do
    copy_file "$1/$f" "$2/$f"
  done < <(cd "$1" && find . -type f ! -name '.DS_Store' | sed 's|^\./||' | sort)
}

copy_settings() { # service-name stack dest_claude_dir
  # a service-specific file (settings.<service>.json) wins over the stack default
  local src="$SHARED/settings.$1.json"
  [ -f "$src" ] || src="$SHARED/settings.$2.json"
  copy_file "$src" "$3/settings.json"
  copy_file "$SHARED/settings.local.json.example" "$3/settings.local.json.example"
}

copy_hooks() { # dest_claude_dir hook...
  local dest=$1; shift
  local h
  for h in "$@"; do
    copy_file "$SHARED/hooks/$h" "$dest/hooks/$h"
    [ "$MODE" = "--check" ] || chmod +x "$dest/hooks/$h"
  done
}

BASE_HOOKS="block-dangerous-bash.sh protect-files.sh scope-guard.sh secret-scan.sh session-start.sh"

# ---- repo root ----
copy_tree "$SHARED/common" "$ROOT/.claude"
copy_hooks "$ROOT/.claude" $BASE_HOOKS protect-contracts.sh validate-contracts.sh
copy_file "$SHARED/settings.local.json.example" "$ROOT/.claude/settings.local.json.example"

# ---- services ----
for dir in "$ROOT"/services/*/; do
  [ -d "$dir" ] || continue
  name=$(basename "$dir")
  stack=$(stack_of "$name")
  dest="$dir.claude"
  case "$stack" in
    spring)
      copy_tree "$SHARED/common" "$dest"
      copy_tree "$SHARED/spring" "$dest"
      copy_hooks "$dest" $BASE_HOOKS protect-migrations.sh
      copy_settings "$name" spring "$dest"
      ;;
    infra)
      copy_tree "$SHARED/common" "$dest"
      copy_file "$SHARED/spring/rules/testing.md"         "$dest/rules/testing.md"
      copy_file "$SHARED/spring/rules/spring-security.md" "$dest/rules/spring-security.md"
      copy_file "$SHARED/spring/agents/test-writer-java.md" "$dest/agents/test-writer-java.md"
      copy_hooks "$dest" $BASE_HOOKS protect-migrations.sh
      copy_settings "$name" spring "$dest"
      ;;
    python)
      copy_tree "$SHARED/common" "$dest"
      copy_tree "$SHARED/python" "$dest"
      copy_hooks "$dest" $BASE_HOOKS format-python.sh
      copy_settings "$name" python "$dest"
      ;;
    *)
      echo "SKIP    services/$name (not listed in stack_of; add it, see skill service-bootstrap)"
      ;;
  esac
done

if [ "$MODE" = "--check" ]; then
  if [ "$DRIFT" -eq 0 ]; then echo "OK: shared Claude files are in sync."; exit 0; fi
  echo "Drift found. Review it, then run tooling/sync-claude.sh to re-copy the masters."
  exit 1
fi
echo "Synced shared Claude files into the repo root and all services."
