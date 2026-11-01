"""The mapping of database errors (decision 0004 section 4): a failed or lost connection is
``DatabaseUnreachable``, which the message consumers read as the application's
``StoreUnreachable`` (decision 0022, the lead's ruling on database outages); a server that
answered with an error stays a plain ``DatabaseError``."""

from __future__ import annotations

import psycopg
from psycopg import errors as pg_errors
from sqlalchemy.exc import DBAPIError

from provisioning.core.application import StoreUnreachable
from provisioning.infrastructure.postgres import DatabaseError, DatabaseUnreachable, map_db_error


def _wrapped(orig: Exception) -> DBAPIError:
    return DBAPIError("SELECT secret_column", {"p": "secret-param"}, orig)


def test_a_failed_connection_is_unreachable_and_names_no_server_text() -> None:
    mapped = map_db_error("claim", _wrapped(psycopg.OperationalError("login role_x failed")))
    assert isinstance(mapped, DatabaseUnreachable)
    assert isinstance(mapped, DatabaseError) and isinstance(mapped, StoreUnreachable)
    assert "role_x" not in str(mapped) and "secret" not in str(mapped)


def test_a_server_error_is_not_unreachable() -> None:
    mapped = map_db_error("claim", _wrapped(pg_errors.UniqueViolation("duplicate")))
    assert isinstance(mapped, DatabaseError)
    assert not isinstance(mapped, StoreUnreachable)
