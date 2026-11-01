"""Private helpers of the PostgreSQL 18 integration harness (prototype task B2, decision 0014).

Used through ``conftest.py`` by every database test. Nothing here reads ``.env`` or touches a server
other than the disposable ``postgres:18`` container that :func:`start_server` starts.

The pieces:

- :class:`Harness` owns one server. It creates the NOLOGIN roles of
  ``scripts/db/bootstrap-roles.sql``, migrates a template database once per target version as
  ``provisioning_migrator`` with ``bin/goose``, and copies it for every test
  (``CREATE DATABASE ... TEMPLATE``).
- :meth:`Harness.login` creates a LOGIN role that is a member of exactly one service role, so a
  guard that tests ``pg_has_role(current_user, ..., 'MEMBER')`` sees what it sees in production.
- :func:`seed` writes rows the guards refuse, in a superuser session with
  ``SET LOCAL session_replication_role = replica`` (decision 0008 section 5).
- :meth:`Harness.leftovers` lists every database and role this run created and that still exists.
"""

from __future__ import annotations

import secrets
import subprocess
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, LiteralString
from urllib.parse import quote

import psycopg
import pytest
from psycopg import conninfo, sql
from psycopg.rows import TupleRow
from testcontainers.core.config import testcontainers_config
from testcontainers.core.container import DockerContainer

REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = REPO_ROOT / "src" / "migrations"
BOOTSTRAP_ROLES = REPO_ROOT / "scripts" / "db" / "bootstrap-roles.sql"
GOOSE = REPO_ROOT / "bin" / "goose"

POSTGRES_IMAGE = "postgres:18"
RYUK_IMAGE = (
    "testcontainers/ryuk:0.14.0"  # pinned, as in the RabbitMQ suite; not the library default
)

ROLE_MIGRATOR = "provisioning_migrator"
ROLE_API = "provisioning_api"
ROLE_WORKER = "provisioning_worker"
ROLE_INTAKE = "provisioning_intake"
ROLE_BUILD = "provisioning_build"
ROLE_OPERATOR = "provisioning_operator"
ROLE_HOSTING_INTAKE = "provisioning_hosting_intake"
ROLE_BOOTSTRAP = "provisioning_bootstrap"
ROLE_PAYMENT_INTAKE = (
    "provisioning_payment_intake"  # replaced by hosting intake in 00011; still bootstrapped
)

OP_TIMEOUT_SECONDS = 20
START_TIMEOUT_SECONDS = 120

type Statement = str | tuple[str, Sequence[object]]
type Conn = psycopg.Connection[TupleRow]


def q(text: str) -> LiteralString:
    """Return ``text`` as the literal type psycopg demands.

    The tests build their SQL from fixed fragments and always pass values as parameters; this only
    satisfies the type, it does not make an arbitrary string safe.
    """
    return text


def rand_hex(nbytes: int = 6) -> str:
    return secrets.token_hex(nbytes)


@dataclass(frozen=True)
class Server:
    host: str
    port: int
    user: str
    password: str

    def conninfo(
        self, database: str, *, user: str | None = None, password: str | None = None
    ) -> str:
        return conninfo.make_conninfo(
            host=self.host,
            port=self.port,
            dbname=database,
            user=user or self.user,
            password=password if password is not None else self.password,
            connect_timeout=10,
        )

    def goose_url(self, database: str) -> str:
        """URL for ``GOOSE_DBSTRING``: the session runs as the migrator through ``-c role=``."""
        options = quote(f"-c role={ROLE_MIGRATOR}", safe="")
        return (
            f"postgres://{quote(self.user)}:{quote(self.password)}@{self.host}:{self.port}/{database}"
            f"?sslmode=disable&options={options}"
        )


def start_server() -> tuple[DockerContainer, Server]:
    """Start a disposable ``postgres:18`` and return ``(container, server)``.

    Raises ``RuntimeError`` (never skips) when Docker or the image is unavailable or the server does
    not accept a TCP connection in time. The caller stops the container.
    """
    testcontainers_config.ryuk_image = RYUK_IMAGE
    user, password = "postgres", "postgres"
    container = (
        DockerContainer(POSTGRES_IMAGE)
        .with_env("POSTGRES_USER", user)
        .with_env("POSTGRES_PASSWORD", password)
        .with_env("POSTGRES_DB", "postgres")
        .with_exposed_ports(5432)
        # A throwaway server: no durability needed, and it keeps the template copies fast.
        .with_command(["postgres", "-c", "fsync=off", "-c", "max_connections=100"])
    )
    try:
        container.start()
    except Exception as exc:  # any Docker failure must fail the run with one message
        raise RuntimeError(
            f"cannot start {POSTGRES_IMAGE} with Testcontainers (is Docker running?): {exc}"
        ) from None
    try:
        server = Server(
            container.get_container_host_ip(), int(container.get_exposed_port(5432)), user, password
        )
        # The entrypoint's temporary server listens on a unix socket only, so a TCP connection means
        # the final server is up.
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while True:
            try:
                with psycopg.connect(server.conninfo("postgres")):
                    break
            except psycopg.OperationalError as exc:
                if time.monotonic() > deadline:
                    raise RuntimeError(
                        f"{POSTGRES_IMAGE} did not accept connections: {exc}"
                    ) from None
                time.sleep(0.5)
    except BaseException:
        container.stop()
        raise
    return container, server


