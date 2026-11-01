-- Creates the nine NOLOGIN group roles used by the Provisioning service:
--   provisioning_migrator        owns the schemas and runs the migrations;
--   provisioning_api             the HTTP and MCP process (decision 0004 section 8);
--   provisioning_worker          the release worker and reconciler;
--   provisioning_intake          release intake: the inbox of Project's "source published" event, the
--                                accepted release and its first build step (decision 0008 section 5);
--   provisioning_build           build attempts: start, result, failure and deadline sweep (decision 0008);
--   provisioning_operator        an operator's re-drive of an inbox event whose budget ran out
--                                (decision 0011 section 6, migration 00010);
--   provisioning_payment_intake  the only role whose members may insert suspend_hosting and
--                                resume_hosting commands (decision 0011 section 6); no member and no
--                                privilege until Phase 6;
--   provisioning_hosting_intake  replaces provisioning_payment_intake from migration 00011 (decision
--                                0012 section 4): the only role whose members may insert
--                                suspend_hosting and resume_hosting commands. Migration 00011 refuses
--                                to run without it. provisioning_payment_intake stays created so that
--                                00010 still runs on an empty server; after 00011 its membership
--                                grants nothing and the owner may drop it.
--   provisioning_bootstrap       the bootstrap component's read-only login (decision 0014 section 16
--                                item 5, migration 00015): SELECT on the few columns it reads, no
--                                write. Migration 00015 refuses to run without it.
-- The build_worker process logs in as a member of provisioning_intake and of provisioning_build; the
-- operator CLI logs in as a member of provisioning_operator. No process uses provisioning_payment_intake.
-- Run once per PostgreSQL cluster by an administrator (roles are cluster-wide, so
-- this is not a migration). Idempotent. It sets no passwords and drops nothing.
-- Login roles for operators and the service are created separately and made
-- members of these groups, one group per binary. The privileges of each role come
-- from the migrations.
--
-- Two runs at the same moment can both find a role missing. The loser then gets
-- duplicate_object or, when it loses inside the catalog insert, unique_violation;
-- both mean the role now exists.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_migrator') THEN
        CREATE ROLE provisioning_migrator NOLOGIN;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN
    NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_api') THEN
        CREATE ROLE provisioning_api NOLOGIN;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN
    NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_worker') THEN
        CREATE ROLE provisioning_worker NOLOGIN;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN
    NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_intake') THEN
        CREATE ROLE provisioning_intake NOLOGIN;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN
    NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_build') THEN
        CREATE ROLE provisioning_build NOLOGIN;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN
    NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_operator') THEN
        CREATE ROLE provisioning_operator NOLOGIN;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN
    NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_payment_intake') THEN
        CREATE ROLE provisioning_payment_intake NOLOGIN;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN
    NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_hosting_intake') THEN
        CREATE ROLE provisioning_hosting_intake NOLOGIN;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN
    NULL;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'provisioning_bootstrap') THEN
        CREATE ROLE provisioning_bootstrap NOLOGIN;
    END IF;
EXCEPTION WHEN duplicate_object OR unique_violation THEN
    NULL;
END
$$;
