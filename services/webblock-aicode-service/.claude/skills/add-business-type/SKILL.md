---
name: add-business-type
description: Add a business category to onboarding (BusinessType enum, its Drizzle domain tables and sample Puck offerings). Use when asked to support a new kind of business in onboarding (POST /v1/onboardings).
---

# Add a business type to onboarding

Read `.claude/rules/business-onboarding.md` first. Three files change, plus a migration when the type needs a new table:

1. **`app/schemas/business.py`**: add the value to `BusinessType` (lowercase `snake_case`, e.g. `FITNESS = "fitness"`).
2. **`app/services/drizzle_generator.py`**: `generate_drizzle_schema` chooses domain tables by keyword match (`is_food`, `is_service`, otherwise retail).
   - If an existing group fits, add the keyword to that group's tuple.
   - Otherwise add a new flag and an `elif` branch that emits its `pgTable(...)` lines, in the same style as the existing branches.
   - Don't add a separate catalog table (`menu_items`, `services`...). Every type lists its offerings in the shared `products` table (`PRODUCTS_TABLE`), which the storefront, the product portal and the seed depend on. Branches add only extras such as bookings.
   - Interpolate user values only through `_escape_ts`.
   - A new table must also exist in the shared `site_data` schema, or the site builds and then fails at runtime: add a new migration (`new-migration` skill) that creates it the way `sql/006_site_data.sql` does (`project_id`, `UNIQUE (project_id, id)`, row-level security, grants to `webblock_site`; `.claude/rules/sql-migrations.md`). Reusing an existing group's tables needs no migration.
3. **`app/services/business_layout.py`**: add a keyword branch to `_generate_sample_items` that returns 3–4 `{"name", "price", "description"}` items. They feed the `ProductCards` block and the seeded `products` rows (`seed_generator.template_products`), so keep them within that block's catalog props.

Add tests to `tests/test_business_onboarding.py`. They should assert that the new tables appear in the generated schema and that `generate_puck_layout` still passes `validate_puck`. Then run:
```bash
pytest tests/test_business_onboarding.py tests/test_onboarding_routes.py tests/test_site_data.py
pytest && ruff check .
```
If the README lists the business types, update it there too.