@dataclass
class Database:
    """One throwaway database owned by ``provisioning_migrator``."""

    harness: Harness
    name: str

    def admin(self) -> Conn:
        """A superuser session on this database (autocommit); the caller closes it."""
        return self.harness.connect(self.name)

    @contextmanager
    def as_role(self, role: str) -> Iterator[Conn]:
        """A superuser session switched with ``SET ROLE``: ``current_user`` is the role itself.

        The owner of every object is ``provisioning_migrator``, so ``as_role(ROLE_MIGRATOR)`` is the
        owner. Use :meth:`login` instead when a guard tests membership.
        """
        with self.harness.connect(self.name) as conn:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            yield conn

    @contextmanager
    def login(self, *groups: str) -> Iterator[Conn]:
        """A session of a fresh LOGIN role that is a member of ``groups`` and of nothing else.

        Pass exactly one group to act as one service role. Two groups are for tests that need a
        role's privileges and a second role's membership (the hosting-intake guard).
        """
        with self.harness.login(self, groups) as conn:
            yield conn

    def migrate(self, to: int | None = None) -> str:
        """Apply the migrations (all, or up to ``to``) as the migrator and return goose's output."""
        return self.harness.goose(self.name, to)


@dataclass
class Harness:
    server: Server
    _databases: list[str] = field(default_factory=list)
    _roles: list[str] = field(default_factory=list)
    _templates: dict[int | None, str] = field(default_factory=dict)

    # --- connections ------------------------------------------------------------------------------

    def connect(
        self, database: str = "postgres", *, user: str | None = None, password: str | None = None
    ) -> Conn:
        return psycopg.connect(
            self.server.conninfo(database, user=user, password=password), autocommit=True
        )

    # --- setup ------------------------------------------------------------------------------------

    def bootstrap(self) -> None:
        """Check the server is PostgreSQL 18 and create the NOLOGIN roles."""
        with self.connect() as conn:
            version = conn.execute("SELECT current_setting('server_version_num')::int").fetchone()
            assert version is not None
            if not 180000 <= version[0] < 190000:
                raise RuntimeError(
                    f"server_version_num = {version[0]}, want PostgreSQL 18.x (180000-189999)"
                )
            # No parameters: the simple protocol, which allows several statements.
            conn.execute(q(BOOTSTRAP_ROLES.read_text()))

    def goose(self, database: str, to: int | None = None) -> str:
        if not GOOSE.is_file():
            raise RuntimeError(f"{GOOSE} is missing; run `make py-tools` (or link the shared bin/)")
        env = {
            "PATH": "/usr/bin:/bin",
            "GOOSE_DRIVER": "postgres",
            "GOOSE_DBSTRING": self.server.goose_url(database),
            "GOOSE_MIGRATION_DIR": str(MIGRATIONS_DIR),
        }
        args = [str(GOOSE), "up-to", str(to)] if to is not None else [str(GOOSE), "up"]
        done = subprocess.run(  # noqa: S603 - fixed argv built from constants, no shell
            args, env=env, capture_output=True, text=True, timeout=120, check=False
        )
        out = done.stdout + done.stderr
        if done.returncode != 0:
            raise RuntimeError(
                f"goose {' '.join(args[1:])} on {database} exited {done.returncode}: {out.strip()}"
            )
        return out

    def _template(self, version: int | None) -> str:
        if version not in self._templates:
            name = f"prov_tmpl_{rand_hex()}_{'latest' if version is None else version}"
            self._create_database(name, template=None)
            self.goose(name, version)
            self._templates[version] = name
        return self._templates[version]

    def _create_database(self, name: str, *, template: str | None) -> None:
        stmt = sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(name), sql.Identifier(ROLE_MIGRATOR)
        )
        if template is not None:
            stmt += sql.SQL(" TEMPLATE {}").format(sql.Identifier(template))
        self._databases.append(name)
        with self.connect() as conn:
            conn.execute(stmt)

    def new_database(self, version: int | None = None) -> Database:
        """Create ``prov_test_<random>`` from the template migrated to ``version`` (None: all)."""
        template = self._template(version)
        name = f"prov_test_{rand_hex()}"
        self._create_database(name, template=template)
        return Database(self, name)

    def new_empty_database(self) -> Database:
        """Create ``prov_test_<random>`` with no migration applied."""
        name = f"prov_test_{rand_hex()}"
        self._create_database(name, template=None)
        return Database(self, name)

    def drop_database(self, database: Database) -> None:
        with self.connect() as conn:
            conn.execute(
                sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                    sql.Identifier(database.name)
                )
            )

    @contextmanager
    def login(self, database: Database, groups: Sequence[str]) -> Iterator[Conn]:
        role = f"prov_login_{rand_hex()}"
        password = rand_hex(16)
        self._roles.append(role)
        with self.connect() as admin:
            admin.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} IN ROLE {}").format(
                    sql.Identifier(role),
                    sql.Literal(password),
                    sql.SQL(", ").join(sql.Identifier(g) for g in groups),
                )
            )
        conn = psycopg.connect(
            self.server.conninfo(database.name, user=role, password=password), autocommit=True
        )
        try:
            yield conn
        finally:
            conn.close()
            with self.connect() as admin:
                admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

    # --- teardown ---------------------------------------------------------------------------------

    def drop_templates(self) -> None:
        """Drop the template databases, which are not tests' own and are not leftovers."""
        with self.connect() as conn:
            for name in self._templates.values():
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )

    def drop_all(self) -> None:
        """Drop every database this run created, then every login role (databases first)."""
        with self.connect() as conn:
            for name in self._databases:
                conn.execute(
                    sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
                )
        # Roles that tests created and that a failing test left behind.
        with self.connect() as conn:
            for role in self._roles:
                conn.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

    def leftovers(self) -> list[str]:
        """Names of the databases and roles this run created that still exist."""
        with self.connect() as conn:
            dbs = conn.execute(
                "SELECT datname::text FROM pg_database WHERE datname = ANY(%s::text[]) ORDER BY 1",
                [self._databases],
            ).fetchall()
            roles = conn.execute(
                "SELECT rolname::text FROM pg_roles WHERE rolname = ANY(%s::text[]) ORDER BY 1",
                [self._roles],
            ).fetchall()
        return [f"database {d[0]}" for d in dbs] + [f"role {r[0]}" for r in roles]


