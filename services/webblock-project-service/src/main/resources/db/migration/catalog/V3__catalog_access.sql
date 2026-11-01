-- Catalog schema, owned by the Project service (spec section 4.1; platform design revision 19 section 6).
-- Moved on 2026-10-14 from Provisioning's prototype catalog script 00003_catalog_access. Run by Flyway as catalog_owner
-- (docs/runbooks/catalog-database.md). Forward-only and immutable: fix a mistake with a new file.


DO $$
BEGIN
    IF current_user <> 'catalog_owner' THEN
        RAISE EXCEPTION 'catalog migrations must run as role catalog_owner (current role: %)', current_user
            USING ERRCODE = '42501';
    END IF;
END
$$;

ALTER TABLE catalog.business_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog.business_profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE catalog.categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog.categories FORCE ROW LEVEL SECURITY;
ALTER TABLE catalog.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog.products FORCE ROW LEVEL SECURITY;
ALTER TABLE catalog.product_images ENABLE ROW LEVEL SECURITY;
ALTER TABLE catalog.product_images FORCE ROW LEVEL SECURITY;

CREATE POLICY business_profiles_own_website ON catalog.business_profiles
    TO catalog_site
    USING (website_id = (SELECT catalog.current_website_id()))
    WITH CHECK (website_id = (SELECT catalog.current_website_id()));

CREATE POLICY business_profiles_platform ON catalog.business_profiles
    TO catalog_platform
    USING (true)
    WITH CHECK (true);

CREATE POLICY categories_own_website ON catalog.categories
    TO catalog_site
    USING (website_id = (SELECT catalog.current_website_id()))
    WITH CHECK (website_id = (SELECT catalog.current_website_id()));

CREATE POLICY products_own_website ON catalog.products
    TO catalog_site
    USING (website_id = (SELECT catalog.current_website_id()))
    WITH CHECK (website_id = (SELECT catalog.current_website_id()));

CREATE POLICY product_images_own_website ON catalog.product_images
    TO catalog_site
    USING (website_id = (SELECT catalog.current_website_id()))
    WITH CHECK (website_id = (SELECT catalog.current_website_id()));

-- catalog_platform: inserts websites and the first business_profiles row; updates websites.status
-- and the two limit columns.
GRANT USAGE ON SCHEMA catalog TO catalog_platform;
GRANT INSERT ON catalog.websites TO catalog_platform;
GRANT UPDATE (status, storage_limit_bytes, max_image_bytes) ON catalog.websites TO catalog_platform;
GRANT INSERT ON catalog.business_profiles TO catalog_platform;

-- catalog_site: the tenant tables, the reference tables read-only, and current_website_id(). No DDL,
-- no ownership, no BYPASSRLS, and nothing on catalog.websites.
GRANT USAGE ON SCHEMA catalog TO catalog_site;
GRANT SELECT, INSERT, UPDATE, DELETE
    ON catalog.business_profiles, catalog.categories, catalog.products, catalog.product_images
    TO catalog_site;
GRANT SELECT ON catalog.business_types, catalog.product_types, catalog.product_type_schemas
    TO catalog_site;
GRANT EXECUTE ON FUNCTION catalog.current_website_id() TO catalog_site;
