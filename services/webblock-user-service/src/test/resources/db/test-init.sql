-- Test database only (Testcontainers). Never used against the real database.
-- application.yml runs Flyway with "SET ROLE account" (DevOps setup), but a fresh PostgreSQL has no such role,
-- and since PostgreSQL 15 only the schema owner may create tables in "public". So create the role and let it create tables.
CREATE ROLE account;
GRANT ALL ON SCHEMA public TO account;
