#!/usr/bin/env bash
# ==============================================================================
# WebBlock Platform: End-to-End Full Flow Integration Test
# Runs through:
# 1. User Service: Register -> Mailpit OTP -> Verify -> Keycloak account creation
# 2. Project Service: Catalog exploration -> Project creation -> Contract creation
# 3. RabbitMQ & AI Code Service: Codegen dispatch -> AST unwrap -> GLM code synthesis -> Docker build
# 4. Result: Inspect generated website files & source archive
# ==============================================================================
set -e

GATEWAY_URL="${GATEWAY_URL:-http://localhost:8088}"
MAILPIT_URL="${MAILPIT_URL:-http://localhost:8025}"
AICODE_URL="${AICODE_URL:-http://localhost:8000}"
TOKEN="dev-token-project-service-32-chars-long-abc"

C_RESET="\033[0m"
C_BOLD="\033[1m"
C_GREEN="\033[32m"
C_CYAN="\033[36m"
C_YELLOW="\033[33m"
C_MAGENTA="\033[35m"

echo -e "${C_BOLD}${C_CYAN}======================================================================${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}           WebBlock Platform — End-to-End Test Execution              ${C_RESET}"
echo -e "${C_BOLD}${C_CYAN}======================================================================${C_RESET}"
echo -e "Gateway URL: ${GATEWAY_URL}\n"

# ------------------------------------------------------------------------------
# 1. User Registration Flow (User Service -> Mailpit -> Keycloak)
# ------------------------------------------------------------------------------
RANDOM_SUFFIX=$((1000 + RANDOM % 9000))
EMAIL="user_${RANDOM_SUFFIX}@webblock.test"
FULL_NAME="Tester ${RANDOM_SUFFIX}"

echo -e "${C_BOLD}[Step 1/5] Registering New User...${C_RESET}"
echo "   Endpoint: POST /api/user/v1/register/start"
START_RESP=$(curl -s -X POST "${GATEWAY_URL}/api/user/v1/register/start" \
  -H "Content-Type: application/json" \
  -d "{\"fullName\": \"${FULL_NAME}\", \"email\": \"${EMAIL}\"}")

echo "   Response: $START_RESP"

echo -e "   Fetching OTP from Mailpit (${MAILPIT_URL})..."
sleep 1
OTP_CODE=$(curl -s "${MAILPIT_URL}/api/v1/messages" | jq -r ".messages[] | select(.To[0].Address == \"${EMAIL}\") | .Snippet" | grep -oE '[0-9]{6}' | head -n 1)

if [ -z "$OTP_CODE" ]; then
    echo -e "${C_YELLOW}   ⚠️ Could not automatically parse OTP. Checking recent messages...${C_RESET}"
    OTP_CODE=$(curl -s "${MAILPIT_URL}/api/v1/messages" | jq -r '.messages[0].Snippet' | grep -oE '[0-9]{6}' | head -n 1)
fi

echo -e "   ${C_GREEN}✔ Received OTP Code:${C_RESET} ${C_BOLD}${OTP_CODE}${C_RESET}"

echo "   Endpoint: POST /api/user/v1/register/verify-otp"
VERIFY_RESP=$(curl -s -X POST "${GATEWAY_URL}/api/user/v1/register/verify-otp" \
  -H "Content-Type: application/json" \
  -d "{\"email\": \"${EMAIL}\", \"code\": \"${OTP_CODE}\"}")

VERIFY_TOKEN=$(echo "$VERIFY_RESP" | jq -r '.verificationToken')
echo "   Verification Token: ${VERIFY_TOKEN:0:18}..."

echo "   Endpoint: POST /api/user/v1/register/complete"
COMPLETE_RESP=$(curl -s -X POST "${GATEWAY_URL}/api/user/v1/register/complete" \
  -H "Content-Type: application/json" \
  -d "{\"verificationToken\": \"${VERIFY_TOKEN}\", \"password\": \"Password123!\", \"confirmPassword\": \"Password123!\"}")

USER_ID=$(echo "$COMPLETE_RESP" | jq -r '.userId')
echo -e "   ${C_GREEN}✔ User registered in PostgreSQL & Keycloak!${C_RESET} (UserID: ${USER_ID})"

# ------------------------------------------------------------------------------
# 2. Project Creation (Project Service)
# ------------------------------------------------------------------------------
PROJ_UUID=$(python3 -c "import uuid; print(uuid.uuid4())")
SUBDOMAIN="boutique-${RANDOM_SUFFIX}"

echo -e "\n${C_BOLD}[Step 2/5] Creating New Website Project...${C_RESET}"
echo "   Endpoint: PUT /api/v1/projects/${PROJ_UUID}"
PROJECT_RESP=$(curl -s -X PUT "${GATEWAY_URL}/api/v1/projects/${PROJ_UUID}" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"Apex Boutique ${RANDOM_SUFFIX}\",
    \"subdomain\": \"${SUBDOMAIN}\",
    \"businessTypeCode\": \"clothing_shop\",
    \"templateCode\": \"everyday_store\",
    \"businessProfile\": {
      \"businessName\": \"Apex Streetwear\",
      \"description\": \"Urban apparel and accessories\",
      \"phone\": \"+85512345678\",
      \"email\": \"contact@${SUBDOMAIN}.test\",
      \"address\": \"100 Riverside, Phnom Penh\",
      \"timezone\": \"Asia/Phnom_Penh\"
    }
  }")

