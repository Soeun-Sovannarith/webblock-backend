"""What the four composition roots share (task G): reporting a refused configuration, routing the
standard library's log records through the filtered JSON output, one SQLAlchemy engine per role
login, the transaction bounds, small clock and ID sources, and running a set of loops until a stop
signal.

It imports no Kubernetes, broker or HTTP client, so every host may use it (import-linter keeps
Kubernetes out of the API host and the bootstrap adapter out of the worker host). The Jenkins
adapter is built in ``hosts.build_worker.wiring``, never here, so the API does not link it (finding
F-6 of H-V; the import-linter contract ``api-no-build-system``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import uuid
from collections.abc import Callable, Coroutine, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final, TextIO

from sqlalchemy.ext.asyncio import AsyncEngine

from provisioning.infrastructure.config import (
    DatabaseConfig,
    DatabaseLogin,
    LoadError,
)
from provisioning.infrastructure.postgres import (
    EngineSettings,
    Timeouts,
    database_url,
    open_engine,
)
from provisioning.infrastructure.telemetry import stdlib_handler

EXIT_OK: Final = 0
EXIT_FAILURE: Final = 1
EXIT_CONFIG: Final = 2

DEFAULT_TOKEN_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
"""Where Kubernetes mounts a pod's ServiceAccount token when automountServiceAccountToken is on."""


def default_token_refused(out: TextIO, label: str) -> bool:
    """True, after one line on ``out``, when a default ServiceAccount token is mounted. The service
    never uses the default automount (decision 0016 section 9 ruling 2): the worker and the
    bootstrap read an explicitly projected token, and the API and the build worker hold none.
    Every host calls this first and exits 2 when it is true, so a pod spec that turned the
    automount on fails at once and visibly. Reads the module's DEFAULT_TOKEN_FILE at call time."""
    # The folder too: an unreadable folder or a dangling link hides the file from exists(), and
    # with the automount off the folder does not exist.
    if not (DEFAULT_TOKEN_FILE.exists() or DEFAULT_TOKEN_FILE.parent.exists()):
        return False
    out.write(
        f"{label}: a default ServiceAccount token is mounted at {DEFAULT_TOKEN_FILE}; the pod must "
        "set automountServiceAccountToken: false (decision 0016 section 9 ruling 2)\n"
    )
    return True


# Client loggers kept at WARNING: aio-pika describes messages at DEBUG (E2 report, concern 8) and
# SQLAlchemy echoes every statement at INFO. kubernetes_asyncio writes whole API responses at DEBUG
# (a Secret's apply response carries its data; H5a's finding) and urllib3, the Sentry SDK's
# transport, its requests (task H4).
_QUIET_LOGGERS: Final = (
    "aio_pika",
    "aiormq",
    "aiohttp",
    "httpx2",
    "httpcore",
    "sqlalchemy",
    "kubernetes_asyncio",
    "urllib3",
)


class InvalidSettings(Exception):
    """A configuration that passed its keys' checks but that a component refused while being
    built (its ``validate``). The text names the setting, never a value."""


def report_config(out: TextIO, label: str, err: LoadError) -> None:
    """Every refused key with its reason, one per line; the reasons never contain a value."""
    out.write(f"{label}: configuration refused; {len(err.problems)} key(s) missing or invalid:\n")
    for problem in err.problems:
        out.write(f"  {problem.key}: {problem.reason}\n")


def report_settings(out: TextIO, label: str, err: InvalidSettings) -> None:
    out.write(f"{label}: configuration refused: {err}\n")


