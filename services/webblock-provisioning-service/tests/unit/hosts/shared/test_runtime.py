"""The hosts' shared runtime (task G): log routing keeps the clients' chatty loggers out of the
JSON output, and engine settings follow the database section."""

from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest

from provisioning.hosts.shared import runtime
from provisioning.hosts.shared.runtime import (
    default_token_refused,
    engine_settings_of,
    route_standard_logging,
)
from provisioning.infrastructure.config import DatabaseConfig


@pytest.fixture
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    names = (
        "sqlalchemy",
        "sqlalchemy.engine.Engine",
        "aio_pika",
        "provisioning.worker",
        "kubernetes_asyncio",
        "urllib3",
    )
    levels = {n: logging.getLogger(n).level for n in names}
    yield
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in handlers:
        root.addHandler(h)
    root.setLevel(level)
    for n, lv in levels.items():
        logging.getLogger(n).setLevel(lv)


def test_sql_statements_and_client_chatter_never_reach_the_log(restore_logging: None) -> None:
    # Found running the processes: at INFO, SQLAlchemy's engine logger wrote every statement.
    out = io.StringIO()
    route_standard_logging(out, logging.INFO)
    logging.getLogger("sqlalchemy.engine.Engine").info("SELECT 1 FROM provisioning.releases")
    logging.getLogger("aio_pika").info("publishing a message")
    logging.getLogger("provisioning.worker").info("worker running")
    text = out.getvalue()
    assert "SELECT" not in text
    assert "publishing" not in text
    assert "worker running" in text


def test_at_debug_no_kubernetes_response_body_or_urllib3_line_reaches_the_log(
    restore_logging: None,
) -> None:
    """H5a's finding: kubernetes_asyncio logs whole API responses at DEBUG ("response body:
    ..."), and a Secret's apply response carries its data. With LOG_LEVEL=debug the root logger is
    at DEBUG, so the client's logger is clamped to WARNING like the other clients; urllib3 (the
    Sentry SDK's transport) too."""
    out = io.StringIO()
    route_standard_logging(out, logging.DEBUG)
    data = "c2l0ZS1wYXNzd29yZA=="  # a Secret's base64 data, invisible to the scrubber
    logging.getLogger("kubernetes_asyncio.client.rest").debug("response body: %s", data)
    logging.getLogger("urllib3.connectionpool").debug("POST /api/42/envelope/ %s", data)
    logging.getLogger("provisioning.worker").debug("worker debug line")
    text = out.getvalue()
    assert data not in text
    assert "worker debug line" in text


def test_a_warning_of_a_client_still_reaches_the_log(restore_logging: None) -> None:
    out = io.StringIO()
    route_standard_logging(out, logging.INFO)
    logging.getLogger("aio_pika").warning("connection lost")
    assert "connection lost" in out.getvalue()


def test_engine_settings_round_the_connect_timeout_up_to_whole_seconds() -> None:
    database = DatabaseConfig(
        host="127.0.0.1",
        port=5432,
        name="provisioning",
        ssl_mode="disable",
        max_conns=8,
        connect_timeout=timedelta(milliseconds=1500),
        tx_timeout=timedelta(seconds=30),
        statement_timeout=timedelta(seconds=10),
        lock_timeout=timedelta(seconds=5),
    )
    settings = engine_settings_of(database)
    assert settings.pool_size == 8
    assert settings.max_overflow == 0
    assert settings.connect_timeout == timedelta(seconds=2)
    settings.validate()


def test_a_mounted_default_token_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = tmp_path / "token"
    token.write_text("x")
    monkeypatch.setattr(runtime, "DEFAULT_TOKEN_FILE", token)
    out = io.StringIO()
    assert default_token_refused(out, "provisioning-api") is True
    assert out.getvalue() == (
        f"provisioning-api: a default ServiceAccount token is mounted at {token}; the pod must "
        "set automountServiceAccountToken: false (decision 0016 section 9 ruling 2)\n"
    )


def test_no_default_token_passes_silently(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime, "DEFAULT_TOKEN_FILE", tmp_path / "absent" / "token")
    out = io.StringIO()
    assert default_token_refused(out, "provisioning-api") is False
    assert out.getvalue() == ""
