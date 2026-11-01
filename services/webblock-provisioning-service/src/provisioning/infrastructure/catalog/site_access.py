"""The catalog side of access_setup: the per-website login role ``site_<hex>`` in the shared
catalog database (Notion 6 · Shared catalog; decision 0011 sections 5 and 11, decision 0014
section 14).

- ``role_exists``: whether the project's role exists.
- ``set_site_role``: creates the role when it is absent, as a login member of ``catalog_site`` only,
  or sets a new password on the existing role when it is this administrator's and has that shape
  (a squatted name is ``SiteAccessInvalid`` and is not altered). The password reaches the server
  only as a SCRAM-SHA-256 verifier computed here. Then it logs in as the role with the password on
  a new connection and checks that the role reads only its own website's rows.
- ``verify_site_role``: checks an existing role without its password: held by this
  administrator, a login member of ``catalog_site`` only that no other role can act as, able to log
  in, no access to ``catalog.websites``, and, acting as the role, no row of another website in any
  catalog table it can reach, each with forced row-level security.

SQL safety (the owner's requirement; proto-sql-safety.md): every statement is a module-level
constant. Values are bound parameters. DDL needs identifiers, which cannot be parameters: the role
name is checked against ``^site_[0-9a-f]{32}$`` and then quoted with ``psycopg.sql.Identifier``, and
the SCRAM verifier, checked against its shape, is the only ``psycopg.sql.Literal``. Nothing is built
by string formatting.

Connections are short-lived, at most two at a time (decision 0011 section 11), never the service's
pool. Only ``scram-sha-256`` authentication is accepted from the server (``require_auth``), so a
password is never sent in clear text. Every failure is a fixed message with at most the SQLSTATE;
driver and server text never pass through (``from None``).
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

from provisioning.infrastructure.catalog.credential import (
    RuntimeCredential,
    check_site_role_name,
    site_role_name,
)
from provisioning.infrastructure.catalog.errors import (
    CatalogUnavailable,
    InvalidSiteRoleName,
    SiteAccessInvalid,
)
from provisioning.infrastructure.catalog.scram import new_scram_verifier

MAX_CONNECTIONS = 2
APPLICATION_NAME = "provisioning-access-setup"
SSL_MODES = frozenset({"disable", "require", "verify-ca", "verify-full"})
_MAX_TIMEOUT_MS = 60_000
_MIN_LOCK_TIMEOUT_MS = 100
_REDACTED = "[REDACTED]"
_SQLSTATE = re.compile(r"[0-9A-Z]{5}")
_VERIFIER = re.compile(r"SCRAM-SHA-256\$[0-9]+:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+")

# --- statements: module-level constants only ------------------------------------------------------

ROLE_EXISTS = "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s)"

# What the administrator's session reads about a site role, as seen by the current role.
ROLE_SHAPE = """
SELECT r.rolsuper, r.rolcreaterole, r.rolcreatedb, r.rolreplication, r.rolbypassrls,
       r.rolcanlogin,
       (r.rolvaliduntil IS NULL OR r.rolvaliduntil > pg_catalog.now()),
       r.rolconnlimit <> 0,
       pg_catalog.has_database_privilege(r.oid, pg_catalog.current_database(), 'CONNECT'),
       EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
               WHERE m.roleid = r.oid AND m.admin_option
                 AND m.member = (SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user)),
       pg_catalog.pg_has_role(current_user, r.oid, 'SET'),
       (SELECT count(*) FROM pg_catalog.pg_auth_members m WHERE m.member = r.oid),
       EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
               JOIN pg_catalog.pg_roles g ON g.oid = m.roleid
               WHERE m.member = r.oid AND g.rolname = 'catalog_site' AND NOT g.rolcanlogin
                 AND m.inherit_option AND NOT m.set_option AND NOT m.admin_option),
       (SELECT count(*) FROM pg_catalog.pg_auth_members m
        JOIN pg_catalog.pg_roles o ON o.oid = m.member
        WHERE m.roleid = r.oid AND o.rolname <> current_user)
