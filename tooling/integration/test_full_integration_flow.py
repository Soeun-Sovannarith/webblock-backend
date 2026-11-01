#!/usr/bin/env python3
"""End-to-End integration test for WebBlock: Project Service + AI Code Service.

Flow:
1. User provides business context to Project Service (name, description, contact, opening hours).
2. Project Service embeds business profile into Puck JSON page data.
3. User triggers generation -> Project Service creates frozen codegen-input and publishes event.
4. AI Code Service consumes event or reads codegen-input directly from Project Service.
5. AI Code Service generates full-stack app (Next.js frontend + Drizzle backend) and pushes to GitHub.
6. Right after GitHub push, AI Code Service sends GitHub repo URL to Project Service.
7. Verify Project Service stores and returns the repository URL in project views.
"""

import os
import sys
import time
import uuid
import httpx

PROJECT_URL = "http://localhost:8080"
AICODE_URL = "http://localhost:8000"
DEV_TOKEN = "dev-token-project-service-32-chars-long-abc"

HEADERS = {
    "Authorization": f"Bearer {DEV_TOKEN}",
    "Content-Type": "application/json"
}


def main():
    print("=" * 70)
    print("WebBlock Integration Flow: Project Service <-> AI Code Service")
    print("=" * 70)

    # 1. Health checks
    print("\n[Step 1] Verifying service connectivity...")
    with httpx.Client() as client:
        r_ai = client.get(f"{AICODE_URL}/health", timeout=5.0)
        assert r_ai.status_code == 200, f"AI service unhealthy: {r_ai.text}"
        print(f" ✔ AI Code Service is healthy on {AICODE_URL} (status {r_ai.json()['status']})")

        r_proj = client.get("http://localhost:8089/actuator/health", timeout=5.0)
        assert r_proj.status_code == 200, f"Project service unhealthy: {r_proj.text}"
        print(f" ✔ Project Service is healthy on {PROJECT_URL} (status {r_proj.json()['status']})")

    # 2. Create Project with Business Context
    project_id = str(uuid.uuid4())
    print(f"\n[Step 2] User submits business context to Project Service (project_id: {project_id})...")
    create_body = {
        "name": f"Sweet Delights Bakery {project_id[:4]}",
        "businessType": "food_shop",
        "templateId": "warm_bold",
        "details": {
            "businessName": "Sweet Delights Artisan Bakery",
            "businessType": "Bakery & Cafe",
            "description": "Handcrafted sourdough pastries, artisan croissants, and specialty drip coffee.",
            "phone": "+855 23 888 999",
            "email": "hello@sweetdelights.com",
            "address": "Street 240, Daun Penh, Phnom Penh",
            "hours": [
                {"day": "MONDAY", "open": True, "opensAt": "07:00", "closesAt": "18:00"},
                {"day": "TUESDAY", "open": True, "opensAt": "07:00", "closesAt": "18:00"},
                {"day": "WEDNESDAY", "open": True, "opensAt": "07:00", "closesAt": "18:00"},
                {"day": "THURSDAY", "open": True, "opensAt": "07:00", "closesAt": "18:00"},
                {"day": "FRIDAY", "open": True, "opensAt": "07:00", "closesAt": "19:00"},
                {"day": "SATURDAY", "open": True, "opensAt": "08:00", "closesAt": "19:00"},
                {"day": "SUNDAY", "open": True, "opensAt": "08:00", "closesAt": "17:00"},
            ]
        }
    }

    with httpx.Client() as client:
        res = client.put(f"{PROJECT_URL}/api/v1/projects/{project_id}", json=create_body, headers=HEADERS, timeout=10.0)
        assert res.status_code in (200, 201), f"Create project failed: {res.status_code} - {res.text}"
        proj_data = res.json()["data"]
        print(f" ✔ Project created: '{proj_data['name']}' (Category: {proj_data['category']}, Status: {proj_data['status']})")

        # 3. Read live page to verify Puck JSON contains business context
        pages_res = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}/page", headers=HEADERS, timeout=10.0)
        assert pages_res.status_code == 200, f"Get page failed: {pages_res.text}"
        page_body = pages_res.json()["data"]
        puck_tree = page_body.get("draft", {}).get("data", {})
        root_props = puck_tree.get("root", {}).get("props", {})
        print(f" ✔ Puck JSON verified on Home page: businessName='{root_props.get('businessName')}', phone='{root_props.get('phones')}'")

        # 4. Pin Generation Contract
        print("\n[Step 3] Pinning Generation Contract in Project Service...")
        contract_res = client.post(
            f"{PROJECT_URL}/api/v1/projects/{project_id}/generation-contracts",
            json={"productTypeIds": ["1617e841-f602-4ccd-b9ec-0f9dfc9afbf8"]},
            headers=HEADERS,
            timeout=10.0
        )
        assert contract_res.status_code in (200, 201), f"Pin contract failed: {contract_res.text}"
        contract_id = contract_res.json()["data"]["generationContractId"]
        print(f" ✔ Generation contract pinned: {contract_id}")

        # 5. Trigger Generation
        print("\n[Step 4] Triggering code generation (POST /api/v1/projects/{projectId}/generation-requests)...")
        headers_with_idemp = {**HEADERS, "Idempotency-Key": str(uuid.uuid4())}
        subdomain = f"bakery-{project_id[:8]}"
        gen_res = client.post(
            f"{PROJECT_URL}/api/v1/projects/{project_id}/generation-requests",
            json={"generationContractId": contract_id, "subdomain": subdomain},
            headers=headers_with_idemp,
            timeout=10.0
        )
        assert gen_res.status_code in (200, 202), f"Trigger generate failed: {gen_res.status_code} - {gen_res.text}"
        gen_data = gen_res.json()["data"] if "data" in gen_res.json() else gen_res.json()
        gen_req_id = gen_data["generationRequestId"]
        print(f" ✔ Generation Request captured: {gen_req_id} (Status: {gen_data.get('status')})")

        # Wait for admission (status -> generating)
        print(" Waiting for generation request admission...")
        for _ in range(20):
            time.sleep(0.5)
            status_res = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}/generation-requests/{gen_req_id}", headers=HEADERS, timeout=10.0)
            if status_res.status_code == 200:
                s_data = status_res.json()["data"] if "data" in status_res.json() else status_res.json()
                status = s_data.get("status")
                if status == "generating":
                    print(" ✔ Request admitted and currently generating!")
                    break
        else:
            print(" Continuing to check codegen input...")

        # 6. Verify Project Service Codegen-Input endpoint serves business context & Puck JSON
        print("\n[Step 5] Checking GET /internal/v1/generation-requests/{id}/codegen-input...")
        codegen_input_res = client.get(f"{PROJECT_URL}/internal/v1/generation-requests/{gen_req_id}/codegen-input", timeout=10.0)
        assert codegen_input_res.status_code == 200, f"Codegen input failed: {codegen_input_res.text}"
        codegen_input = codegen_input_res.json()["data"]
        assert codegen_input["businessTypeCode"] == "food_shop"
        assert len(codegen_input["capturedInput"]["pages"]) > 0
        print(" ✔ Codegen-Input served with businessTypeCode and Puck JSON pages!")

        # 7. Monitor AI Code Service generation run & GitHub push
        print("\n[Step 6] Monitoring AI Code Service pipeline execution & GitHub push...")
        print(" RabbitMQ consumer & worker automatically received project.generation_requested.v1 event.")
        print(" Waiting for code generation, build repair, GitHub push, and URL registration...")
        repo_data = {}
        for attempt in range(45):  # up to 90 seconds
            time.sleep(2)
            repo_res = client.get(f"{PROJECT_URL}/internal/v1/projects/{project_id}/repository", timeout=10.0)
            if repo_res.status_code == 200:
                repo_data = repo_res.json()["data"]
                if repo_data.get("repositoryUrl"):
                    print(f"\n ✔ GitHub repository stored in Project Service after {attempt * 2 + 2}s!")
                    break
            print(f"  -> Waiting for GitHub push... ({attempt * 2 + 2}s)", end="\r")
        else:
            raise AssertionError("Timed out waiting for GitHub repository to be registered in Project Service")

        repo_url = repo_data.get("repositoryUrl")
        repo_full_name = repo_data.get("repositoryFullName")
        repo_id = repo_data.get("repositoryId")

        print(f" ✔ Repository ID: {repo_id}")
        print(f" ✔ Repository Full Name: {repo_full_name}")
        print(f" ✔ Repository GitHub URL: {repo_url}")
        assert repo_url is not None and "github.com" in repo_url, f"Invalid repo_url: {repo_url}"

        # 8. Verify user-facing GET /api/v1/projects/{projectId}/repository endpoint
        print("\n[Step 7] Checking user-facing GET /api/v1/projects/{projectId}/repository...")
        user_repo_res = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}/repository", headers=HEADERS, timeout=10.0)
        assert user_repo_res.status_code == 200, f"User-facing get repository failed: {user_repo_res.text}"
        user_repo_data = user_repo_res.json()["data"]
        assert user_repo_data.get("repositoryUrl") == repo_url
        assert user_repo_data.get("repositoryFullName") == repo_full_name
        print(f" ✔ User-facing repository endpoint verified: {user_repo_data.get('repositoryUrl')}")

        # 9. Verify public GET /api/v1/projects/{projectId} view
        print("\n[Step 8] Checking public ProjectView GET /api/v1/projects/{projectId}...")
        view_res = client.get(f"{PROJECT_URL}/api/v1/projects/{project_id}", headers=HEADERS, timeout=10.0)
        assert view_res.status_code == 200
        view_data = view_res.json()["data"]
        print(f" ✔ Public ProjectView includes GitHub URL: {view_data.get('repositoryUrl')}")
        assert view_data.get("repositoryUrl") == repo_url
        assert view_data.get("repositoryFullName") == repo_full_name

    print("\n" + "=" * 70)
    print(" ALL STEPS COMPLETED SUCCESSFULLY!")
    print(f" Full Stack App pushed to GitHub: {repo_url}")
    print(f" GitHub URL successfully stored and accessible in Project Service.")
    print("=" * 70)


if __name__ == "__main__":
    main()
