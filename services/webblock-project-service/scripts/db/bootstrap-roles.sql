-- Run once per PostgreSQL server, as a role with CREATEROLE. Safe to run again.
-- Login roles are created per environment and granted membership (.claude/docs/runbooks/database.md).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'project_migrator') THEN
    CREATE ROLE project_migrator NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'project_app') THEN
    CREATE ROLE project_app NOLOGIN;
  END IF;
END
$$;
