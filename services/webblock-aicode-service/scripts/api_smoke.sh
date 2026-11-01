#!/usr/bin/env bash
# Calls every HTTP endpoint of the AI Code Service once, with sample data, chaining the IDs between calls.
#
#   docker compose up -d && uvicorn app.main:app --reload     # in another terminal
#   scripts/api_smoke.sh                                       # BASE=http://localhost:8000 by default
#
# Runs are polled until they finish, so with BUILD_RUNNER=docker and CODEGEN_MODE=llm the whole script takes minutes;
# BUILD_RUNNER=skip and CODEGEN_MODE=hybrid make it fast. Set SKIP_ONBOARDING=1 or SKIP_ASSISTANT=1 to leave those out
# (both call GLM). Needs curl and jq.
set -uo pipefail

BASE="${BASE:-http://localhost:8000}"
SUFFIX="$(date +%s)"
POLL_SECONDS="${POLL_SECONDS:-900}"
OUT="$(mktemp)"; HEADERS="$(mktemp)"; trap 'rm -f "$OUT" "$HEADERS"' EXIT

bold() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

# call METHOD PATH [JSON] [extra curl args...]: prints status and body, leaves the body in $BODY and status in $CODE.
call() {
  local method="$1" path="$2" data="${3:-}"
  shift $(( $# < 3 ? $# : 3 ))
  local args=(-s -D "$HEADERS" -o "$OUT" -w '%{http_code}' -X "$method" "$BASE$path" "$@")
  [[ -n "$data" ]] && args+=(-H 'Content-Type: application/json' -d "$data")
  CODE="$(curl "${args[@]}")"
  BODY="$(cat "$OUT")"
  printf '%s %s -> %s%s\n' "$method" "$path" "$CODE" "$(grep -i '^location:' "$HEADERS" | tr -d '\r' | sed 's/^/   /')"
  echo "$BODY" | jq -C . 2>/dev/null | head -40 || echo "$BODY" | head -c 2000
}

# wait_run RUN_ID: polls GET /v1/generation-runs/{id} until SUCCEEDED or FAILED.
wait_run() {
  local run="$1" waited=0 status step
  [[ -z "$run" || "$run" == "null" ]] && { echo "(no run to wait for)"; return; }
  while (( waited < POLL_SECONDS )); do
    read -r status step < <(curl -s "$BASE/v1/generation-runs/$run" | jq -r '"\(.status) \(.step)"')
    printf '  run %s: %s %s\n' "$run" "$status" "$step"
    [[ "$status" == "SUCCEEDED" || "$status" == "FAILED" ]] && return
    sleep 5; waited=$(( waited + 5 ))
  done
  echo "  gave up after ${POLL_SECONDS}s"
}

# --- sample data ------------------------------------------------------------------------------------------------

HOME_PUCK='{
  "root": {"props": {"title": "Petal & Stem", "theme": "warm", "accent": "#a4532b", "font": "serif"}},
  "content": [
    {"type": "Hero", "props": {"id": "hero-1", "title": "Fresh flowers, every morning", "subtitle": "Hand-tied bouquets from Prek Eng", "tone": "accent", "align": "center", "spacing": "spacious"}},
    {"type": "ProductCards", "props": {"id": "products-1", "title": "Bestsellers", "products": [
      {"name": "Rose bouquet", "price": "$25", "description": "Twelve red roses"},
      {"name": "Tulip bunch", "price": "$18", "description": "Seasonal tulips"},
      {"name": "Orchid pot", "price": "$32", "description": "White phalaenopsis"}]}},
    {"type": "About", "props": {"id": "about-1", "title": "About us", "text": "A family florist since 2010.", "tone": "surface"}},
    {"type": "Location", "props": {"id": "location-1", "title": "Visit us", "address": "St. 2004, Prek Eng, Phnom Penh"}},
    {"type": "Footer", "props": {"id": "footer-1", "text": "© 2026 Petal & Stem", "tone": "contrast"}}
  ]
}'

# Same page with an edited hero title and one more product: a PUCK_JSON-only change.
HOME_PUCK_EDITED="$(echo "$HOME_PUCK" | jq '.content[0].props.title = "Fresh flowers, delivered today"
  | .content[1].props.products += [{"name": "Sunflower trio", "price": "$15", "description": "Three sunflowers"}]')"

ABOUT_PAGE='{
  "name": "About",
  "path": "/about",
  "seo_title": "About Petal & Stem",
  "seo_description": "Our story and our florists.",
  "sort_order": 1,
  "puck_tree": {
    "root": {"props": {"title": "About"}},
    "content": [
      {"type": "Hero", "props": {"id": "about-hero", "title": "Our story", "subtitle": "Three generations of florists"}},
      {"type": "Text", "props": {"id": "about-text", "text": "We grow most of our flowers ourselves.", "align": "left"}}
    ]
  }
}'

