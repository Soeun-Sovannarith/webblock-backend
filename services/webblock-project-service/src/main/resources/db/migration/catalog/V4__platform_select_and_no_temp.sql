-- Catalog schema, owned by the Project service (spec section 4.1; platform design revision 19 section 6).
-- Moved on 2026-10-14 from Provisioning's prototype catalog script 00005_platform_select_and_no_temp. Run by Flyway as catalog_owner
-- (docs/runbooks/catalog-database.md). Forward-only and immutable: fix a mistake with a new file.


DO $$
BEGIN
    IF current_user <> 'catalog_owner' THEN
        RAISE EXCEPTION 'catalog migrations must run as role catalog_owner (current role: %)', current_user
            USING ERRCODE = '42501';
    END IF;
END
$$;

GRANT SELECT ON catalog.websites TO catalog_platform;
GRANT SELECT ON catalog.business_types TO catalog_platform;

-- The database name is not fixed, so the statement is built for the current one.
DO $$
BEGIN
    EXECUTE pg_catalog.format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', pg_catalog.current_database());
END
$$;
