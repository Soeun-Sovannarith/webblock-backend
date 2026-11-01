-- Drops the NOLOGIN role that Provisioning no longer uses (decision 0021 D2):
--   provisioning_payment_intake  replaced by provisioning_hosting_intake in migration 00011; since
--                                then its membership grants nothing. Migration 00018 asserts that it
--                                holds no privilege, owns nothing and has no role membership in the
--                                database it migrates.
--
-- Run by an administrator (roles are cluster-wide and provisioning_migrator has no CREATEROLE), once,
-- connected to the Provisioning database, after `make migrate-up` has applied 00018 there:
--
--     psql -h <host> -U "$PG_USER" -d <provisioning database> -v ON_ERROR_STOP=1 -f scripts/db/drop-retired-roles.sql
--
-- It refuses, and drops nothing, unless goose_db_version of the connected database shows migration
-- 18 or later applied. DROP ROLE itself refuses while the role still holds a privilege or owns an
-- object in any database of the cluster, so a server with several Provisioning databases needs 00018
-- in each of them first. Idempotent: a role that is already gone is not an error.
--
-- scripts/db/bootstrap-roles.sql keeps creating the role, because migration 00010 refuses to run
-- without it on an empty server; on such a server run this script again after 00018.
DO $$
DECLARE
    applied bigint;
BEGIN
    IF pg_catalog.to_regclass('public.goose_db_version') IS NULL THEN
        RAISE EXCEPTION 'database % has no goose_db_version table; connect to the Provisioning database', pg_catalog.current_database()
            USING ERRCODE = '55000';
    END IF;
    SELECT max(v.version_id) INTO applied FROM public.goose_db_version AS v WHERE v.is_applied;
    IF applied IS NULL OR applied < 18 THEN
        RAISE EXCEPTION 'database % is at migration %, not 18 or later; run make migrate-up first', pg_catalog.current_database(), coalesce(applied::text, 'none')
            USING ERRCODE = '55000';
    END IF;
    DROP ROLE IF EXISTS provisioning_payment_intake;
END
$$;
