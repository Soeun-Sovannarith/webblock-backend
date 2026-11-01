"""The dead-letter commands' configuration (task W6-E2): the broker URL and two bounds, every key
required, every problem named by key without its value; the inbox commands' keys are unchanged and
carry no broker setting."""

from __future__ import annotations

from datetime import timedelta

import pytest

from provisioning.infrastructure.config import LoadError, operator_keys
from provisioning.infrastructure.config.loader import required_keys
from provisioning.infrastructure.config.operator import (
    load_operator_broker,
    read_operator_broker,
)

URL = "amqp://operator:broker-secret-value-0001@127.0.0.1:5672/"  # a marker, not a credential
GOOD = {
    "PROVISIONING_PROFILE": "production",  # the operator depends on no open contract (task H2)
    "RABBITMQ_URL": URL,
    "RABBITMQ_DIAL_TIMEOUT": "5s",
    "DLQ_CALL_TIMEOUT": "10s",
}


def test_the_dead_letter_commands_need_the_broker_url_and_two_bounds_only() -> None:
    assert required_keys(read_operator_broker) == (
        "DLQ_CALL_TIMEOUT",
        "PROVISIONING_PROFILE",
        "RABBITMQ_DIAL_TIMEOUT",
        "RABBITMQ_URL",
    )
    assert not any(key.startswith(("PG_", "DB_")) for key in required_keys(read_operator_broker))


def test_the_inbox_commands_still_hold_no_broker_setting() -> None:
    assert not any(key.startswith(("RABBITMQ_", "DLQ_")) for key in operator_keys())


def test_a_good_environment_loads_and_the_url_prints_redacted() -> None:
    config = load_operator_broker({**GOOD, "LOG_LEVEL": "debug"}.get)
    assert config.url.reveal() == URL
    assert config.dial_timeout == timedelta(seconds=5)
    assert config.call_timeout == timedelta(seconds=10)
    assert "broker-secret-value-0001" not in repr(config)
    assert "broker-secret-value-0001" not in str(config)


def test_an_empty_environment_names_every_key() -> None:
    with pytest.raises(LoadError) as caught:
        load_operator_broker(dict[str, str]().get)
    assert caught.value.keys() == required_keys(read_operator_broker)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("RABBITMQ_URL", "http://127.0.0.1:5672/"),
        ("RABBITMQ_URL", "amqp://"),
        ("RABBITMQ_URL", "amqp://user:pass@host\n/"),
        ("RABBITMQ_URL", "amqp://user:pass@[::1/"),
        ("RABBITMQ_DIAL_TIMEOUT", "0s"),
        ("RABBITMQ_DIAL_TIMEOUT", "1h"),
        ("DLQ_CALL_TIMEOUT", "10ms"),
        ("DLQ_CALL_TIMEOUT", "6m"),
        ("DLQ_CALL_TIMEOUT", "soon"),
    ],
)
def test_a_bad_value_is_refused_by_key_without_its_value(key: str, value: str) -> None:
    with pytest.raises(LoadError) as caught:
        load_operator_broker({**GOOD, key: value}.get)
    assert caught.value.keys() == (key,)
    assert "broker-secret-value-0001" not in str(caught.value)
    assert value not in str(caught.value) or len(value) < 3
