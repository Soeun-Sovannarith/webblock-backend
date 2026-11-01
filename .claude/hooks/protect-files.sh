#!/usr/bin/env bash
# PreToolUse(Edit|Write|MultiEdit|NotebookEdit): block edits to secrets, production config,
# CI/security config, and Claude's own guardrail files. Exit 2 = block.
set -u
command -v jq >/dev/null 2>&1 || { echo "BLOCKED: jq is required by the .claude hooks (install jq)." >&2; exit 2; }

FILE=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty')
[ -z "$FILE" ] && exit 0

LOWER=$(printf '%s' "$FILE" | tr '[:upper:]' '[:lower:]')
BASE=${LOWER##*/}

block() {
  echo "BLOCKED by protect-files: $FILE - $1" >&2
  echo "A human must change this file by hand. Describe the change you need instead." >&2
  exit 2
}

case "$BASE" in
  .env.example|.env.sample|.env.template) exit 0 ;;
  .env|.env.*)                      block "environment file (may hold secrets)" ;;
  *.pem|*.key|*.p12|*.pfx|*.jks|*.keystore|id_rsa*|id_ed25519*|*.kdbx) block "key or credential file" ;;
  application-prod*|application-production*|bootstrap-prod*) block "production configuration" ;;
  jenkinsfile|.gitlab-ci.yml|.gitleaks.toml|codeowners|.mcp.json) block "CI / security / MCP configuration" ;;
esac

case "$LOWER" in
  */secrets/*|*/.ssh/*|*/.aws/*|*/.gnupg/*)   block "secrets directory" ;;
  */.github/workflows/*)                       block "CI workflow" ;;
  */.claude/settings*.json|*/.claude/hooks/*)  block "Claude guardrail configuration" ;;
esac

exit 0
