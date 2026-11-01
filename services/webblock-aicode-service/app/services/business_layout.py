from typing import Any
from app.schemas.business import BusinessOnboardingRequest
from app.services.catalog import validate_puck


def _format_schedule_text(data: BusinessOnboardingRequest) -> str:
    parts = []
    if data.address:
        parts.append(f"{data.address}")

    contact_bits = []
    if data.phone_number:
        contact_bits.append(f"Tel: {data.phone_number}")
    if data.email:
        contact_bits.append(f"Email: {data.email}")
    if contact_bits:
        parts.append(" | ".join(contact_bits))

    if data.opening_hours:
        schedule_lines = ["\nOpening Hours:"]
        for schedule in data.opening_hours:
            if schedule.is_open:
                schedule_lines.append(f"• {schedule.day}: {schedule.opens} - {schedule.closes}")
            else:
                schedule_lines.append(f"• {schedule.day}: Closed")
        parts.append("\n".join(schedule_lines))

    return "\n\n".join(parts) if parts else "Visit us or reach out online!"


def _generate_sample_items(b_type: str) -> list[dict[str, str]]:
    b_type_lower = b_type.lower()
    if any(k in b_type_lower for k in ("flower", "petal", "stem", "floral", "plant")):
        return [
            {"name": "Wildflower Meadow Bouquet", "price": "$38.00", "description": "Hand-tied seasonal blooms and fresh eucalyptus."},
            {"name": "Ceramic Botanical Vase", "price": "$26.00", "description": "Hand-crafted stoneware vase in a neutral matte finish."},
            {"name": "Scented Soy Candle", "price": "$18.00", "description": "Notes of jasmine, cedarwood, and morning blossom."},
            {"name": "Dried Floral Arrangement", "price": "$45.00", "description": "Everlasting centerpiece with natural textures."},
        ]
    if any(k in b_type_lower for k in ("restaurant", "cafe", "coffee", "bakery", "food")):
        return [
            {"name": "Artisan Sourdough Loaf", "price": "$8.50", "description": "Naturally fermented, stone-baked with a crisp golden crust."},
            {"name": "Signature Cold Brew", "price": "$5.00", "description": "Slow-steeped single-origin beans with velvety undertones."},
            {"name": "Cardamom Morning Bun", "price": "$4.50", "description": "Flaky laminated pastry dusted with Swedish pearl sugar."},
        ]
    if any(k in b_type_lower for k in ("service", "agency", "consulting", "design")):
        return [
            {"name": "Discovery & Brand Strategy", "price": "From $850", "description": "Comprehensive market research and visual positioning."},
            {"name": "Complete Digital Package", "price": "From $1,500", "description": "Custom Next.js design, copy, and database setup."},
            {"name": "Monthly Advisory & Support", "price": "$400 / mo", "description": "Continuous enhancements and dedicated priority care."},
        ]
    # Default handmade / store items
    return [
        {"name": "Hand-Poured Ceramic Mug", "price": "$24.00", "description": "Small-batch pottery with ergonomic handle and organic glaze."},
        {"name": "Linen Everyday Tote", "price": "$32.00", "description": "Durable organic linen with reinforced double stitching."},
        {"name": "Handmade Brass Keepsake", "price": "$29.00", "description": "Polished solid brass minimal desk or tabletop piece."},
    ]


def sample_products(data: BusinessOnboardingRequest) -> list[dict[str, str]]:
    """Template offerings for the business (no GLM): what the site shows and seeds until the owner edits them."""
    return _generate_sample_items(f"{data.business_type or 'everyday_store'} {data.business_name} {data.business_description}")


def generate_puck_layout(data: BusinessOnboardingRequest, products: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Turn the business details from onboarding into a complete Puck JSON document

    matching the editor layout preview, validated against the Puck component catalog. products (the rows
    seeded into the site's database, see seed_generator) fill the ProductCards block; sample offerings otherwise.
    """
    b_name = data.business_name
    b_desc = data.business_description

    # Tone & style properties
    theme = data.theme or "warm"
    accent = data.accent or "#a4532b"
    font = data.font or "sans"

    products = [{"name": p["name"], "price": p.get("price") or "", "description": p.get("description") or ""}
                for p in (products if products is not None else sample_products(data))]
    location_text = _format_schedule_text(data)

    content: list[dict[str, Any]] = [
        {
            "type": "Hero",
            "props": {
                "title": f"Welcome to {b_name}",
                "subtitle": b_desc,
                "tone": "default",
                "align": "left",
                "spacing": "spacious",
            },
        },
        {
            "type": "ProductCards",
            "props": {
                "title": "Fresh Picks & Featured Items",
                "products": products,
                "tone": "surface",
                "align": "left",
                "spacing": "default",
            },
        },
        {
            "type": "About",
            "props": {
                "title": f"About {b_name}",
                "text": (
                    f"{b_desc} We take pride in quality, dedication, and attention to detail. "
                    "Every offering is thoughtfully crafted to bring you the best experience possible."
                ),
                "tone": "default",
                "align": "left",
                "spacing": "default",
            },
        },
        {
            "type": "Location",
            "props": {
                "title": "Visit Us & Hours",
                "address": location_text,
                "tone": "surface",
                "align": "left",
                "spacing": "default",
            },
        },
        {
            "type": "Footer",
            "props": {
                "text": f"© 2026 {b_name}. All rights reserved.",
                "tone": "contrast",
                "align": "center",
                "spacing": "compact",
            },
        },
    ]

    puck_tree: dict[str, Any] = {
        "root": {
            "props": {
                "title": b_name,
                "theme": theme,
                "accent": accent,
                "font": font,
            }
        },
        "content": content,
    }

    # Validate against catalog to guarantee correctness
    validate_puck(puck_tree)
    return puck_tree
