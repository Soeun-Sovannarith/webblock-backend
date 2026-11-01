"""Sections shared by the worker, build worker and bootstrap processes (task G, decision 0014
section 3): the broker, a Kubernetes connection (KUBE_CONNECTION: a project-local kubeconfig on the
host, the projected token in a pod), a bounded backoff and the extra checks a database
login pool needs. Every key is required (OD-15); a reason never contains a value.

The sections return plain values. The hosts turn them into the adapters' and loops' own
configuration objects, whose ``validate`` methods are the final check; this package imports
neither the RabbitMQ adapter nor the worker loops.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Final
from urllib.parse import urlsplit

from provisioning.core.application.shared.profile import Profile
from provisioning.infrastructure.config.database import DatabaseLogin
from provisioning.infrastructure.config.identity import absolute_path
from provisioning.infrastructure.config.loader import Loader, has_control
from provisioning.infrastructure.config.secret import Secret


@dataclass(frozen=True, slots=True)
class BackoffSettings:
    """A bounded backoff: a positive base and a cap at least the base."""

    base: timedelta
    max: timedelta


@dataclass(frozen=True, slots=True)
class BrokerSettings:
    """The RabbitMQ connection and the limits of the queues a process declares (decision 0005
    section 7). ``url`` carries the credentials and prints redacted."""

    url: Secret
    heartbeat: timedelta
    dial_timeout: timedelta
    reconnect: BackoffSettings
    declare_topology: bool
    max_in_flight_publishes: int
    max_message_bytes: int
    queue_max_length: int
    delivery_limit: int
    consumer_timeout: timedelta
    dlq_max_length: int
    dlq_message_ttl: timedelta


KUBE_CONNECTION_KEY: Final = "KUBE_CONNECTION"
KUBECONFIG_CONNECTION: Final = "kubeconfig"
IN_CLUSTER_CONNECTION: Final = "in-cluster"
_IN_CLUSTER_KEYS: Final = ("KUBE_TOKEN_FILE", "KUBE_CA_FILE", "KUBE_EXPECTED_USERNAME")
_SERVICE_ACCOUNT = re.compile(
    r"system:serviceaccount:[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?:[a-z0-9]([-.a-z0-9]{0,251}[a-z0-9])?",
    re.ASCII,
)


@dataclass(frozen=True, slots=True)
class KubeconfigConnection:
    """The process runs on the host (prototype profile only): an explicit, project-local
    kubeconfig, which the client refuses unless it names a local Kind or k3d cluster on
    127.0.0.1."""

    path: Path


@dataclass(frozen=True, slots=True)
class InClusterConnection:
    """The process runs in a pod (decision 0016 section 9 ruling 2): the explicitly mounted,
    projected ServiceAccount token and the cluster's CA, and the username a SelfSubjectReview
    must return at start."""

    token_file: Path
    ca_file: Path
    expected_username: str


@dataclass(frozen=True, slots=True)
class ClusterSettings:
    """One Kubernetes identity: how the process connects, and the bound of each API request."""

    connection: KubeconfigConnection | InClusterConnection
    request_timeout: timedelta


def read_backoff(loader: Loader, base_key: str, max_key: str) -> BackoffSettings:
    """``base_key`` and ``max_key``, each from 1ms to 1h, the maximum at least the base."""
    base = loader.duration(base_key, timedelta(milliseconds=1), timedelta(hours=1))
    cap = loader.duration(max_key, timedelta(milliseconds=1), timedelta(hours=1))
    if loader.ok(base_key, max_key) and cap < base:
        loader.fail(max_key, f"must be at least {base_key}")
    return BackoffSettings(base=base, max=cap)


def check_store_hold(
    loader: Loader, max_key: str, hold: BackoffSettings, broker: BrokerSettings
) -> None:
    """Decision 0023 G8d: a consumer holds a failed delivery up to ``max_key`` before it settles
    it, and the broker closes the channel of a delivery left unsettled for
    ``RABBITMQ_CONSUMER_TIMEOUT``; so the hold's maximum must be below the timeout. The reason
    names both keys, never a value."""
    if loader.ok(max_key, "RABBITMQ_CONSUMER_TIMEOUT") and hold.max >= broker.consumer_timeout:
        loader.fail(max_key, "must be below RABBITMQ_CONSUMER_TIMEOUT")


def distinct_logins(loader: Loader, *logins: tuple[str, DatabaseLogin]) -> None:
    """Each login is a member of one role, so no two keys may name the same user; the later key
    is reported."""
    seen: dict[str, str] = {}
    for key, login in logins:
        if not loader.ok(key):
            continue
        first = seen.get(login.user)
        if first is not None:
            loader.fail(key, f"must name another login than {first}: each login has one role")
            continue
        seen[login.user] = key


def read_broker(loader: Loader) -> BrokerSettings:
    """RABBITMQ_URL (amqp or amqps), RABBITMQ_HEARTBEAT, RABBITMQ_DIAL_TIMEOUT, the reconnect
    backoff, RABBITMQ_DECLARE_TOPOLOGY, RABBITMQ_MAX_IN_FLIGHT_PUBLISHES,
    RABBITMQ_MAX_MESSAGE_BYTES and the queue limits (RABBITMQ_DELIVERY_LIMIT is 5 when unset,
    decision 0022 E6)."""
    return BrokerSettings(
        url=loader.secret("RABBITMQ_URL", amqp_url),
        heartbeat=loader.duration("RABBITMQ_HEARTBEAT", timedelta(seconds=1), timedelta(hours=1)),
        dial_timeout=loader.duration(
            "RABBITMQ_DIAL_TIMEOUT", timedelta(milliseconds=10), timedelta(minutes=5)
        ),
        reconnect=read_backoff(
            loader, "RABBITMQ_RECONNECT_BACKOFF_BASE", "RABBITMQ_RECONNECT_BACKOFF_MAX"
        ),
        declare_topology=loader.boolean("RABBITMQ_DECLARE_TOPOLOGY"),
        max_in_flight_publishes=loader.integer("RABBITMQ_MAX_IN_FLIGHT_PUBLISHES", 1, 10_000),
        max_message_bytes=loader.integer("RABBITMQ_MAX_MESSAGE_BYTES", 256, 1 << 20),
        queue_max_length=loader.integer("RABBITMQ_QUEUE_MAX_LENGTH", 1, 10_000_000),
        delivery_limit=loader.optional_integer("RABBITMQ_DELIVERY_LIMIT", 5, 1, 1_000),
        consumer_timeout=loader.duration(
            "RABBITMQ_CONSUMER_TIMEOUT", timedelta(seconds=1), timedelta(days=1)
        ),
        dlq_max_length=loader.integer("RABBITMQ_DLQ_MAX_LENGTH", 1, 10_000_000),
        dlq_message_ttl=loader.duration(
            "RABBITMQ_DLQ_MESSAGE_TTL", timedelta(seconds=1), timedelta(days=24)
        ),
    )


def read_cluster(loader: Loader, profile: Profile, kubeconfig_key: str) -> ClusterSettings:
    """KUBE_CONNECTION, KUBE_REQUEST_TIMEOUT and the keys of the chosen connection (decision 0016
    section 9 ruling 2):
    - ``kubeconfig``: ``kubeconfig_key``, an absolute path; the in-cluster keys must be unset;
      refused in production;
    - ``in-cluster``: KUBE_TOKEN_FILE and KUBE_CA_FILE, absolute paths, and KUBE_EXPECTED_USERNAME,
      ``system:serviceaccount:<namespace>:<name>``; ``kubeconfig_key`` must be unset.
    A missing or unknown mode reads the kubeconfig key as the required one, so an empty environment
    names both."""
    mode = loader.choice(KUBE_CONNECTION_KEY, (KUBECONFIG_CONNECTION, IN_CLUSTER_CONNECTION))
    connection: KubeconfigConnection | InClusterConnection
    if mode == IN_CLUSTER_CONNECTION:
        if loader.optional(kubeconfig_key) is not None:
            loader.fail(
                kubeconfig_key,
                f"must be unset unless {KUBE_CONNECTION_KEY} is {KUBECONFIG_CONNECTION}",
            )
        connection = InClusterConnection(
            token_file=Path(loader.text("KUBE_TOKEN_FILE", absolute_path)),
            ca_file=Path(loader.text("KUBE_CA_FILE", absolute_path)),
            expected_username=loader.text("KUBE_EXPECTED_USERNAME", _service_account),
        )
    else:
        if mode == KUBECONFIG_CONNECTION and profile is Profile.PRODUCTION:
            loader.fail(
                KUBE_CONNECTION_KEY,
                f"must be {IN_CLUSTER_CONNECTION} in production (decision 0016 section 9 ruling 2)",
            )
        if mode:
            for key in _IN_CLUSTER_KEYS:
                if loader.optional(key) is not None:
                    loader.fail(
                        key,
                        f"must be unset unless {KUBE_CONNECTION_KEY} is {IN_CLUSTER_CONNECTION}",
                    )
        connection = KubeconfigConnection(Path(loader.text(kubeconfig_key, absolute_path)))
    return ClusterSettings(
        connection=connection,
        request_timeout=loader.duration(
            "KUBE_REQUEST_TIMEOUT", timedelta(milliseconds=100), timedelta(minutes=5)
        ),
    )


def _service_account(value: str) -> str | None:
    if _SERVICE_ACCOUNT.fullmatch(value) is None:
        return "must be system:serviceaccount:<namespace>:<name>"
    return None


def amqp_url(value: str) -> str | None:
    """The broker URL check of every process that reads RABBITMQ_URL: amqp or amqps with a host,
    no control character, at most 2048 characters; the reason never quotes the value."""
    reason = "must be an amqp or amqps URL with a host"
    if has_control(value) or len(value) > 2048:
        return reason
    try:
        parts = urlsplit(value)
        host = parts.hostname
    except ValueError:
        return reason
    if parts.scheme not in ("amqp", "amqps") or not host:
        return reason
    return None
