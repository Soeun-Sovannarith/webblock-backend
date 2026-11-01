#!/usr/bin/env bash
# J1 exit check (spec section 13): Provisioning's ProjectAccess calls the Project service.
# Needs: PROJECT_URL, PROVISIONING_URL, PROJECT_TOKEN_A (Project dev token of user A), PROVISIONING_TOKEN_A and
# PROVISIONING_TOKEN_B (Provisioning dev tokens whose user_id are A and B), BUSINESS_TYPE (an active *_shop code).
# Optional: --expect-unavailable (run while the Project service is stopped). Tokens come from the environment only.
set -euo pipefail

for v in PROJECT_URL PROVISIONING_URL PROVISIONING_TOKEN_A; do
  [ -n "${!v:-}" ] || { echo "missing $v" >&2; exit 2; }
done
fail=0
check() { # name expected actual
  if [ "$2" = "$3" ]; then echo "PASS $1 ($3)"; else echo "FAIL $1: expected $2, got $3"; fail=1; fi
}
status() { # method url token [body]
  curl -s -o /dev/null -w '%{http_code}' -X "$1" -H "Authorization: Bearer $3" \
    -H 'Content-Type: application/json' ${4:+--data "$4"} "$2"
}

if [ "${1:-}" = "--expect-unavailable" ]; then
  [ -n "${PROJECT_ID:-}" ] || { echo "set PROJECT_ID to the project the first run created" >&2; exit 2; }
  check "Project down: Provisioning fails closed" 503 \
    "$(status GET "$PROVISIONING_URL/v1/projects/$PROJECT_ID/releases" "$PROVISIONING_TOKEN_A")"
  exit $fail
fi

for v in PROJECT_TOKEN_A PROVISIONING_TOKEN_B BUSINESS_TYPE; do
  [ -n "${!v:-}" ] || { echo "missing $v" >&2; exit 2; }
done
project_id="$(python3 -I -c 'import uuid; print(uuid.uuid4())')"
# A sub-domain is never reused, so each run takes a new one from its project ID (one DNS label, not reserved)
subdomain="j1-$(printf '%s' "$project_id" | tr -d '-' | cut -c1-12)"
body="$(printf '{"name":"J1 exit","subdomain":"%s","businessTypeCode":"%s","templateCode":"blank","businessProfile":{"businessName":"J1 exit","description":null,"phone":null,"email":null,"address":null,"timezone":"UTC"}}' "$subdomain" "$BUSINESS_TYPE")"

check "create project" 201 "$(status PUT "$PROJECT_URL/api/v1/projects/$project_id" "$PROJECT_TOKEN_A" "$body")"
check "repeat create" 200 "$(status PUT "$PROJECT_URL/api/v1/projects/$project_id" "$PROJECT_TOKEN_A" "$body")"
check "owner lists releases through Provisioning" 200 \
  "$(status GET "$PROVISIONING_URL/v1/projects/$project_id/releases" "$PROVISIONING_TOKEN_A")"
check "another user is concealed" 404 \
  "$(status GET "$PROVISIONING_URL/v1/projects/$project_id/releases" "$PROVISIONING_TOKEN_B")"
check "unknown project is concealed" 404 \
  "$(status GET "$PROVISIONING_URL/v1/projects/$(python3 -I -c 'import uuid; print(uuid.uuid4())')/releases" "$PROVISIONING_TOKEN_A")"
echo "PROJECT_ID=$project_id"
exit $fail
