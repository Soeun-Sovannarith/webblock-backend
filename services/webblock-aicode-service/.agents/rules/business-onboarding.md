# Business Onboarding & Drizzle Backend Generation Rules

These rules govern the business onboarding data model, dynamic Drizzle schema generation, and automatic Puck layout synthesis.

## 1. Onboarding Data Model (`app/schemas/business.py`)

The onboarding payload captures the two-step wizard UI:
- **Step 1: Business Details**:
  - `business_name`: 1–100 chars, sanitized.
  - `business_type`: `everyday_store`, `handmade`, `retail`, `restaurant`, `cafe`, `services`, `portfolio`, `agency`.
  - `business_description`: 5–2000 chars, sanitized.
  - `logo_url`: Optional image URL.
- **Step 2: Contact & Hours**:
  - `phone_number`: Contact phone.
  - `email`: Formatted email address.
  - `address`: Physical address or service area.
  - `opening_hours`: Array of 7 days (`day`, `is_open`, `opens`, `closes`).
- **Styling**:
  - `theme`: `"warm" | "light" | "dark"`.
  - `accent`: 6-digit hex color (e.g. `"#a4532b"`).
  - `font`: `"sans" | "serif" | "mono"`.

## 2. Drizzle ORM Schema Rules (`app/services/drizzle_generator.py`)

The generated `db/schema.ts` only describes tables of the shared `site_data` schema (`sql/006_site_data.sql`); nothing creates tables from it. Every table it declares must exist there, so a new table also needs a new `sql/00N` migration (`tests/test_site_data.py`).


1. **Always Include Core Tables**:
   - `business_profiles`: Stores identity, description, contact info, and `opening_hours` as typed JSONB.
   - `contact_inquiries`: Captures inbound customer messages (`pending`, `read`, `resolved`).
2. **Domain-Specific Table Generation**:
   - **Every type**: `products`, the site's catalog (price, category, image, availability, sort order). The storefront, the product portal (`/portal`) and `db/seed.sql` use it.
   - **Store / Handmade / Retail**: `orders` (with JSONB line items).
   - **Restaurant / Cafe**: `reservations`.
   - **Services / Agency / Portfolio**: `bookings`, `testimonials`.
3. **Seed data (`app/services/seed_generator.py`)**: GLM (`glm.request_seed_products`, `llm` mode) or the templates propose product rows; the seed is stored as data (`build_seed`) and `render_seed_sql` escapes it into `db/seed.sql` together with the business profile on every run. The `DEPLOY` step runs it as the site's tenant (`site_data.prepare_site`); inserts only fill tables that have none of this site's rows (`WHERE project_id = site_data.current_project_id()`). User text never goes into SQL or TypeScript comments.
4. **Escaping**:
   - Never inject raw user strings into TypeScript template strings without `_escape_ts()`.

## 3. Puck Component Synthesis Rules (`app/services/business_layout.py`)

1. **Catalog Conformance**:
   - All synthesized blocks in `puck_tree["content"]` must strictly conform to `app/services/catalog.py`:
     - `Hero`: Welcome title, business description, style props.
     - `ProductCards`: Contextually relevant product/service offerings.
     - `About`: Business story and quality guarantee.
     - `Location`: Formatted address, phone, email, and daily opening hours.
     - `Footer`: Copyright and business details.
2. **Validation**:
   - Must run `validate_puck(puck_tree)` before returning.

## 4. Endpoints

- `POST /v1/onboardings`: One-step creation (creates project + generates Drizzle schema + seed data + Puck layout + queues LangGraph run). The response includes `seed_sql_preview` and `seed_source`.
- `POST /v1/projects/{project_id}/onboardings`: Generates and queues for an existing project.
