"""Mapping of database errors to the application's typed errors (decision 0004 section 4;
review-port-arch.md H4; proto-sql-safety.md rule 5).

SQLAlchemy's ``DBAPIError`` text contains the SQL statement and, without ``hide_parameters``, its
parameters; the driver's message can contain row values, the login and the database. Neither ever
leaves a store: the mapping reads only the SQLSTATE and the constraint, schema and table names from
the psycopg error's ``diag``, and every mapped error is raised ``from None``.

- lock timeout (55P03), deadlock (40P01), serialization failure (40001): ``Contention``;
- statement timeout or cancel request (57014): ``Timeout``;
- a failure to connect or a lost connection: ``DatabaseUnreachable`` (a ``DatabaseError`` and the
  application's ``StoreUnreachable``) with reason ``connection``;
- anything else: ``DatabaseError`` naming the SQLSTATE and the constraint (internal, 500).
"""

from __future__ import annotations

import psycopg
from sqlalchemy.exc import DBAPIError

from provisioning.core.application import Contention, StoreUnreachable, Timeout

SQLSTATE_UNIQUE_VIOLATION = "23505"
_CONTENTION = frozenset({"55P03", "40P01", "40001"})
_TIMEOUT = frozenset({"57014"})


class InvalidArgument(ValueError):
    """An argument a store refuses before any SQL that has no domain rule of its own: a duration
    below one microsecond, a limit out of range, a worker identity that does not fit the column.
    The message names the argument, never its value."""


class DatabaseError(Exception):
    """An internal error of the stores: the operation, the SQLSTATE and the constraint, schema and
    table names of a server error, never its message or detail, and no wrapped driver error."""

    def __init__(
        self,
        op: str,
        *,
        sqlstate: str | None = None,
        constraint: str | None = None,
        table: str | None = None,
        reason: str | None = None,
    ) -> None:
        self.op = op
        self.sqlstate = sqlstate
        self.constraint = constraint
        self.table = table
        parts = [f"postgres: {op}"]
        if reason is not None:
            parts.append(reason)
        if sqlstate is not None:
            parts.append(f"SQLSTATE {sqlstate}")
        if constraint is not None:
            parts.append(f"constraint {constraint}" + (f" on {table}" if table else ""))
        super().__init__(": ".join(parts))


class DatabaseUnreachable(DatabaseError, StoreUnreachable):
    """The connection to the database failed or was lost: a ``DatabaseError`` for the stores'
    callers and the application's ``StoreUnreachable`` for the message consumers."""


def server_error(exc: BaseException) -> psycopg.Error | None:
    """The psycopg error inside a SQLAlchemy ``DBAPIError``, or None."""
    if isinstance(exc, DBAPIError) and isinstance(exc.orig, psycopg.Error):
        return exc.orig
    return None


def constraint_of(exc: BaseException) -> tuple[str | None, str | None]:
    """``(sqlstate, constraint name)`` of a server error, ``(None, None)`` for anything else."""
    orig = server_error(exc)
    if orig is None:
        return None, None
    return orig.sqlstate, orig.diag.constraint_name


def is_unique_violation(exc: BaseException, constraints: frozenset[str]) -> bool:
    """Whether ``exc`` is a unique violation of one of ``constraints``."""
    sqlstate, constraint = constraint_of(exc)
    return sqlstate == SQLSTATE_UNIQUE_VIOLATION and constraint in constraints


def map_db_error(op: str, exc: DBAPIError) -> Exception:
    """The typed error for a SQLAlchemy ``DBAPIError``; the caller raises it ``from None``."""
    orig = server_error(exc)
    sqlstate = orig.sqlstate if orig is not None else None
    if sqlstate in _CONTENTION:
        return Contention(f"postgres: {op}: SQLSTATE {sqlstate}")
    if sqlstate in _TIMEOUT:
        return Timeout(f"postgres: {op}: statement timeout (SQLSTATE {sqlstate})")
    if orig is None or sqlstate is None or sqlstate.startswith("08") or exc.connection_invalidated:
        # The connection error's text names the login role and the database: never used.
        return DatabaseUnreachable(op, sqlstate=sqlstate, reason="database connection failed")
    return DatabaseError(
        op,
        sqlstate=sqlstate,
        constraint=orig.diag.constraint_name,
        table=orig.diag.table_name,
    )
