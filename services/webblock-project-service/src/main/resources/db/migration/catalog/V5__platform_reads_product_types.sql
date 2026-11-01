-- Catalog schema, owned by the Project service (spec section 4.1; platform design revision 19 section 6).
-- Added on 2026-10-18 for J2. Run by Flyway as catalog_owner (docs/runbooks/catalog-database.md).
-- Forward-only and immutable: fix a mistake with a new file.


DO $$
BEGIN
    IF current_user <> 'catalog_owner' THEN
        RAISE EXCEPTION 'catalog migrations must run as role catalog_owner (current role: %)', current_user
            USING ERRCODE = '42501';
    END IF;
END
$$;

-- J2 (spec decision J2-11): the Project service freezes product-type schemas into generation contracts and serves
-- their field schemas to AI Codegen, reading them as catalog_platform.
GRANT SELECT ON catalog.product_types, catalog.product_type_schemas TO catalog_platform;
