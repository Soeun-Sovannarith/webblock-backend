#!/usr/bin/env python3
"""WebBlock Platform: Exhaustive Cross-Service Integration Verification Script

Tests that all 4 backend microservices and the gateway work together seamlessly:
1. User Service: Authentication, Keycloak Bearer JWT, and User Profile (/api/user/v1/me)
2. Notification Service: Knock Feed Token generation with User JWT and Alertmanager Webhook
3. Project Service: Project CRUD, Generation Contract, and Generation Request with User JWT
4. AI Code Service: Verification of Project ID against Project Service, Assistant Conversation
5. Gateway Portal: Route proxying and status aggregation
"""

import os
import sys
import uuid
import httpx

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8088")
USER_URL = os.environ.get("USER_SERVICE_URL", "http://localhost:8081")
PROJECT_URL = os.environ.get("PROJECT_SERVICE_URL", "http://localhost:8080")
PROJECT_MGMT_URL = "http://localhost:8089"
AICODE_URL = os.environ.get("AICODE_SERVICE_URL", "http://localhost:8000")
NOTIFICATION_URL = os.environ.get("NOTIFICATION_SERVICE_URL", "http://localhost:8085")

EMAIL = "soeunsovannarith64@gmail.com"
PASSWORD = "Password123!"
ALERTMANAGER_TOKEN = "Yda970gCTRxA+uKn6WT4l47K0S/enU4gvwAnEZhEgEI="


