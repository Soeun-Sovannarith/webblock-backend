"""One SQLAlchemy ``AsyncEngine`` per database role login (decision 0014 section 11).

Each process connects as a login that is a member of exactly one Provisioning role, so each store
gets the engine of its role: ``provisioning_api`` (acceptance, reads), ``provisioning_worker``
(jobs, fenced writes), ``provisioning_intake`` (release intake), ``provisioning_build`` (builds)
and ``provisioning_hosting_intake`` (Payment-event commands). Settings come from configuration;
nothing here reads the environment, and creating an engine opens no connection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

DRIVER = "postgresql+psycopg"

# PostgreSQL's idle_in_transaction_session_timeout is an integer of milliseconds.
_MS = timedelta(milliseconds=1)
_MAX_SETTING_MS = 2**31 - 1


class InvalidEngineSettings(ValueError):
    """Engine settings that are out of range; the message names the setting, never a value."""


@dataclass(frozen=True, slots=True)
class EngineSettings:
    """The bounded pool of one engine (required configuration, no defaults; OD-15).

    ``pool_size`` connections are kept, at most ``max_overflow`` more are opened under load, a
    caller waits at most ``pool_timeout`` for one, and ``connect_timeout`` bounds each connect.

    ``idle_in_transaction_timeout`` is every session's ``idle_in_transaction_session_timeout``
    (task H5b): a process paused inside a transaction
    (SIGSTOP, a VM or GC pause), whose own transaction timeout cannot fire, has its session ended
    by the server, so its row locks are freed for the worker that takes its command over. A whole
    number of milliseconds, 1 to 2^31-1. A session idle outside a transaction (the pool's, the
    build hint listener's autocommit LISTEN) is not affected."""

    pool_size: int
    max_overflow: int
    pool_timeout: timedelta
    connect_timeout: timedelta
    idle_in_transaction_timeout: timedelta

    def validate(self) -> None:
        if self.pool_size < 1:
            raise InvalidEngineSettings("pool_size must be at least 1")
        if self.max_overflow < 0:
            raise InvalidEngineSettings("max_overflow must not be negative")
        if self.pool_timeout <= timedelta(0):
            raise InvalidEngineSettings("pool_timeout must be positive")
        if self.connect_timeout < timedelta(seconds=1):
            raise InvalidEngineSettings("connect_timeout must be at least one second")
        idle = self.idle_in_transaction_timeout
        if idle < _MS or idle % _MS != timedelta(0) or idle // _MS > _MAX_SETTING_MS:
            raise InvalidEngineSettings(
                "idle_in_transaction_timeout must be a whole number of milliseconds, 1 to 2^31-1"
            )


def database_url(*, host: str, port: int, database: str, user: str, password: str) -> URL:
    """The psycopg URL of one login. ``URL`` hides the password when rendered."""
    return URL.create(
        DRIVER, username=user, password=password, host=host, port=port, database=database
    )


def open_engine(url: URL, settings: EngineSettings) -> AsyncEngine:
    """The engine of one role login. ``hide_parameters`` keeps bound values out of every
    SQLAlchemy error text, and ``echo`` stays off. ``pool_pre_ping`` replaces a connection the
    server closed before it is handed out."""
    settings.validate()
    if url.drivername != DRIVER:
        raise InvalidEngineSettings(f"the URL must use the {DRIVER} driver")
    return create_async_engine(
        url,
        hide_parameters=True,
        echo=False,
        pool_size=settings.pool_size,
        max_overflow=settings.max_overflow,
        pool_timeout=settings.pool_timeout.total_seconds(),
        pool_pre_ping=True,
        # Every store checks the row count of its conditional writes; SQLAlchemy keeps the
        # driver's rowcount for an INSERT only with this option.
        execution_options={"preserve_rowcount": True},
        connect_args={
            "connect_timeout": int(settings.connect_timeout.total_seconds()),
            "options": "-c idle_in_transaction_session_timeout="
            f"{settings.idle_in_transaction_timeout // _MS}",
        },
    )
