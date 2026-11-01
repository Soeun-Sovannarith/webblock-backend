#!/usr/bin/env bash
# Local development only (compose.yaml): the login roles and the two databases of
# .claude/docs/runbooks/database.md and catalog-database.md, with the names and passwords from .env.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "${POSTGRES_USER:-postgres}" --dbname postgres \
  -v project_app_user="$PROJECT_DB_APP_USER" \
  -v project_app_password="$PROJECT_DB_APP_PASSWORD" \
  -v project_migrator_user="$PROJECT_DB_MIGRATOR_USER" \
  -v project_migrator_password="$PROJECT_DB_MIGRATOR_PASSWORD" \
  -v catalog_platform_user="$CATALOG_DB_PLATFORM_USER" \
  -v catalog_platform_password="$CATALOG_DB_PLATFORM_PASSWORD" \
  -v catalog_migrator_user="$CATALOG_DB_MIGRATOR_USER" \
  -v catalog_migrator_password="$CATALOG_DB_MIGRATOR_PASSWORD" <<'SQL'
CREATE ROLE :"project_migrator_user" LOGIN PASSWORD :'project_migrator_password' IN ROLE project_migrator;
CREATE ROLE :"project_app_user" LOGIN PASSWORD :'project_app_password' IN ROLE project_app;
CREATE DATABASE webblock_project OWNER project_migrator;
REVOKE ALL ON DATABASE webblock_project FROM PUBLIC;
GRANT CONNECT ON DATABASE webblock_project TO project_app;

CREATE ROLE :"catalog_migrator_user" LOGIN PASSWORD :'catalog_migrator_password';
GRANT catalog_owner TO :"catalog_migrator_user" WITH INHERIT FALSE, SET TRUE;
CREATE ROLE :"catalog_platform_user" LOGIN PASSWORD :'catalog_platform_password';
GRANT catalog_platform TO :"catalog_platform_user" WITH INHERIT TRUE, SET FALSE;
CREATE DATABASE catalog OWNER catalog_owner;
SQL
