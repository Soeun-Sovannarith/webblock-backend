-- Catalog schema, owned by the Project service (spec section 4.1; platform design revision 19 section 6).
-- Moved on 2026-10-14 from Provisioning's prototype catalog script 00002_catalog_functions. Run by Flyway as catalog_owner
-- (docs/runbooks/catalog-database.md). Forward-only and immutable: fix a mistake with a new file.


DO $$
BEGIN
    IF current_user <> 'catalog_owner' THEN
        RAISE EXCEPTION 'catalog migrations must run as role catalog_owner (current role: %)', current_user
            USING ERRCODE = '42501';
    END IF;
END
$$;

-- The website of the role that logged in, while that website is active; NULL otherwise. It must use
-- session_user: inside a SECURITY DEFINER function current_user is the function's owner, so a
-- current_user lookup would match no website (design section 6; decision 0011 section 12, G3).
-- session_user is the login role, which a site role cannot change.
CREATE FUNCTION catalog.current_website_id() RETURNS UUID
    LANGUAGE sql
    STABLE
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
    SELECT w.website_id
    FROM catalog.websites AS w
    WHERE w.db_role_name = session_user AND w.status = 'active'
$$;

-- catalog.websites: business_type_id (fixed at creation) and db_role_name are immutable.
CREATE FUNCTION catalog.websites_immutable_columns() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF NEW.business_type_id IS DISTINCT FROM OLD.business_type_id
       OR NEW.db_role_name IS DISTINCT FROM OLD.db_role_name THEN
        RAISE EXCEPTION 'catalog.websites: business_type_id and db_role_name are immutable'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER websites_immutable_columns
    BEFORE UPDATE ON catalog.websites
    FOR EACH ROW EXECUTE FUNCTION catalog.websites_immutable_columns();

-- catalog.product_type_schemas: a changed definition is always a new version row.
CREATE FUNCTION catalog.product_type_schemas_immutable_columns() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF NEW.product_type_id IS DISTINCT FROM OLD.product_type_id
       OR NEW.version_number IS DISTINCT FROM OLD.version_number
       OR NEW.field_schema IS DISTINCT FROM OLD.field_schema THEN
        RAISE EXCEPTION 'catalog.product_type_schemas: product_type_id, version_number and field_schema are immutable; add a new version'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER product_type_schemas_immutable_columns
    BEFORE UPDATE ON catalog.product_type_schemas
    FOR EACH ROW EXECUTE FUNCTION catalog.product_type_schemas_immutable_columns();

-- catalog.products: the pinned schema's product type must belong to the website's business type.
-- SECURITY DEFINER because it reads catalog.websites, which site roles cannot read; it returns
-- nothing from that table, it only refuses the row.
CREATE FUNCTION catalog.products_schema_matches_business_type() RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM catalog.product_type_schemas AS s
        JOIN catalog.product_types AS t ON t.product_type_id = s.product_type_id
        JOIN catalog.websites AS w ON w.business_type_id = t.business_type_id
        WHERE s.product_type_schema_id = NEW.product_type_schema_id
          AND w.website_id = NEW.website_id
    ) THEN
        RAISE EXCEPTION 'catalog.products: the product type schema does not belong to the website''s business type'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END
$$;

CREATE TRIGGER products_schema_matches_business_type
    BEFORE INSERT OR UPDATE OF website_id, product_type_schema_id ON catalog.products
    FOR EACH ROW EXECUTE FUNCTION catalog.products_schema_matches_business_type();

-- Storage accounting (design section 6, catalog.product_images): after every insert, delete or
-- byte_size change of an image, and every logo change of a profile. An increase is applied only
-- while the website stays within storage_limit_bytes and the new object within max_image_bytes;
-- otherwise catalog_storage_limit is raised. The row update serializes concurrent uploads of one
-- website. A decrease is subtracted.
CREATE FUNCTION catalog.track_media_bytes() RETURNS trigger
    LANGUAGE plpgsql
    SECURITY DEFINER
    SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    w        UUID;
    old_size BIGINT := 0;
    new_size BIGINT := 0;
    delta    BIGINT;
    changed  BIGINT;
BEGIN
    IF TG_TABLE_NAME = 'product_images' THEN
        IF TG_OP IN ('UPDATE', 'DELETE') THEN
            old_size := OLD.byte_size;
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') THEN
            new_size := NEW.byte_size;
        END IF;
    ELSIF TG_TABLE_NAME = 'business_profiles' THEN
        IF TG_OP IN ('UPDATE', 'DELETE') THEN
            old_size := COALESCE(OLD.logo_byte_size, 0);
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') THEN
            new_size := COALESCE(NEW.logo_byte_size, 0);
        END IF;
    ELSE
        RAISE EXCEPTION 'catalog.track_media_bytes: not a media table' USING ERRCODE = '39P01';
    END IF;
    IF TG_OP = 'DELETE' THEN
        w := OLD.website_id;
    ELSE
        w := NEW.website_id;
    END IF;
    delta := new_size - old_size;
    IF delta > 0 THEN
        UPDATE catalog.websites
        SET storage_used_bytes = storage_used_bytes + delta
        WHERE website_id = w
          AND storage_used_bytes + delta <= storage_limit_bytes
          AND new_size <= max_image_bytes;
        GET DIAGNOSTICS changed = ROW_COUNT;
        IF changed = 0 THEN
            RAISE EXCEPTION 'catalog_storage_limit' USING ERRCODE = '53400';
        END IF;
    ELSIF delta < 0 THEN
        UPDATE catalog.websites
        SET storage_used_bytes = storage_used_bytes + delta
        WHERE website_id = w;
    END IF;
    RETURN NULL;
END
$$;

CREATE TRIGGER product_images_track_media_bytes
    AFTER INSERT OR DELETE OR UPDATE OF byte_size ON catalog.product_images
    FOR EACH ROW EXECUTE FUNCTION catalog.track_media_bytes();

CREATE TRIGGER business_profiles_track_media_bytes
    AFTER INSERT OR DELETE OR UPDATE OF logo_storage_key, logo_byte_size ON catalog.business_profiles
    FOR EACH ROW EXECUTE FUNCTION catalog.track_media_bytes();

REVOKE ALL ON FUNCTION catalog.current_website_id() FROM PUBLIC;
REVOKE ALL ON FUNCTION catalog.websites_immutable_columns() FROM PUBLIC;
REVOKE ALL ON FUNCTION catalog.product_type_schemas_immutable_columns() FROM PUBLIC;
REVOKE ALL ON FUNCTION catalog.products_schema_matches_business_type() FROM PUBLIC;
REVOKE ALL ON FUNCTION catalog.track_media_bytes() FROM PUBLIC;