FROM pg_catalog.pg_roles r
WHERE r.rolname = %s
"""

CREATE_ROLE = (
    "CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
    "NOINHERIT PASSWORD {}"
)
# Inherit catalog_site's privileges, but never act as the group itself.
GRANT_GROUP = "GRANT catalog_site TO {} WITH INHERIT TRUE, SET FALSE"
# Lets verify_site_role check the role's view of the catalog without its password.
GRANT_SET_TO_ADMIN = "GRANT {} TO CURRENT_USER WITH INHERIT FALSE, SET TRUE"
ALTER_PASSWORD = "ALTER ROLE {} WITH PASSWORD {}"  # noqa: S105 - a statement; the {} is a verifier
SET_LOCAL_ROLE = "SET LOCAL ROLE {}"

# Whether the current role has any privilege on catalog.websites; no row when it does not exist.
WEBSITES_ACCESS = """
SELECT pg_catalog.has_table_privilege(c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR pg_catalog.has_any_column_privilege(c.oid, 'SELECT, INSERT, UPDATE, REFERENCES')
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'catalog' AND c.relname = 'websites' AND c.relkind IN ('r', 'p')
"""  # noqa: E501 - one SQL expression

CURRENT_WEBSITE = "SELECT catalog.current_website_id()"

# Every relation in schema catalog other than websites with a website_id column on which the current
# role has any privilege.
TENANT_TABLES = """
SELECT c.relname::text, c.relkind::text, c.relrowsecurity, c.relforcerowsecurity,
       pg_catalog.has_column_privilege(c.oid, 'website_id', 'SELECT')
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'catalog' AND c.relname <> 'websites' AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
  AND EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
              WHERE a.attrelid = c.oid AND a.attname = 'website_id' AND a.attnum > 0
                AND NOT a.attisdropped)
  AND (pg_catalog.has_table_privilege(c.oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE')
       OR pg_catalog.has_any_column_privilege(c.oid, 'SELECT, INSERT, UPDATE'))
ORDER BY 1
"""

FOREIGN_ROWS = "SELECT EXISTS (SELECT 1 FROM {} WHERE website_id IS DISTINCT FROM %s)"


# --- configuration --------------------------------------------------------------------------------


def check_endpoint(host: str, port: int, database: str, sslmode: str) -> None:
    """Where a catalog connection goes: a host, a TCP port, a database and an ``sslmode`` of
    ``SSL_MODES``. Raises ``ValueError`` naming the first bad part."""
    if not host:
        raise ValueError("host is required")
    if not 1 <= port <= 65535:
        raise ValueError("port must be 1 to 65535")
    if not database:
        raise ValueError("database is required")
    if sslmode not in SSL_MODES:
        raise ValueError("sslmode must be disable, require, verify-ca or verify-full")


@dataclass(frozen=True, slots=True)
class CatalogConnection:
    """How access_setup reaches the catalog database (prototype: from the stack's stack.env).
    ``sslmode`` is one that never silently falls back to plain text when TLS is asked for."""

    host: str
    port: int
    database: str
    sslmode: str
    connect_timeout_seconds: int
    statement_timeout_ms: int
    lock_timeout_ms: int

    def __post_init__(self) -> None:
        check_endpoint(self.host, self.port, self.database, self.sslmode)
        if not 1 <= self.connect_timeout_seconds <= 60:
            raise ValueError("connect_timeout_seconds must be 1 to 60")
        if not 1 <= self.statement_timeout_ms <= _MAX_TIMEOUT_MS:
            raise ValueError("statement_timeout_ms must be 1 to 60000")
        if not _MIN_LOCK_TIMEOUT_MS <= self.lock_timeout_ms <= _MAX_TIMEOUT_MS:
            raise ValueError("lock_timeout_ms must be 100 to 60000")

    @property
    def operation_budget_seconds(self) -> float:
        """The bound of one public call: two connections and a handful of statements."""
        return 2 * self.connect_timeout_seconds + 10 * self.statement_timeout_ms / 1000


@dataclass(frozen=True, slots=True)
class AdminLogin:
    """A login with CREATEROLE and ADMIN OPTION on ``catalog_site``; never a site role. The password
    prints as a marker and is read only to connect."""

    user: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.user or not self.password:
            raise ValueError("the administrator login needs a user and a password")
        try:
            check_site_role_name(self.user)
        except InvalidSiteRoleName:
            return
        raise ValueError("the administrator login must not be a site role")

    def __repr__(self) -> str:
        return f"AdminLogin(user={self.user}, password={_REDACTED})"

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class _RoleShape:
    superuser: bool
    create_role: bool
    create_db: bool
    replication: bool
    bypass_rls: bool
    can_login: bool
    valid_now: bool
    may_connect: bool
    database_connect: bool
    admin_holds: bool
    admin_can_set: bool
    memberships: int
    in_group: bool
    other_members: int

    def check_owned(self) -> None:
        """A role this administrator made: held by it, no dangerous attribute, a member of
        catalog_site and of nothing else, and no other role can act as it."""
        if not self.admin_holds or not self.admin_can_set:
            raise SiteAccessInvalid("the site role is not held by this administrator")
        if self.superuser or self.create_role or self.create_db or self.replication:
            raise SiteAccessInvalid("the site role has an attribute a site role must not have")
        if self.bypass_rls:
            raise SiteAccessInvalid("the site role has an attribute a site role must not have")
        if self.memberships != 1 or not self.in_group:
            raise SiteAccessInvalid("the site role is not a member of catalog_site only")
        if self.other_members != 0:
            raise SiteAccessInvalid("another role can act as the site role")

    def check_loginable(self) -> None:
        if not (self.can_login and self.valid_now and self.may_connect and self.database_connect):
            raise SiteAccessInvalid("the site role cannot log in to the catalog")


def _unavailable(while_: str, error: BaseException) -> CatalogUnavailable:
    code = getattr(error, "sqlstate", None)
    if isinstance(code, str) and _SQLSTATE.fullmatch(code):
        return CatalogUnavailable(f"the catalog refused {while_} (SQLSTATE {code})")
    return CatalogUnavailable(f"the catalog failed while {while_}")


def _verifier_literal(verifier: str) -> sql.Literal:
    if _VERIFIER.fullmatch(verifier) is None:
        raise ValueError("refusing a password string that is not a SCRAM-SHA-256 verifier")
    return sql.Literal(verifier)


type _Conn = psycopg.AsyncConnection[tuple[Any, ...]]


class PostgresSiteAccess:
    """access_setup's catalog adapter. Safe for concurrent use; at most two sessions at a time."""

    def __init__(self, connection: CatalogConnection, admin: AdminLogin) -> None:
        self._connection = connection
        self._admin = admin
        self._slots = asyncio.Semaphore(MAX_CONNECTIONS)

    def __repr__(self) -> str:
        c = self._connection
        return f"PostgresSiteAccess({c.host}:{c.port}/{c.database}, admin={self._admin!r})"

    @asynccontextmanager
    async def _open(self, user: str, password: str, while_: str) -> AsyncIterator[_Conn]:
        c = self._connection
        options = (
            f"-c statement_timeout={c.statement_timeout_ms} -c lock_timeout={c.lock_timeout_ms} "
            f"-c idle_in_transaction_session_timeout={c.statement_timeout_ms}"
        )
        async with self._slots:
            try:
                async with asyncio.timeout(c.connect_timeout_seconds + 1):
                    conn = await psycopg.AsyncConnection.connect(
                        host=c.host,
                        port=c.port,
                        dbname=c.database,
                        user=user,
                        password=password,
                        sslmode=c.sslmode,
                        connect_timeout=c.connect_timeout_seconds,
                        application_name=APPLICATION_NAME,
                        options=options,
                        require_auth="scram-sha-256",
                    )
            except psycopg.OperationalError as e:
                code = e.sqlstate
                if code is not None and code.startswith("28") and user != self._admin.user:
                    raise SiteAccessInvalid(
                        "the site role cannot log in with its password"
                    ) from None
                raise _unavailable(f"connecting while {while_}", e) from None
            except (psycopg.Error, OSError, TimeoutError) as e:
                raise _unavailable(f"connecting while {while_}", e) from None
            try:
                yield conn
            finally:
                await conn.close()

    async def role_exists(self, project_id: UUID) -> bool:
        """Whether the project's site role exists."""
        role = site_role_name(project_id)
        try:
            async with asyncio.timeout(self._connection.operation_budget_seconds):
                async with self._open(self._admin.user, self._admin.password, "looking up") as conn:
                    cursor = await conn.execute(ROLE_EXISTS, (role,))
                    row = await cursor.fetchone()
                    await conn.rollback()
        except psycopg.Error as e:
            raise _unavailable("looking up the site role", e) from None
        except TimeoutError:
            raise CatalogUnavailable("the catalog did not answer in time") from None
        return bool(row and row[0])

    async def set_site_role(self, credential: RuntimeCredential) -> bool:
        """Create the role or set its password, then log in as it and check its isolation. True
        when the role was created."""
        role = check_site_role_name(credential.role)
        if role != site_role_name(credential.project_id):
            raise InvalidSiteRoleName("the credential's role is not its project's site role")
        password = _verifier_literal(new_scram_verifier(credential.reveal_password()))
        try:
            async with asyncio.timeout(self._connection.operation_budget_seconds):
                created = await self._store_verifier(role, password)
                await self._check_login(credential)
        except psycopg.Error as e:
            raise _unavailable("setting the site role", e) from None
        except TimeoutError:
            raise CatalogUnavailable("the catalog did not answer in time") from None
        return created

    async def verify_site_role(self, project_id: UUID) -> None:
        """Check the existing role without its password; SiteAccessInvalid when a check fails.
        Nothing is changed."""
        role = site_role_name(project_id)
        try:
            async with asyncio.timeout(self._connection.operation_budget_seconds):
                async with self._open(self._admin.user, self._admin.password, "verifying") as conn:
                    await conn.set_read_only(True)
                    async with conn.transaction():
                        shape = await _read_shape(conn, role)
                        if shape is None:
                            raise SiteAccessInvalid("the site role does not exist")
                        shape.check_owned()
                        shape.check_loginable()
                        await conn.execute(sql.SQL(SET_LOCAL_ROLE).format(sql.Identifier(role)))
                        await _check_isolation(conn, project_id)
        except psycopg.Error as e:
            raise _unavailable("verifying the site role", e) from None
        except TimeoutError:
            raise CatalogUnavailable("the catalog did not answer in time") from None

    async def _store_verifier(self, role: str, password: sql.Literal) -> bool:
        """One administrator transaction: create the role when it is absent, or check that it is
        this administrator's and set its password."""
        async with (
            self._open(self._admin.user, self._admin.password, "storing the role") as conn,
            conn.transaction(),
        ):
            shape = await _read_shape(conn, role)
            if shape is None:
                await conn.execute(sql.SQL(CREATE_ROLE).format(sql.Identifier(role), password))
                await conn.execute(sql.SQL(GRANT_GROUP).format(sql.Identifier(role)))
                await conn.execute(sql.SQL(GRANT_SET_TO_ADMIN).format(sql.Identifier(role)))
                return True
            shape.check_owned()
            await conn.execute(sql.SQL(ALTER_PASSWORD).format(sql.Identifier(role), password))
            return False

    async def _check_login(self, credential: RuntimeCredential) -> None:
        """A new connection as the role with its password, then the isolation checks in a
        read-only transaction."""
        async with self._open(
            credential.role, credential.reveal_password(), "logging in as the site role"
        ) as conn:
            await conn.set_read_only(True)
            async with conn.transaction():
                await _check_isolation(conn, credential.project_id)


async def _read_shape(conn: _Conn, role: str) -> _RoleShape | None:
    cursor = await conn.execute(ROLE_SHAPE, (role,))
    row = await cursor.fetchone()
    if row is None:
        return None
    return _RoleShape(
        superuser=bool(row[0]),
        create_role=bool(row[1]),
        create_db=bool(row[2]),
        replication=bool(row[3]),
        bypass_rls=bool(row[4]),
        can_login=bool(row[5]),
        valid_now=bool(row[6]),
        may_connect=bool(row[7]),
        database_connect=bool(row[8]),
        admin_holds=bool(row[9]),
        admin_can_set=bool(row[10]),
        memberships=int(row[11]),
        in_group=bool(row[12]),
        other_members=int(row[13]),
    )


async def _check_isolation(conn: _Conn, project_id: UUID) -> None:
    """As the current role of ``conn`` (the site role): no access to catalog.websites,
    current_website_id() maps it to its own website or to none, and every catalog relation with a
    website_id it can reach has forced row-level security and shows it no row of another website."""
    cursor = await conn.execute(WEBSITES_ACCESS)
    row = await cursor.fetchone()
    if row is None:
        raise CatalogUnavailable("the catalog has no catalog.websites")
    if row[0]:
        raise SiteAccessInvalid("the site role has access to catalog.websites")

    cursor = await conn.execute(CURRENT_WEBSITE)
    row = await cursor.fetchone()
    current = row[0] if row else None
    if current is not None and current != project_id:
        raise SiteAccessInvalid("the catalog maps the site role to another website")

    cursor = await conn.execute(TENANT_TABLES)
    tables = await cursor.fetchall()
    for name, kind, row_security, forced, selectable in tables:
        if kind in ("r", "p") and not (row_security and forced):
            raise SiteAccessInvalid(
                "a catalog table the site role can reach has no forced row-level security"
            )
        if not selectable:
            continue
        cursor = await conn.execute(
            sql.SQL(FOREIGN_ROWS).format(sql.Identifier("catalog", name)), (project_id,)
        )
        foreign = await cursor.fetchone()
        if foreign is not None and foreign[0]:
            raise SiteAccessInvalid("the site role reads rows of another website")
