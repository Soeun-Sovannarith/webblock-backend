# Phase 1 database runbook

Scope: the `provisioning` schema, its goose migrations, and the integration tests that prove them against PostgreSQL 18.
This is local evidence only. It is not a production storage, backup, or recovery plan.

The current tables, roles and grants are in Notion "5 · Provisioning"
(https://app.notion.com/p/40058175966e81769596d1ebcadb333d) and in the migrations. The integration suite is
`make py-test-integration` (section "Run the integration tests" below).

## Rules for migrations

- Migrations in `src/migrations/` are immutable and forward-only. Never edit an applied file and never write a
  `-- +goose Down` section that drops data. Correct mistakes with a new numbered migration.
- One recorded exception: `00001` to `00003` were rewritten once, in Phase 1R, to the revision 16 baseline (decision
  `0007` section 3). The exception ends when Phase 1R is merged. See "A database migrated before the rewrite" below.
- Migrations run as the role `provisioning_migrator`. `00001` raises SQLSTATE 42501 if the current role is anyone else,
  so a superuser run cannot create objects the migrator does not own. `00003` fails with SQLSTATE 42704 if
  `provisioning_api` or `provisioning_worker` does not exist, `00008` likewise if `provisioning_intake` or
  `provisioning_build` does not exist, and `00010` if `provisioning_operator` or `provisioning_payment_intake` does not
  exist; the message names every missing role and the failing migration grants nothing. A database created before Phase
  3, or before `00010`, therefore needs the bootstrap script run again (it is idempotent) before `make migrate-up`.
- `00010` adds `attempt_budget` to the inbox and sets it to `GREATEST(attempts, 1)` on existing rows, so those rows take
  no further attempt beyond what they used until an operator re-drives them. It also validates the 2^53−1 bounds against
  existing rows: a row above them (only an administrator could have written one) makes it fail and change nothing.
- `00008` builds the unique index `release_steps_one_active_attempt`. On a database whose `release_steps` already hold
  two pending or running attempts of one release and step name (only an administrator could have written such rows), it
  fails and changes nothing.
- If a run as the wrong role is refused, goose may already have created `goose_db_version` under that role. Drop that
  bookkeeping table (or the throwaway database) before retrying; do not drop it in a database that holds real data
  without review.

## A database migrated before the rewrite

goose records version numbers, not file content. A database that applied versions 1 to 3 before the rewrite reports
nothing pending, raises no error, and **keeps the earlier schema** (five tables, `frozen` and `generating`, grants to
`provisioning_app`). The service and the tests cannot use it. Nothing was ever deployed and the earlier rows cannot be
mapped forward, so the only path is to drop the database and create it again.

To tell which baseline a database holds (the earlier one has the table `tenant_resources`; `t` means it must be
recreated):

    docker exec -i <container> psql -U "$PG_USER" -d <database> -Atc "SELECT to_regclass('provisioning.tenant_resources') IS NOT NULL"

To recreate it, on a developer machine only, and never on a database that holds data anyone needs:

    docker exec -i <container> psql -U "$PG_USER" -d postgres -c 'DROP DATABASE <database> WITH (FORCE)'
    docker exec -i <container> psql -U "$PG_USER" -d postgres < scripts/db/bootstrap-roles.sql
    docker exec -i <container> psql -U "$PG_USER" -d postgres -c 'CREATE DATABASE <database> OWNER provisioning_migrator'
    PG_DATABASE=<database> make migrate-up

`<container>` is the container that runs PostgreSQL 18 and `<database>` the database name; the commands carry no
password, because `psql` inside the container connects over the local socket. Databases named `prov_test_*` need none of
this: the tests create them fresh on every run.

The role `provisioning_app` is no longer created, granted anything, or used. On a server that already has it, it stays
until the owner asks for it to be dropped: no script and no test drops it. `DROP ROLE provisioning_app` fails while any
database still holds grants for it, which is the case for every database migrated before the rewrite.

**Upgrade tests with old rows.** `tests/integration/test_schema_00011.py` upgrades a database at `00010` holding
representative rows of every table, and each later `test_schema_000*.py` file tests its own migration with old
rows.

## Prerequisites

- PostgreSQL 18, and Docker. `make py-test-integration` always needs Docker: it starts its own server (see "Run the
  integration tests"). The migrate targets do not need Docker.
- An SQL client for the manual commands below: `psql` on the host, or, on a host without it, `docker exec` into the
  container that runs the server. Replace `psql -h localhost -U "$PG_USER" -d postgres` with
  `docker exec -i <container> psql -U "$PG_USER" -d postgres` (and `-f file` with `< file`). The tests and
  `make migrate-*` need no SQL client.
- `make py-tools` installs the pinned goose v3.28.0 (with uv, osv-scanner and grype) into `./bin`.
- `.env` (git-ignored) defines `PG_USER` and `PG_PASS`, an administrator. Optional: `PG_HOST` (default `localhost`),
  `PG_PORT` (default `5432`), and `PG_DATABASE` for the migrate targets.

### How `.env` is read

Make never reads `.env`. Only `migrate-up` and `migrate-status` load it, and they do so inside the recipe (`load_env` in
the Makefile), so no other target (the `py-*` and `proto-*` targets included) has `PG_USER` or `PG_PASS` in its
environment. Values in the file replace variables of the same name already in the environment. A missing `.env` is not
an error.

The file is read as data, line by line. Nothing in it is executed or expanded: it is not sourced. The accepted lines are
a subset of shell syntax, so a line means the same here as it would to a shell.

- Write `NAME=value`, one per line. Blank lines and lines that start with `#` are ignored. An `export ` prefix is
  accepted. Spaces and tabs at either end of a line are dropped.
- Unquoted, a value may hold only letters, digits and `_ . , : / @ % + = -`. Wrap anything else in single quotes, for
  example `PG_PASS='ab#cd$ef gh'`; everything between single quotes is kept as written. A single quote inside the value
  is written `'\''`.
- Double quotes are accepted for a value without `$`, `` ` `` or `\`, for example `"two
  words"`. With one of those three characters, use single quotes.
- Any other line stops the target before anything runs, with a non-zero exit and one message on standard error that
  gives the file and the line number and never any part of the line: `.env: line 2: the value needs single quotes or has
  an unclosed quote; nothing was run (see .claude/docs/runbooks/phase-1-database.md, How .env is read)`. The reason is
  `expected NAME=value` for a line without `=` or a name that is not a shell identifier, and
  `a double-quoted value holds a character that needs single quotes` for `$`, `` ` `` or `\` between double quotes.
- Three forms that a shell would take are refused this way on purpose:
  - a comment after a value, such as `PG_HOST=localhost # local`. Put the comment on its own line.
  - an unquoted `~`, such as `PG_PASS=~abc`. A shell could expand it; write `'~abc'`.
  - Windows line endings (CRLF). A shell would keep the carriage return as the last character of the value. Save the
    file with LF endings.
- Also refused unquoted: a space, `#`, `$`, `` ` ``, `\`, `;`, `&`, `|`, `<`, `>`, `(`, `)`, `*`, `?`, `!`, and an
  unclosed quote. A `.env` that exists but is not a readable regular file stops the target with `.env: not a readable
  regular file`.
- The file holds credentials: keep it owner-readable only.
- `PG_DATABASE` must match `^[a-z_][a-z0-9_]{0,62}$`, `PG_HOST` `^[A-Za-z0-9._-]{1,253}$` and `PG_PORT` one to five
  digits; the migrate targets refuse anything else because these three are pasted into the connection string. `PG_USER`
  and `PG_PASS` reach goose through `PGUSER` and `PGPASSWORD`, so they need no escaping beyond the quoting above.

## Bootstrap the roles (once per cluster, as an administrator)

Roles are cluster-wide, so this is not a migration. The script is idempotent, creates the seven `NOLOGIN` group roles
`provisioning_migrator`, `provisioning_api`, `provisioning_worker`, `provisioning_intake`, `provisioning_build`,
`provisioning_operator` and `provisioning_payment_intake`, sets no passwords, and drops nothing:

    psql -h localhost -U "$PG_USER" -d postgres -f scripts/db/bootstrap-roles.sql

Create the database owned by the migrator, then migrate:

    psql -h localhost -U "$PG_USER" -d postgres -c 'CREATE DATABASE provisioning OWNER provisioning_migrator'
    PG_DATABASE=provisioning make migrate-up
    PG_DATABASE=provisioning make migrate-status

`make migrate-up` connects as `PG_USER` with `options=-c role=provisioning_migrator`, so every statement runs with that
current role. A superuser (as locally) or any login role that is a member of `provisioning_migrator` may do this. For a
real deployment, make a dedicated migration login role a member of the migrator, the API's login role a member of
`provisioning_api` only, and the worker's login role a member of `provisioning_worker` only; give neither service the
migrator role or the other service's role. Role names are fixed in the migrations, so an environment attaches its own
login roles by membership. Password handling for those roles is outside this phase.

**One login role per process.** Each process connects with logins that are members of one group role each, and of
nothing else:

| Process | Group role of each login |
| --- | --- |
| `api` | `provisioning_api` |
| `worker` | `provisioning_worker` (jobs, fenced writes, usage outbox) and `provisioning_hosting_intake` (hosting commands from events) |
| `build_worker` | `provisioning_intake` (the "source published" inbox) and `provisioning_build` (build attempts) |
| `bootstrap` | `provisioning_bootstrap` (read-only) |
| `operator` CLI | `provisioning_operator` |

`provisioning_payment_intake` has no member and holds no privilege (`00018` checks it). Never create a login that is
a member of two roles, and never grant one role to another or give a login the right to `SET ROLE` to another:
either would unite two sets of grants in one session. The guard `releases_build_transition` treats any session that
is a member of `provisioning_build` as the build process, so a login that is also a member of `provisioning_worker`
would lose the worker's release writes. Tests show that a member of one role cannot `SET ROLE` to another, to the
migrator, or to a superuser. To check a server:

    docker exec -i <container> psql -U "$PG_USER" -d postgres -Atc "SELECT m.rolname, string_agg(r.rolname, ',') FROM pg_auth_members a JOIN pg_roles m ON m.oid = a.member JOIN pg_roles r ON r.oid = a.roleid WHERE r.rolname LIKE 'provisioning\_%' GROUP BY 1 HAVING count(*) > 1"

It prints nothing when no login is a member of more than one group role.

Grants and guards (`.claude/rules/migrations.md`). The migrator owns the schemas, the tables, the types and the
guard functions. Each migration lists its grants; `tests/integration/test_schema_000*.py` pins them.

- **What the guards do not stop.** They limit bugs and a compromised service role, not the database administrator: the
  schema owner can disable or drop a trigger, and a superuser can also set `session_replication_role = replica`, which
  skips them. Neither service role can do either. They also do not check that a well-formed command meets its
  preconditions, nor any status change between statuses that are not final; those rules are in the application.
- The guard functions run with the caller's rights, set `search_path` to `pg_catalog`, and are owned by the migrator.
  `EXECUTE` on them is revoked from PUBLIC and granted to no one; a trigger fires all the same. One exception, from
  `00018`: `provisioning.offline_reasons_distinct` is called by a `CHECK` on `project_hosting`, and a check runs its
  function with the rights of the role that writes the row, so `EXECUTE` on it is granted to the four writers of that
  table (`provisioning_api`, `provisioning_worker`, `provisioning_intake`, `provisioning_hosting_intake`).
- Nothing is granted to PUBLIC, which has no `USAGE` on the schema. Two PostgreSQL defaults remain: PUBLIC keeps `USAGE`
  on the enum types, which cannot be reached without `USAGE` on the schema, and every role keeps `CONNECT` and `TEMP` on
  the database, so it can create temporary tables. Nothing in this phase revokes those two; whether to do so is a
  deployment decision.

## Migration 00018 and the retired role

`00018` (decision `0021`, Notion "5 · Provisioning") adds the type `provisioning.offline_reason`, the columns
`project_hosting.offline_reasons` and `owner_status_version` and `commands.hosting_reason`, and renames
`github_repository_id` to `repository_id` in `releases` and `source_published_events`. It fills the new columns of
existing rows: an online site gets no reason; an offline site gets `project_deleted` when it has a `project-deleted`
suspend that succeeded or was skipped, else `billing`; each hosting command gets the reason of its key. It needs
`provisioning_api`, `provisioning_intake`, `provisioning_worker` and `provisioning_hosting_intake`.

New grants: the worker may update `offline_reasons`; the hosting intake may insert a `project_hosting` row and update
`owner_status_version`. The hosting intake inserts owner-status rows only (version 1 or more, online with no reason or
offline with `user_suspended`); every other role inserts version 0, online, no reason.
`tests/integration/test_schema_00018.py` upgrades a database at `00017` that holds representative rows and checks every
row is kept and filled as above.

Stop the API, the worker and both intakes before `make migrate-up` applies `00018`. It updates every row of
`project_hosting` and `commands` and alters both tables under `ACCESS EXCLUSIVE` locks, so a write from live traffic can
deadlock with it, and a deadlock rolls the whole migration back.

`00018` also checks that `provisioning_payment_intake` (unused since `00011`) holds no privilege, owns nothing and has
no role membership. If it does, the migration fails and changes nothing; revoke what it holds and run it again. After
`make migrate-up` has applied `00018`, an administrator drops the role once, connected to the Provisioning database:

    psql -h localhost -U "$PG_USER" -d provisioning -v ON_ERROR_STOP=1 -f scripts/db/drop-retired-roles.sql

The script refuses, and drops nothing, while that database is below `00018`. On a server with several Provisioning
databases, migrate each one first: `DROP ROLE` refuses while any database still depends on the role.
`bootstrap-roles.sql` still creates the role, because `00010` needs it on an empty server; run the drop script again
after `00018` there.

## Migrations 00019 to 00021 and the sub-domain backfill

Notion's next Provisioning migration is three files (decision `0021` D1). `00019` adds the enum values `admin_suspended`
and `redeploy`. `00020` adds the checks that use them and the nullable column `project_hosting.subdomain`. `00021` makes
that column `NOT NULL`. Goose runs each file in its own transaction.

`00020` refuses, and changes nothing, when the database already holds a hosting command whose key starts with `admin:`
or any `redeploy` command (the code cannot write either before `00020`). `00021` refuses, and changes nothing, while any
hosting row has no sub-domain. So for a database with rows from before `00020`:

1. Run `make migrate-up`. It applies `00019` and `00020`, then stops at `00021` with
   `N hosting row(s) have no subdomain` (SQLSTATE 23502). The database is at `00020`.
2. Run `operator subdomain missing`, then `operator subdomain set --project P --subdomain S --by NAME` for each project
   it lists, with the value Project holds (`.claude/docs/runbooks/operator.md` section 7).
3. Run `make migrate-up` again. `00021` applies and takes back every grant `00020` gave `provisioning_operator` for the
   backfill, so the operator holds exactly its rights at `00019` (the inbox manual retry).

A new database, or one without hosting rows, migrates in one run. `tests/integration/test_schema_00020.py` and
`test_schema_00021.py` prove these steps.

## Migration 00022: the tables in Notion's order

`00022` makes the database exactly as Notion "5 · Provisioning" (decision `0023` G16). It rebuilds `project_hosting`,
`commands` and `source_published_events` in Notion's column order, puts `release_step_name` in Notion's order, and adds
four rules: a usage event's `completed_at` is its release's `promoted_at`; a sent usage event, a finished step and a
finished command never change; only a hosting command ends `skipped`. Every row, grant, key, index, check, foreign key
and trigger is kept.

1. Stop the API, the workers and the operator CLI. The file locks all seven tables, and a process that keeps a session
   open across it may hold cached plans for the old tables and type.
2. Run `make migrate-up`. It runs in one transaction; on any error nothing changes.
3. If it stops with `N usage event(s) have a completed_at that is not the promoted_at of their release` or
   `N command(s) are skipped but are not a hosting command` (SQLSTATE 23514), the database is still at `00021`. Neither
   row can come from the code. An administrator decides how to correct each row, then runs `make migrate-up` again.
4. Start the processes again.

The copy takes time in proportion to the rows of the three tables. `tests/integration/test_schema_00022.py` proves the
steps.

## Migration 00023: the bootstrap reads the sub-domain

`00023` is one grant (decision `0023` G18a): `provisioning_bootstrap` may read `project_hosting.subdomain`, so the
bootstrap can label each tenant namespace with its sub-domain for the route admission policy. Nothing else changes. Run
`make migrate-up` before the new bootstrap starts (it reads the column; the old one does not); the other processes may
keep running. On its first round the new bootstrap adds the label to the tenant namespaces that already exist.
`tests/integration/test_schema_00023.py` proves it.

## A sub-domain held by two projects (FR-54)

There is no unique index on `project_hosting (subdomain)`: the database matches Notion "5 · Provisioning" exactly
(migration `00024` was reverted). The code checks instead (TR-53): an
owner-status event that carries a taken sub-domain is dead-lettered at once (`SubdomainTaken`). The release intake
cannot read `subdomain`, so it may store such a row; the bootstrap then makes no tenant for either project and logs
at error `tenant refused: another project's hosting row holds its sub-domain (project_id=...)` every round.

To fix it:

1. List them: `SELECT subdomain, array_agg(project_id) FROM provisioning.project_hosting GROUP BY subdomain
   HAVING count(*) > 1;`
2. Ask the Project team which project owns each name, and the right sub-domain of every other project in the list.
   Check that each right value is free: `SELECT project_id FROM provisioning.project_hosting WHERE subdomain =
   '<right value>';` returns no row.
3. Correct each wrong row as a database superuser, one row per transaction:

       BEGIN;
       SET LOCAL session_replication_role = replica;
       UPDATE provisioning.project_hosting
          SET subdomain = '<right value>', updated_at = now()
        WHERE project_id = '<project id>' AND subdomain = '<wrong value>';
       -- expect UPDATE 1, else ROLLBACK
       COMMIT;

   `subdomain` is set once (the guard `project_hosting_reasons_guard` of `00020`), so a plain `UPDATE` is refused.
   `SET LOCAL` skips the triggers for this one transaction only and ends with it; the check on the column still
   applies. Never drop, disable or replace the guard, and never delete the row: it holds the project's command
   numbers, live release and owner status.
4. The bootstrap ensures both tenants again in its next round. The corrected project's site route keeps the old
   host until the worker points the route again (the next deploy, rollback, suspend or resume).

## Run the integration tests

    make py-tools               # once: bin/goose, which the suite migrates with
    make py-test-integration

The suite (`tests/integration`, harness `tests/integration/_pg.py` and `conftest.py`) starts one disposable
`postgres:18` container through Testcontainers per session and never connects to any other server; it reads no `.env`. A
missing Docker, image or server fails the run; it never skips (`tests/conftest.py`). The harness creates the roles of
`scripts/db/bootstrap-roles.sql` in that container, migrates a template database once per target version as
`provisioning_migrator` with `bin/goose`, and copies it for every test. Privilege and guard checks run through real
login roles that are members of exactly one group role, never with `SET ROLE`. Rows that the guards refuse on purpose
are seeded through the seeding path of decision `0008` section 5: a superuser session that runs
`SET LOCAL session_replication_role = replica` in its own transaction, which skips triggers, foreign keys included, for
that transaction only; check, `NOT NULL` and unique constraints still apply. When the session ends, a leftover check
fails the run if a database or role it created still exists.

Do not run it next to a Kind cluster or another Testcontainers suite (decision `0009` section 1; `mk/python.mk`).

## What the tests leave behind

Nothing on a shared server: the suite runs only against the container it started, and the container goes with
the session.
