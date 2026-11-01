# Seed data of the clothing shop (`seed.json`)

`seed.json` holds the content of the demo shop "Juniper & Thread": its business profile, 4 categories and 13 products (12 published, 1 draft) with 16 pictures. It is loaded into the shared catalog by the prototype's `catalog_platform` stand-in (task H):

```sh
python -m provisioning.hosts.devtools.catalog_platform seed --project <project id> --file deploy/prototype/demo-sites/clothing-shop/seed/seed.json
```

The file is independent of any website: it carries no IDs, no website ID and no timestamps. The loader supplies those. Every field maps to one column of Notion "6 · Shared catalog", as listed below. Nothing here adds a column, and no value is meant to be put into SQL text: each one is a bound parameter.

## How a loader applies it

1. The website must already be registered (`catalog_platform register`): its `catalog.websites` row exists with business type `clothing_shop`, and its first `catalog.business_profiles` row exists.
2. The loader logs in as the website's own role `site_<project id without hyphens>` and writes through row-level security, as the site's CMS would. With that role, `website_id` is always the website's own ID; any other value is refused by the `WITH CHECK` policies.
3. `business_profile` updates the existing profile row (or inserts it if there is none).
4. `categories` are inserted, then `products` in file order, then each product's `images`.
5. Run it once per website. Running it again inserts the categories a second time and fails on `UNIQUE (website_id, slug)`; how to handle a repeat (refuse, or clear first) is the loader's choice.

## Top level

| Field | Type | Meaning |
| --- | --- | --- |
| `format_version` | integer | Always `1`. A loader refuses any other value. |
| `business_type` | string | `catalog.business_types.code` the website must have: `clothing_shop`. A loader refuses a website of another type. |
| `business_profile` | object | The website's profile; see below. |
| `categories` | array | The website's categories; see below. |
| `products` | array | The website's products, each with its images; see below. |

## `business_profile` → `catalog.business_profiles`

| Field | Column | Notes |
| --- | --- | --- |
| *(loader)* | `website_id` | The website's ID. |
| `business_name` | `business_name` VARCHAR(255) | Shown as the shop's name in the header and the hero. |
| `description` | `description` TEXT NULL | Shown as the shop's tagline. |
| *(absent)* | `logo_storage_key`, `logo_byte_size` | Null: the demo has no logo. |
| `phone` | `phone` VARCHAR(50) NULL | Fictional number (555-01xx). |
| `email` | `email` VARCHAR(320) NULL | Fictional address on the reserved `.example` domain. |
| `address` | `address` TEXT NULL | |
| `timezone` | `timezone` VARCHAR(100) | IANA name. |
| `opening_hours` | `opening_hours` JSONB | Array of `{from_day, to_day, opens_at, closes_at, closes_next_day}`. Days are lowercase English names (`monday` … `sunday`), times are `HH:MM` local time. |
| *(loader)* | `created_at`, `updated_at` | Kept by the loader or the database. |

## `categories[]` → `catalog.categories`

| Field | Column | Notes |
| --- | --- | --- |
| *(loader)* | `category_id` UUID | A new UUID. |
| *(loader)* | `website_id` | The website's ID. |
| `slug` | `slug` VARCHAR(100) | Unique per website; `[a-z0-9-]`. Products refer to their category by it. |
| `name` | `name` VARCHAR(100) | |
| `sort_order` | `sort_order` INTEGER | |
| *(loader)* | `created_at`, `updated_at` | |

## `products[]` → `catalog.products`

| Field | Column | Notes |
| --- | --- | --- |
| *(loader)* | `product_id` UUID | A new UUID. The shop's details page is `/products/<product_id>`. |
| *(loader)* | `website_id` | The website's ID. |
| `product_type` | `product_type_schema_id` UUID | `catalog.product_types.code` within business type `clothing_shop`. The loader uses that product type's active `catalog.product_type_schemas` row (highest `version_number` with `is_active`). |
| `category_slug` | `category_id` UUID NULL | The `category_id` of this website's category with that slug. |
| `name` | `name` VARCHAR(255) | |
| `description` | `description` TEXT NULL | |
| `price` | `price` NUMERIC(12,2) NULL | A decimal **string** such as `"49.00"`, so no binary floating point is involved. US dollars, as the design defines; there is no currency column. `null` would mean "no price shown". |
| `attributes` | `attributes` JSONB | A JSON object valid against the product type's `field_schema`; keys below. |
| `status` | `status` `catalog.product_status` | `published` or `draft`. The shop shows only `published`; the draft "Quilted Liner Vest" checks that. |
| `sort_order` | `sort_order` INTEGER | Public ordering. |
| *(loader)* | `row_version` | `1`. |
| *(loader)* | `created_at`, `updated_at` | |
| `images` | — | The product's pictures; see below. May be empty. |

