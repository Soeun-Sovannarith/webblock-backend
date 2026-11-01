"""The entry point of the dead-letter commands: they load only the broker section, so a missing
or invalid setting names every broker key (and no database key), never a value, and exits 2 before
any connection; an unreachable broker exits 1 without the URL."""

from __future__ import annotations

import pytest

from provisioning.hosts.operator.__main__ import main
from provisioning.infrastructure.config.loader import required_keys
from provisioning.infrastructure.config.operator import read_operator_broker

SECRET = "broker-secret-value-0002"  # noqa: S105 - a marker, not a credential


def test_an_empty_environment_names_every_broker_key_and_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["dlq", "list"], environ={}) == 2
    err = capsys.readouterr().err
    for key in required_keys(read_operator_broker):
        assert key in err
    assert "PG_PASS" not in err and "PG_USER" not in err


def test_the_broker_url_is_never_printed(capsys: pytest.CaptureFixture[str]) -> None:
    environ = {
        "RABBITMQ_URL": f"http://user:{SECRET}@127.0.0.1:1/",
        "RABBITMQ_DIAL_TIMEOUT": "1s",
        "DLQ_CALL_TIMEOUT": "nonsense",
    }
    assert main(["dlq", "list"], environ=environ) == 2
    captured = capsys.readouterr()
    assert SECRET not in captured.err + captured.out
    assert "RABBITMQ_URL" in captured.err and "DLQ_CALL_TIMEOUT" in captured.err


def test_an_unreachable_broker_exits_1_without_the_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    environ = {
        # Port 1 on the loopback refuses at once; nothing listens there.
        "RABBITMQ_URL": f"amqp://user:{SECRET}@127.0.0.1:1/",
        "PROVISIONING_PROFILE": "prototype",
        "RABBITMQ_DIAL_TIMEOUT": "2s",
        "DLQ_CALL_TIMEOUT": "2s",
    }
    assert main(["dlq", "list"], environ=environ) == 1
    captured = capsys.readouterr()
    assert "broker_unreachable" in captured.err
    assert SECRET not in captured.err + captured.out


def test_a_redrive_on_an_unreachable_broker_is_one_failed_audit_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    environ = {
        "RABBITMQ_URL": f"amqp://user:{SECRET}@127.0.0.1:1/",
        "PROVISIONING_PROFILE": "prototype",
        "RABBITMQ_DIAL_TIMEOUT": "2s",
        "DLQ_CALL_TIMEOUT": "2s",
    }
    argv = [
        "dlq",
        "redrive",
        "--queue",
        "provisioning.hosting_entitlement_ended.v1.dlq",
        "--message-id",
        "m-1",
        "--by",
        "alice",
    ]
    assert main(argv, environ=environ) == 1
    err = capsys.readouterr().err
    audit = [line for line in err.splitlines() if '"audit": true' in line or '"audit":true' in line]
    assert len(audit) == 1, err
    assert '"dlq_redrive"' in audit[0] and '"broker_unreachable"' in audit[0]
    assert SECRET not in err
