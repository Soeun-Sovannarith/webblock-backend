"""The operator CLI's configuration (decision 0014 section 21 ruling 11).

The operator tool re-drives an inbox event whose budget ran out. It connects as a login that is a
member of ``provisioning_operator`` alone (PG_USER, PG_PASS), with the same server settings and
transaction bounds as every other process (``read_database``). It holds no broker and no cluster
credential.

The dead-letter commands (task W6-E2) read a separate section, ``read_operator_broker``: the broker
URL and two bounds, and no database setting. Each command family loads only its own section.

Task H2: both sections require PROVISIONING_PROFILE. Neither command family depends on an open
contract (the re-drive and the dead-letter queue are Provisioning's own storage), so production
is accepted as it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from provisioning.core.application.shared.profile import Profile
from provisioning.infrastructure.config.api import read_log_level
from provisioning.infrastructure.config.common import amqp_url
from provisioning.infrastructure.config.database import (
    DatabaseConfig,
    DatabaseLogin,
    read_database,
    read_login,
)
from provisioning.infrastructure.config.loader import (
    Loader,
    Lookup,
    load,
    required_keys,
)
from provisioning.infrastructure.config.profile import read_profile
from provisioning.infrastructure.config.secret import Secret


@dataclass(frozen=True, slots=True)
class OperatorConfig:
    """Everything the operator CLI reads from its environment."""

    profile: Profile
    log_level: int
    database: DatabaseConfig
    operator_login: DatabaseLogin


def load_operator(lookup: Lookup) -> OperatorConfig:
    """Read and check the operator configuration; ``LoadError`` names every refused key."""
    return load(lookup, read_operator)


def operator_keys() -> tuple[str, ...]:
    """The required keys of the operator CLI, sorted."""
    return required_keys(read_operator)


def read_operator(loader: Loader) -> OperatorConfig:
    """The operator CLI's section reader."""
    profile = read_profile(loader)
    database = read_database(loader)
    return OperatorConfig(
        profile=profile,
        log_level=read_log_level(loader),
        database=database,
        operator_login=read_login(loader, "PG_USER", "PG_PASS"),
    )


@dataclass(frozen=True, slots=True)
class OperatorBrokerConfig:
    """What the dead-letter commands (``operator dlq ...``, task W6-E2) read: the broker URL, which
    holds the credentials and prints redacted, the bound of the connect and the bound of each
    broker call. They need no database setting, and the inbox commands no broker setting."""

    profile: Profile
    log_level: int
    url: Secret
    dial_timeout: timedelta
    call_timeout: timedelta


def load_operator_broker(lookup: Lookup) -> OperatorBrokerConfig:
    """Read and check the dead-letter commands' configuration; ``LoadError`` names every refused
    key."""
    return load(lookup, read_operator_broker)


def read_operator_broker(loader: Loader) -> OperatorBrokerConfig:
    """The dead-letter commands' section reader: RABBITMQ_URL (amqp or amqps with a host),
    RABBITMQ_DIAL_TIMEOUT (10 ms to 5 min), DLQ_CALL_TIMEOUT (100 ms to 5 min)."""
    return OperatorBrokerConfig(
        profile=read_profile(loader),
        log_level=read_log_level(loader),
        url=loader.secret("RABBITMQ_URL", amqp_url),
        dial_timeout=loader.duration(
            "RABBITMQ_DIAL_TIMEOUT", timedelta(milliseconds=10), timedelta(minutes=5)
        ),
        call_timeout=loader.duration(
            "DLQ_CALL_TIMEOUT", timedelta(milliseconds=100), timedelta(minutes=5)
        ),
    )