BUSINESS='{
  "business_name": "Kopi Corner '"$SUFFIX"'",
  "business_type": "cafe",
  "business_description": "A small neighbourhood cafe serving Khmer iced coffee and fresh pastries.",
  "logo_url": "https://example.com/logo.png",
  "phone_number": "+855 12 345 678",
  "email": "hello@kopicorner.example",
  "address": "St. 63, BKK1, Phnom Penh",
  "opening_hours": [
    {"day": "Mon", "opens": "7:00 AM", "closes": "6:00 PM"},
    {"day": "Tuesday", "opens": "7:00 AM", "closes": "6:00 PM"},
    {"day": "Wednesday", "opens": "7:00 AM", "closes": "6:00 PM"},
    {"day": "Thursday", "opens": "7:00 AM", "closes": "6:00 PM"},
    {"day": "Friday", "opens": "7:00 AM", "closes": "8:00 PM"},
    {"day": "Saturday", "opens": "8:00 AM", "closes": "8:00 PM"},
    {"day": "Sunday", "is_open": false}
  ],
  "theme": "light",
  "accent": "#2f6f4e",
  "font": "sans",
  "codegen_mode": "hybrid"
}'

# --- health -----------------------------------------------------------------------------------------------------

bold "Health"
call GET /health
call GET /internal/v1/health

# --- projects and pages -----------------------------------------------------------------------------------------

bold "Projects"
call POST /v1/projects '{"name": "Petal & Stem '"$SUFFIX"'", "subdomain": "petal-and-stem-'"$SUFFIX"'"}'
PROJECT="$(echo "$BODY" | jq -r .project_id)"
call GET "/v1/projects/$PROJECT"
call POST /v1/projects '{"name": "Bad repo", "repository_url": "https://gitlab.com/x/y"}'          # expect 422

bold "Pages"
call GET "/v1/projects/$PROJECT/pages"
HOME_PAGE="$(echo "$BODY" | jq -r '.[] | select(.path=="/") | .page_id')"
call POST "/v1/projects/$PROJECT/pages" "$ABOUT_PAGE"
ABOUT="$(echo "$BODY" | jq -r .page_id)"
REVISION="$(echo "$BODY" | jq -r .edit_revision)"
call GET "/v1/projects/$PROJECT/pages/$ABOUT"
call PATCH "/v1/projects/$PROJECT/pages/$ABOUT" '{"seo_title": "About us", "base_edit_revision": '"$REVISION"'}'
call PATCH "/v1/projects/$PROJECT/pages/$ABOUT" '{"seo_title": "Stale", "base_edit_revision": 0}'          # expect 409
call POST "/v1/projects/$PROJECT/pages" '{"name": "Portal", "path": "/portal"}'                            # expect 422 (reserved)
call POST "/v1/projects/$PROJECT/pages" '{"name": "X", "path": "/x", "puck_tree": {"content": [{"type": "Carousel", "props": {}}]}}'  # expect 422
call POST "/v1/projects/$PROJECT/pages" '{"name": "Temp", "path": "/temp"}'
TEMP="$(echo "$BODY" | jq -r .page_id)"
call DELETE "/v1/projects/$PROJECT/pages/$TEMP"
call GET "/v1/projects/$PROJECT/versions/latest"

# --- Flow 1: generate -------------------------------------------------------------------------------------------

bold "Flow 1: POST /v1/projects/{id}/generation-requests with puck_json"
GEN_PUCK="$(jq -n --argjson puck "$HOME_PUCK" '{puck_json: $puck}')"
call POST "/v1/projects/$PROJECT/generation-requests" "$GEN_PUCK"
RUN1="$(echo "$BODY" | jq -r .run_id)"
call POST "/v1/projects/$PROJECT/generation-requests" "$(echo "$HOME_PUCK_EDITED" | jq '{puck_json: .}')"  # expect 409 while RUN1 is active
wait_run "$RUN1"
call GET "/v1/generation-runs/$RUN1"
call GET "/v1/generation-runs/$RUN1/files"
call GET "/v1/generation-runs/$RUN1/check-report"
call GET "/v1/generation-runs/00000000-0000-0000-0000-000000000000"                                      # expect 404

# --- Flow 2: JSON edit, then a code update ----------------------------------------------------------------------

bold "Flow 2: content edit only (X-Codegen-Mode header)"
call POST "/v1/projects/$PROJECT/generation-requests" "$(echo "$HOME_PUCK_EDITED" | jq '{puck_json: .}')" -H 'X-Codegen-Mode: hybrid'
wait_run "$(echo "$BODY" | jq -r .run_id)"

