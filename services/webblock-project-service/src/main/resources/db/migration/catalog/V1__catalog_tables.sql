-- Catalog schema, owned by the Project service (spec section 4.1; platform design revision 19 section 6).
-- Moved on 2026-10-14 from Provisioning's prototype catalog script 00001_catalog_tables. Run by Flyway as catalog_owner
-- (docs/runbooks/catalog-database.md). Forward-only and immutable: fix a mistake with a new file.


DO $$
BEGIN
    IF current_user <> 'catalog_owner' THEN
        RAISE EXCEPTION 'catalog migrations must run as role catalog_owner (current role: %)', current_user
            USING ERRCODE = '42501';
    END IF;
END
$$;

CREATE SCHEMA catalog;

CREATE TYPE catalog.website_status AS ENUM ('active', 'suspended', 'archived');

CREATE TYPE catalog.product_status AS ENUM ('draft', 'published', 'archived');

CREATE TABLE catalog.business_types (
    business_type_id UUID         NOT NULL,
    code             VARCHAR(50)  NOT NULL,
    name             VARCHAR(100) NOT NULL,
    is_active        BOOLEAN      NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL,
    CONSTRAINT business_types_pkey PRIMARY KEY (business_type_id),
    CONSTRAINT business_types_code_key UNIQUE (code)
);

CREATE TABLE catalog.product_types (
    product_type_id  UUID         NOT NULL,
    business_type_id UUID         NOT NULL,
    code             VARCHAR(50)  NOT NULL,
    name             VARCHAR(100) NOT NULL,
    is_active        BOOLEAN      NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL,
    CONSTRAINT product_types_pkey PRIMARY KEY (product_type_id),
    CONSTRAINT product_types_business_type_id_fkey FOREIGN KEY (business_type_id)
        REFERENCES catalog.business_types (business_type_id),
    CONSTRAINT product_types_business_type_id_code_key UNIQUE (business_type_id, code)
);

CREATE TABLE catalog.product_type_schemas (
    product_type_schema_id UUID        NOT NULL,
    product_type_id        UUID        NOT NULL,
    version_number         INTEGER     NOT NULL,
    field_schema           JSONB       NOT NULL,
    is_active              BOOLEAN     NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL,
    CONSTRAINT product_type_schemas_pkey PRIMARY KEY (product_type_schema_id),
    CONSTRAINT product_type_schemas_product_type_id_fkey FOREIGN KEY (product_type_id)
        REFERENCES catalog.product_types (product_type_id),
    CONSTRAINT product_type_schemas_product_type_id_version_number_key
        UNIQUE (product_type_id, version_number),
    CONSTRAINT product_type_schemas_version_number_check CHECK (version_number > 0)
);

CREATE TABLE catalog.websites (
    website_id          UUID                   NOT NULL,
    business_type_id    UUID                   NOT NULL,
    db_role_name        VARCHAR(63)            NOT NULL,
    status              catalog.website_status NOT NULL,
    storage_limit_bytes BIGINT                 NOT NULL,
    max_image_bytes     BIGINT                 NOT NULL,
    storage_used_bytes  BIGINT                 NOT NULL,
    created_at          TIMESTAMPTZ            NOT NULL,
    updated_at          TIMESTAMPTZ            NOT NULL,
    CONSTRAINT websites_pkey PRIMARY KEY (website_id),
    CONSTRAINT websites_business_type_id_fkey FOREIGN KEY (business_type_id)
        REFERENCES catalog.business_types (business_type_id),
    CONSTRAINT websites_db_role_name_key UNIQUE (db_role_name),
    CONSTRAINT websites_storage_used_bytes_check CHECK (storage_used_bytes >= 0),
    CONSTRAINT websites_storage_limit_bytes_check CHECK (storage_limit_bytes >= 0),
    CONSTRAINT websites_max_image_bytes_check CHECK (max_image_bytes > 0)
);