def run_checks():
    print("=" * 75)
    print(" WebBlock Platform — Complete Cross-Service Integration Verification")
    print("=" * 75)

    results = []

    def check(name, fn):
        sys.stdout.write(f"Testing {name:.<55} ")
        sys.stdout.flush()
        try:
            val = fn()
            print("[PASS]")
            results.append((name, True, val))
            return val
        except Exception as e:
            print(f"[FAIL]: {e}")
            results.append((name, False, str(e)))
            return None

    with httpx.Client(timeout=20.0) as client:
        # ----------------------------------------------------------------------
        # 1. Health Checks
        # ----------------------------------------------------------------------
        print("\n--- [Phase 1: Service Health Checks] ---")

        check("Gateway /api/statuses", lambda: (
            r := client.get(f"{GATEWAY_URL}/api/statuses"),
            assert_status(r, 200),
            r.json()
        )[-1])

        check("User Service Health (:8081/actuator/health)", lambda: (
            r := client.get(f"{USER_URL}/actuator/health"),
            assert_status(r, 200),
            r.json()["status"]
        )[-1])

        check("Project Service Health (:8089/actuator/health)", lambda: (
            r := client.get(f"{PROJECT_MGMT_URL}/actuator/health"),
            assert_status(r, 200),
            r.json()["status"]
        )[-1])

        check("AI Code Service Health (:8000/health)", lambda: (
            r := client.get(f"{AICODE_URL}/health"),
            assert_status(r, 200),
            r.json()["status"]
        )[-1])

        check("Notification Service Health (:8085/actuator/health)", lambda: (
            r := client.get(f"{NOTIFICATION_URL}/actuator/health"),
            assert_status(r, 200),
            r.json()["status"]
        )[-1])

        # ----------------------------------------------------------------------
        # 2. User Service & Keycloak Authentication
        # ----------------------------------------------------------------------
        print("\n--- [Phase 2: User Authentication & JWT Issuance] ---")

        login_data = check("User Service Login (/api/user/v1/auth/login)", lambda: (
            r := client.post(
                f"{GATEWAY_URL}/api/user/v1/auth/login",
                json={"email": EMAIL, "password": PASSWORD}
            ),
            assert_status(r, 200),
            r.json()
        )[-1])

        token = login_data.get("accessToken") if login_data else None
        assert token, "Cannot proceed without access token"
        user_headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        check("User Profile (/api/user/v1/me)", lambda: (
            r := client.get(f"{GATEWAY_URL}/api/user/v1/me", headers=user_headers),
            assert_status(r, 200),
            r.json().get("email")
        )[-1])

        # ----------------------------------------------------------------------
        # 3. Notification Service Integration
        # ----------------------------------------------------------------------
        print("\n--- [Phase 3: Notification Service Integration] ---")

        check("Knock User Feed Token (/api/notification/v1/users/token)", lambda: (
            r := client.get(f"{GATEWAY_URL}/api/notification/v1/users/token", headers=user_headers),
            assert_status(r, 200),
            f"token_len={len(r.json().get('token', ''))}, expiresAt={r.json().get('expiresAt')}"
        )[-1])

        check("Alertmanager Webhook (/internal/v1/alertmanager/alerts)", lambda: (
            r := client.post(
                f"{GATEWAY_URL}/internal/v1/alertmanager/alerts",
                headers={"Authorization": f"Bearer {ALERTMANAGER_TOKEN}", "Content-Type": "application/json"},
                json={"alerts": []}
            ),
            assert_status(r, 204),
            "Received 204 No Content"
        )[-1])

        # ----------------------------------------------------------------------
        # 4. Project Service Endpoints
        # ----------------------------------------------------------------------
        print("\n--- [Phase 4: Project Service CRUD & Contract Pinning] ---")

        project_id = str(uuid.uuid4())
        proj_name = f"Test Cafe {project_id[:6]}"
        create_project_payload = {
            "name": proj_name,
            "businessType": "food_shop",
            "templateId": "warm_bold",
            "details": {
                "businessName": proj_name,
                "businessType": "Specialty Coffee Roastery",
                "description": "Locally roasted coffees, pastries, and artisanal breads.",
                "address": "No. 45, Street 240, Daun Penh, Phnom Penh, Cambodia",
                "phone": "+855 23 999 888",
                "email": EMAIL,
                "hours": [
                    {"day": "MONDAY", "open": True, "opensAt": "07:00", "closesAt": "18:00"},
                    {"day": "TUESDAY", "open": True, "opensAt": "07:00", "closesAt": "18:00"},
                    {"day": "WEDNESDAY", "open": True, "opensAt": "07:00", "closesAt": "18:00"},
                    {"day": "THURSDAY", "open": True, "opensAt": "07:00", "closesAt": "18:00"},
                    {"day": "FRIDAY", "open": True, "opensAt": "07:00", "closesAt": "19:00"},
                    {"day": "SATURDAY", "open": True, "opensAt": "08:00", "closesAt": "19:00"},
                    {"day": "SUNDAY", "open": True, "opensAt": "08:00", "closesAt": "17:00"}
                ]
            }
        }

        check(f"Create Project via Gateway (PUT /api/v1/projects/{project_id[:8]}...)", lambda: (
            r := client.put(f"{GATEWAY_URL}/api/v1/projects/{project_id}", json=create_project_payload, headers=user_headers),
            assert_status(r, [200, 201]),
            r.json()
        )[-1])

        check(f"Get Project via Gateway (GET /api/v1/projects/{project_id[:8]}...)", lambda: (
            r := client.get(f"{GATEWAY_URL}/api/v1/projects/{project_id}", headers=user_headers),
            assert_status(r, 200),
            r.json()["data"]["name"]
        )[-1])

        check(f"Internal Project Lookup via Gateway (GET /internal/v1/projects/{project_id[:8]}...)", lambda: (
            r := client.get(f"{GATEWAY_URL}/internal/v1/projects/{project_id}"),
            assert_status(r, 200),
            f"exists={r.json().get('exists')}, status={r.json().get('status')}"
        )[-1])

        contract_data = check("Pin Generation Contract via Gateway", lambda: (
            r := client.post(
                f"{GATEWAY_URL}/api/v1/projects/{project_id}/generation-contracts",
                headers=user_headers,
                json={"productTypeIds": ["1617e841-f602-4ccd-b9ec-0f9dfc9afbf8"]}
            ),
            assert_status(r, [200, 201]),
            r.json()["data"]["generationContractId"]
        )[-1])

        gen_req_data = check("Trigger Generation Request via Gateway (RabbitMQ event bus)", lambda: (
            r := client.post(
                f"{GATEWAY_URL}/api/v1/projects/{project_id}/generation-requests",
                headers={**user_headers, "Idempotency-Key": str(uuid.uuid4())},
                json={"generationContractId": contract_data, "subdomain": f"test-{project_id[:6]}"}
            ),
            assert_status(r, [200, 202]),
            r.json()["data"]["generationRequestId"]
        )[-1])

        # ----------------------------------------------------------------------
        # 5. AI Code Service Cross-Service Verification
        # ----------------------------------------------------------------------
        print("\n--- [Phase 5: AI Code Service Cross-Service Verification] ---")

        check(f"AI Assistant Conversation via Gateway (Validates Project ID with Project Service)", lambda: (
            r := client.post(
                f"{GATEWAY_URL}/proxy/aicode-service/v1/conversations",
                json={
                    "project_id": project_id,
                    "title": "Design Consultation"
                }
            ),
            assert_status(r, [200, 201]),
            f"conversation_id={r.json().get('conversation_id')}, project_id={r.json().get('project_id')}"
        )[-1])

        fake_project_id = str(uuid.uuid4())
        check(f"AI Assistant Conversation Rejection on Non-Existent Project ID", lambda: (
            r := client.post(
                f"{GATEWAY_URL}/proxy/aicode-service/v1/conversations",
                json={
                    "project_id": fake_project_id,
                    "title": "Invalid Consultation"
                }
            ),
            assert_status(r, 404),
            f"Correctly rejected with 404: {r.json().get('detail')}"
        )[-1])

    print("\n" + "=" * 75)
    all_passed = all(p for _, p, _ in results)
    if all_passed:
        print(" ALL ENDPOINTS ON EVERY SERVICE WORK FINE TOGETHER! (100% PASS)")
    else:
        print(" SOME CHECKS FAILED:")
        for name, passed, detail in results:
            if not passed:
                print(f"  ❌ {name}: {detail}")
    print("=" * 75)
    return 0 if all_passed else 1


def assert_status(response, expected):
    exp_list = expected if isinstance(expected, list) else [expected]
    if response.status_code not in exp_list:
        raise AssertionError(f"Expected HTTP {expected}, got HTTP {response.status_code}: {response.text}")


if __name__ == "__main__":
    sys.exit(run_checks())