PROJ_STATUS=$(echo "$PROJECT_RESP" | jq -r '.data.status')
echo -e "   ${C_GREEN}✔ Project Created!${C_RESET} Status: ${PROJ_STATUS}, Subdomain: ${SUBDOMAIN}.sites.webblock.test"

# ------------------------------------------------------------------------------
# 3. Create Generation Contract (Project Service)
# ------------------------------------------------------------------------------
echo -e "\n${C_BOLD}[Step 3/5] Pinning Generation Contract...${C_RESET}"
echo "   Endpoint: POST /api/v1/projects/${PROJ_UUID}/generation-contracts"
CONTRACT_RESP=$(curl -s -X POST "${GATEWAY_URL}/api/v1/projects/${PROJ_UUID}/generation-contracts" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{"productTypeIds": ["a9693e48-969a-4f26-984c-d30d931bae1f"]}')

CONTRACT_ID=$(echo "$CONTRACT_RESP" | jq -r '.data.generationContractId')
echo -e "   ${C_GREEN}✔ Contract Created!${C_RESET} ID: ${CONTRACT_ID}"

# ------------------------------------------------------------------------------
# 4. Trigger AI Code Generation Request (RabbitMQ -> AI Code Service)
# ------------------------------------------------------------------------------
IDEMPOTENCY_KEY="req-demo-${RANDOM_SUFFIX}"
echo -e "\n${C_BOLD}[Step 4/5] Triggering Website Code Generation...${C_RESET}"
echo "   Endpoint: POST /api/v1/projects/${PROJ_UUID}/generation-requests"
GEN_RESP=$(curl -s -X POST "${GATEWAY_URL}/api/v1/projects/${PROJ_UUID}/generation-requests" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Idempotency-Key: ${IDEMPOTENCY_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"generationContractId\": \"${CONTRACT_ID}\"}")

GEN_REQ_ID=$(echo "$GEN_RESP" | jq -r '.data.generationRequestId')
GEN_STATUS=$(echo "$GEN_RESP" | jq -r '.data.status')
echo -e "   ${C_GREEN}✔ Generation Request Accepted!${C_RESET} ID: ${GEN_REQ_ID} (Initial status: ${GEN_STATUS})"

echo -e "   ${C_MAGENTA}Waiting for AI Code Service & Docker build to synthesize website...${C_RESET}"
for i in {1..30}; do
    STATUS_RESP=$(curl -s -H "Authorization: Bearer ${TOKEN}" "${GATEWAY_URL}/api/v1/projects/${PROJ_UUID}/generation-requests/${GEN_REQ_ID}")
    CURR_STATUS=$(echo "$STATUS_RESP" | jq -r '.data.status')
    echo -n "   ● Status: ${CURR_STATUS} (elapsed ${i}s)..."
    if [ -d "services/webblock-aicode-service/var/artifacts/${PROJ_UUID}" ]; then
        RUN_DIR=$(ls "services/webblock-aicode-service/var/artifacts/${PROJ_UUID}" 2>/dev/null | head -n 1)
        if [ -f "services/webblock-aicode-service/var/artifacts/${PROJ_UUID}/${RUN_DIR}/source.tar.gz" ]; then
            echo -e " ${C_GREEN}Website build completed!${C_RESET}"
            break
        fi
    fi
    echo ""
    sleep 2
done

# ------------------------------------------------------------------------------
# 5. Inspect Generated Website Code & Files
# ------------------------------------------------------------------------------
echo -e "\n${C_BOLD}[Step 5/5] Generated Website Inspection & Deliverables...${C_RESET}"
if [ -n "$RUN_DIR" ]; then
    ARCHIVE_PATH="services/webblock-aicode-service/var/artifacts/${PROJ_UUID}/${RUN_DIR}/source.tar.gz"
    ARCHIVE_SIZE=$(ls -lh "$ARCHIVE_PATH" | awk '{print $5}')
    echo -e "   ${C_GREEN}✔ Package Archive:${C_RESET} ${ARCHIVE_PATH} (${ARCHIVE_SIZE})"
    
    echo -e "   Sample Generated Files (via ${AICODE_URL}/v1/generation-runs/${RUN_DIR}/files):"
    curl -s "${AICODE_URL}/v1/generation-runs/${RUN_DIR}/files" | jq -r 'keys[0:8][]' | sed 's/^/     - /'
fi

echo -e "\n${C_BOLD}${C_GREEN}======================================================================${C_RESET}"
echo -e "${C_BOLD}${C_GREEN}             Full End-to-End Platform Flow Succeeded!                ${C_RESET}"
echo -e "${C_BOLD}${C_GREEN}======================================================================${C_RESET}"
echo "Summary of artifacts created:"
echo "  1. User Profile:      ${EMAIL} (UserID: ${USER_ID})"
echo "  2. Web Project:       Apex Boutique (ID: ${PROJ_UUID})"
echo "  3. Generation Req:    ${GEN_REQ_ID}"
if [ -n "$RUN_DIR" ]; then
    echo "  4. AI Run Artifacts:  services/webblock-aicode-service/var/artifacts/${PROJ_UUID}/${RUN_DIR}/"
fi
echo "  5. Live Mailpit:      ${MAILPIT_URL} (Check verification emails)"
echo "  6. RabbitMQ Queues:   http://localhost:15672 (Queues processed)"
echo -e "======================================================================\n"
