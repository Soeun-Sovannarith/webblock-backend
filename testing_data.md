# WebBlock Platform: Complete End-to-End Testing Data & API Reference

> **Welcome to the WebBlock Backend Ecosystem!**  
> This guide is designed so that **anyone**—frontend engineers, QA testers, backend teammates, or evaluators—can test every microservice and the entire end-to-end application without needing prior knowledge of the internal codebase.

---

## 1. Quick Reference & Pre-Configured Test Credentials

You do not need to generate new credentials from scratch to test authenticated routes. You can use the verified credentials and IDs below:

| Resource | Value | Notes |
| :--- | :--- | :--- |
| **Verified Test User Email** | `soeunsovannarith64@gmail.com` | Registered in Keycloak and PostgreSQL |
| **Verified Test User Password** | `Password123!` | Used for `/api/user/v1/auth/login` |
| **Alertmanager Secret Token** | `Yda970gCTRxA+uKn6WT4l47K0S/enU4gvwAnEZhEgEI=` | Used for Notification Alertmanager webhook |
| **Valid Template Codes** | `warm_bold`, `everyday_store`, `sweet_pastel`, `clean_mono` | Used in Project Creation |
| **Valid Business Types** | `food_shop`, `clothing_shop`, `furniture_shop`, `beauty_shop`, `tech_shop` | Used in Project Creation |
| **Valid Product Type ID** | `1617e841-f602-4ccd-b9ec-0f9dfc9afbf8` | Menu Item / Product ID for Generation Contracts |
| **Keycloak Admin Console** | `http://localhost:8180` | Username: `admin` / Password: `adminpassword` |
| **RabbitMQ Management Dashboard** | `http://localhost:15672` | Username: `webblock` / Password: `webblock` |
| **Mailpit Web Mail UI** | `http://localhost:8025` | Mock inbox capturing local OTP emails |
| **Combined Swagger Portal** | `http://localhost:8088` | All service OpenAPI docs and reverse proxy |

---

## 2. Architecture & Service Port Directory

All APIs can be called either **directly** via each service's private port or **through the unified Gateway Portal** on port `8088`.

| Microservice | Direct Host Port | Gateway Route (`http://localhost:8088`) | Core Responsibilities |
| :--- | :--- | :--- | :--- |
| **Gateway Portal** | `8088` | `/` | Swagger UI dashboard, health aggregator & reverse proxy |
| **User Service** | `8081` | `/api/user/**` | OTP registration, login, Keycloak sync, profile management |
| **Project Service** | `8080` (Mgmt: `8089`) | `/api/v1/**`, `/internal/v1/**` | Business context wizard, Puck layout draft, contracts, subdomains |
| **AI Code Service** | `8000` | `/proxy/aicode-service/**`, `/v1/**` | Autonomous Next.js/Drizzle codegen, Docker build, GitHub push |
| **Notification Service** | `8085` | `/api/notification/**`, `/internal/v1/**` | Knock user feed tokens, push notifications, Alertmanager webhook |
| **Keycloak Identity** | `8180` | External: `https://auth.roa-dev.digital` | OIDC / OAuth2 token issuance & realm management |
| **Mailpit Mailer** | `8025` (SMTP: `1025`) | — | Local mock mail server capturing OTP emails |
| **RabbitMQ Broker** | `15672` (AMQP: `5672`) | — | Shared AMQP 0-9-1 message broker |
| **PostgreSQL DB** | `5432` | — | Multi-database storage (`webblock_user`, `webblock_project`, `aicode_db`) |

---

## 3. Gateway & Portal Endpoints (`tooling/swagger`)

### Step 0.1: Live Cluster Health Status
- **Method & Endpoint**: `GET /api/statuses`
- **Gateway URL**: `http://localhost:8088/api/statuses`
- **Purpose**: Checks the real-time operational status of all microservices in the platform.

```bash
curl -X GET "http://localhost:8088/api/statuses"
```
**Expected Response:** `200 OK`
```json
{
  "user-service": true,
  "project-service": true,
  "aicode-service": true,
  "provisioning-service": true,
  "notification-service": true
}
```

---

