-- Hardening for the shared catalog (007): site roles run code this service didn't write (GLM may write it), on the
-- same database the harness uses with RLS-bypassing privileges.
-- 1. No role may create objects in public. Otherwise a site could plant a function or operator there (such as
--    public.=(varchar, text)) that a harness query resolves to instead of the built-in one. This is the default from
--    Postgres 15; older versions and upgraded databases still grant it to PUBLIC. site_catalog.prepare_site also pins
--    its search_path, so this is the second guard. Revoking needs the schema's owner (on PG 15+ the database owner,
--    ai_codegen); anyone else gets a WARNING and nothing changes.
-- 2. webblock_site holds the grants and must never log in itself. An earlier setup created it with LOGIN; changing
--    that needs CREATEROLE, so where the migration role lacks it this only raises a NOTICE for a DBA to act on.
-- Idempotent: REVOKE of a missing privilege and ALTER ROLE ... NOLOGIN on a NOLOGIN role change nothing.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'webblock_site' AND rolcanlogin) THEN
    ALTER ROLE webblock_site NOLOGIN;
  END IF;
EXCEPTION WHEN insufficient_privilege THEN
  RAISE NOTICE 'Cannot alter role webblock_site here; as a DBA run: ALTER ROLE webblock_site NOLOGIN;';
END $$;
