-- +goose Up
-- The work row of decision 0005 section 2: one row per accepted command, in the separate
-- operational schema provisioning_ops. It is not a fifth business table. The acceptance
-- transaction inserts it (decision 0004 section 4 step 7); the dispatcher and the worker
-- update it. It carries the dispatch schedule and the worker lease.
--
-- Forward-only and immutable, like every migration: fix a mistake with a new one.
--
-- No column has a default: the application supplies every value at insert. Lease and
-- dispatch arithmetic on update uses the database clock (clock_timestamp()), so skew
-- between workers cannot stretch a lease (decision 0005 section 2).
--
-- The row holds no copy of the generation: commands.fencing_generation is the single
-- source.

-- Ownership is the privilege boundary, so refuse to run as anyone but the migrator.
-- +goose StatementBegin
DO $$
BEGIN
    IF current_user <> 'provisioning_migrator' THEN
        RAISE EXCEPTION 'migrations must run as role provisioning_migrator (current role: %); see docs/runbooks/phase-1-database.md', current_user
            USING ERRCODE = '42501';
    END IF;
END
$$;
-- +goose StatementEnd

CREATE SCHEMA provisioning_ops;

CREATE TABLE provisioning_ops.command_work (
    command_id        uuid         NOT NULL,
    -- Carried into every job message for the command.
    correlation_id    varchar(128) NOT NULL,
    -- Latest time the command may still be worked.
    deadline_at       timestamptz  NOT NULL,
    -- When the dispatcher may publish again. A claim moves it to the lease expiry.
    next_dispatch_at  timestamptz  NOT NULL,
    publish_attempts  integer      NOT NULL,
    last_confirmed_at timestamptz,
    -- Execution attempts: only a successful claim increments it.
    claim_count       integer      NOT NULL,
    -- Fencing token: incremented on every claim; every worker write compares it.
    lease_token       bigint       NOT NULL,
    claimed_by        varchar(100),
    lease_expires_at  timestamptz,
    -- Earliest time a claim may follow a transient failure.
    retry_not_before  timestamptz,
    last_error_code   varchar(100),
    -- Set in the transaction that gives the command its terminal status, so that the
    -- dispatcher's partial index holds only unfinished commands.
    finished_at       timestamptz,
    created_at        timestamptz  NOT NULL,
    updated_at        timestamptz  NOT NULL,
    CONSTRAINT command_work_pkey PRIMARY KEY (command_id),
    -- The table has the same cardinality as commands: nobody deletes either.
    CONSTRAINT command_work_command_fk FOREIGN KEY (command_id)
        REFERENCES provisioning.commands (command_id) ON DELETE RESTRICT,
    CONSTRAINT command_work_publish_attempts_check CHECK (publish_attempts >= 0),
    CONSTRAINT command_work_claim_count_check CHECK (claim_count >= 0),
    CONSTRAINT command_work_lease_token_check CHECK (lease_token >= 0),
    -- The lease owner and its expiry are set or cleared together.
    CONSTRAINT command_work_lease_owner_with_expiry CHECK ((claimed_by IS NULL) = (lease_expires_at IS NULL)),
    CONSTRAINT command_work_claimed_by_check CHECK (claimed_by <> ''),
    CONSTRAINT command_work_correlation_id_check CHECK (correlation_id <> ''),
    -- The domain.ValidateCode format. ^ and $ match only at the ends of the whole value.
    CONSTRAINT command_work_last_error_code_check CHECK (last_error_code ~ '^[a-z][a-z0-9_]{0,99}$')
);

-- The dispatcher's scan (decision 0005 section 3 step 1): due rows of unfinished commands,
-- oldest first. "Unfinished" lives on commands, which a partial index on this table cannot
-- see, hence finished_at. The index size follows the number of unfinished commands, not
-- the size of the table. The heartbeat writes only lease_expires_at and updated_at, which
-- no index covers, so it stays a heap-only update: do not index them.
CREATE INDEX command_work_due ON provisioning_ops.command_work (next_dispatch_at)
    WHERE finished_at IS NULL;

-- Insert guard (security review H2 item 3). A work row is inserted fresh: token and
-- counters at zero, no lease, no confirm, no retry time, no error, not finished, and for a
-- command that is accepted. So the API role, which may insert, cannot hand a worker a row
-- with a lease already set or a finished command. A null in a NOT NULL column is left to
-- that column's own constraint and a missing command to the foreign key, so each is still
-- reported as such.
--
-- Hygiene as for the baseline guards (decision 0004 section 9): caller's rights, a fixed
-- search_path, every object named with its schema, owned by the migrator, no EXECUTE for
-- PUBLIC. The read of commands needs SELECT on it, which every role that may insert a
-- work row has.
-- +goose StatementBegin
CREATE FUNCTION provisioning_ops.command_work_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    command_status provisioning.command_status;
    refused boolean := false;
BEGIN
    IF NEW.publish_attempts <> 0 OR NEW.claim_count <> 0 OR NEW.lease_token <> 0
       OR NEW.claimed_by IS NOT NULL OR NEW.lease_expires_at IS NOT NULL
       OR NEW.last_confirmed_at IS NOT NULL OR NEW.retry_not_before IS NOT NULL
       OR NEW.last_error_code IS NOT NULL OR NEW.finished_at IS NOT NULL THEN
        refused := true;
    ELSIF NEW.command_id IS NOT NULL THEN
        SELECT c.status
          INTO command_status
          FROM provisioning.commands AS c
         WHERE c.command_id = NEW.command_id;
        refused := FOUND AND command_status <> 'accepted'::provisioning.command_status;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "command_work" violates guard "command_work_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning_ops',
                  TABLE = 'command_work',
                  CONSTRAINT = 'command_work_insert_shape',
                  DETAIL = 'A work row is inserted with token and counters at zero, without a lease, confirm, retry time, error or finish, for an accepted command.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning_ops.command_work_insert_shape() FROM PUBLIC;

CREATE TRIGGER command_work_insert_shape
    BEFORE INSERT ON provisioning_ops.command_work
    FOR EACH ROW EXECUTE FUNCTION provisioning_ops.command_work_insert_shape();

-- Grants (decision 0005 section 2). Nothing for PUBLIC; nobody may DELETE or TRUNCATE.
-- provisioning_api inserts the row in the acceptance transaction and reads nothing here.
-- provisioning_worker reads and updates it: the dispatcher's take and records, the claim,
-- the heartbeat, the release for retry and the fenced finish. Its UPDATE covers every
-- column except the row's identity (command_id, correlation_id, deadline_at, created_at),
-- which no worker path writes; the UPDATE privilege also makes its row locks legal.
GRANT USAGE ON SCHEMA provisioning_ops TO provisioning_api, provisioning_worker;
GRANT INSERT ON provisioning_ops.command_work TO provisioning_api;
GRANT SELECT ON provisioning_ops.command_work TO provisioning_worker;
GRANT UPDATE (next_dispatch_at, publish_attempts, last_confirmed_at, claim_count, lease_token, claimed_by,
              lease_expires_at, retry_not_before, last_error_code, finished_at, updated_at)
    ON provisioning_ops.command_work TO provisioning_worker;
