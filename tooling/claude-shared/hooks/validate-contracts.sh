#!/usr/bin/env bash
# PostToolUse(Edit|Write|MultiEdit) - ROOT only: validate contract files right after an edit.
# JSON must parse; YAML must parse (if PyYAML is installed); OpenAPI is linted with spectral if installed.
# Exit 2 = feed the error back to Claude.
set -u
command -v jq >/dev/null 2>&1 || { echo "BLOCKED: jq is required by the .claude hooks (install jq)." >&2; exit 2; }

FILE=$(jq -r '.tool_input.file_path // empty')
case "$FILE" in */contracts/*.yaml|*/contracts/*.yml|*/contracts/*.json) ;; *) exit 0 ;; esac
[ -f "$FILE" ] || exit 0

python3 -I - "$FILE" <<'PY' || exit 2
import json, sys
p = sys.argv[1]
try:
    if p.endswith(".json"):
        json.load(open(p))
    else:
        try:
            import yaml
        except ImportError:
            sys.exit(0)
        yaml.safe_load(open(p))
except Exception as e:
    sys.stderr.write("INVALID contract file %s: %s\n" % (p, e))
    sys.exit(1)
PY

if command -v spectral >/dev/null 2>&1 && printf '%s' "$FILE" | grep -q '/openapi/'; then
  spectral lint "$FILE" >&2 || exit 2
fi
exit 0
