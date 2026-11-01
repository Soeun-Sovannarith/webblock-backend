-- Initialize databases and roles for WebBlock Microservices Integration
CREATE DATABASE webblock_user;
CREATE DATABASE webblock_project;
CREATE DATABASE catalog;
CREATE DATABASE aicode_db;
CREATE DATABASE provisioning;

-- Roles for project-service & catalog
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'project_migrator') THEN
    CREATE ROLE project_migrator NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'project_app') THEN
    CREATE ROLE project_app NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'project_app_login') THEN
    CREATE ROLE project_app_login WITH LOGIN PASSWORD 'project_secret' IN ROLE project_app;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'project_migrator_login') THEN
    CREATE ROLE project_migrator_login WITH LOGIN PASSWORD 'project_secret' IN ROLE project_migrator;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'catalog_owner') THEN
    CREATE ROLE catalog_owner NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'catalog_platform') THEN
    CREATE ROLE catalog_platform NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'catalog_site') THEN
    CREATE ROLE catalog_site NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'catalog_platform_login') THEN
    CREATE ROLE catalog_platform_login WITH LOGIN PASSWORD 'catalog_secret' IN ROLE catalog_platform;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'catalog_migrator_login') THEN
    CREATE ROLE catalog_migrator_login WITH LOGIN PASSWORD 'catalog_secret' IN ROLE catalog_owner;
  END IF;

  -- AI Code Service role
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'webblock') THEN
    CREATE ROLE webblock WITH LOGIN SUPERUSER PASSWORD 'webblock';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'webblock_site') THEN
    CREATE ROLE webblock_site NOLOGIN;
  END IF;

  -- Provisioning Service roles
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_migrator') THEN
    CREATE ROLE provisioning_migrator WITH LOGIN SUPERUSER PASSWORD 'provisioning';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_api') THEN
    CREATE ROLE provisioning_api WITH LOGIN PASSWORD 'provisioning';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_worker') THEN
    CREATE ROLE provisioning_worker WITH LOGIN PASSWORD 'provisioning';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_intake') THEN
    CREATE ROLE provisioning_intake WITH LOGIN PASSWORD 'provisioning';
  END IF;

  -- User Service role
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'account') THEN
    CREATE ROLE account WITH LOGIN SUPERUSER PASSWORD 'account';
  END IF;
END
$$;

-- Grant database ownerships
ALTER DATABASE webblock_user OWNER TO account;
ALTER DATABASE webblock_project OWNER TO project_migrator_login;
ALTER DATABASE catalog OWNER TO catalog_migrator_login;
ALTER DATABASE aicode_db OWNER TO webblock;
ALTER DATABASE provisioning OWNER TO provisioning_migrator;
