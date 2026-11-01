-- Shared catalog (team design "Database Design", section "Shared catalog"): every generated website's business
-- profile, categories, products and product images, plus the business type and product type lists, in schema
-- catalog. The design gives the schema to the Project service; this service keeps a local copy, as it does for the
-- project and provisioning tables. website_id is the project's id and references project.projects (no foreign key).
-- Each site's backend logs in as its own role (catalog.websites.db_role_name, created by the harness:
-- app/services/site_catalog.py). Row-level security maps that role to its website, and only an active website gets
-- any rows; inserts fill website_id from catalog.current_website_id(), so the site's SQL never names it. The owner
-- (ai_codegen) and its members, such as the harness login, are not subject to the policies: they register websites
-- and seed them. Image and logo files live in object storage; a trigger adds every stored byte_size to
-- websites.storage_used_bytes and refuses a file over max_image_bytes or past storage_limit_bytes.
-- Extensions to the design, for the site's owner portal: business_profiles.maps_url and portal_edited_at, and the
-- table portal_revoked_sessions. Enum values are lowercase, as in the design.
-- Replaces site_data (006), which held no rows anywhere it was applied.
-- Idempotent: IF EXISTS / IF NOT EXISTS, CREATE OR REPLACE, duplicate_object guards, DROP ... IF EXISTS before each
-- policy and trigger, ON CONFLICT DO NOTHING for the reference data; grants only when the group role exists.
DROP SCHEMA IF EXISTS site_data CASCADE;

CREATE SCHEMA IF NOT EXISTS catalog;

