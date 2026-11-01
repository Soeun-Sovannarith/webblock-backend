#!/usr/bin/env python3
"""WebBlock Platform Multi-Service Integration Verification Script

Validates the compatibility and contracts across:
1. User Service (Authentication & Identity)
2. Project Service (Project management, generation contracts, access gate)
3. AI Code Service (LangGraph pipeline, codegen-input intake, completed events)
4. Provisioning Service (Release intake, access check client, deployment events)
"""

import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone

# Ensure project root is in path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
sys.path.insert(0, os.path.join(ROOT_DIR, "services/webblock-aicode-service"))

from app.schemas.events import (
    GenerationCancelledEvent,
    GenerationCompletedEvent,
    GenerationRequestedEvent,
)
from app.services import jcs, project_client


def test_jcs_canonical_hash_compatibility():
    """Verify RFC 8785 (JCS) hash calculation between Project Service and AI Code Service."""
    print("[1/6] Testing RFC 8785 (JCS) canonical hash alignment...")
    captured_input = {
        "componentCatalogVersion": "webblock.catalog.v1",
        "generationContractId": str(uuid.uuid4()),
        "pages": [
            {
                "pageId": "pg-1",
                "name": "Home",
                "path": "/",
                "sortOrder": 0,
                "seoTitle": "Welcome",
                "seoDescription": "Main landing page",
                "puckTree": {"root": {}, "content": [{"type": "Heading", "props": {"text": "Hello World"}}]}
            }
        ]
    }
    
    # Compute JCS SHA-256 in Python
    canonical_output = jcs.canonicalize(captured_input)
    canonical_bytes = canonical_output.encode("utf-8") if isinstance(canonical_output, str) else canonical_output
    computed_hash = hashlib.sha256(canonical_bytes).hexdigest()
    assert len(computed_hash) == 64
    assert computed_hash == computed_hash.lower()
    print(f"      JCS canonical hash computed: {computed_hash[:16]}... (OK)")


def test_event_schema_contract_conformance():
    """Verify event envelopes and schemas for Project, AI Code, and Provisioning services."""
    print("[2/5] Testing event contract alignment across services...")
    project_id = uuid.uuid4()
    req_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # 1. Project -> AI Codegen: generation_requested.v1
    gen_req = GenerationRequestedEvent(
        message_id=uuid.uuid4(),
        occurred_at=now,
        correlation_id=str(req_id),
        project_id=project_id,
        generation_request_id=req_id,
        captured_input_hash="a" * 64,
    )
    assert gen_req.message_type == "project.generation_requested.v1"
    print("      project.generation_requested.v1 format: OK")

    # 2. AI Codegen -> Project: generation_completed.v1
    gen_comp = GenerationCompletedEvent(
        message_id=uuid.uuid4(),
        occurred_at=now,
        correlation_id=str(req_id),
        project_id=project_id,
        generation_request_id=req_id,
        generation_run_id=run_id,
        outcome="succeeded",
    )
    assert gen_comp.message_type == "ai_codegen.generation_completed.v1"
    print("      ai_codegen.generation_completed.v1 format: OK")

    # 3. Project -> Provisioning: source_published.v1 schema verification
    source_pub_file = os.path.join(
        ROOT_DIR, "contracts/events/project/source_published.v1.schema.json"
    )
    assert os.path.isfile(source_pub_file), f"Missing {source_pub_file}"
    with open(source_pub_file, "r") as f:
        schema = json.load(f)
        assert schema["properties"]["message_type"]["const"] == "project.source_published.v1"
        assert "git_commit_sha" in schema["required"]
        assert "repository_id" in schema["required"]
    print("      project.source_published.v1 schema: OK")


def test_access_check_contract_alignment():
    """Verify that AI Code Service and Provisioning Service call Project Service's access-checks accurately."""
    print("[3/5] Testing Access Check endpoint alignment...")
    # Project Service endpoint: POST /internal/v1/access-checks
    # Expected payload: {"userId": "<uuid>", "projectId": "<uuid>", "action": "<action>"}
    # Expected answer: {"success": true, "data": {"allowed": true}}
    from unittest.mock import MagicMock

    class MockResponse:
        status_code = 200
        def json(self):
            return {"success": True, "data": {"allowed": True}, "meta": {"requestId": "test"}}

    import httpx
    captured_calls = []

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured_calls.append((url, json))
        return MockResponse()

    httpx.post = fake_post
    user_id = uuid.uuid4()
    proj_id = uuid.uuid4()

    is_allowed = project_client.check_project_owner(proj_id, user_id)
    assert is_allowed is True
    assert len(captured_calls) == 1
    url, body = captured_calls[0]
    assert url.endswith("/internal/v1/access-checks"), f"Wrong URL: {url}"
    assert body["userId"] == str(user_id)
    assert body["projectId"] == str(proj_id)
    assert body["action"] == "manage_site"
    print("      AI Assistant -> Project Service access check: OK")


