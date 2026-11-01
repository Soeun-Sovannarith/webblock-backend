-- +goose Up
-- The four business tables of revision 16 (decision 0007), with the constraints and
-- the three trigger guards of decision 0004 section 9.
--
-- No column has a default: the application supplies every ID, timestamp, status and
-- counter, so a forgotten column fails instead of becoming 'accepted' or 0.
-- Every constraint is named, because tests and the error mapper match on names.
-- Every foreign key is ON DELETE RESTRICT: release history is never cascaded.
--
-- What the database does not enforce, on purpose: which status may follow which,
-- that active_release_id names the live release, how route_state and hosting_state
-- move, the fence itself, and the older-release rule. Those live in Go.

CREATE TABLE provisioning.releases (
    release_id                   uuid                        NOT NULL,
    project_id                   uuid                        NOT NULL,
    source_generation_request_id uuid                        NOT NULL,
    source_repository_id         bigint                      NOT NULL,
    source_commit_sha            varchar(64)                 NOT NULL,
    image_digest                 varchar(71),
    status                       provisioning.release_status NOT NULL,
    failure_code                 varchar(100),
    failure_summary              text,
    created_at                   timestamptz                 NOT NULL,
    updated_at                   timestamptz                 NOT NULL,
    promoted_at                  timestamptz,
    CONSTRAINT releases_pkey PRIMARY KEY (release_id),
    -- Target of the same-project composite foreign keys.
    CONSTRAINT releases_project_release_key UNIQUE (project_id, release_id),
    -- One release per Project generation request: a repeated publication event replays it.
    CONSTRAINT releases_project_request_key UNIQUE (project_id, source_generation_request_id),
    CONSTRAINT releases_source_repository_id_check CHECK (source_repository_id > 0),
    -- SHA-1 (40) or SHA-256 (64) in lowercase hexadecimal. ^ and $ match only at the
    -- ends of the whole value, so a trailing line feed does not pass.
    CONSTRAINT releases_source_commit_sha_check
        CHECK (source_commit_sha ~ '^[0-9a-f]{40}([0-9a-f]{24})?$'),
    -- The bare digest: no registry, repository or tag.
    CONSTRAINT releases_image_digest_check
        CHECK (image_digest ~ '^sha256:[0-9a-f]{64}$'),
    -- A digest from 'built' onward. A release may fail before or after its image exists.
    CONSTRAINT releases_digest_after_build
        CHECK (status IN ('accepted', 'building', 'failed') OR image_digest IS NOT NULL),
    CONSTRAINT releases_no_digest_before_built
        CHECK (status NOT IN ('accepted', 'building') OR image_digest IS NULL),
    -- promoted_at exactly for a release that was confirmed live. 'rolled_back' never was.
    CONSTRAINT releases_promoted_at_matches_status
        CHECK ((status IN ('live', 'superseded')) = (promoted_at IS NOT NULL)),
    CONSTRAINT releases_failure_code_matches_status
        CHECK ((status IN ('failed', 'rolled_back')) = (failure_code IS NOT NULL))
);

-- Partial unique indexes cannot be deferred. Going live is therefore two statements
-- in a fixed order: the old release to 'superseded', then the new one to 'live'.
CREATE UNIQUE INDEX releases_one_live_per_project ON provisioning.releases (project_id)
    WHERE status = 'live';

-- The route moves for one release at a time. Unfinished releases are otherwise not limited.
CREATE UNIQUE INDEX releases_one_promoting_per_project ON provisioning.releases (project_id)
    WHERE status = 'promoting';

CREATE TABLE provisioning.project_release_state (
    project_id           uuid                       NOT NULL,
    active_release_id    uuid,
    operation_generation bigint                     NOT NULL,
    route_state          provisioning.route_state   NOT NULL,
    hosting_state        provisioning.hosting_state NOT NULL,
    updated_at           timestamptz                NOT NULL,
    CONSTRAINT project_release_state_pkey PRIMARY KEY (project_id),
    -- 0 means that no command was ever accepted. The upper bound 2^53-1 is enforced in Go.
    CONSTRAINT project_release_state_operation_generation_check CHECK (operation_generation >= 0),
    -- MATCH SIMPLE (the default): a null active_release_id is not checked.
    CONSTRAINT project_release_state_active_fk FOREIGN KEY (project_id, active_release_id)
        REFERENCES provisioning.releases (project_id, release_id) ON DELETE RESTRICT
);