DO $$ BEGIN CREATE TYPE catalog.website_status AS ENUM ('active','suspended','archived'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE catalog.product_status AS ENUM ('draft','published','archived'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS catalog.business_types (
 business_type_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 code varchar(50) NOT NULL UNIQUE,
 name varchar(100) NOT NULL,
 is_active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog.product_types (
 product_type_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 business_type_id uuid NOT NULL REFERENCES catalog.business_types(business_type_id),
 code varchar(50) NOT NULL,
 name varchar(100) NOT NULL,
 is_active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (business_type_id, code)
);

CREATE TABLE IF NOT EXISTS catalog.product_type_schemas (
 product_type_schema_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 product_type_id uuid NOT NULL REFERENCES catalog.product_types(product_type_id),
 version_number integer NOT NULL CHECK (version_number > 0),
 field_schema jsonb NOT NULL,
 is_active boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (product_type_id, version_number)
);

CREATE TABLE IF NOT EXISTS catalog.websites (
 website_id uuid PRIMARY KEY,
 business_type_id uuid NOT NULL REFERENCES catalog.business_types(business_type_id),
 db_role_name varchar(63) NOT NULL UNIQUE,
 status catalog.website_status NOT NULL DEFAULT 'active',
 storage_limit_bytes bigint NOT NULL CHECK (storage_limit_bytes >= 0),
 max_image_bytes bigint NOT NULL CHECK (max_image_bytes > 0),
 storage_used_bytes bigint NOT NULL DEFAULT 0 CHECK (storage_used_bytes >= 0),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CONSTRAINT ck_websites_storage_within_limit CHECK (storage_used_bytes <= storage_limit_bytes)
);

-- The website of the current login role, or NULL (another role, or a website that is not active). Runs as the caller,
-- so a site reads only its own websites row.
CREATE OR REPLACE FUNCTION catalog.current_website_id() RETURNS uuid
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
  SELECT w.website_id FROM catalog.websites w WHERE w.db_role_name = current_user AND w.status = 'active'
$$;

-- The extra-field rules a new product follows: the active 'item' product type of the website's business type.
CREATE OR REPLACE FUNCTION catalog.item_schema_id(p_website_id uuid) RETURNS uuid
LANGUAGE sql STABLE SET search_path = pg_catalog AS $$
  SELECT s.product_type_schema_id
  FROM catalog.websites w
  JOIN catalog.product_types t ON t.business_type_id = w.business_type_id AND t.code = 'item' AND t.is_active
  JOIN catalog.product_type_schemas s ON s.product_type_id = t.product_type_id AND s.is_active
  WHERE w.website_id = p_website_id
  ORDER BY s.version_number DESC
  LIMIT 1
$$;

CREATE TABLE IF NOT EXISTS catalog.business_profiles (
 website_id uuid PRIMARY KEY REFERENCES catalog.websites(website_id) ON DELETE CASCADE,
 business_name varchar(255) NOT NULL,
 description text,
 logo_storage_key varchar(500),
 logo_byte_size bigint CHECK (logo_byte_size >= 0),
 phone varchar(50),
 email varchar(320),
 address text,
 timezone varchar(100) NOT NULL,
 opening_hours jsonb NOT NULL DEFAULT '[]'::jsonb,
 maps_url text,
 portal_edited_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS catalog.categories (
 category_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 website_id uuid NOT NULL DEFAULT catalog.current_website_id() REFERENCES catalog.websites(website_id) ON DELETE CASCADE,
 name varchar(100) NOT NULL,
 slug varchar(100) NOT NULL,
 sort_order integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (website_id, slug),
 UNIQUE (website_id, category_id)
);

-- category_id and product images reference their parent together with website_id, so no row can point at another
-- website's category or product.
CREATE TABLE IF NOT EXISTS catalog.products (
 product_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 website_id uuid NOT NULL DEFAULT catalog.current_website_id() REFERENCES catalog.websites(website_id) ON DELETE CASCADE,
 product_type_schema_id uuid NOT NULL DEFAULT catalog.item_schema_id(catalog.current_website_id())
   REFERENCES catalog.product_type_schemas(product_type_schema_id),
 category_id uuid,
 name varchar(255) NOT NULL,
 description text,
 price numeric(12,2) CHECK (price >= 0),
 attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
 status catalog.product_status NOT NULL DEFAULT 'published',
 sort_order integer NOT NULL DEFAULT 0,
 row_version bigint NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (website_id, product_id),
 FOREIGN KEY (website_id, category_id) REFERENCES catalog.categories(website_id, category_id) ON DELETE SET NULL (category_id)
);
CREATE INDEX IF NOT EXISTS ix_catalog_products_order ON catalog.products(website_id, sort_order, created_at);

CREATE TABLE IF NOT EXISTS catalog.product_images (
 product_image_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 website_id uuid NOT NULL DEFAULT catalog.current_website_id() REFERENCES catalog.websites(website_id) ON DELETE CASCADE,
 product_id uuid NOT NULL,
 storage_key varchar(500) NOT NULL UNIQUE,
 byte_size bigint NOT NULL CHECK (byte_size >= 0),
 alt_text varchar(500),
 sort_order integer NOT NULL DEFAULT 0,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY (website_id, product_id) REFERENCES catalog.products(website_id, product_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_catalog_product_images_product ON catalog.product_images(website_id, product_id, sort_order);

-- Extension for the owner portal: sessions Keycloak ended (back-channel logout), refused by the site's portal.
CREATE TABLE IF NOT EXISTS catalog.portal_revoked_sessions (
 website_id uuid NOT NULL DEFAULT catalog.current_website_id() REFERENCES catalog.websites(website_id) ON DELETE CASCADE,
 sid varchar(255) NOT NULL,
 revoked_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (website_id, sid)
);

-- Edits count: updated_at, and row_version (+1 per product edit).
CREATE OR REPLACE FUNCTION catalog.touch_row() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
  NEW.updated_at := now();
  IF TG_TABLE_NAME = 'products' THEN NEW.row_version := OLD.row_version + 1; END IF;
  RETURN NEW;
END $$;

-- Storage accounting for product images and the logo; SECURITY DEFINER because sites may not update websites.
CREATE OR REPLACE FUNCTION catalog.track_storage() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $$
DECLARE
  site uuid;
  added bigint := 0;
  removed bigint := 0;
  max_bytes bigint;
BEGIN
  -- Separate branches: PL/pgSQL resolves every field a statement names against the trigger's table.
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    site := NEW.website_id;
    IF TG_TABLE_NAME = 'product_images' THEN added := NEW.byte_size; ELSE added := COALESCE(NEW.logo_byte_size, 0); END IF;
  END IF;
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    site := COALESCE(site, OLD.website_id);
    IF TG_TABLE_NAME = 'product_images' THEN removed := OLD.byte_size; ELSE removed := COALESCE(OLD.logo_byte_size, 0); END IF;
  END IF;
  IF added > 0 AND (TG_OP = 'INSERT' OR added IS DISTINCT FROM removed) THEN
    SELECT w.max_image_bytes INTO max_bytes FROM catalog.websites w WHERE w.website_id = site;
    IF added > max_bytes THEN
      RAISE EXCEPTION 'The file is % bytes; this website allows % bytes per image.', added, max_bytes
        USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_websites_max_image_bytes';
    END IF;
  END IF;
  IF added <> removed THEN
    UPDATE catalog.websites SET storage_used_bytes = storage_used_bytes + added - removed, updated_at = now()
    WHERE website_id = site;
  END IF;
  RETURN NULL;
END $$;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['websites','business_profiles','categories','products'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS tr_touch ON catalog.%I', t);
    EXECUTE format('CREATE TRIGGER tr_touch BEFORE UPDATE ON catalog.%I FOR EACH ROW EXECUTE FUNCTION catalog.touch_row()', t);
  END LOOP;
  FOREACH t IN ARRAY ARRAY['product_images','business_profiles'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS tr_storage ON catalog.%I', t);
    EXECUTE format('CREATE TRIGGER tr_storage AFTER INSERT OR UPDATE OR DELETE ON catalog.%I FOR EACH ROW EXECUTE FUNCTION catalog.track_storage()', t);
  END LOOP;
END $$;

-- Row-level security: a site sees its own websites row, and only its own website's rows elsewhere.
ALTER TABLE catalog.websites ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS website_own_row ON catalog.websites;
CREATE POLICY website_own_row ON catalog.websites FOR SELECT USING (db_role_name = current_user);

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['business_profiles','categories','products','product_images','portal_revoked_sessions'] LOOP
    EXECUTE format('ALTER TABLE catalog.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS website_isolation ON catalog.%I', t);
    EXECUTE format('CREATE POLICY website_isolation ON catalog.%I USING (website_id = (SELECT catalog.current_website_id())) '
                   'WITH CHECK (website_id = (SELECT catalog.current_website_id()))', t);
  END LOOP;
END $$;

-- Reference data for a local copy: the business types onboarding offers (app/schemas/business.py BusinessType), each
-- with one product type 'item' whose version-1 field_schema allows any attributes. Project's catalog owns the real lists.
INSERT INTO catalog.business_types (code, name) VALUES
 ('everyday_store', 'Everyday store'), ('handmade', 'Handmade'), ('retail', 'Retail'), ('restaurant', 'Restaurant'),
 ('cafe', 'Cafe'), ('services', 'Services'), ('portfolio', 'Portfolio'), ('agency', 'Agency')
ON CONFLICT (code) DO NOTHING;
INSERT INTO catalog.product_types (business_type_id, code, name)
SELECT business_type_id, 'item', 'Item' FROM catalog.business_types
ON CONFLICT (business_type_id, code) DO NOTHING;
INSERT INTO catalog.product_type_schemas (product_type_id, version_number, field_schema)
SELECT product_type_id, 1, '{"type": "object"}'::jsonb FROM catalog.product_types WHERE code = 'item'
ON CONFLICT (product_type_id, version_number) DO NOTHING;

-- Sites' privileges go to the group role webblock_site; each site's login role is a member. Creating roles needs
-- CREATEROLE, so a DBA creates the group where the migration role may not (README section 9.7) and re-runs this block.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'webblock_site') THEN
    CREATE ROLE webblock_site NOLOGIN;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'Cannot create role webblock_site here; create it as a DBA and re-run the grants in sql/007_catalog.sql.';
END $$;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'webblock_site') THEN
    GRANT USAGE ON SCHEMA catalog TO webblock_site;
    GRANT SELECT ON catalog.business_types, catalog.product_types, catalog.product_type_schemas, catalog.websites TO webblock_site;
    GRANT SELECT, UPDATE ON catalog.business_profiles TO webblock_site;
    GRANT SELECT, INSERT, UPDATE, DELETE ON catalog.categories, catalog.products, catalog.product_images TO webblock_site;
    GRANT SELECT, INSERT, DELETE ON catalog.portal_revoked_sessions TO webblock_site;
    GRANT EXECUTE ON FUNCTION catalog.current_website_id(), catalog.item_schema_id(uuid) TO webblock_site;
  END IF;
END $$;