def route_standard_logging(
    out: TextIO, level: int, extra: tuple[logging.Handler, ...] = ()
) -> None:
    """Send the standard library's records (the loops, uvicorn, the SDKs) through the filtered
    JSON output, and to ``extra`` (the Sentry reporter's handler, task H4); the clients' own
    loggers (SQLAlchemy echoes statements at INFO) stay at WARNING."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(stdlib_handler(out, level))
    for handler in extra:
        root.addHandler(handler)
    root.setLevel(level)
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(max(level, logging.WARNING))


def timeouts_of(database: DatabaseConfig) -> Timeouts:
    """DB_TX_TIMEOUT, DB_STATEMENT_TIMEOUT and DB_LOCK_TIMEOUT as the stores' bounds."""
    timeouts = Timeouts(
        transaction=database.tx_timeout,
        statement=database.statement_timeout,
        lock=database.lock_timeout,
    )
    try:
        timeouts.validate()
    except ValueError as exc:
        raise InvalidSettings(f"DB_*_TIMEOUT: {exc}") from None
    return timeouts


def engine_settings_of(database: DatabaseConfig) -> EngineSettings:
    """DB_MAX_CONNS connections per login, none beyond; a caller waits at most
    DB_CONNECT_TIMEOUT for one, and the driver's connect timeout is that value in whole seconds,
    rounded up (as ``DatabaseConfig.dsn`` does). A session idle inside a transaction for
    DB_TX_TIMEOUT is ended by the server (task H5b): no transaction of ours lasts longer, so only a
    stalled process holds one that long, and its row locks are freed."""
    seconds = max(1, math.ceil(database.connect_timeout.total_seconds()))
    return EngineSettings(
        pool_size=database.max_conns,
        max_overflow=0,
        pool_timeout=database.connect_timeout,
        connect_timeout=timedelta(seconds=seconds),
        idle_in_transaction_timeout=database.tx_timeout,
    )


def open_role_engine(database: DatabaseConfig, login: DatabaseLogin) -> AsyncEngine:
    """The engine of one role login; opens no connection until used."""
    url = database_url(
        host=database.host,
        port=database.port,
        database=database.name,
        user=login.user,
        password=login.password.reveal(),
    ).update_query_dict({"sslmode": database.ssl_mode})
    return open_engine(url, engine_settings_of(database))


class SystemClock:
    """UTC now, truncated to microseconds by ``datetime`` itself."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class UUIDv7:
    def new_id(self) -> uuid.UUID:
        return uuid.uuid7()


type Loop = Callable[[], Coroutine[Any, Any, None]]


async def run_until_stopped(
    stop: asyncio.Event,
    *,
    first: tuple[Loop, ...],
    then: tuple[Loop, ...],
    logger: logging.Logger,
) -> None:
    """Run the ``first`` loops (connections, such as the broker client) and the ``then`` loops
    (the work that uses them) until ``stop`` is set or a loop ends. On the way out the ``then``
    loops are cancelled first and awaited, so that a consumer can still release its claim and
    requeue its delivery over the open connection; the ``first`` loops follow.

    A loop that ends on its own (it returned or raised) is an error: ``LoopEnded``."""
    ended: list[str] = []

    async def watch(name: str, loop: Loop) -> None:
        try:
            await loop()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # reported below; the process stops
            logger.error("a loop ended with an error (loop=%s error=%s)", name, type(exc).__name__)
            ended.append(name)
        else:
            ended.append(name)
        stop.set()

    async with asyncio.TaskGroup() as outer:
        base = [outer.create_task(watch(_name(loop), loop)) for loop in first]
        async with asyncio.TaskGroup() as inner:
            work = [inner.create_task(watch(_name(loop), loop)) for loop in then]
            await stop.wait()
            for task in work:
                task.cancel()
        for task in base:
            task.cancel()
    if ended:
        raise LoopEnded(", ".join(ended))


class LoopEnded(Exception):
    """A loop ended without a stop request."""


def _name(loop: Loop) -> str:
    return getattr(loop, "__qualname__", type(loop).__name__)


@contextlib.contextmanager
def settings_refused() -> Iterator[None]:
    """Turn a component's ``ValueError`` while being built into ``InvalidSettings``."""
    try:
        yield
    except ValueError as exc:
        raise InvalidSettings(str(exc)) from None