CREATE TABLE provisioning.release_steps (
    release_step_id uuid                             NOT NULL,
    release_id      uuid                             NOT NULL,
    step_name       provisioning.release_step_name   NOT NULL,
    attempt         integer                          NOT NULL,
    status          provisioning.release_step_status NOT NULL,
    started_at      timestamptz,
    finished_at     timestamptz,
    deadline_at     timestamptz,
    reason_code     varchar(100),
    details_ref     varchar(500),
    CONSTRAINT release_steps_pkey PRIMARY KEY (release_step_id),
    CONSTRAINT release_steps_release_fk FOREIGN KEY (release_id)
        REFERENCES provisioning.releases (release_id) ON DELETE RESTRICT,
    -- Two workers cannot both record the same attempt.
    CONSTRAINT release_steps_attempt_key UNIQUE (release_id, step_name, attempt),
    CONSTRAINT release_steps_attempt_check CHECK (attempt > 0),
    -- A finished step has a start. A step that fails before it starts records both as
    -- the same instant (decision 0001 section 8).
    CONSTRAINT release_steps_finish_after_start
        CHECK (finished_at IS NULL OR (started_at IS NOT NULL AND finished_at >= started_at)),
    -- Otherwise the sweeper, which looks for running steps past their deadline, never finds it.
    CONSTRAINT release_steps_running_has_deadline
        CHECK (status <> 'running' OR deadline_at IS NOT NULL)
);

CREATE INDEX release_steps_running_deadline ON provisioning.release_steps (deadline_at)
    WHERE status = 'running';

CREATE TABLE provisioning.commands (
    command_id         uuid                        NOT NULL,
    project_id         uuid                        NOT NULL,
    release_id         uuid,
    kind               provisioning.command_kind   NOT NULL,
    idempotency_key    varchar(255)                NOT NULL,
    request_hash       char(64)                    NOT NULL,
    fencing_generation bigint                      NOT NULL,
    requested_by       varchar(255),
    status             provisioning.command_status NOT NULL,
    failure_code       varchar(100),
    created_at         timestamptz                 NOT NULL,
    updated_at         timestamptz                 NOT NULL,
    CONSTRAINT commands_pkey PRIMARY KEY (command_id),
    CONSTRAINT commands_fencing_key UNIQUE (project_id, fencing_generation),
    -- MATCH SIMPLE (the default): a hosting command's null release_id is not checked.
    CONSTRAINT commands_release_fk FOREIGN KEY (project_id, release_id)
        REFERENCES provisioning.releases (project_id, release_id) ON DELETE RESTRICT,
    -- Hosting commands name no release; every other kind names one.
    CONSTRAINT commands_release_by_kind
        CHECK ((release_id IS NULL) = (kind IN ('suspend_hosting', 'resume_hosting'))),
    -- char(64) pads a shorter value with spaces, which the pattern refuses.
    CONSTRAINT commands_request_hash_check CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    -- The counter's value after the acceptance that created the command, so never 0.
    CONSTRAINT commands_fencing_generation_check CHECK (fencing_generation > 0),
    CONSTRAINT commands_failure_code_matches_status
        CHECK ((status IN ('failed', 'skipped')) = (failure_code IS NOT NULL)),
    -- Who issued the command: the subject of the caller's verified token. A deploy,
    -- promote or rollback has one and it is not empty; a hosting command comes from a
    -- Payment event and has none. The character rules are the domain's.
    CONSTRAINT commands_requested_by_by_kind
        CHECK (((requested_by IS NULL) = (kind IN ('suspend_hosting', 'resume_hosting')))
               AND (requested_by IS NULL OR requested_by <> ''))
);

-- One command in flight per project.
CREATE UNIQUE INDEX commands_one_in_flight ON provisioning.commands (project_id)
    WHERE status IN ('accepted', 'running');

-- Keys are unique per project in two separate classes, so a client's key and a Payment
-- event key can never meet: a tenant who guesses a future event key and sends it as
-- an Idempotency-Key cannot make the later suspend_hosting collide with it.
CREATE UNIQUE INDEX commands_idempotency_key ON provisioning.commands (project_id, idempotency_key)
    WHERE kind IN ('deploy', 'promote', 'rollback');

CREATE UNIQUE INDEX commands_hosting_event_key ON provisioning.commands (project_id, idempotency_key)
    WHERE kind IN ('suspend_hosting', 'resume_hosting');

