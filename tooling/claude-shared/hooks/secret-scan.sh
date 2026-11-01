#!/usr/bin/env bash
# PostToolUse(Edit|Write|MultiEdit): scan the file Claude just wrote for hard-coded secrets.
# Exit 2 = feed the finding back to Claude so it removes the secret.
# Add `allowlist-secret` on a line to exempt a known-safe test value.
set -u
command -v jq >/dev/null 2>&1 || { echo "BLOCKED: jq is required by the .claude hooks (install jq)." >&2; exit 2; }

FILE=$(jq -r '.tool_input.file_path // empty')
[ -z "$FILE" ] && exit 0
[ -f "$FILE" ] || exit 0

case "${FILE##*/}" in .env.example|.env.sample|.env.template) exit 0 ;; esac

report() {
  echo "SECRET SCAN: possible hard-coded secret in $FILE ($1)." >&2
  echo "Remove it. Read it from config-server / environment / Keycloak instead, never commit it." >&2
  exit 2
}

if command -v gitleaks >/dev/null 2>&1; then
  if gitleaks dir "$FILE" --no-banner --redact -q >/dev/null 2>&1; then :; else
    # gitleaks exits non-zero on leaks (and on unknown subcommand in old versions)
    if gitleaks detect --no-git --source "$FILE" --no-banner --redact -q >/dev/null 2>&1; then :; else
      report "gitleaks"
    fi
  fi
fi

BODY=$(grep -v 'allowlist-secret' "$FILE" 2>/dev/null || true)
printf '%s' "$BODY" | grep -Eq -- '-----BEGIN [A-Z ]*PRIVATE KEY-----'          && report "private key"
printf '%s' "$BODY" | grep -Eq -- 'AKIA[0-9A-Z]{16}'                             && report "AWS access key"
printf '%s' "$BODY" | grep -Eq -- 'gh[pousr]_[A-Za-z0-9]{30,}'                   && report "GitHub token"
printf '%s' "$BODY" | grep -Eq -- 'xox[baprs]-[A-Za-z0-9-]{10,}'                 && report "Slack token"
printf '%s' "$BODY" | grep -Eq -- 'sk-[A-Za-z0-9_-]{24,}'                        && report "API key (sk-...)"
printf '%s' "$BODY" | grep -Eq -- 'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.' && report "JWT"
printf '%s' "$BODY" | grep -Eiq -- "(password|passwd|secret|api[_-]?key|client[_-]?secret|token)[\"']?[[:space:]]*[:=][[:space:]]*[\"'][A-Za-z0-9/+_.-]{12,}[\"']" \
  && ! printf '%s' "$BODY" | grep -Eiq -- '(\$\{|<[a-z_-]+>|changeme|example|dummy|placeholder|xxxx|test)' \
  && report "hard-coded credential literal"
exit 0