# --- helpers used by the tests ---------------------------------------------------------------


def seed(database: Database, *statements: Statement) -> None:
    """Write rows the guards refuse, through the seeding path of decision 0008 section 5.

    One superuser transaction with ``SET LOCAL session_replication_role = replica``: trigger guards
    and foreign keys are skipped for this transaction only; check, NOT NULL and unique constraints
    and unique indexes still apply, so a seed must name parents that exist. Either every statement
    is stored or none.
    """
    with database.admin() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        for statement in statements:
            if isinstance(statement, str):
                conn.execute(q(statement))
            else:
                conn.execute(q(statement[0]), statement[1])


def rows(conn: Conn, query: str, params: Sequence[object] | None = None) -> list[TupleRow]:
    return conn.execute(q(query), params).fetchall()


def column(conn: Conn, query: str, params: Sequence[object] | None = None) -> list[object]:
    return [r[0] for r in rows(conn, query, params)]


def scalar(conn: Conn, query: str, params: Sequence[object] | None = None) -> object:
    result = rows(conn, query, params)
    assert len(result) == 1, f"expected one row, got {len(result)}"
    return result[0][0]


def has_column(conn: Conn, table: str, column_name: str) -> bool:
    """Whether ``schema.table`` has ``column_name`` in the database of ``conn``: a statement
    shared by tests at several versions adds a column only where it exists (00018's
    ``hosting_reason``). The catalogue is readable by every role."""
    schema, name = table.split(".")
    found = conn.execute(
        "SELECT count(*) FROM pg_catalog.pg_attribute AS a"
        " JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid"
        " JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace"
        " WHERE n.nspname = %s AND c.relname = %s AND a.attname = %s AND NOT a.attisdropped",
        (schema, name, column_name),
    ).fetchone()
    return found is not None and found[0] == 1


@contextmanager
def refused(
    conn: Conn, *, constraint: str, table: str | None = None, sqlstate: str = "23514"
) -> Iterator[None]:
    """Run the body in a transaction that must fail with ``sqlstate`` naming ``constraint``.

    The default SQLSTATE 23514 is what every guard raises with its own name as the constraint name.
    The body may end the transaction itself by leaving the block: a deferred guard fires at commit.
    The transaction is rolled back, so the next check starts from the same rows.
    """
    with pytest.raises(psycopg.Error) as info:
        with conn.transaction():
            yield
    error = info.value
    assert (error.sqlstate, error.diag.constraint_name) == (sqlstate, constraint), str(error)
    if table is not None:
        assert error.diag.table_name == table, str(error)


@contextmanager
def allowed(conn: Conn) -> Iterator[None]:
    """Run the body in a transaction that must succeed, then roll it back."""
    with conn.transaction(force_rollback=True):
        yield


# The operator's backfill of decision 0021 D8, as one statement on the seeding path: every hosting
# row from before 00020 gets a label made from its project ID.
BACKFILL_SUBDOMAINS: Final = (
    "UPDATE provisioning.project_hosting"
    " SET subdomain = 'site-' || substr(md5(project_id::text), 1, 12) WHERE subdomain IS NULL"
)


def migrate_with_backfill(database: Database) -> None:
    """Migrate an older database to the latest migration in the order of
    .claude/docs/runbooks/phase-1-database.md: up to 00020, fill every missing sub-domain, then on
    (00021 refuses while a row has none)."""
    database.migrate(20)
    seed(database, BACKFILL_SUBDOMAINS)
    database.migrate()
