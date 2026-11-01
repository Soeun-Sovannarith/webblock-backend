"""Seed data for the generated site's rows in the shared catalog (db/seed.sql, sql/007_catalog.sql).

GLM (glm.request_seed_products) or the sample templates only choose the rows; this module writes the SQL, so
every value is escaped here and the script can only insert into the catalog tables. The DEPLOY step runs it for one
website (site_catalog.prepare_site), which sets webblock.website_id for the transaction; every row takes its
website_id from that setting. It inserts the profile only while the website has none, and the categories and products
only while it has no products, so running it again never duplicates rows or overwrites what the owner changed in the
product portal.
"""
import json
import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from app.core.config import settings
from app.schemas.business import BusinessOnboardingRequest
from app.services.business_layout import sample_products

log = logging.getLogger("webblock.seed")

MAX_PRODUCTS = 12
LIMITS = {"name": 120, "price": 40, "description": 1000, "category": 60}
_CONTROL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")


def _text(value: Any, limit: int) -> str:
    return _CONTROL.sub("", str(value or "")).strip()[:limit] if isinstance(value, (str, int, float)) else ""


def clean_products(items: Any) -> list[dict[str, str]]:
    """Normalize proposed products (from GLM, templates or Puck): strip control characters, enforce the column
    limits, drop nameless items and repeated names, and keep at most MAX_PRODUCTS."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        product = {key: _text(item.get(key), limit) for key, limit in LIMITS.items()}
        if not product["name"] or product["name"].casefold() in seen:
            continue
        seen.add(product["name"].casefold())
        product["category"] = product["category"] or "General"
        out.append(product)
        if len(out) == MAX_PRODUCTS:
            break
    return out


def template_products(data: BusinessOnboardingRequest) -> list[dict[str, str]]:
    return clean_products(sample_products(data))


def propose_products(data: BusinessOnboardingRequest, *, use_glm: bool) -> tuple[list[dict[str, str]], str]:
    """The products to seed and show: GLM's proposal when use_glm and GLM is configured, else the templates.
    Returns (products, source) with source "glm" or "template"; a GLM failure falls back to the templates."""
    from app.services import glm
    if use_glm and glm.is_configured():
        try:
            products = clean_products(glm.request_seed_products(data.model_dump(mode="json"))["products"])
            if products:
                return products, "glm"
            log.warning("GLM proposed no usable seed products; using the templates.")
        except glm.GLMError as e:
            log.warning("GLM seed proposal failed, using the templates: %s", e)
    return template_products(data), "template"


def literal(value: Any) -> str:
    """A Postgres literal (standard_conforming_strings on, which the script sets)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    return "'" + _CONTROL.sub("", str(value)).replace("'", "''") + "'"


WEBSITE = "current_setting('webblock.website_id')::uuid"  # set by site_catalog.prepare_site
_NO_PRODUCTS = f"NOT EXISTS (SELECT 1 FROM catalog.products p WHERE p.website_id = {WEBSITE})"
MAX_PRICE = Decimal("9999999999.99")  # catalog.products.price is NUMERIC(12,2)


_OTHER_CURRENCY = re.compile(r"[^\W\d_]|[€£¥៛₫฿]")


def price_value(price: str) -> Decimal | None:
    """"$3.50", "3.5", "USD 1,200" → the price in USD (catalog.products.price). None hides the price: no number, a
    number too large, or another currency ("45,000 KHR", "€5"), which the harness can't convert."""
    text = (price or "").strip()
    if _OTHER_CURRENCY.search(re.sub(r"(?i)\busd\b|us\$", "", text)):
        return None
    match = re.search(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d+)?", text)
    if not match:
        return None
    number = match.group(0)
    number = number.replace(",", "") if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", number) else number.replace(",", ".")
    try:
        value = Decimal(number).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None
    return value if value <= MAX_PRICE else None


