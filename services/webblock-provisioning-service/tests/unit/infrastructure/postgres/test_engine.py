"""The engine's server-side bound on a stalled transaction (task H5b, lead's ruling): it
frees a project's row lock if a worker stalls inside a transaction.

A process paused inside one of its short transactions (SIGSTOP, a VM or GC pause) keeps its row
locks, and the client-side transaction timeout cannot fire while it is paused. The server's
``idle_in_transaction_session_timeout`` ends such a session, so another worker's claim gets the
lock. Every engine (``open_engine``) sets it as a connection option; the hosts bound it by
DB_TX_TIMEOUT.
"""

from datetime import timedelta
from typing import Any

import pytest

from provisioning.infrastructure.postgres import engine as engine_module
from provisioning.infrastructure.postgres.engine import (
    EngineSettings,
    InvalidEngineSettings,
    database_url,
    open_engine,
)


def _settings(idle: timedelta = timedelta(seconds=30)) -> EngineSettings:
    return EngineSettings(
        pool_size=2,
        max_overflow=0,
        pool_timeout=timedelta(seconds=5),
        connect_timeout=timedelta(seconds=5),
        idle_in_transaction_timeout=idle,
    )


PLACEHOLDER = "placeholder"  # no server is contacted


def _url() -> Any:
    return database_url(host="127.0.0.1", port=5432, database="d", user="u", password=PLACEHOLDER)


def test_every_engine_sets_the_idle_in_transaction_timeout_in_milliseconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    def fake_create(url: Any, **kwargs: Any) -> str:
        seen.update(kwargs)
        return "engine"

    monkeypatch.setattr(engine_module, "create_async_engine", fake_create)
    assert open_engine(_url(), _settings(timedelta(milliseconds=1500))) == "engine"
    connect = seen["connect_args"]
    assert connect["options"] == "-c idle_in_transaction_session_timeout=1500"
    assert connect["connect_timeout"] == 5


@pytest.mark.parametrize(
    "idle",
    [timedelta(0), timedelta(microseconds=500), timedelta(milliseconds=1, microseconds=1)],
)
def test_the_idle_timeout_must_be_a_positive_whole_number_of_milliseconds(
    idle: timedelta,
) -> None:
    with pytest.raises(InvalidEngineSettings, match="idle_in_transaction_timeout"):
        _settings(idle).validate()


def test_the_idle_timeout_fits_postgres_integer_setting() -> None:
    with pytest.raises(InvalidEngineSettings, match="idle_in_transaction_timeout"):
        _settings(timedelta(milliseconds=2**31)).validate()
    _settings(timedelta(milliseconds=2**31 - 1)).validate()
