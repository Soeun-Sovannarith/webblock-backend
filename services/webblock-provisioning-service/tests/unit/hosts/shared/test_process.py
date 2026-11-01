"""TR-2: the one start sequence of the long-lived processes (``hosts.shared.process``). A refused
key or setting exits 2 by name and reason, never a value; the host's part gets the logger and
the audit log; the observability is closed on every way out."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import pytest

from provisioning.core.application import AuditLog
from provisioning.hosts.shared import runtime
from provisioning.hosts.shared.observability import Observability
from provisioning.hosts.shared.process import run_process
from provisioning.hosts.shared.runtime import EXIT_CONFIG, InvalidSettings
from provisioning.infrastructure.config import DatabaseConfig, LoadError
from provisioning.infrastructure.config.loader import Lookup, Problem
from provisioning.infrastructure.config.telemetry import SentryConfig, TracingConfig
from provisioning.infrastructure.telemetry import Logger
from provisioning.infrastructure.telemetry.metrics import MeteredAuditLog
from provisioning.infrastructure.telemetry.tracing import tracing_enabled

LABEL = "provisioning-test"
MARKER = "mounted-file-marker-0003"


@dataclass(frozen=True, slots=True)
class _Config:
    log_level: int
    database: DatabaseConfig
    sentry: SentryConfig | None
    tracing: TracingConfig | None


def _config() -> _Config:
    return _Config(
        log_level=logging.INFO,
        database=DatabaseConfig(
            host="127.0.0.1",
            port=5432,
            name="provisioning",
            ssl_mode="disable",
            max_conns=2,
            connect_timeout=timedelta(seconds=5),
            tx_timeout=timedelta(seconds=5),
            statement_timeout=timedelta(seconds=2),
            lock_timeout=timedelta(seconds=1),
        ),
        sentry=None,
        tracing=TracingConfig(endpoint="http://127.0.0.1:9", service_name=None),
    )


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """No default token mounted, and the root logger as it was after each test."""
    monkeypatch.setattr(runtime, "DEFAULT_TOKEN_FILE", tmp_path / "absent" / "token")
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    levels = {
        name: logger.level
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)
    for name, saved in levels.items():
        logging.getLogger(name).setLevel(saved)


def _load(_lookup: Lookup) -> _Config:
    return _config()


def _profile(_config: _Config) -> str:
    return "prototype"


def test_tr2_a_refused_configuration_exits_2_naming_every_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refused(_lookup: Lookup) -> _Config:
        raise LoadError((Problem("PG_HOST", "is required"), Problem("LOG_LEVEL", "is invalid")))

    def never(*_args: object) -> int:
        raise AssertionError("the host's part ran")

    assert run_process(LABEL, refused, never, profile_of=_profile) == EXIT_CONFIG
    assert capsys.readouterr().err.splitlines() == [
        f"{LABEL}: configuration refused; 2 key(s) missing or invalid:",
        "  LOG_LEVEL: is invalid",
        "  PG_HOST: is required",
    ]


def test_tr2_the_hosts_part_gets_the_metered_audit_and_its_exit_code_is_returned() -> None:
    seen: list[tuple[AuditLog, bool]] = []

    def run(_config: _Config, _logger: Logger, audit: AuditLog, _obs: Observability) -> int:
        seen.append((audit, tracing_enabled()))
        return 7

    assert run_process(LABEL, _load, run, profile_of=_profile) == 7
    [(audit, tracing_on)] = seen
    assert isinstance(audit, MeteredAuditLog)
    assert tracing_on
    assert not tracing_enabled()  # the observability was closed on the way out


def test_tr2_a_setting_the_hosts_part_refuses_exits_2_and_closes_the_observability(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def run(_config: _Config, _logger: Logger, _audit: AuditLog, _obs: Observability) -> int:
        assert tracing_enabled()
        raise InvalidSettings("WORKER_BATCH_SIZE must be positive")

    assert run_process(LABEL, _load, run, profile_of=_profile) == EXIT_CONFIG
    err = capsys.readouterr().err
    assert f"{LABEL}: configuration refused: WORKER_BATCH_SIZE must be positive\n" in err
    assert not tracing_enabled()


def test_tr2_a_mounted_default_token_exits_2_before_the_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    token = tmp_path / "serviceaccount" / "token"
    token.parent.mkdir()
    token.write_text(MARKER)
    monkeypatch.setattr(runtime, "DEFAULT_TOKEN_FILE", token)

    def load(_lookup: Lookup) -> _Config:
        raise AssertionError("the configuration was loaded")

    assert run_process(LABEL, load, lambda *_: 0, profile_of=_profile) == EXIT_CONFIG
    err = capsys.readouterr().err
    assert "automountServiceAccountToken: false" in err
    assert MARKER not in err