bold "Flow 2: instructions with an Idempotency-Key"
KEY="smoke-$SUFFIX"
GEN_REQ='{"instructions": "Make the hero title larger and add a subtle fade-in animation to the product cards.", "codegen_mode": "hybrid"}'
call POST "/v1/projects/$PROJECT/generation-requests" "$GEN_REQ" -H "Idempotency-Key: $KEY"
RUN3="$(echo "$BODY" | jq -r .run_id)"
REQ3="$(echo "$BODY" | jq -r .generation_request_id)"
call POST "/v1/projects/$PROJECT/generation-requests" "$GEN_REQ" -H "Idempotency-Key: $KEY"               # same request back
call POST "/v1/projects/$PROJECT/generation-requests" '{"instructions": "Something else"}' -H "Idempotency-Key: $KEY"  # expect 422
wait_run "$RUN3"
call POST "/v1/projects/$PROJECT/generation-requests" '{}'                                                 # no change: SUCCEEDED, run_id null

bold "Project history and hosting"
call GET "/v1/projects/$PROJECT/generation-requests?limit=5"
call GET "/v1/projects/$PROJECT/generation-runs?limit=5"
call GET "/v1/projects/$PROJECT/hosting"
call GET "/v1/projects/$PROJECT/releases?limit=5"

# --- design assistant -------------------------------------------------------------------------------------------

if [[ -z "${SKIP_ASSISTANT:-}" ]]; then
  bold "Design assistant"
  call POST "/v1/projects/$PROJECT/conversations" '{"title": "Homepage tweaks"}'
  CONV="$(echo "$BODY" | jq -r .conversation_id)"
  call POST "/v1/conversations/$CONV/messages" '{"content": "Change the hero subtitle to \"Same-day delivery across Phnom Penh\" and add a Text block after it about our weekly subscription."}'
  CS1="$(echo "$BODY" | jq -r '.change_set.change_set_id // empty')"
  call POST "/v1/conversations/$CONV/messages" "$(jq -n --arg page "$ABOUT" '{content: "Make the about hero use the contrast tone.", page_id: $page}')"
  CS2="$(echo "$BODY" | jq -r '.change_set.change_set_id // empty')"
  call GET "/v1/conversations/$CONV"
  [[ -n "$CS1" ]] && { call GET "/v1/change-sets/$CS1"; call PATCH "/v1/change-sets/$CS1" '{"status": "APPLIED"}'; }
  [[ -n "$CS2" ]] && call PATCH "/v1/change-sets/$CS2" '{"status": "DISCARDED"}'
  [[ -n "$CS2" ]] && call PATCH "/v1/change-sets/$CS2" '{"status": "APPLIED"}'                         # expect 409 (already decided)
  [[ -z "$CS1$CS2" ]] && echo "(GLM proposed no change sets, so PATCH /v1/change-sets/{id} was not exercised)"
fi

# --- internal API (Project Service's view) ----------------------------------------------------------------------

bold "Internal API"
call GET "/internal/v1/generation-runs/$RUN1"
DOWNLOAD="$(echo "$BODY" | jq -r '.data.artifacts[0].downloadUrl // empty')"
call GET "/internal/v1/generation-requests/$REQ3/runs?limit=5"
call GET "/internal/v1/generation-runs/$RUN1/files"
call GET "/internal/v1/generation-runs/$RUN1/check-report"
if [[ -n "$DOWNLOAD" ]]; then
  echo "GET $DOWNLOAD"; curl -s -o /dev/null -w '  -> %{http_code}, %{size_download} bytes\n' "$DOWNLOAD"
else
  echo "(downloadUrl is null: set ARTIFACT_URL_SECRET to get signed artifact URLs)"
fi
call GET "/internal/v1/artifacts/00000000-0000-0000-0000-000000000000?expires=1&signature=bad"             # expect 403
call GET "/internal/v1/generation-runs/00000000-0000-0000-0000-000000000000"                              # expect 404 problem
OWNER="11111111-2222-3333-4444-555555555555"                                                               # a Keycloak user id
call GET "/internal/v1/projects/$PROJECT/portal-owners"                                                    # 503 without Keycloak settings
call PUT "/internal/v1/projects/$PROJECT/portal-owners/$OWNER"
call DELETE "/internal/v1/projects/$PROJECT/portal-owners/$OWNER"

# --- business onboarding ----------------------------------------------------------------------------------------

if [[ -z "${SKIP_ONBOARDING:-}" ]]; then
  bold "Onboarding: POST /v1/onboardings (creates a project)"
  call POST /v1/onboardings "$BUSINESS"
  BIZ_PROJECT="$(echo "$BODY" | jq -r .project_id)"
  wait_run "$(echo "$BODY" | jq -r .run_id)"

  bold "Onboarding: POST /v1/projects/{id}/onboardings (existing project)"
  call POST "/v1/projects/$BIZ_PROJECT/onboardings" \
    "$(echo "$BUSINESS" | jq '.business_description = "Khmer iced coffee, pastries and a quiet corner to work." | .theme = "dark"')"
  wait_run "$(echo "$BODY" | jq -r .run_id)"
  call POST /v1/onboardings '{"business_name": "X", "business_description": "short desc", "email": "not-an-email", "accent": "red"}'  # expect 422
fi

bold "Done"
echo "project: $PROJECT   first run: $RUN1"