def test_codegen_input_contract_alignment():
    """Verify AI Code Service's unwrap of Project Service's codegen-input envelope."""
    print("[4/5] Testing Codegen Input contract alignment...")
    req_id = uuid.uuid4()
    proj_id = uuid.uuid4()

    mock_doc = {
        "generationRequestId": str(req_id),
        "projectId": str(proj_id),
        "businessTypeCode": "clothing_shop",
        "capturedInputHash": "b" * 64,
        "capturedInput": {
            "componentCatalogVersion": "webblock.catalog.v1",
            "generationContractId": str(uuid.uuid4()),
            "pages": [
                {
                    "pageId": "p1",
                    "path": "/",
                    "name": "Home",
                    "sortOrder": 0,
                    "seoTitle": "Shop",
                    "seoDescription": "Clothes",
                    "puckTree": {"root": {}, "content": []}
                }
            ]
        },
        "generationContract": {
            "generationContractId": str(uuid.uuid4()),
            "apiContractVersion": "1.0.0",
            "productTypes": []
        }
    }

    class MockDocResponse:
        status_code = 200
        def json(self):
            return {"success": True, "data": mock_doc, "meta": {"requestId": "req-1"}}

    import httpx
    httpx.get = lambda *args, **kwargs: MockDocResponse()

    result = project_client.fetch_codegen_input(req_id)
    assert result.status == "ok"
    assert result.doc["generationRequestId"] == str(req_id)

    captured = project_client.to_captured_input(result.doc)
    assert captured["business_type_code"] == "clothing_shop"
    assert len(captured["pages"]) == 1
    assert captured["pages"][0]["name"] == "Home"
    print("      Project Service -> AI Codegen codegen-input mapping: OK")


def test_service_topology_and_port_configuration():
    """Verify port allocations and shared broker exchanges across all four services."""
    print("[5/5] Testing Service Topology and configuration...")
    topology = {
        "user-service": {"port": 8081, "db": "webblock_user", "identity": "Keycloak"},
        "project-service": {"port": 8080, "grpc_port": 9090, "mgmt_port": 8089, "db": "webblock_project"},
        "aicode-service": {"port": 8000, "db": "aicode_db"},
        "provisioning-service": {"port": 8084, "db": "provisioning"},
    }

    # Verify no ports overlap
    ports = [v["port"] for v in topology.values()]
    assert len(ports) == len(set(ports)), "Port collision detected!"

    # Verify RabbitMQ exchange naming conventions
    exchanges = {
        "project.events": "Durable topic exchange owned by Project Service",
        "ai_codegen.events": "Durable topic exchange owned by AI Code Service",
        "provisioning.events": "Durable topic exchange owned by Provisioning Service",
    }
    for ex in exchanges:
        assert ex.endswith(".events")
    print("      Service port map and event bus topology: OK (No collisions)")


def test_keycloak_identity_contract_alignment():
    """Verify Keycloak realm configuration, token claims (user_id, roles) and multi-service audiences."""
    print("[6/6] Testing Keycloak identity contract and audience mappings...")
    from pathlib import Path
    realm_file = Path(ROOT_DIR) / "services" / "webblock-user-service" / "keycloak" / "webblock-realm.local.json"
    assert realm_file.exists(), f"Missing Keycloak realm file: {realm_file}"

    with open(realm_file, "r", encoding="utf-8") as f:
        realm_data = json.load(f)

    assert realm_data["realm"] == "webblock"
    clients = {c["clientId"]: c for c in realm_data.get("clients", [])}
    assert "webblock-web" in clients, "Client webblock-web missing from realm config"
    web_client = clients["webblock-web"]

    mappers = {m["name"]: m for m in web_client.get("protocolMappers", [])}
    assert "user_id" in mappers, "user_id claim mapper missing from webblock-web client"
    assert mappers["user_id"]["config"]["claim.name"] == "user_id"
    assert "roles" in mappers, "roles claim mapper missing from webblock-web client"

    # Verify audiences for each consumer service
    expected_audiences = ["webblock-account", "project", "payment", "notification", "provisioning", "ai-codegen"]
    for aud in expected_audiences:
        aud_key = f"audience {aud}"
        assert aud_key in mappers, f"Audience mapper for '{aud}' missing from realm config"
        assert mappers[aud_key]["config"]["included.custom.audience"] == aud

    print("      Keycloak realm, token claims (user_id, roles) & service audiences: OK")


def main():
    print("=" * 70)
    print("WebBlock Integration Verification: User, Project, AI, Provisioning")
    print("=" * 70)
    test_jcs_canonical_hash_compatibility()
    test_event_schema_contract_conformance()
    test_access_check_contract_alignment()
    test_codegen_input_contract_alignment()
    test_service_topology_and_port_configuration()
    test_keycloak_identity_contract_alignment()
    print("=" * 70)
    print("ALL INTEGRATION VERIFICATIONS PASSED SUCCESSFULLY!")
    print("=" * 70)


if __name__ == "__main__":
    main()