def category_slug(name: str) -> str:
    """The URL-safe category name (catalog.categories.slug). Keep in step with categorySlug in the site's
    lib/products.ts, which finds the same category when the owner picks it in the portal."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:100].strip("-")
    return slug or "general"


def _insert_products(products: list[dict[str, str]]) -> list[str]:
    if not products:
        return []
    categories: dict[str, str] = {}
    for product in products:
        categories.setdefault(category_slug(product["category"]), product["category"][:100])
    category_rows = ",\n".join(f"  ({literal(name)}, {literal(slug)}, {i})" for i, (slug, name) in enumerate(categories.items()))
    product_rows = ",\n".join(
        f"  ({literal(p['name'])}, {literal(p['description'] or None)}, {_price(p['price'])}, "
        f"{literal(category_slug(p['category']))}, {i})"
        for i, p in enumerate(products))
    return [
        "-- Categories and products shown on the site and managed in the product portal (/portal).",
        "INSERT INTO catalog.categories (website_id, name, slug, sort_order)",
        f"SELECT {WEBSITE}, v.name, v.slug, v.sort_order",
        f"FROM (VALUES\n{category_rows}\n) AS v(name, slug, sort_order)",
        f"WHERE {_NO_PRODUCTS}",
        "ON CONFLICT (website_id, slug) DO NOTHING;",
        "",
        "INSERT INTO catalog.products (website_id, product_type_schema_id, category_id, name, description, price, sort_order)",
        f"SELECT {WEBSITE}, catalog.item_schema_id({WEBSITE}), c.category_id, v.name, v.description, v.price, v.sort_order",
        f"FROM (VALUES\n{product_rows}\n) AS v(name, description, price, category_slug, sort_order)",
        f"LEFT JOIN catalog.categories c ON c.website_id = {WEBSITE} AND c.slug = v.category_slug",
        f"WHERE {_NO_PRODUCTS};",
        "",
    ]


def _price(price: str) -> str:
    value = price_value(price)
    return "NULL::numeric(12,2)" if value is None else f"{value}::numeric(12,2)"


PROFILE_FIELDS = {"business_name", "business_type", "business_description", "phone_number", "email", "address",
                  "opening_hours", "logo_url"}


def build_seed(products: list[dict[str, str]], business: BusinessOnboardingRequest | None = None) -> dict[str, Any]:
    """The seed as data, frozen into the captured input (captured_input["seed"]). It holds no SQL: render_seed_sql
    re-validates and escapes it on every run, so a seed stored in, or sent by, another service cannot inject SQL."""
    return {"business": business.model_dump(mode="json", include=PROFILE_FIELDS) if business else None,
            "products": clean_products(products)}


def _profile(business: Any) -> BusinessOnboardingRequest | None:
    if not isinstance(business, dict):
        return None
    try:
        return BusinessOnboardingRequest.model_validate(business)
    except ValueError as e:
        log.warning("Seed business profile is invalid and was left out: %s", e)
        return None


def _insert_profile(data: BusinessOnboardingRequest) -> list[str]:
    """The onboarding business details. The business type belongs to catalog.websites (site_catalog.prepare_site).
    A logo given as a web URL is left out: the catalog keeps only object-storage keys (logo_storage_key)."""
    hours = json.dumps([{"day": d.day, "isOpen": d.is_open, "opens": d.opens, "closes": d.closes}
                        for d in data.opening_hours], ensure_ascii=False)
    columns = {
        "website_id": WEBSITE,
        "business_name": literal(data.business_name[:255]),
        "description": literal(data.business_description),
        "phone": literal((data.phone_number or "")[:50] or None),
        "email": literal(data.email),
        "address": literal(data.address),
        "timezone": literal(settings.site_timezone),
        "opening_hours": f"{literal(hours)}::jsonb",
    }
    return [
        "-- The business profile captured at onboarding.",
        f"INSERT INTO catalog.business_profiles ({', '.join(columns)})",
        f"SELECT {', '.join(columns.values())}",
        "ON CONFLICT (website_id) DO NOTHING;",
        "",
    ]


def seed_business_type(seed: dict[str, Any] | None) -> str | None:
    """The onboarding business type in a seed (build_seed), for the website's catalog.business_types code."""
    business = (seed or {}).get("business")
    return business.get("business_type") if isinstance(business, dict) else None


def render_seed_sql(seed: dict[str, Any]) -> str:
    """db/seed.sql from a seed (build_seed): the business profile (onboarding only) and the product catalog.
    User text appears only inside literal(), never in comments, which a carriage return or newline would end."""
    business = _profile(seed.get("business"))
    lines = [
        "-- Seed data generated by the WebBlock AI harness. Do not edit.",
        "-- The harness runs it on every deploy, in one transaction, for this site's website in the shared catalog",
        "-- (webblock.website_id). The profile is inserted only while the website has none, and the categories and",
        "-- products only while it has no products, so re-running it keeps what the owner changed in /portal.",
        "SET LOCAL standard_conforming_strings = on;",
        "",
        *(_insert_profile(business) if business else []),
        *_insert_products(clean_products(seed.get("products"))),
    ]
    return "\n".join(lines) + "\n"


def products_from_input(captured_input: dict[str, Any]) -> list[dict[str, str]]:
    """Every product listed in a ProductCards block, in page and block order (seed for non-onboarding sites)."""
    items: list[Any] = []
    pages = sorted(captured_input.get("pages") or [], key=lambda p: (p.get("sort_order", 0), p.get("path", "")))
    for page in pages:
        for block in (page.get("puck_tree") or {}).get("content", []):
            if isinstance(block, dict) and block.get("type") == "ProductCards":
                items.extend((block.get("props") or {}).get("products") or [])
    return clean_products(items)
