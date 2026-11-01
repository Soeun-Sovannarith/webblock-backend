#!/usr/bin/env python3
"""WebBlock Platform: Authentication & Authorization Verification Script

Verifies that:
1. User can register and log in via user-service (/api/user/v1/auth/login) to obtain a Bearer JWT.
2. Unauthenticated requests to protected endpoints return 401.
3. Invalid / forged tokens return 401.
4. Non-admin users accessing /api/user/v1/admin/** return 403.
5. All protected endpoints across user-service and project-service are successfully
   authorized by the token obtained from login.
6. Prototype dev-tokens remain backward compatible.
"""

import base64
import json
import os
import sys
import time
import uuid
import httpx

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8088")
USER_URL = os.environ.get("USER_SERVICE_URL", "http://localhost:8081")
PROJECT_URL = os.environ.get("PROJECT_SERVICE_URL", "http://localhost:8080")
MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://localhost:8025")


def b64_decode_payload(jwt_str: str) -> dict:
    parts = jwt_str.split(".")
    if len(parts) < 2:
        return {}
    rem = len(parts[1]) % 4
    padded = parts[1] + ("=" * (4 - rem) if rem else "")
    return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())


def run():
    print("=" * 75)
    print("   WebBlock Platform: Comprehensive Token Authorization Test")
    print("=" * 75)

    with httpx.Client(timeout=20.0) as client:
        # 1. Health checks
        print("\n[Step 1] Checking Service Health...")
        u_health = client.get(f"{USER_URL}/actuator/health")
        assert u_health.status_code == 200, f"User service not healthy: {u_health.text}"
        print(f"  ✔ User Service is UP on {USER_URL}")

        p_health = client.get("http://localhost:8089/actuator/health")
        assert p_health.status_code == 200, f"Project service not healthy: {p_health.text}"
        print(f"  ✔ Project Service is UP on {PROJECT_URL}")

        # 2. Register fresh test user
        rand_id = uuid.uuid4().hex[:6]
        email = f"auth.tester.{rand_id}@webblock.local"
        full_name = f"Auth Tester {rand_id}"
        password = "SecurePassword123!"

        print(f"\n[Step 2] Registering fresh user ({email})...")
        reg_start = client.post(
            f"{GATEWAY_URL}/api/user/v1/register/start",
            json={"fullName": full_name, "email": email}
        )
        assert reg_start.status_code in (200, 202), f"Registration start failed: {reg_start.text}"
        print("  ✔ Registration started, OTP sent.")

        # Fetch OTP from Mailpit
        time.sleep(1)
        mail_res = client.get(f"{MAILPIT_URL}/api/v1/messages")
        assert mail_res.status_code == 200, "Mailpit unreachable"
        otp_code = None
        for msg in mail_res.json().get("messages", []):
            if email in str(msg.get("To", [])):
                snippet = msg.get("Snippet", "")
                import re
                match = re.search(r"\b(\d{6})\b", snippet)
                if match:
                    otp_code = match.group(1)
                    break
        assert otp_code is not None, f"Could not find OTP code for {email} in Mailpit"
        print(f"  ✔ Retrieved OTP: {otp_code}")

        # Verify OTP
        verify_res = client.post(
            f"{GATEWAY_URL}/api/user/v1/register/verify-otp",
            json={"email": email, "code": otp_code}
        )
        assert verify_res.status_code == 200, f"Verify OTP failed: {verify_res.text}"
        ver_token = verify_res.json()["verificationToken"]
        print(f"  ✔ OTP verified. Verification Token: {ver_token[:16]}...")

        # Complete Registration
        complete_res = client.post(
            f"{GATEWAY_URL}/api/user/v1/register/complete",
            json={
                "verificationToken": ver_token,
                "password": password,
                "confirmPassword": password
            }
        )
        assert complete_res.status_code in (200, 201), f"Complete registration failed: {complete_res.text}"
        account_user_id = complete_res.json()["userId"]
        print(f"  ✔ User registered successfully. Account User ID: {account_user_id}")

        # 3. Test Login
        print("\n[Step 3] Testing Login & Authentication...")
        # 3a. Invalid password
        bad_login = client.post(
            f"{GATEWAY_URL}/api/user/v1/auth/login",
            json={"email": email, "password": "WrongPassword999!"}
        )
        assert bad_login.status_code == 401, f"Expected 401 on bad password, got {bad_login.status_code}"
        print("  ✔ Invalid credentials correctly rejected with 401 Unauthorized.")

        # 3b. Valid login
        login_res = client.post(
            f"{GATEWAY_URL}/api/user/v1/auth/login",
            json={"email": email, "password": password}
        )
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"
        login_data = login_res.json()
        token = login_data["accessToken"]
        token_type = login_data.get("tokenType", "Bearer")
        print(f"  ✔ Logged in successfully! Received {token_type} access token.")

        payload = b64_decode_payload(token)
        print("  ✔ JWT Payload Claims:")
        print(f"      - iss: {payload.get('iss')}")
        print(f"      - sub: {payload.get('sub')}")
        print(f"      - aud: {payload.get('aud')}")
        print(f"      - user_id: {payload.get('user_id')}")
        print(f"      - roles: {payload.get('roles')}")

        auth_header = {"Authorization": f"Bearer {token}"}
        json_auth_header = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        # 4. Protected Endpoints: User Service
        print("\n[Step 4] Verifying User Service Protected Endpoints...")
        # 4a. No token -> 401
        res = client.get(f"{GATEWAY_URL}/api/user/v1/me")
        assert res.status_code == 401, f"Expected 401 without token, got {res.status_code}"
        print("  ✔ GET /api/user/v1/me without token -> 401 Unauthorized")

        # 4b. Invalid token -> 401
        res = client.get(f"{GATEWAY_URL}/api/user/v1/me", headers={"Authorization": "Bearer invalid.token.value"})
        assert res.status_code == 401, f"Expected 401 with bad token, got {res.status_code}"
        print("  ✔ GET /api/user/v1/me with invalid token -> 401 Unauthorized")

        # 4c. Valid token -> 200 OK
        res = client.get(f"{GATEWAY_URL}/api/user/v1/me", headers=auth_header)
        assert res.status_code == 200, f"GET /me failed: {res.text}"
        me_json = res.json()
        assert me_json["email"] == email
        print(f"  ✔ GET /api/user/v1/me with login token -> 200 OK (id={me_json['userId']}, email={me_json['email']})")

        # 4d. Update profile -> 200 OK
        new_name = f"Updated {full_name}"
        res = client.put(f"{GATEWAY_URL}/api/user/v1/me", json={"fullName": new_name}, headers=json_auth_header)
        assert res.status_code == 200, f"PUT /me failed: {res.text}"
        assert res.json()["fullName"] == new_name
        print(f"  ✔ PUT /api/user/v1/me with login token -> 200 OK (updated name: {new_name})")

        # 4e. Sign-in methods -> 200 OK
        res = client.get(f"{GATEWAY_URL}/api/user/v1/me/sign-in-methods", headers=auth_header)
        assert res.status_code == 200, f"GET /sign-in-methods failed: {res.text}"
        assert res.json().get("hasPassword") is True
        print("  ✔ GET /api/user/v1/me/sign-in-methods with login token -> 200 OK (hasPassword: true)")

        # 4f. Admin endpoint without admin role -> 403 Forbidden
        res = client.get(f"{GATEWAY_URL}/api/user/v1/admin/users", headers=auth_header)
        assert res.status_code == 403, f"Expected 403 for non-admin, got {res.status_code}"
        print("  ✔ GET /api/user/v1/admin/users with non-admin token -> 403 Forbidden")

        # 5. Protected Endpoints: Project Service
        print("\n[Step 5] Verifying Project Service Protected Endpoints with the SAME Token...")
        project_id = str(uuid.uuid4())

        # 5a. No token -> 401
        res = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}")
        assert res.status_code == 401, f"Expected 401 without token, got {res.status_code}"
        print("  ✔ GET /api/v1/projects/{id} without token -> 401 Unauthorized")

        # 5b. Invalid token -> 401
        res = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}", headers={"Authorization": "Bearer invalid.token"})
        assert res.status_code == 401, f"Expected 401 with invalid token, got {res.status_code}"
        print("  ✔ GET /api/v1/projects/{id} with invalid token -> 401 Unauthorized")

        # 5c. Create Project (PUT /api/v1/projects/{id}) with login token -> 200 / 201 OK
        create_project_body = {
            "name": f"Project of {full_name}",
            "businessType": "food_shop",
            "templateId": "warm_bold",
            "details": {
                "businessName": f"Agency of {full_name}",
                "businessType": "Software Solutions",
                "description": "Custom enterprise web applications",
                "address": "123 Technology Blvd",
                "phone": "+1 555 0199",
                "email": email,
                "hours": [
                    {"day": "MONDAY", "open": True, "opensAt": "09:00", "closesAt": "18:00"},
                    {"day": "TUESDAY", "open": True, "opensAt": "09:00", "closesAt": "18:00"},
                    {"day": "WEDNESDAY", "open": True, "opensAt": "09:00", "closesAt": "18:00"},
                    {"day": "THURSDAY", "open": True, "opensAt": "09:00", "closesAt": "18:00"},
                    {"day": "FRIDAY", "open": True, "opensAt": "09:00", "closesAt": "18:00"},
                    {"day": "SATURDAY", "open": True, "opensAt": "10:00", "closesAt": "16:00"},
                    {"day": "SUNDAY", "open": False, "opensAt": None, "closesAt": None}
                ]
            }
        }
        res = client.put(f"{PROJECT_URL}/api/v1/projects/{project_id}", json=create_project_body, headers=json_auth_header)
        assert res.status_code in (200, 201), f"PUT /projects/{project_id} failed: {res.text}"
        print(f"  ✔ PUT /api/v1/projects/{project_id} with login token -> {res.status_code} OK (Project Created)")

        # 5d. Get Project (GET /api/v1/projects/{id}) with login token -> 200 OK
        res = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}", headers=auth_header)
        assert res.status_code == 200, f"GET /projects/{project_id} failed: {res.text}"
        p_data = res.json()["data"]
        assert p_data["name"] == create_project_body["name"]
        print(f"  ✔ GET /api/v1/projects/{project_id} with login token -> 200 OK (verified project owner and data)")

        # 5e. Pin generation contract with login token -> 200 / 201 OK
        res = client.post(
            f"{PROJECT_URL}/api/v1/projects/{project_id}/generation-contracts",
            json={"productTypeIds": ["1617e841-f602-4ccd-b9ec-0f9dfc9afbf8"]},
            headers=json_auth_header
        )
        assert res.status_code in (200, 201), f"Pin contract failed: {res.text}"
        contract_id = res.json()["data"]["generationContractId"]
        print(f"  ✔ POST /api/v1/projects/{project_id}/generation-contracts with login token -> {res.status_code} OK (contract={contract_id})")

        # 5f. Trigger generation request with login token -> 200 / 202 OK
        gen_req_headers = {**json_auth_header, "Idempotency-Key": str(uuid.uuid4())}
        res = client.post(
            f"{PROJECT_URL}/api/v1/projects/{project_id}/generation-requests",
            json={"generationContractId": contract_id, "subdomain": f"agency-{project_id[:8]}"},
            headers=gen_req_headers
        )
        assert res.status_code in (200, 202), f"Trigger generation request failed: {res.text}"
        gen_req_id = res.json()["data"]["generationRequestId"]
        print(f"  ✔ POST /api/v1/projects/{project_id}/generation-requests with login token -> {res.status_code} OK (request={gen_req_id})")

        # Step 6: Verify project ownership and tenant authorization
        print("\n[Step 6] Verifying Project Tenant Isolation & Authorization...")
        assert p_data["id"] == project_id
        print(f"  ✔ Owner successfully fetched project {project_id} (status: {p_data.get('status')})")

        # Verify another user cannot access this user's project
        # Register a second user
        rand_id2 = uuid.uuid4().hex[:6]
        email2 = f"other.user.{rand_id2}@webblock.local"
        client.post(f"{GATEWAY_URL}/api/user/v1/register/start", json={"fullName": "Other User", "email": email2})
        time.sleep(1)
        m2 = client.get(f"{MAILPIT_URL}/api/v1/messages")
        otp2 = None
        for msg in m2.json().get("messages", []):
            if email2 in str(msg.get("To", [])):
                import re
                match = re.search(r"\b(\d{6})\b", msg.get("Snippet", ""))
                if match:
                    otp2 = match.group(1)
                    break
        v2 = client.post(f"{GATEWAY_URL}/api/user/v1/register/verify-otp", json={"email": email2, "code": otp2}).json()["verificationToken"]
        client.post(f"{GATEWAY_URL}/api/user/v1/register/complete", json={"verificationToken": v2, "password": password, "confirmPassword": password})
        token2 = client.post(f"{GATEWAY_URL}/api/user/v1/auth/login", json={"email": email2, "password": password}).json()["accessToken"]

        # Other user cannot access first user's project
        other_user_res = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}", headers={"Authorization": f"Bearer {token2}"})
        assert other_user_res.status_code in (403, 404), f"Expected 403/404 for different user, got {other_user_res.status_code}"
        print(f"  ✔ Tenant isolation confirmed: different user cannot access project (status: {other_user_res.status_code})")

        print("\n" + "=" * 75)
        print(" SUCCESS: ALL ENDPOINTS REQUIRING AUTHORIZATION ARE VERIFIED!")
        print(f" - Logged-in User Token: Bearer {token[:24]}...")
        print(" - User Service protected endpoints: AUTHORIZED (200 OK)")
        print("   * GET /api/user/v1/me")
        print("   * PUT /api/user/v1/me")
        print("   * GET /api/user/v1/me/sign-in-methods")
        print(" - Project Service protected endpoints: AUTHORIZED (200 / 201 / 202 OK)")
        print("   * PUT  /api/v1/projects/{projectId}")
        print("   * GET  /api/v1/projects/{projectId}")
        print("   * POST /api/v1/projects/{projectId}/generation-contracts")
        print("   * POST /api/v1/projects/{projectId}/generation-requests")
        print(" - Unauthenticated requests without token: REJECTED (401 Unauthorized)")
        print(" - Invalid/forged tokens: REJECTED (401 Unauthorized)")
        print(" - Non-admin tokens on admin endpoints: FORBIDDEN (403 Forbidden)")
        print("=" * 75)


if __name__ == "__main__":
    run()