CREATE TABLE catalog.business_profiles (
    website_id       UUID         NOT NULL,
    business_name    VARCHAR(255) NOT NULL,
    description      TEXT         NULL,
    logo_storage_key VARCHAR(500) NULL,
    logo_byte_size   BIGINT       NULL,
    phone            VARCHAR(50)  NULL,
    email            VARCHAR(320) NULL,
    address          TEXT         NULL,
    timezone         VARCHAR(100) NOT NULL,
    opening_hours    JSONB        NOT NULL DEFAULT '[]',
    created_at       TIMESTAMPTZ  NOT NULL,
    updated_at       TIMESTAMPTZ  NOT NULL,
    CONSTRAINT business_profiles_pkey PRIMARY KEY (website_id),
    CONSTRAINT business_profiles_website_id_fkey FOREIGN KEY (website_id)
        REFERENCES catalog.websites (website_id) ON DELETE RESTRICT,
    CONSTRAINT business_profiles_logo_storage_key_check
        CHECK (logo_storage_key IS NULL OR logo_storage_key LIKE website_id::text || '/%'),
    CONSTRAINT business_profiles_logo_pair_check
        CHECK ((logo_storage_key IS NULL) = (logo_byte_size IS NULL)),
    CONSTRAINT business_profiles_logo_byte_size_check
        CHECK (logo_byte_size IS NULL OR logo_byte_size > 0)
);

CREATE TABLE catalog.categories (
    category_id UUID         NOT NULL,
    website_id  UUID         NOT NULL,
    name        VARCHAR(100) NOT NULL,
    slug        VARCHAR(100) NOT NULL,
    sort_order  INTEGER      NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL,
    updated_at  TIMESTAMPTZ  NOT NULL,
    CONSTRAINT categories_pkey PRIMARY KEY (category_id),
    CONSTRAINT categories_website_id_fkey FOREIGN KEY (website_id)
        REFERENCES catalog.websites (website_id) ON DELETE RESTRICT,
    CONSTRAINT categories_website_id_category_id_key UNIQUE (website_id, category_id),
    CONSTRAINT categories_website_id_slug_key UNIQUE (website_id, slug)
);

CREATE TABLE catalog.products (
    product_id             UUID                   NOT NULL,
    website_id             UUID                   NOT NULL,
    product_type_schema_id UUID                   NOT NULL,
    category_id            UUID                   NULL,
    name                   VARCHAR(255)           NOT NULL,
    description            TEXT                   NULL,
    price                  NUMERIC(12, 2)         NULL,
    attributes             JSONB                  NOT NULL,
    status                 catalog.product_status NOT NULL,
    sort_order             INTEGER                NOT NULL,
    row_version            BIGINT                 NOT NULL,
    created_at             TIMESTAMPTZ            NOT NULL,
    updated_at             TIMESTAMPTZ            NOT NULL,
    CONSTRAINT products_pkey PRIMARY KEY (product_id),
    CONSTRAINT products_website_id_fkey FOREIGN KEY (website_id)
        REFERENCES catalog.websites (website_id) ON DELETE RESTRICT,
    CONSTRAINT products_product_type_schema_id_fkey FOREIGN KEY (product_type_schema_id)
        REFERENCES catalog.product_type_schemas (product_type_schema_id),
    CONSTRAINT products_website_id_product_id_key UNIQUE (website_id, product_id),
    -- The column list is required: a plain SET NULL would also null website_id.
    CONSTRAINT products_website_id_category_id_fkey FOREIGN KEY (website_id, category_id)
        REFERENCES catalog.categories (website_id, category_id) ON DELETE SET NULL (category_id),
    CONSTRAINT products_price_check CHECK (price IS NULL OR price >= 0),
    CONSTRAINT products_row_version_check CHECK (row_version > 0),
    CONSTRAINT products_attributes_check CHECK (jsonb_typeof(attributes) = 'object')
);

CREATE INDEX products_listing ON catalog.products (website_id, status, sort_order);

CREATE INDEX products_attributes_gin ON catalog.products USING GIN (attributes jsonb_path_ops);

CREATE TABLE catalog.product_images (
    product_image_id UUID         NOT NULL,
    website_id       UUID         NOT NULL,
    product_id       UUID         NOT NULL,
    storage_key      VARCHAR(500) NOT NULL,
    byte_size        BIGINT       NOT NULL,
    alt_text         VARCHAR(500) NULL,
    sort_order       INTEGER      NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL,
    CONSTRAINT product_images_pkey PRIMARY KEY (product_image_id),
    CONSTRAINT product_images_website_id_fkey FOREIGN KEY (website_id)
        REFERENCES catalog.websites (website_id) ON DELETE RESTRICT,
    CONSTRAINT product_images_website_id_product_id_fkey FOREIGN KEY (website_id, product_id)
        REFERENCES catalog.products (website_id, product_id) ON DELETE CASCADE,
    CONSTRAINT product_images_storage_key_check CHECK (storage_key LIKE website_id::text || '/%'),
    CONSTRAINT product_images_byte_size_check CHECK (byte_size > 0)
);
