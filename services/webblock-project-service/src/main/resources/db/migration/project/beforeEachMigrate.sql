-- Flyway callback: runs before every migration, so a later migration refuses any role but project_migrator as V1 does.
DO $$
BEGIN
  IF current_user <> 'project_migrator' THEN
    RAISE EXCEPTION 'Migrations must run as project_migrator, not %', current_user;
  END IF;
END
$$;