-- Trigger guards (decision 0004 section 9). They hold invariants that never change,
-- for every role including the owner. Each raises SQLSTATE 23514 (check_violation)
-- with its own name as the constraint name, so a caller handles it like a check
-- constraint. The messages name the rule and carry no row values.
--
-- Every guard function runs with the caller's rights (never SECURITY DEFINER), sets
-- search_path to pg_catalog and names every object with its schema, so the caller's
-- search_path cannot redirect anything inside it. It is owned by the migrator, and the
-- default EXECUTE of PUBLIC is revoked: a trigger fires whatever EXECUTE its caller
-- holds, so no role needs it.
--
-- The guards are plain row triggers, not "UPDATE OF" triggers: a column-specific
-- trigger does not fire when another BEFORE trigger changes the column, and these
-- must hold for every update.
--
-- What they do not stop: the owner can disable a trigger and a superuser can bypass
-- one. They limit bugs and a compromised application role, not the administrator.

-- releases: the identity of a release never changes, and a recorded digest is final.
-- +goose StatementBegin
CREATE FUNCTION provisioning.releases_identity_immutable() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.source_generation_request_id IS DISTINCT FROM OLD.source_generation_request_id
       OR NEW.source_repository_id IS DISTINCT FROM OLD.source_repository_id
       OR NEW.source_commit_sha IS DISTINCT FROM OLD.source_commit_sha
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR (OLD.image_digest IS NOT NULL AND NEW.image_digest IS DISTINCT FROM OLD.image_digest) THEN
        RAISE EXCEPTION 'update of relation "releases" violates guard "releases_identity_immutable"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'releases',
                  CONSTRAINT = 'releases_identity_immutable',
                  DETAIL = 'release_id, project_id, source_generation_request_id, source_repository_id, source_commit_sha and created_at never change; image_digest changes only from null to a value.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.releases_identity_immutable() FROM PUBLIC;

CREATE TRIGGER releases_identity_immutable
    BEFORE UPDATE ON provisioning.releases
    FOR EACH ROW EXECUTE FUNCTION provisioning.releases_identity_immutable();

-- releases: superseded, failed and rolled_back are final. Without this a faulty worker
-- could turn a failed release back into built.
-- +goose StatementBegin
CREATE FUNCTION provisioning.releases_terminal_status_final() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF OLD.status IN ('superseded'::provisioning.release_status,
                      'failed'::provisioning.release_status,
                      'rolled_back'::provisioning.release_status)
       AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'update of relation "releases" violates guard "releases_terminal_status_final"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'releases',
                  CONSTRAINT = 'releases_terminal_status_final',
                  DETAIL = 'The status of a superseded, failed or rolled_back release never changes.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.releases_terminal_status_final() FROM PUBLIC;

CREATE TRIGGER releases_terminal_status_final
    BEFORE UPDATE ON provisioning.releases
    FOR EACH ROW EXECUTE FUNCTION provisioning.releases_terminal_status_final();

-- commands: the identity of a command, including who requested it, never changes.
-- +goose StatementBegin
CREATE FUNCTION provisioning.commands_identity_immutable() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NEW.command_id IS DISTINCT FROM OLD.command_id
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.release_id IS DISTINCT FROM OLD.release_id
       OR NEW.kind IS DISTINCT FROM OLD.kind
       OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
       OR NEW.request_hash IS DISTINCT FROM OLD.request_hash
       OR NEW.fencing_generation IS DISTINCT FROM OLD.fencing_generation
       OR NEW.requested_by IS DISTINCT FROM OLD.requested_by
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'update of relation "commands" violates guard "commands_identity_immutable"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_identity_immutable',
                  DETAIL = 'command_id, project_id, release_id, kind, idempotency_key, request_hash, fencing_generation, requested_by and created_at never change.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_identity_immutable() FROM PUBLIC;

CREATE TRIGGER commands_identity_immutable
    BEFORE UPDATE ON provisioning.commands
    FOR EACH ROW EXECUTE FUNCTION provisioning.commands_identity_immutable();

-- commands: succeeded, failed and skipped are final.
-- +goose StatementBegin
CREATE FUNCTION provisioning.commands_terminal_status_final() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF OLD.status IN ('succeeded'::provisioning.command_status,
                      'failed'::provisioning.command_status,
                      'skipped'::provisioning.command_status)
       AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'update of relation "commands" violates guard "commands_terminal_status_final"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_terminal_status_final',
                  DETAIL = 'The status of a succeeded, failed or skipped command never changes.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_terminal_status_final() FROM PUBLIC;

CREATE TRIGGER commands_terminal_status_final
    BEFORE UPDATE ON provisioning.commands
    FOR EACH ROW EXECUTE FUNCTION provisioning.commands_terminal_status_final();

