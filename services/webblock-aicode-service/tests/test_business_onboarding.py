from app.schemas.business import BusinessOnboardingRequest, DaySchedule
from app.services.business_layout import generate_puck_layout
from app.services.onboarding import prepare_site_inputs
from app.services.catalog import validate_puck


def test_business_onboarding_to_puck_and_drizzle():
    req = BusinessOnboardingRequest(
        business_name="Petal & Stem",
        business_type="everyday_store",
        business_description="Hand-tied bouquets, single stems, and potted flowers arranged with seasonal foliage.",
        logo_url="https://example.com/logo.png",
        phone_number="016992144",
        email="soeunsovannarith@gmail.com",
        address="Prek Eng",
        opening_hours=[
            DaySchedule(day="Monday", is_open=True, opens="9:00 AM", closes="6:00 PM"),
            DaySchedule(day="Tuesday", is_open=True, opens="9:00 AM", closes="6:00 PM"),
            DaySchedule(day="Wednesday", is_open=True, opens="9:00 AM", closes="6:00 PM"),
            DaySchedule(day="Thursday", is_open=True, opens="9:00 AM", closes="6:00 PM"),
            DaySchedule(day="Friday", is_open=True, opens="9:00 AM", closes="6:00 PM"),
            DaySchedule(day="Saturday", is_open=False, opens="9:00 AM", closes="6:00 PM"),
            DaySchedule(day="Sunday", is_open=False, opens="9:00 AM", closes="6:00 PM"),
        ],
        theme="warm",
        accent="#a4532b",
        font="sans",
    )

    # 1. Test Puck layout generation & catalog validation
    puck = generate_puck_layout(req)
    assert puck["root"]["props"]["title"] == "Petal & Stem"
    assert puck["root"]["props"]["theme"] == "warm"
    assert puck["root"]["props"]["accent"] == "#a4532b"

    # Must pass strict Puck catalog validation without throwing
    validated = validate_puck(puck)
    assert len(validated) >= 4

    # Ensure location block contains phone, email, and address
    location_block = next(b for b in puck["content"] if b["type"] == "Location")
    addr = location_block["props"]["address"]
    assert "Prek Eng" in addr
    assert "016992144" in addr
    assert "soeunsovannarith@gmail.com" in addr
    assert "Monday: 9:00 AM - 6:00 PM" in addr
    assert "Saturday: Closed" in addr

    # 2. The Drizzle schema is the shared catalog's description, the same for every business (sql/007_catalog.sql)
    drizzle_ts = prepare_site_inputs(req)["drizzle_schema"]
    assert 'export const businessProfiles = catalog.table("business_profiles"' in drizzle_ts
    assert 'export const products = catalog.table("products"' in drizzle_ts
    assert "Petal & Stem" not in drizzle_ts


def test_every_business_type_gets_the_same_catalog_schema():
    schemas = {prepare_site_inputs(BusinessOnboardingRequest(
        business_name="Acme", business_type=business_type, business_description="We make good things."))["drizzle_schema"]
        for business_type in ("everyday_store", "restaurant", "services")}
    assert len(schemas) == 1
    assert "reservations" not in schemas.pop()  # per-business tables were replaced by product types (team design)
