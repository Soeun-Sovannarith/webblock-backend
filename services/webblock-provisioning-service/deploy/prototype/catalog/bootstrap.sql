-- The prototype's login roles and the database of the shared catalog (decision 0014 section 14;
-- decision 0017 section 1). Not a production artefact.
--
-- Run once by deploy/kind/proto-catalog.sh, as the container's superuser, on the database postgres of
-- a new container webblock-catalog-db, right after the Project service's
-- scripts/db/catalog-bootstrap-roles.sql has created the three NOLOGIN roles of design section 6
-- (catalog_owner, catalog_platform, catalog_site). The caller first sets three psql variables, each a
-- SCRAM-SHA-256 verifier computed on the host (never a cleartext password):
--   migrator_verifier, platform_verifier, access_admin_verifier
--
-- Login roles of the stand-in (prototype only; the design names who uses the roles, not the logins):
--   catalog_migrator            applies the Project service's catalog migrations with Flyway; its
--                               session runs as catalog_owner (ALTER ROLE ... SET role below, and
--                               Flyway's initSql), so every object the migrations create is owned by
--                               catalog_owner
--   project_catalog_platform    the catalog_platform stand-in tool's login (python -m
--                               provisioning.hosts.devtools.catalog_platform register)
--   provisioning_catalog_admin  Provisioning's access_setup: CREATEROLE with ADMIN OPTION on
--                               catalog_site, so it can create site_<hex> roles and add them to
--                               catalog_site; it is not a member that can use catalog_site itself
--                               (INHERIT FALSE, SET FALSE), and it is not a superuser (OD-46 keeps
--                               the production administrator open)

\set ON_ERROR_STOP on

CREATE ROLE catalog_migrator LOGIN PASSWORD :'migrator_verifier';
GRANT catalog_owner TO catalog_migrator WITH INHERIT FALSE, SET TRUE;

CREATE ROLE project_catalog_platform LOGIN PASSWORD :'platform_verifier';
GRANT catalog_platform TO project_catalog_platform WITH INHERIT TRUE, SET FALSE;

CREATE ROLE provisioning_catalog_admin LOGIN CREATEROLE PASSWORD :'access_admin_verifier';
GRANT catalog_site TO provisioning_catalog_admin WITH ADMIN TRUE, INHERIT FALSE, SET FALSE;

CREATE DATABASE catalog OWNER catalog_owner;

\connect catalog
ALTER ROLE catalog_migrator IN DATABASE catalog SET role = 'catalog_owner';
