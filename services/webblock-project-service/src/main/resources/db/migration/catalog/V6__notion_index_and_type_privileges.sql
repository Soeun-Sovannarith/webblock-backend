-- Brings the catalog database level with Notion "6 · Shared catalog" (platform-db-v6): the page names one index of
-- its own on catalog.products (the GIN on attributes) and gives no role but the owner anything on the two enums.
-- Drops the extra listing index and revokes PostgreSQL's default PUBLIC USAGE on catalog.website_status and
-- catalog.product_status. Tables, columns, rows, constraints, the GIN index, policies and role grants do not change.
-- Run by Flyway as catalog_owner. Forward-only and immutable: fix a mistake with a new file.


DO $$
BEGIN
    IF current_user <> 'catalog_owner' THEN
        RAISE EXCEPTION 'catalog migrations must run as role catalog_owner (current role: %)', current_user
            USING ERRCODE = '42501';
    END IF;
END
$$;

DROP INDEX catalog.products_listing;

REVOKE ALL ON TYPE catalog.website_status, catalog.product_status FROM PUBLIC;
