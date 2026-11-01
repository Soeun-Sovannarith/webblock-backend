-- Brings the catalog database level with Notion "6 · Shared catalog" (platform-db-v6), "Who can read and write".
-- 1. PostgreSQL makes a row type for every table and gives PUBLIC USAGE on it by default; Notion gives no role but
--    the four it lists anything. V6 took that back on the two enums only; this revokes it on the row type of the
--    eight catalog tables and of the catalog's Flyway history table.
-- 2. A business profile is never deleted while its website exists, and websites are never deleted: the site's CMS
--    edits the profile. catalog_site loses DELETE on catalog.business_profiles and keeps SELECT, INSERT and UPDATE.
-- Tables, columns, rows, constraints, indexes, triggers, policies and every other grant do not change.
-- Run by Flyway as catalog_owner. Forward-only and immutable: fix a mistake with a new file.


DO $$
BEGIN
    IF current_user <> 'catalog_owner' THEN
        RAISE EXCEPTION 'catalog migrations must run as role catalog_owner (current role: %)', current_user
            USING ERRCODE = '42501';
    END IF;
END
$$;

REVOKE ALL ON TYPE
    catalog.business_types,
    catalog.product_types,
    catalog.product_type_schemas,
    catalog.websites,
    catalog.business_profiles,
    catalog.categories,
    catalog.products,
    catalog.product_images,
    catalog_flyway.flyway_schema_history
  FROM PUBLIC;

REVOKE DELETE ON catalog.business_profiles FROM catalog_site;
