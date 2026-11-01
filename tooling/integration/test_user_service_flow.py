#!/usr/bin/env python3
"""WebBlock Platform: End-to-End User Service Registration & Cross-Service Integration Test

Tests:
1. User Registration: Step 1 (Start / OTP generation) -> Step 2 (Verify OTP via Mailpit) -> Step 3 (Complete with password)
2. User Authentication: Keycloak Direct Access Grant -> Bearer JWT Token
3. User Profile: GET /api/user/v1/me with JWT Token
4. Project Creation: User creates a project in Project Service
5. Generation Pipeline: Pin contract -> Trigger generation request -> AI Code Service compiles & pushes to GitHub
6. Repository Persistence: Project Service stores and returns the repository URL for the user's project
"""

import os
import sys
import time
import uuid
import httpx

USER_URL = os.environ.get("USER_SERVICE_URL", "http://localhost:8081")
PROJECT_URL = os.environ.get("PROJECT_SERVICE_URL", "http://localhost:8080")
AICODE_URL = os.environ.get("AICODE_SERVICE_URL", "http://localhost:8000")
MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://localhost:8025")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://localhost:8088")


def main():
    print("=" * 70)
    print(" WebBlock End-to-End Flow: User -> Project -> AI Code -> GitHub")
    print("=" * 70)

    with httpx.Client(timeout=15.0) as client:
        # Step 1: Health checks
        print("\n[Step 1] Checking service health...")
        user_health = client.get(f"{USER_URL}/actuator/health")
        assert user_health.status_code == 200, f"User service unhealthy: {user_health.text}"
        print(f" ✔ User Service is healthy on {USER_URL}")

        project_health = client.get("http://localhost:8089/actuator/health")
        assert project_health.status_code == 200, f"Project service unhealthy: {project_health.text}"
        print(f" ✔ Project Service is healthy on {PROJECT_URL} (management :8089)")

        aicode_health = client.get(f"{AICODE_URL}/health")
        assert aicode_health.status_code == 200, f"AI Code service unhealthy: {aicode_health.text}"
        print(f" ✔ AI Code Service is healthy on {AICODE_URL}")

        # Step 2: Register User
        rand_id = uuid.uuid4().hex[:6]
        email = f"user.{rand_id}@webblock.local"
        full_name = f"Test User {rand_id}"
        password = "Password123!"

        print(f"\n[Step 2] Registering user ({email})...")
        reg_start = client.post(
            f"{GATEWAY_URL}/api/user/v1/register/start",
            json={"fullName": full_name, "email": email}
        )
        assert reg_start.status_code in (200, 202), f"Registration start failed: {reg_start.text}"
        print(" ✔ Registration started. OTP sent to Mailpit.")

        # Fetch OTP code from Mailpit
        time.sleep(1)
        mail_res = client.get(f"{MAILPIT_URL}/api/v1/messages")
        assert mail_res.status_code == 200, "Could not reach Mailpit"
        messages = mail_res.json().get("messages", [])
        otp_code = None
        for msg in messages:
            if email in str(msg.get("To", [])):
                snippet = msg.get("Snippet", "")
                import re
                match = re.search(r"\b(\d{6})\b", snippet)
                if match:
                    otp_code = match.group(1)
                    break
        assert otp_code is not None, f"Could not find OTP code for {email} in Mailpit"
        print(f" ✔ Retrieved OTP from email: {otp_code}")

        # Verify OTP
        verify_res = client.post(
            f"{GATEWAY_URL}/api/user/v1/register/verify-otp",
            json={"email": email, "code": otp_code}
        )
        assert verify_res.status_code == 200, f"Verify OTP failed: {verify_res.text}"
        ver_token = verify_res.json()["verificationToken"]
        print(f" ✔ OTP verified! Verification Token: {ver_token[:16]}...")

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
        user_id = complete_res.json()["userId"]
        print(f" ✔ User registered in Keycloak & database! User ID: {user_id}")

        # Step 3: Authenticate and fetch profile
        print("\n[Step 3] Authenticating user & verifying /api/user/v1/me...")
        login_res = client.post(
            f"{GATEWAY_URL}/api/user/v1/auth/login",
            json={"email": email, "password": password}
        )
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"
        token = login_res.json()["accessToken"]
        print(" ✔ Bearer JWT access token received from user service login.")

        me_res = client.get(f"{GATEWAY_URL}/api/user/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert me_res.status_code == 200, f"Get /me failed: {me_res.text}"
        me_data = me_res.json()
        print(f" ✔ /api/user/v1/me profile verified: {me_data['fullName']} ({me_data['email']})")

        # Step 4: Create Project in Project Service with user context
        print("\n[Step 4] Creating project in Project Service...")
        project_id = str(uuid.uuid4())
        # Use the Bearer token from user-service login
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        create_project_body = {
            "name": f"Bakery of {full_name}",
            "businessType": "food_shop",
            "templateId": "warm_bold",
            "details": {
                "businessName": f"Bakery of {full_name}",
                "businessType": "Bakery & Cafe",
                "description": "Artisanal breads and French pastries",
                "address": "456 Main St, Suite 10",
                "phone": "+855 12 345 678",
                "email": email,
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
        proj_res = client.put(f"{PROJECT_URL}/api/v1/projects/{project_id}", json=create_project_body, headers=headers)
        assert proj_res.status_code in (200, 201), f"Create project failed: {proj_res.text}"
        print(f" ✔ Project created: '{create_project_body['name']}' (ID: {project_id})")

        # Step 5: Pin Contract & Trigger Generation Request
        print("\n[Step 5] Pinning Generation Contract & triggering generation...")
        contract_res = client.post(
            f"{PROJECT_URL}/api/v1/projects/{project_id}/generation-contracts",
            json={"productTypeIds": ["1617e841-f602-4ccd-b9ec-0f9dfc9afbf8"]},
            headers=headers
        )
        assert contract_res.status_code in (200, 201), f"Pin contract failed: {contract_res.text}"
        contract_id = contract_res.json()["data"]["generationContractId"]
        print(f" ✔ Generation contract pinned: {contract_id}")

        subdomain = f"bakery-{project_id[:8]}"
        idemp_headers = {**headers, "Idempotency-Key": str(uuid.uuid4())}
        gen_res = client.post(
            f"{PROJECT_URL}/api/v1/projects/{project_id}/generation-requests",
            json={"generationContractId": contract_id, "subdomain": subdomain},
            headers=idemp_headers
        )
        assert gen_res.status_code in (200, 202), f"Trigger generation failed: {gen_res.text}"
        gen_req_id = gen_res.json()["data"]["generationRequestId"]
        print(f" ✔ Generation Request captured: {gen_req_id}")

        # Wait for admission
        for _ in range(15):
            time.sleep(0.5)
            chk = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}/generation-requests/{gen_req_id}", headers=headers)
            if chk.status_code == 200 and chk.json()["data"].get("status") == "generating":
                print(" ✔ Generation request admitted and generating!")
                break

        # Step 6: Monitor AI Code Service push to GitHub
        print("\n[Step 6] Monitoring AI Code Service generation and GitHub push...")
        repo_data = {}
        for attempt in range(80):
            time.sleep(2)
            repo_res = client.get(f"{PROJECT_URL}/internal/v1/projects/{project_id}/repository")
            if repo_res.status_code == 200:
                repo_data = repo_res.json()["data"]
                if repo_data.get("repositoryUrl"):
                    print(f"\n ✔ GitHub repository registered in Project Service after {attempt * 2 + 2}s!")
                    break
            # Also poll AI run status for richer feedback
            run_check = client.get(f"{AICODE_URL}/internal/v1/projects/{project_id}/runs?limit=1")
            step_info = ""
            if run_check.status_code == 200 and run_check.json():
                latest_run = run_check.json()[0]
                step_info = f" [Step: {latest_run.get('step')}, Status: {latest_run.get('status')}]"
            print(f"  -> Waiting for build & push... ({attempt * 2 + 2}s){step_info}    ", end="\r")
        else:
            raise AssertionError("Timed out waiting for repository URL")

        repo_url = repo_data.get("repositoryUrl")
        repo_full_name = repo_data.get("repositoryFullName")
        repo_id = repo_data.get("repositoryId")

        print(f" ✔ Repository ID: {repo_id}")
        print(f" ✔ Repository Full Name: {repo_full_name}")
        print(f" ✔ Repository GitHub URL: {repo_url}")
        assert repo_url is not None and "github.com" in repo_url

        # Step 7: Verify public user-facing repository view
        print("\n[Step 7] Verifying public user-facing endpoints...")
        user_repo = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}/repository", headers=headers)
        assert user_repo.status_code == 200
        assert user_repo.json()["data"]["repositoryUrl"] == repo_url

        view_res = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}", headers=headers)
        assert view_res.status_code == 200
        assert view_res.json()["data"]["repositoryUrl"] == repo_url
        print(f" ✔ Verified repository in ProjectView: {view_res.json()['data']['repositoryUrl']}")

    print("\n" + "=" * 70)
    print(" ALL INTEGRATION STEPS COMPLETED SUCCESSFULLY!")
    print(f" User registered: {email}")
    print(f" Project created: {project_id}")
    print(f" GitHub repo generated & stored: {repo_url}")
    print("=" * 70)


if __name__ == "__main__":
    main()
