"""PostgreSQL settings (decision 0004 section 10,
decision 0005 section 7).

The server and its limits are one section; each login is another, because a process may hold one
pool per database role (the API: ``provisioning_api``, and ``provisioning_build`` for build_release)
and every login is a member of exactly one role. The pool itself is built by the stores (task B3)
from ``DatabaseConfig.dsn`` and the limits; this package knows no role names.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Final
from urllib.parse import quote, urlencode

from provisioning.infrastructure.config.loader import Loader, has_control, plain_text
from provisioning.infrastructure.config.net import valid_host
from provisioning.infrastructure.config.secret import Secret

MIN_TIMEOUT: Final = timedelta(milliseconds=10)
_SSL_MODES: Final = ("disable", "require", "verify-ca", "verify-full")


@dataclass(frozen=True, slots=True)
class DatabaseLogin:
    """One login: its user name and its password."""

    user: str
    password: Secret


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    """The server, the pool size and the transaction, statement and lock timeouts that decision
    0004 section 10 makes required. ``ssl_mode`` is disable, require, verify-ca or verify-full;
    "allow" and "prefer" can fall back to plain text and are refused."""

    host: str
    port: int
    name: str
    ssl_mode: str
    max_conns: int
    connect_timeout: timedelta
    tx_timeout: timedelta
    statement_timeout: timedelta
    lock_timeout: timedelta

    def dsn(self, login: DatabaseLogin) -> Secret:
        """A ``postgresql://`` URL for ``login`` with the credentials escaped, as a Secret: it
        carries the password. Pool size and the transaction timeouts are not part of it; the pool
        applies them from the fields. libpq counts whole seconds, so a sub-second connect timeout
        is rounded up rather than becoming 0, which means "wait forever" there."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        query = urlencode(
            {
                "sslmode": self.ssl_mode,
                "connect_timeout": str(max(1, math.ceil(self.connect_timeout.total_seconds()))),
            }
        )
        return Secret(
            f"postgresql://{quote(login.user, safe='')}:{quote(login.password.reveal(), safe='')}"
            f"@{host}:{self.port}/{quote(self.name, safe='')}?{query}"
        )


def read_database(loader: Loader) -> DatabaseConfig:
    """PG_HOST, PG_PORT, PG_DATABASE, PG_SSLMODE, DB_MAX_CONNS and the four DB_* timeouts, with
    the statement timeout at most the transaction timeout and the lock timeout at most the
    statement timeout."""
    config = DatabaseConfig(
        host=loader.text("PG_HOST", _db_host),
        port=loader.integer("PG_PORT", 1, 65535),
        name=loader.text("PG_DATABASE", plain_text(63)),
        ssl_mode=loader.choice("PG_SSLMODE", _SSL_MODES),
        max_conns=loader.integer("DB_MAX_CONNS", 1, 500),
        connect_timeout=loader.duration("DB_CONNECT_TIMEOUT", MIN_TIMEOUT, timedelta(minutes=5)),
        tx_timeout=loader.duration("DB_TX_TIMEOUT", MIN_TIMEOUT, timedelta(minutes=10)),
        statement_timeout=loader.duration(
            "DB_STATEMENT_TIMEOUT", MIN_TIMEOUT, timedelta(minutes=10)
        ),
        lock_timeout=loader.duration("DB_LOCK_TIMEOUT", MIN_TIMEOUT, timedelta(minutes=1)),
    )
    if (
        loader.ok("DB_STATEMENT_TIMEOUT", "DB_TX_TIMEOUT")
        and config.statement_timeout > config.tx_timeout
    ):
        loader.fail("DB_STATEMENT_TIMEOUT", "must not exceed DB_TX_TIMEOUT")
    if (
        loader.ok("DB_LOCK_TIMEOUT", "DB_STATEMENT_TIMEOUT")
        and config.lock_timeout > config.statement_timeout
    ):
        loader.fail("DB_LOCK_TIMEOUT", "must not exceed DB_STATEMENT_TIMEOUT")
    return config


def read_login(loader: Loader, user_key: str, password_key: str) -> DatabaseLogin:
    """One login from ``user_key`` (at most 63 bytes, PostgreSQL's identifier limit) and
    ``password_key``."""
    return DatabaseLogin(
        user=loader.text(user_key, plain_text(63)),
        password=loader.secret(password_key, password),
    )


def password(value: str) -> str | None:
    """Any non-empty value without control characters, at most 1024 bytes. Like every reason, it
    says nothing about the value."""
    if len(value.encode("utf-8", "surrogatepass")) > 1024 or has_control(value):
        return "must be at most 1024 bytes without control characters"
    return None


def _db_host(value: str) -> str | None:
    if not valid_host(value):
        return "must be an IP address or a host name"
    return None