-- commands: a command is inserted as accepted, without a failure code, and with the
-- project's current operation_generation as its fencing generation. So the project
-- has a row, and acceptance advances the counter before it inserts, in the same
-- transaction; the guard then reads the row that transaction updated and holds
-- locked. The read needs SELECT on project_release_state, which every role that may
-- insert a command has. A null in a NOT NULL column is left to that column's own
-- constraint, so a forgotten column is still reported as such.
-- +goose StatementBegin
CREATE FUNCTION provisioning.commands_insert_shape() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    counter bigint;
    refused boolean := false;
BEGIN
    IF NEW.status <> 'accepted'::provisioning.command_status OR NEW.failure_code IS NOT NULL THEN
        refused := true;
    ELSIF NEW.project_id IS NOT NULL AND NEW.fencing_generation IS NOT NULL THEN
        SELECT s.operation_generation
          INTO counter
          FROM provisioning.project_release_state AS s
         WHERE s.project_id = NEW.project_id;
        refused := counter IS DISTINCT FROM NEW.fencing_generation;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'insert into relation "commands" violates guard "commands_insert_shape"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'commands',
                  CONSTRAINT = 'commands_insert_shape',
                  DETAIL = 'A command is inserted as accepted, without a failure code, and with fencing_generation equal to the current operation_generation of its project.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.commands_insert_shape() FROM PUBLIC;

CREATE TRIGGER commands_insert_shape
    BEFORE INSERT ON provisioning.commands
    FOR EACH ROW EXECUTE FUNCTION provisioning.commands_insert_shape();

-- project_release_state: project_id never changes, and the counter stays the same or
-- increases by exactly one. The three tests run one after another so that the
-- subtraction is reached only when NEW is above OLD, and OLD is never negative: it
-- cannot overflow, also at the bigint maximum.
-- +goose StatementBegin
CREATE FUNCTION provisioning.project_release_state_generation_step() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
    refused boolean := false;
BEGIN
    IF NEW.project_id IS DISTINCT FROM OLD.project_id THEN
        refused := true;
    ELSIF NEW.operation_generation < OLD.operation_generation THEN
        refused := true;
    ELSIF NEW.operation_generation > OLD.operation_generation THEN
        refused := NEW.operation_generation - OLD.operation_generation > 1;
    END IF;
    IF refused THEN
        RAISE EXCEPTION 'update of relation "project_release_state" violates guard "project_release_state_generation_step"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'project_release_state',
                  CONSTRAINT = 'project_release_state_generation_step',
                  DETAIL = 'project_id never changes; operation_generation stays the same or increases by exactly one.';
    END IF;
    RETURN NEW;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.project_release_state_generation_step() FROM PUBLIC;

CREATE TRIGGER project_release_state_generation_step
    BEFORE UPDATE ON provisioning.project_release_state
    FOR EACH ROW EXECUTE FUNCTION provisioning.project_release_state_generation_step();

-- project_release_state: the counter cannot move without a command. This is a
-- constraint trigger deferred to commit, because acceptance advances the counter
-- first and inserts the command second: when the transaction commits, a command of
-- the project carries the new value as its fencing generation. Without it a faulty
-- or compromised API process could move the counter alone, and the project's running
-- command could never pass its fence again. The read needs SELECT on commands, which
-- every role that may advance the counter has.
-- +goose StatementBegin
CREATE FUNCTION provisioning.project_release_state_generation_has_command() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM provisioning.commands AS c
         WHERE c.project_id = NEW.project_id
           AND c.fencing_generation = NEW.operation_generation
    ) THEN
        RAISE EXCEPTION 'update of relation "project_release_state" violates guard "project_release_state_generation_has_command"'
            USING ERRCODE = '23514',
                  SCHEMA = 'provisioning',
                  TABLE = 'project_release_state',
                  CONSTRAINT = 'project_release_state_generation_has_command',
                  DETAIL = 'operation_generation changed, and no command of the project carries the new value as its fencing_generation.';
    END IF;
    RETURN NULL;
END
$$;
-- +goose StatementEnd

REVOKE ALL ON FUNCTION provisioning.project_release_state_generation_has_command() FROM PUBLIC;

CREATE CONSTRAINT TRIGGER project_release_state_generation_has_command
    AFTER UPDATE ON provisioning.project_release_state
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW
    WHEN (OLD.operation_generation IS DISTINCT FROM NEW.operation_generation)
    EXECUTE FUNCTION provisioning.project_release_state_generation_has_command();