### Step 0.2: Interactive Unified Swagger UI
- **Method & Endpoint**: `GET /`
- **Gateway URL**: `http://localhost:8088/`
- **Purpose**: Interactive browser interface combining Swagger UI specifications from every microservice into a single portal.
- **Action**: Open [http://localhost:8088](http://localhost:8088) in any browser.

---

## 4. User Service Endpoints (`webblock-user-service`)

Operates on **Port 8081** (Gateway prefix: `/api/user/**`).

---

### Step 1.1: Health Check
- **Method & Endpoint**: `GET /actuator/health`
- **Direct URL**: `http://localhost:8081/actuator/health`
- **Gateway URL**: `http://localhost:8088/proxy/user-service/actuator/health`

```bash
curl -X GET "http://localhost:8081/actuator/health"
```
**Expected Response:** `200 OK`
```json
{
  "status": "UP"
}
```

---

### Step 1.2: Authenticate & Obtain Bearer JWT Access Token
- **Method & Endpoint**: `POST /api/user/v1/auth/login`
- **Direct URL**: `http://localhost:8081/api/user/v1/auth/login`
- **Gateway URL**: `http://localhost:8088/api/user/v1/auth/login`
- **Headers**: `Content-Type: application/json`
- **Purpose**: Exchanges email and password for a Keycloak-signed Bearer JWT access token and refresh token.

**Request Body:**
```json
{
  "email": "youremail@gmail.com",
  "password": "[PASSWORD]"
}
```

**Curl Command:**
```bash
curl -X POST "http://localhost:8088/api/user/v1/auth/login" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "youremail@gmail.com",
    "password": "[PASSWORD]"
  }'
```
**Expected Response:** `200 OK`
```json
{
  "accessToken": "eyJhbGciOiJSUzI1NiIs...",
  "refreshToken": "eyJhbGciOiJ...",
  "expiresIn": 3600,
  "refreshExpiresIn": 1800,
  "tokenType": "Bearer",
  "userId": "<YOUR_USER_ID>",
  "email": "youremail@gmail.com",
  "roles": ["USER"]
}
```

> [!TIP]
> **Export the token for subsequent requests**:
> ```bash
> export TOKEN="<PASTE_ACCESS_TOKEN_HERE>"
> ```

---

### Step 1.3: Fetch Signed-in User Profile (`/me`)
- **Method & Endpoint**: `GET /api/user/v1/me`
- **Direct URL**: `http://localhost:8081/api/user/v1/me`
- **Gateway URL**: `http://localhost:8088/api/user/v1/me`
- **Headers**:
  - `Authorization: Bearer $TOKEN`
- **Purpose**: Validates the Bearer JWT and retrieves the account profile.

```bash
curl -X GET "http://localhost:8088/api/user/v1/me" \
  -H "Authorization: Bearer $TOKEN"
```
**Expected Response:** `200 OK`
```json
{
  "userId": "<YOUR_USER_ID>",
  "fullName": "rith",
  "email": "youremail@gmail.com",
  "roles": ["USER"],
  "phoneNumber": null,
  "profileImageUrl": null,
  "status": "ACTIVE"
}
```

---

### Step 1.4: Inspect Sign-in Methods
- **Method & Endpoint**: `GET /api/user/v1/me/sign-in-methods`
- **Direct URL**: `http://localhost:8081/api/user/v1/me/sign-in-methods`
- **Gateway URL**: `http://localhost:8088/api/user/v1/me/sign-in-methods`
- **Headers**: `Authorization: Bearer $TOKEN`

```bash
curl -X GET "http://localhost:8088/api/user/v1/me/sign-in-methods" \
  -H "Authorization: Bearer $TOKEN"
```
**Expected Response:** `200 OK`
```json
{
  "hasPassword": true,
  "googleConnected": false
}
```

---

### Step 1.5: Register a New User (OTP Verification Flow)
To register a brand-new user from scratch:

#### 1. Start Registration (Sends OTP):
- **Method & Endpoint**: `POST /api/user/v1/register/start`
- **Gateway URL**: `http://localhost:8088/api/user/v1/register/start`

```bash
curl -X POST "http://localhost:8088/api/user/v1/register/start" \
  -H "Content-Type: application/json" \
  -d '{
    "fullName": "Jane Doe",
    "email": "jane.doe@example.com"
  }'
```
**Expected Response:** `200 OK`
```json
{
  "message": "OTP sent to email"
}
```

#### 2. Retrieve the 6-Digit OTP:
- If running with default local mock mail, check Mailpit at [`http://localhost:8025`](http://localhost:8025) or via API:
  ```bash
  curl -s "http://localhost:8025/api/v1/messages" | jq '.messages[0].Snippet'
  ```
- If running with Gmail SMTP, check your email inbox.

#### 3. Verify OTP:
- **Method & Endpoint**: `POST /api/user/v1/register/verify-otp`
- **Gateway URL**: `http://localhost:8088/api/user/v1/register/verify-otp`

```bash
curl -X POST "http://localhost:8088/api/user/v1/register/verify-otp" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "jane.doe@example.com",
    "code": "123456"
  }'
```
**Expected Response:** `200 OK`
```json
{
  "verificationToken": "m9ZizwDhXE4QUxbvA9..."
}
```

#### 4. Complete Registration:
- **Method & Endpoint**: `POST /api/user/v1/register/complete`
- **Gateway URL**: `http://localhost:8088/api/user/v1/register/complete`

```bash
curl -X POST "http://localhost:8088/api/user/v1/register/complete" \
  -H "Content-Type: application/json" \
  -d '{
    "verificationToken": "m9ZizwDhXE4QUxbvA9...",
    "password": "Password123!",
    "confirmPassword": "Password123!"
  }'
```
**Expected Response:** `201 Created`
```json
{
  "userId": "1864cfe9-d2e7-49d8-bc67-2f8b0452f323",
  "email": "jane.doe@example.com",
  "fullName": "Jane Doe",
  "roles": ["USER"]
}
```

---

## 5. Project Service Endpoints (`webblock-project-service`)

Operates on **Port 8080** (Management port: `8089`, Gateway prefix: `/api/v1/**` and `/internal/v1/**`).

---

### Step 2.1: Health Check
- **Method & Endpoint**: `GET /actuator/health`
- **Direct Management URL**: `http://localhost:8089/actuator/health`

```bash
curl -X GET "http://localhost:8089/actuator/health"
```
**Expected Response:** `200 OK`
```json
{
  "status": "UP"
}
```

---

### Step 2.2: List Available Catalog Templates
- **Method & Endpoint**: `GET /api/v1/templates`
- **Direct URL**: `http://localhost:8080/api/v1/templates`
- **Gateway URL**: `http://localhost:8088/api/v1/templates`
- **Headers**: `Authorization: Bearer $TOKEN`

```bash
curl -X GET "http://localhost:8088/api/v1/templates" \
  -H "Authorization: Bearer $TOKEN"
```
**Expected Response:** `200 OK`
```json
{
  "success": true,
  "data": [
    {
      "id": "everyday_store",
      "name": "Everyday store",
      "description": "Products, categories and clear prices",
      "thumbnailUrl": "/mock/templates/everyday-store.png",
      "badge": "Popular"
    },
    {
      "id": "warm_bold",
      "name": "Warm & bold",
      "description": "Warm typography and rich imagery",
      "thumbnailUrl": "/mock/templates/warm-bold.png",
      "badge": "Recommended"
    }
  ]
}
```

---

### Step 2.3: List Active Business Types
- **Method & Endpoint**: `GET /api/v1/business-types`
- **Gateway URL**: `http://localhost:8088/api/v1/business-types`
- **Headers**: `Authorization: Bearer $TOKEN`

```bash
curl -X GET "http://localhost:8088/api/v1/business-types" \
  -H "Authorization: Bearer $TOKEN"
```
**Expected Response:** `200 OK`
```json
{
  "success": true,
  "data": [
    { "code": "clothing_shop", "name": "Fashion" },
    { "code": "food_shop", "name": "Food & Beverage" },
    { "code": "furniture_shop", "name": "Furniture" },
    { "code": "beauty_shop", "name": "Health & Beauty" },
    { "code": "tech_shop", "name": "Technology" }
  ]
}
```

---

### Step 2.4: Check Subdomain Availability
- **Method & Endpoint**: `GET /api/v1/subdomains/{subdomain}`
- **Gateway URL**: `http://localhost:8088/api/v1/subdomains/{subdomain}`
- **Headers**: `Authorization: Bearer $TOKEN`

```bash
curl -X GET "http://localhost:8088/api/v1/subdomains/artisan-roasters" \
  -H "Authorization: Bearer $TOKEN"
```
**Expected Response:** `200 OK`
```json
{
  "success": true,
  "data": {
    "name": "artisan-roasters",
    "host": "artisan-roasters.sites.webblock.test",
    "available": true,
    "problem": null
  }
}
```

---

### Step 2.5: Create or Update Project (Business Context Wizard)
- **Method & Endpoint**: `PUT /api/v1/projects/{projectId}`
- **Direct URL**: `http://localhost:8080/api/v1/projects/{projectId}`
- **Gateway URL**: `http://localhost:8088/api/v1/projects/{projectId}`
- **Headers**:
  - `Authorization: Bearer $TOKEN`
  - `Content-Type: application/json`

> [!IMPORTANT]
> **Business Hours Constraint**:
> The `hours` array must contain **all 7 days of the week** (`MONDAY` through `SUNDAY`).
> For open days: `open: true`, `opensAt: "HH:mm"`, `closesAt: "HH:mm"` (`opensAt` < `closesAt`).
> For closed days: `open: false`, `opensAt: null`, `closesAt: null`.

```bash
# Generate a new random project UUID:
PROJECT_ID="$(uuidgen | tr '[:upper:]' '[:lower:]')"
echo "Using PROJECT_ID: $PROJECT_ID"

curl -X PUT "http://localhost:8088/api/v1/projects/$PROJECT_ID" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "templateId": "warm_bold",
    "name": "Artisan Coffee Roasters",
    "businessType": "food_shop",
    "category": "Cafe & Roastery",
    "thumbnailUrl": "https://images.unsplash.com/photo-1501339847302-ac426a4a7cbb?w=600",
    "details": {
      "businessName": "Artisan Coffee Roasters",
      "businessType": "Specialty Coffee Roastery",
      "description": "Locally roasted single-origin coffees, cold brew, and fresh pastries.",
      "logoUrl": "https://images.unsplash.com/photo-1514432324607-a09d9b4aefdd?w=200",
      "phone": "+855 23 999 888",
      "email": "hello@artisancoffee.com",
      "address": "No. 45, Street 240, Daun Penh, Phnom Penh, Cambodia",
      "hours": [
        { "day": "MONDAY", "open": true, "opensAt": "07:00", "closesAt": "18:00" },
        { "day": "TUESDAY", "open": true, "opensAt": "07:00", "closesAt": "18:00" },
        { "day": "WEDNESDAY", "open": true, "opensAt": "07:00", "closesAt": "18:00" },
        { "day": "THURSDAY", "open": true, "opensAt": "07:00", "closesAt": "18:00" },
        { "day": "FRIDAY", "open": true, "opensAt": "07:00", "closesAt": "19:00" },
        { "day": "SATURDAY", "open": true, "opensAt": "08:00", "closesAt": "19:00" },
        { "day": "SUNDAY", "open": false, "opensAt": null, "closesAt": null }
      ]
    }
  }'
```
**Expected Response:** `201 Created`
```json
{
  "success": true,
  "data": {
    "projectId": "b5e29780-692a-466d-88f6-a81d0f5cb130",
    "name": "Artisan Coffee Roasters",
    "status": "DRAFT",
    "templateId": "warm_bold"
  }
}
```

---

### Step 2.6: Get Project Details
- **Method & Endpoint**: `GET /api/v1/projects/{projectId}`
- **Gateway URL**: `http://localhost:8088/api/v1/projects/{projectId}`
- **Headers**: `Authorization: Bearer $TOKEN`

```bash
curl -X GET "http://localhost:8088/api/v1/projects/$PROJECT_ID" \
  -H "Authorization: Bearer $TOKEN"
```
**Expected Response:** `200 OK`
```json
{
  "success": true,
  "data": {
    "id": "b5e29780-692a-466d-88f6-a81d0f5cb130",
    "name": "Artisan Coffee Roasters",
    "status": "DRAFT",
    "businessType": "food_shop",
    "repositoryUrl": null
  }
}
```

---

### Step 2.7: Inter-Service Project Verification (Internal API)
- **Method & Endpoint**: `GET /internal/v1/projects/{projectId}`
- **Direct URL**: `http://localhost:8080/internal/v1/projects/{projectId}`
- **Gateway URL**: `http://localhost:8088/internal/v1/projects/{projectId}`
- **Purpose**: Called by AI Code Service and Notification Service to verify that a project exists, get its status, and inspect repository linkage without needing user credentials.

```bash
curl -X GET "http://localhost:8088/internal/v1/projects/$PROJECT_ID"
```
**Expected Response:** `200 OK`
```json
{
  "projectId": "b5e29780-692a-466d-88f6-a81d0f5cb130",
  "name": "Artisan Coffee Roasters",
  "status": "DRAFT",
  "ownerUserId": "7270bcf1-964c-40c1-9cb0-ca7461d45654",
  "exists": true,
  "repositoryUrl": null
}
```

---

### Step 2.8: Pin Generation Contract
- **Method & Endpoint**: `POST /api/v1/projects/{projectId}/generation-contracts`
- **Gateway URL**: `http://localhost:8088/api/v1/projects/{projectId}/generation-contracts`
- **Headers**:
  - `Authorization: Bearer $TOKEN`
  - `Content-Type: application/json`

```bash
curl -X POST "http://localhost:8088/api/v1/projects/$PROJECT_ID/generation-contracts" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "productTypeIds": ["1617e841-f602-4ccd-b9ec-0f9dfc9afbf8"]
  }'
```
**Expected Response:** `201 Created`
```json
{
  "success": true,
  "data": {
    "generationContractId": "01a19685-0697-70a2-927d-67e5ae5a0bdc",
    "projectId": "b5e29780-692a-466d-88f6-a81d0f5cb130",
    "version": 1
  }
}
```

---

### Step 2.9: Trigger Code Generation Request
- **Method & Endpoint**: `POST /api/v1/projects/{projectId}/generation-requests`
- **Gateway URL**: `http://localhost:8088/api/v1/projects/{projectId}/generation-requests`
- **Headers**:
  - `Authorization: Bearer $TOKEN`
  - `Content-Type: application/json`
  - `Idempotency-Key: <unique-string>`

```bash
CONTRACT_ID="<PASTE_CONTRACT_ID_HERE>"
IDEMP_KEY="idemp-$(uuidgen | cut -c1-8)"

curl -X POST "http://localhost:8088/api/v1/projects/$PROJECT_ID/generation-requests" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEMP_KEY" \
  -d '{
    "generationContractId": "'"$CONTRACT_ID"'",
    "subdomain": "artisan-roasters"
  }'
```
**Expected Response:** `202 Accepted`
```json
{
  "success": true,
  "data": {
    "generationRequestId": "01a19685-06ab-74ae-85fc-bacd79855c12",
    "status": "captured",
    "repositoryUrl": "https://github.com/WebBlock-Organization/project-b5e29780"
  }
}
```

---

## 6. AI Code Service Endpoints (`webblock-aicode-service`)

Operates on **Port 8000** (Gateway prefix: `/proxy/aicode-service/**` or `/v1/**`).

---

### Step 3.1: Health Check
- **Method & Endpoint**: `GET /health`
- **Direct URL**: `http://localhost:8000/health`
- **Gateway URL**: `http://localhost:8088/proxy/aicode-service/health`

```bash
curl -X GET "http://localhost:8000/health"
```
**Expected Response:** `200 OK`
```json
{
  "status": "ok",
  "service": "WebBlock AI Harness"
}
```

---

### Step 3.2: Create AI Assistant Conversation (Verifies Project ID Cross-Service)
- **Method & Endpoint**: `POST /v1/conversations`
- **Direct URL**: `http://localhost:8000/v1/conversations`
- **Gateway URL**: `http://localhost:8088/proxy/aicode-service/v1/conversations`
- **Headers**: `Content-Type: application/json`
- **Purpose**: Initializes a new design consultation chat session. AI Code Service calls `GET /internal/v1/projects/{projectId}` on Project Service to verify ownership and valid status before proceeding.

**Request Body:**
```json
{
  "project_id": "b5e29780-692a-466d-88f6-a81d0f5cb130",
  "title": "Homepage Design Consultation"
}
```

**Curl Command:**
```bash
curl -X POST "http://localhost:8088/proxy/aicode-service/v1/conversations" \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "'"$PROJECT_ID"'",
    "title": "Homepage Design Consultation"
  }'
```
**Expected Response:** `201 Created`
```json
{
  "conversation_id": "3c7b6f8a-9a1c-4b72-b89e-876543210abc",
  "project_id": "b5e29780-692a-466d-88f6-a81d0f5cb130",
  "title": "Homepage Design Consultation",
  "created_at": "2026-11-01T16:25:00Z"
}
```

---

### Step 3.3: Send Assistant Chat Message
- **Method & Endpoint**: `POST /v1/conversations/{conversationId}/messages`
- **Direct URL**: `http://localhost:8000/v1/conversations/{conversationId}/messages`
- **Gateway URL**: `http://localhost:8088/proxy/aicode-service/v1/conversations/{conversationId}/messages`
- **Headers**: `Content-Type: application/json`

```bash
CONV_ID="<PASTE_CONVERSATION_ID_HERE>"

curl -X POST "http://localhost:8088/proxy/aicode-service/v1/conversations/$CONV_ID/messages" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Make the header darker and emphasize our single-origin pour over coffee."
  }'
```
**Expected Response:** `201 Created` with the AI assistant reply and proposed Puck JSON change set.

---

### Step 3.4: Inspect AI Generation Run
- **Method & Endpoint**: `GET /v1/generation-runs/{runId}`
- **Direct URL**: `http://localhost:8000/v1/generation-runs/{runId}`
- **Gateway URL**: `http://localhost:8088/proxy/aicode-service/v1/generation-runs/{runId}`

```bash
RUN_ID="<PASTE_RUN_ID_HERE>"

curl -X GET "http://localhost:8000/v1/generation-runs/$RUN_ID"
```
**Expected Response:** `200 OK`
```json
{
  "run_id": "9b8beaa3-8dca-4bb5-a0a4-e59b4357cacd",
  "project_id": "b5e29780-692a-466d-88f6-a81d0f5cb130",
  "flow": "INITIAL_GENERATION",
  "status": "SUCCEEDED",
  "step": "DONE",
  "summary": "Generated Next.js + Drizzle project (127 files).",
  "validations": [
    { "validation_type": "INSTALL", "status": "PASSED" },
    { "validation_type": "BUILD", "status": "PASSED" }
  ]
}
```

---

### Step 3.5: Inspect Generated Project Files Snapshot
- **Method & Endpoint**: `GET /v1/generation-runs/{runId}/files`
- **Gateway URL**: `http://localhost:8088/proxy/aicode-service/v1/generation-runs/{runId}/files`

```bash
curl -X GET "http://localhost:8088/proxy/aicode-service/v1/generation-runs/$RUN_ID/files" | jq 'keys'
```
**Expected Response:** JSON array listing all 120+ Next.js, Drizzle, and Tailwind project files.

---

## 7. Notification Service Endpoints (`webblock-notification-service`)

Operates on **Port 8085** (Gateway prefix: `/api/notification/**` and `/internal/v1/alertmanager/**`).

---

### Step 4.1: Health Check
- **Method & Endpoint**: `GET /actuator/health`
- **Direct URL**: `http://localhost:8085/actuator/health`
- **Gateway URL**: `http://localhost:8088/proxy/notification-service/actuator/health`

```bash
curl -X GET "http://localhost:8085/actuator/health"
```
**Expected Response:** `200 OK`
```json
{
  "groups": ["liveness", "readiness"],
  "status": "UP"
}
```

---

### Step 4.2: Generate Knock User Feed Token
- **Method & Endpoint**: `GET /api/notification/v1/users/token`
- **Direct URL**: `http://localhost:8085/api/notification/v1/users/token`
- **Gateway URL**: `http://localhost:8088/api/notification/v1/users/token`
- **Headers**:
  - `Authorization: Bearer $TOKEN`
- **Purpose**: Authenticates via Keycloak JWT, extracts the signed-in user's subject UUID, and issues a short-lived RSA-signed user token for the Knock notification bell in the frontend.

```bash
curl -X GET "http://localhost:8088/api/notification/v1/users/token" \
  -H "Authorization: Bearer $TOKEN"
```
**Expected Response:** `200 OK`
```json
{
  "token": "eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjE3OTM1NTM0NjMsInN1YiI6IjcyNzBiY2YxLTk2NGMtNDBjMS05Y2IwLWNhNzQ2MWQ0NTY1NCIsImlhdCI6MTc5MzU0OTg2M30...",
  "expiresAt": "2026-11-01T17:17:43.163769Z"
}
```

---

### Step 4.3: Ingest Alertmanager Infrastructure Alerts
- **Method & Endpoint**: `POST /internal/v1/alertmanager/alerts`
- **Direct URL**: `http://localhost:8085/internal/v1/alertmanager/alerts`
- **Gateway URL**: `http://localhost:8088/internal/v1/alertmanager/alerts`
- **Headers**:
  - `Authorization: Bearer Yda970gCTRxA+uKn6WT4l47K0S/enU4gvwAnEZhEgEI=`
  - `Content-Type: application/json`
- **Purpose**: Receives infrastructure alerts from Prometheus Alertmanager (`WebsiteDown`, `GenerationStuck`, `WebsiteResourceLimitExceeded`, `PlatformServiceDown`).

**Request Body:**
```json
{
  "alerts": [
    {
      "status": "firing",
      "labels": {
        "alertname": "WebsiteDown",
        "project_id": "b5e29780-692a-466d-88f6-a81d0f5cb130"
      },
      "startsAt": "2026-11-01T23:00:00Z",
      "fingerprint": "a1b2c3d4e5f6"
    }
  ]
}
```

**Curl Command:**
```bash
curl -i -X POST "http://localhost:8088/internal/v1/alertmanager/alerts" \
  -H "Authorization: Bearer Yda970gCTRxA+uKn6WT4l47K0S/enU4gvwAnEZhEgEI=" \
  -H "Content-Type: application/json" \
  -d '{
    "alerts": [
      {
        "status": "firing",
        "labels": {
          "alertname": "WebsiteDown",
          "project_id": "b5e29780-692a-466d-88f6-a81d0f5cb130"
        },
        "startsAt": "2026-11-01T23:00:00Z",
        "fingerprint": "a1b2c3d4e5f6"
      }
    ]
  }'
```
**Expected Response:** `204 No Content`

---

### Step 4.4: RabbitMQ Quorum Queue Topology Reference

| Producer Exchange | Routing Key | Notification Quorum Queue | DLQ | Knock Workflow Triggered |
| :--- | :--- | :--- | :--- | :--- |
| `project.events` | `source_published.v1` | `notification.source_published.v1` | `notification.source_published.v1.dlq` | `generation-completed` |
| `provisioning.events` | `publish_completed.v1` | `notification.publish_completed.v1` | `notification.publish_completed.v1.dlq` | `deployment-succeeded` / `deployment-failed-owner` |
| `payment.events` | `subscription_activated.v1` | `notification.subscription_activated.v1` | `notification.subscription_activated.v1.dlq` | `subscription-activated-admin` |
| `payment.events` | `hosting_entitlement_ended.v1` | `notification.hosting_entitlement_ended.v1` | `notification.hosting_entitlement_ended.v1.dlq` | `subscription-expired-admin` |
| `payment.events` | `payment_due.v1` | `notification.payment_due.v1` | `notification.payment_due.v1.dlq` | `payment-due-owner` |

---

## 8. Automated Cross-Service Verification Script

To run an automated verification covering all 4 microservices and gateway proxying in under 3 seconds:

```bash
python3 tooling/integration/test_all_services_together.py
```

### What This Script Tests:
1. **Live Health Checks**: Verifies `/actuator/health` and `/health` across ports `8081`, `8089`, `8000`, `8085`, and `8088`.
2. **User Service & Keycloak**: Authenticates with test credentials and calls `/api/user/v1/me`.
3. **Notification Service**: Generates a Knock user feed token with JWT and calls the Alertmanager webhook.
4. **Project Service**: Creates a project with business hours, pins a generation contract, and captures a generation request.
5. **AI Code Service**: Validates project existence cross-service against Project Service and checks rejection on invalid project IDs.

---

## 9. Sharing with Remote Teammates (ngrok Tunnel)

To expose the entire platform (Combined Swagger Portal and all 4 microservices) to remote teammates or external evaluators:

```bash
./tunnel.sh
```

This launches an ngrok public HTTPS tunnel pointing to port `8088`. Anyone with the tunnel URL can:
- Open the unified Swagger dashboard in their browser.
- Test all API endpoints directly with live responses.
- Inspect real-time service health statuses.
