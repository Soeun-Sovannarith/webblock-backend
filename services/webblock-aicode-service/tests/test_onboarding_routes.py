import uuid
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from app.main import app
from app.api.routes import projects
from app.core.config import settings

client = TestClient(app)

SAMPLE_ONBOARDING_PAYLOAD = {
    "business_name": "Petal & Stem",
    "business_type": "everyday_store",
    "business_description": "Hand-tied bouquets, single stems, and potted flowers arranged with seasonal foliage.",
    "logo_url": "https://example.com/logo.png",
    "phone_number": "016992144",
    "email": "soeunsovannarith@gmail.com",
    "address": "Prek Eng",
    "opening_hours": [
        {"day": "Monday", "is_open": True, "opens": "9:00 AM", "closes": "6:00 PM"},
        {"day": "Tuesday", "is_open": True, "opens": "9:00 AM", "closes": "6:00 PM"},
        {"day": "Wednesday", "is_open": True, "opens": "9:00 AM", "closes": "6:00 PM"},
        {"day": "Thursday", "is_open": True, "opens": "9:00 AM", "closes": "6:00 PM"},
        {"day": "Friday", "is_open": True, "opens": "9:00 AM", "closes": "6:00 PM"},
        {"day": "Saturday", "is_open": False, "opens": "9:00 AM", "closes": "6:00 PM"},
        {"day": "Sunday", "is_open": False, "opens": "9:00 AM", "closes": "6:00 PM"},
    ],
    "theme": "warm",
    "accent": "#a4532b",
    "font": "sans",
}


def test_onboard_and_generate_route(monkeypatch):
    monkeypatch.setattr(settings, "codegen_mode", "hybrid")  # never call GLM, whatever .env says
    project_id = uuid.uuid4()
    mock_enqueue = MagicMock(return_value={
        "generation_request_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "status": "QUEUED",
    })
    monkeypatch.setattr(projects, "enqueue_generation", mock_enqueue)

    resp = client.post(f"/v1/projects/{project_id}/onboardings", json=SAMPLE_ONBOARDING_PAYLOAD)
    assert resp.status_code == 202
    data = resp.json()
    assert data["project_id"] == str(project_id)
    assert data["status"] == "QUEUED"
    assert "project_name" not in data  # only POST /v1/onboardings creates (and names) the project
    assert "businessProfiles" in data["drizzle_schema_preview"]
    assert "products" in data["drizzle_schema_preview"]
    assert len(data["puck_blocks"]) >= 4

    # Verify enqueue_generation was called with synthesized Drizzle schema and Puck tree
    mock_enqueue.assert_called_once()
    call_args = mock_enqueue.call_args
    assert call_args[0][0] == project_id
    assert 'catalog.table("business_profiles"' in call_args[1]["drizzle_schema"]
    assert call_args[1]["home_puck"]["root"]["props"]["title"] == "Petal & Stem"
    assert call_args[1]["home_puck"]["root"]["props"]["theme"] == "warm"

    # The seed script inserts the business profile and the same products the home page shows.
    assert data["seed_source"] == "template"
    seed, preview = call_args[1]["seed"], data["seed_sql_preview"]
    assert seed["business"]["business_name"] == "Petal & Stem"
    assert "INSERT INTO catalog.business_profiles" in preview and "'Petal & Stem'" in preview
    cards = next(b for b in call_args[1]["home_puck"]["content"] if b["type"] == "ProductCards")
    assert [p["name"] for p in seed["products"]] == [p["name"] for p in cards["props"]["products"]]
    assert all(p["name"].replace("'", "''") in preview for p in cards["props"]["products"])


