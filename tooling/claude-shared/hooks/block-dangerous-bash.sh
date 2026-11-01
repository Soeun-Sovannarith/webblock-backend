#!/usr/bin/env bash
# PreToolUse(Bash): block destructive, privilege-escalating or exfiltrating shell commands.
# Exit 2 = block the call and show stderr to Claude.
# NOTE: a guardrail, not a sandbox. Pattern matching can be bypassed by obfuscation;
# for real isolation run Claude in a devcontainer without production credentials.
set -u
command -v jq >/dev/null 2>&1 || { echo "BLOCKED: jq is required by the .claude hooks (install jq)." >&2; exit 2; }

CMD=$(jq -r '.tool_input.command // empty')
[ -z "$CMD" ] && exit 0

deny() {
  echo "BLOCKED by block-dangerous-bash: $1" >&2
  echo "Command: $CMD" >&2
  echo "Do not retry with a variation. Explain to the user what you need and let them run it." >&2
  exit 2
}
check() { printf '%s' "$CMD" | grep -Eiq -- "$1" && deny "$2"; }

# destructive file / git operations
check 'rm[[:space:]]+(-[a-zA-Z]*[rR]|--recursive|--no-preserve-root)'        "recursive delete"
check 'git[[:space:]]+push([[:space:]].*)?[[:space:]](-f|--force|--force-with-lease|--delete)([[:space:]]|$)' "force push / remote delete"
check 'git[[:space:]]+push[[:space:]].*[[:space:]]\+[^[:space:]]+'            "force push via + refspec"
check 'git[[:space:]]+reset[[:space:]]+--hard'                                "git reset --hard"
check 'git[[:space:]]+clean[[:space:]]+-[a-zA-Z]*f'                           "git clean -f"
check 'git[[:space:]]+(checkout|restore)[[:space:]]+(--[[:space:]]+)?\.([[:space:]]|$)' "discard all changes"
check 'git[[:space:]]+branch[[:space:]]+-D'                                   "force-delete branch"
check 'git[[:space:]]+(commit|push|merge|rebase)[[:space:]].*--no-verify'     "bypassing git hooks"
check 'chmod[[:space:]]+(-R[[:space:]]+)?[0-7]*777'                           "world-writable permissions"
check '(mkfs|dd[[:space:]]+if=|>[[:space:]]*/dev/sd|>[[:space:]]*/etc/)'      "disk / system overwrite"

# privilege escalation and remote code
check '(^|[;&|[:space:]])(sudo|su|doas)([[:space:]]|$)'                       "privilege escalation"
check '\|[[:space:]]*(sudo[[:space:]]+)?(ba|z|da|k)?sh([[:space:]]|$)'        "piping into a shell"
check '(^|[;&|[:space:]])eval[[:space:]]'                                     "eval"

# network exfiltration
check '(^|[;&|[:space:]])(curl|wget|ssh|scp|sftp|rsync|nc|ncat|telnet|ftp)([[:space:]]|$)' "network tool (data exfiltration risk)"

# infrastructure and database destruction
check '(drop|truncate)[[:space:]]+(database|table|schema)'                    "destructive SQL"
check 'docker[[:space:]]+(system[[:space:]]+prune|volume[[:space:]]+(rm|prune)|rm[[:space:]]+-f|rmi[[:space:]]+-f)' "destructive docker command"
check 'kubectl[[:space:]]+(delete|drain|cordon)|helm[[:space:]]+(uninstall|delete)' "destructive cluster command"
check 'terraform[[:space:]]+(destroy|apply)|pulumi[[:space:]]+(destroy|up)'   "infrastructure change"

# secrets and environment dumps
check '(^|[;&|[:space:]])(printenv|env)([[:space:]]*$|[[:space:]]*[|;&])'     "environment dump"
if printf '%s' "$CMD" | grep -Eiq '(\.env(\.[a-z0-9_-]+)?|\.pem|\.p12|\.jks|id_rsa|id_ed25519|\.aws/|\.ssh/|\.gnupg/|application-prod)'; then
  if ! printf '%s' "$CMD" | grep -Eiq '\.env\.(example|sample|template)'; then
    deny "touching a secrets or production-config file from the shell"
  fi
fi

exit 0
