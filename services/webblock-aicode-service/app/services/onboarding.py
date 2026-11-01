from typing import Any
from app.core.config import settings
from app.schemas.business import BusinessOnboardingRequest
from app.services import seed_generator
from app.services.business_layout import generate_puck_layout
from app.services.scaffold import catalog_schema


def prepare_site_inputs(payload: BusinessOnboardingRequest) -> dict[str, Any]:
    """Everything onboarding derives from the business details: the Drizzle schema (db/schema.ts, the shared catalog
    tables, the same for every site), the starting
    products, the seed data and its script (db/seed.sql) and the home page's Puck tree, whose ProductCards shows the same
    products. In llm mode (the request's codegen_mode, else CODEGEN_MODE) GLM proposes the products
    (seed_generator.propose_products), otherwise the templates."""
    mode = payload.codegen_mode or settings.codegen_mode
    products, seed_source = seed_generator.propose_products(payload, use_glm=mode == "llm")
    seed = seed_generator.build_seed(products, payload)
    return {
        "drizzle_schema": catalog_schema(),
        "seed": seed,
        "seed_sql": seed_generator.render_seed_sql(seed),
        "seed_source": seed_source,
        "puck_tree": generate_puck_layout(payload, products),
    }