def test_onboarding_in_llm_mode_seeds_glm_products(monkeypatch):
    from app.services import glm
    mock_enqueue = MagicMock(return_value={"generation_request_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()), "status": "QUEUED"})
    monkeypatch.setattr(projects, "enqueue_generation", mock_enqueue)
    monkeypatch.setattr(glm, "is_configured", lambda: True)
    monkeypatch.setattr(glm, "request_seed_products", lambda business: {"products": [
        {"name": "Rose Bouquet", "price": "45,000 KHR", "description": "Twelve red roses.", "category": "Bouquets"}]})

    resp = client.post(f"/v1/projects/{uuid.uuid4()}/onboardings",
                       json={**SAMPLE_ONBOARDING_PAYLOAD, "codegen_mode": "llm"})
    assert resp.status_code == 202
    assert resp.json()["seed_source"] == "glm"
    kwargs = mock_enqueue.call_args[1]
    assert kwargs["seed"]["products"][0]["category"] == "Bouquets"
    # The catalog keeps prices in USD, so a riel price is stored hidden (NULL) rather than misread.
    assert "('Rose Bouquet', 'Twelve red roses.', NULL::numeric(12,2), 'bouquets', 0)" in resp.json()["seed_sql_preview"]
    cards = next(b for b in kwargs["home_puck"]["content"] if b["type"] == "ProductCards")
    assert cards["props"]["products"] == [{"name": "Rose Bouquet", "price": "45,000 KHR", "description": "Twelve red roses."}]


def test_onboarding_without_codegen_mode_follows_the_setting(monkeypatch):
    from app.services import glm
    mock_enqueue = MagicMock(return_value={"generation_request_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4()), "status": "QUEUED"})
    monkeypatch.setattr(projects, "enqueue_generation", mock_enqueue)
    monkeypatch.setattr(glm, "is_configured", lambda: True)
    monkeypatch.setattr(glm, "request_seed_products", lambda business: {"products": [
        {"name": "Rose Bouquet", "price": "45,000 KHR", "description": "Twelve red roses.", "category": "Bouquets"}]})

    monkeypatch.setattr(settings, "codegen_mode", "llm")
    resp = client.post(f"/v1/projects/{uuid.uuid4()}/onboardings", json=SAMPLE_ONBOARDING_PAYLOAD)
    assert resp.json()["seed_source"] == "glm"
    assert mock_enqueue.call_args[1]["codegen_mode"] is None  # enqueue_generation applies CODEGEN_MODE

    monkeypatch.setattr(settings, "codegen_mode", "hybrid")
    resp = client.post(f"/v1/projects/{uuid.uuid4()}/onboardings", json=SAMPLE_ONBOARDING_PAYLOAD)
    assert resp.json()["seed_source"] == "template"


def test_onboard_and_generate_validation_errors():
    project_id = uuid.uuid4()
    # 1. Invalid email
    bad_email_payload = {**SAMPLE_ONBOARDING_PAYLOAD, "email": "not-an-email"}
    resp = client.post(f"/v1/projects/{project_id}/onboardings", json=bad_email_payload)
    assert resp.status_code == 422

    # 2. Invalid accent color (not 6-digit hex)
    bad_accent_payload = {**SAMPLE_ONBOARDING_PAYLOAD, "accent": "blue"}
    resp = client.post(f"/v1/projects/{project_id}/onboardings", json=bad_accent_payload)
    assert resp.status_code == 422

    # 3. Invalid day name
    bad_day_payload = {
        **SAMPLE_ONBOARDING_PAYLOAD,
        "opening_hours": [{"day": "Funday", "is_open": True, "opens": "9:00 AM", "closes": "6:00 PM"}],
    }
    resp = client.post(f"/v1/projects/{project_id}/onboardings", json=bad_day_payload)
    assert resp.status_code == 422


def test_generate_from_business_route(monkeypatch):
    monkeypatch.setattr(settings, "codegen_mode", "hybrid")  # never call GLM, whatever .env says
    mock_project_id = uuid.uuid4()
    mock_db = MagicMock()
    mock_db.__enter__.return_value = mock_db
    monkeypatch.setattr(projects, "SessionLocal", lambda: mock_db)
    monkeypatch.setattr(projects.store, "create_project", lambda db, name, repository_full_name, subdomain: {
        "project_id": mock_project_id,
        "name": name,
        "subdomain": "petal-stem-12345678",
    })

    mock_enqueue = MagicMock(return_value={
        "generation_request_id": str(uuid.uuid4()),
        "run_id": str(uuid.uuid4()),
        "status": "QUEUED",
    })
    monkeypatch.setattr(projects, "enqueue_generation", mock_enqueue)

    resp = client.post("/v1/onboardings", json=SAMPLE_ONBOARDING_PAYLOAD)
    assert resp.status_code == 202
    data = resp.json()
    assert data["project_id"] == str(mock_project_id)
    assert data["project_name"] == "Petal & Stem"
    assert data["status"] == "QUEUED"
    assert "businessProfiles" in data["drizzle_schema_preview"]


def test_onboarding_answers_locate_the_run(monkeypatch):
    monkeypatch.setattr(settings, "codegen_mode", "hybrid")  # never call GLM, whatever .env says
    run_id = str(uuid.uuid4())
    monkeypatch.setattr(projects, "enqueue_generation", MagicMock(return_value={
        "generation_request_id": str(uuid.uuid4()), "run_id": run_id, "status": "QUEUED"}))

    resp = client.post(f"/v1/projects/{uuid.uuid4()}/onboardings", json=SAMPLE_ONBOARDING_PAYLOAD)
    assert resp.status_code == 202
    assert resp.headers["Location"] == f"/v1/generation-runs/{run_id}"