## `products[].images[]` → `catalog.product_images`

| Field | Column | Notes |
| --- | --- | --- |
| *(loader)* | `product_image_id` UUID | A new UUID. |
| *(loader)* | `website_id`, `product_id` | The website's ID and the product's new ID. |
| `file` | `storage_key` VARCHAR(500) | Path of the picture inside the shop's `public/` folder, always `products/<name>.svg`. The loader stores `<website id>/` + `file`, which satisfies `CHECK (storage_key LIKE website_id::text || '/%')`. The shop serves `<website id>/products/<name>.svg` as `/products/<name>.svg` from its own image and shows a placeholder for any other key. |
| `byte_size` | `byte_size` BIGINT | The file's exact size in bytes (checked by the shop's tests). Counted against the website's storage limit by `catalog.track_media_bytes()`. |
| `alt_text` | `alt_text` VARCHAR(500) NULL | Used as the picture's alt text. |
| `sort_order` | `sort_order` INTEGER | Gallery order; the first picture is the card picture. |
| *(loader)* | `created_at` | |

The 16 pictures total **52,181 bytes**; the largest is **14,005 bytes**. A website loading this seed needs `storage_limit_bytes` of at least 52,181 and `max_image_bytes` of at least 14,005.

## Reference data the catalog provides

The seed refers to these codes. They are reference data of the catalog owner, the Project service: its repeatable Flyway migration `R__catalog_reference_data.sql`, which `make proto-up` applies (decision `0017` section 1), not here.

`catalog.business_types`: code `clothing_shop`, name `Clothing shop`, active.

`catalog.product_types` of `clothing_shop`, each with one active `product_type_schemas` row, version 1:

| Code | Name | `field_schema` |
| --- | --- | --- |
| `top` | Top | `TOP_SCHEMA` below |
| `outerwear` | Outerwear | `TOP_SCHEMA` below |
| `trousers` | Trousers | `TROUSERS_SCHEMA` below |
| `accessory` | Accessory | `ACCESSORY_SCHEMA` below |

```json
{
  "TOP_SCHEMA": {
    "type": "object",
    "properties": {
      "size": { "type": "array", "items": { "enum": ["XS", "S", "M", "L", "XL", "XXL"] } },
      "color": { "type": "array", "items": { "type": "string" } },
      "material": { "type": "string" },
      "fit": { "enum": ["slim", "regular", "relaxed", "oversized"] },
      "care": { "type": "string" }
    },
    "additionalProperties": false
  },
  "TROUSERS_SCHEMA": {
    "type": "object",
    "properties": {
      "size": { "type": "array", "items": { "enum": ["26", "28", "30", "32", "34", "36", "38", "40"] } },
      "color": { "type": "array", "items": { "type": "string" } },
      "material": { "type": "string" },
      "fit": { "enum": ["slim", "regular", "relaxed", "oversized"] },
      "care": { "type": "string" }
    },
    "additionalProperties": false
  },
  "ACCESSORY_SCHEMA": {
    "type": "object",
    "properties": {
      "size": { "type": "array", "items": { "enum": ["One size"] } },
      "color": { "type": "array", "items": { "type": "string" } },
      "material": { "type": "string" },
      "care": { "type": "string" }
    },
    "additionalProperties": false
  }
}
```

The shop renders any attribute generically: lists as option chips, single values as text. It labels the keys above as Sizes, Colors, Material, Fit and Care.

## Pictures

All pictures are original flat illustrations drawn for this shop (SVG, 800 × 1000). They contain no photographs, no third-party artwork and no external references.
