-- Catalog roles (spec section 4.1; platform design revision 19 section 6). Run once per PostgreSQL server, as a role
-- with CREATEROLE. Safe to run again. Login roles and the database are created per environment
-- (.claude/docs/runbooks/catalog-database.md).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'catalog_owner') THEN
    CREATE ROLE catalog_owner NOLOGIN;      -- owns the schema; used only by the migrations
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'catalog_platform') THEN
    CREATE ROLE catalog_platform NOLOGIN;   -- the Project service's runtime writes of website rows
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'catalog_site') THEN
    CREATE ROLE catalog_site NOLOGIN;       -- the group of every site_<project id hex> login (Provisioning creates those)
  END IF;
END
$$;
