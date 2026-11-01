"""The RabbitMQ adapter's configuration and topology (decision 0005 sections 6 and 7).

The names are those of decision 0022 E6. Each consumed queue is a quorum queue that dead-letters
to its own dead-letter queue through a shared dead-letter exchange, with an explicit delivery
limit (the default differs between broker series), a maximum length that rejects further publishes
and a consumer timeout. The broker URL holds credentials and is never printed or logged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final, Literal
from urllib.parse import urlsplit

# The AMQP short-string limit for exchange, queue, routing key and connection names.
MAX_NAME_LEN = 255
_MAX_INT32_MS = 2**31 - 1


class InvalidBrokerConfig(ValueError):
    """A missing or out-of-range value; the text never contains a configured value."""


type ExchangeType = Literal["direct", "topic"]

# The suffix of an owned topic exchange's alternate exchange and of its queue.
UNROUTED_SUFFIX: Final = ".unrouted"


@dataclass(frozen=True, slots=True)
class ExchangeNames:
    """An exchange: its name, its type and whether this process's service owns it. An owned
    exchange is declared (checked only when declaration is off); a foreign one is only ever
    checked passively, because its owner declares it."""

    name: str
    exchange_type: ExchangeType
    owned: bool

    @property
    def alternate_exchange(self) -> str | None:
        """An owned topic exchange's alternate exchange ``<name>.unrouted`` (fanout, with a
        queue of the same name), so that a publish no queue is bound for is kept, not returned;
        None for any other exchange."""
        if self.owned and self.exchange_type == "topic":
            return self.name + UNROUTED_SUFFIX
        return None


@dataclass(frozen=True, slots=True)
class QueueNames:
    exchange: str  # the producer publishes here
    exchange_type: ExchangeType
    exchange_owned: bool  # False: another service declares it; only checked passively
    queue: str  # quorum; the consumer reads it
    routing_keys: tuple[str, ...]  # bind queue to exchange, and the dead-letter queue to the DLX
    dead_letter_exchange: str  # direct, durable, owned; receives rejected and over-limit messages
    dead_letter_queue: str  # quorum; evidence, no consumer

    @property
    def exchange_names(self) -> ExchangeNames:
        return ExchangeNames(self.exchange, self.exchange_type, self.exchange_owned)


# Decision 0022 E6 (Notion "Web Block architecture" 4.2 and 4.4): exchanges ``<service>.events``
# (topic), routing keys ``<event>.v1``, queues ``provisioning.<event>.v1`` (quorum), dead-letter
# queues ``<queue>.dlq``.
PROVISIONING_EVENTS: Final = "provisioning.events"
PROVISIONING_EVENTS_UNROUTED: Final = PROVISIONING_EVENTS + UNROUTED_SUFFIX
PROJECT_EVENTS: Final = "project.events"
PAYMENT_EVENTS: Final = "payment.events"
DEAD_LETTER_EXCHANGE: Final = "provisioning.dlx"

# Provisioning's own events exchange; the usage publisher publishes to it.
PROVISIONING_EVENTS_EXCHANGE: Final = ExchangeNames(
    name=PROVISIONING_EVENTS, exchange_type="topic", owned=True
)
# "publish completed" (decision 0014 section 21 answer 2), published to ``provisioning.events``;
# Payment owns and declares its queue ``payment.publish_completed.v1``.
PUBLISH_COMPLETED_ROUTING_KEY: Final = "publish_completed.v1"


def _event_queue(exchange: str, event: str) -> QueueNames:
    queue = f"provisioning.{event}.v1"
    return QueueNames(
        exchange=exchange,
        exchange_type="topic",
        exchange_owned=False,
        queue=queue,
        routing_keys=(f"{event}.v1",),
        dead_letter_exchange=DEAD_LETTER_EXCHANGE,
        dead_letter_queue=f"{queue}.dlq",
    )


# Command jobs (decision 0005 section 6): Provisioning's internal queue on its own exchange.
COMMAND_JOB_NAMES: Final = QueueNames(
    exchange="provisioning.jobs",
    exchange_type="direct",
    exchange_owned=True,
    queue="provisioning.command_job.v1",
    routing_keys=("command_job.v1",),
    dead_letter_exchange=DEAD_LETTER_EXCHANGE,
    dead_letter_queue="provisioning.command_job.v1.dlq",
)
# Project's events: "source published" (read by the build worker), "project deleted" and "owner
# status changed" (read by the worker).
SOURCE_PUBLISHED_NAMES: Final = _event_queue(PROJECT_EVENTS, "source_published")
PROJECT_DELETED_NAMES: Final = _event_queue(PROJECT_EVENTS, "project_deleted")
OWNER_STATUS_NAMES: Final = _event_queue(PROJECT_EVENTS, "owner_status_changed")
# Payment's hosting entitlement events, read by the worker with one handler.
ENTITLEMENT_ENDED_NAMES: Final = _event_queue(PAYMENT_EVENTS, "hosting_entitlement_ended")
ENTITLEMENT_RESTORED_NAMES: Final = _event_queue(PAYMENT_EVENTS, "hosting_entitlement_restored")

# Every queue Provisioning owns, in this order.
PROVISIONING_QUEUES: Final = (
    COMMAND_JOB_NAMES,
    SOURCE_PUBLISHED_NAMES,
    PROJECT_DELETED_NAMES,
    OWNER_STATUS_NAMES,
    ENTITLEMENT_ENDED_NAMES,
    ENTITLEMENT_RESTORED_NAMES,
)


@dataclass(frozen=True, slots=True)
class QueueLimits:
    queue_max_length: int  # RABBITMQ_QUEUE_MAX_LENGTH
    delivery_limit: int  # RABBITMQ_DELIVERY_LIMIT
    consumer_timeout: timedelta  # RABBITMQ_CONSUMER_TIMEOUT, whole milliseconds
    dlq_max_length: int  # RABBITMQ_DLQ_MAX_LENGTH
    dlq_message_ttl: timedelta  # RABBITMQ_DLQ_MESSAGE_TTL, whole milliseconds


def _valid_name(name: str) -> bool:
    return (
        1 <= len(name) <= MAX_NAME_LEN and name.isprintable() and not any(c.isspace() for c in name)
    )


def _whole_ms(value: timedelta) -> int:
    return value // timedelta(milliseconds=1)


@dataclass(frozen=True, slots=True)
class QueueTopology:
    names: QueueNames
    limits: QueueLimits

    def validate(self) -> None:
        """Printable names without spaces (1 to 255), at least one routing key, a dead-letter
        side distinct from the main one, positive limits that fit the broker's 32-bit
        arguments (``InvalidBrokerConfig``)."""
        n, lim = self.names, self.limits
        names = (n.exchange, n.queue, n.dead_letter_exchange, n.dead_letter_queue, *n.routing_keys)
        if not n.routing_keys or not all(_valid_name(name) for name in names):
            raise InvalidBrokerConfig(
                f"topology names must be 1 to {MAX_NAME_LEN} printable characters without spaces, "
                "with at least one routing key"
            )
        if n.exchange_type not in ("direct", "topic"):
            raise InvalidBrokerConfig("the exchange type must be direct or topic")
        if n.exchange == n.dead_letter_exchange or n.queue == n.dead_letter_queue:
            raise InvalidBrokerConfig(
                "the dead-letter exchange and queue must differ from the main ones"
            )
        if min(lim.queue_max_length, lim.delivery_limit, lim.dlq_max_length) < 1:
            raise InvalidBrokerConfig("queue lengths and the delivery limit must be positive")
        for value in (lim.consumer_timeout, lim.dlq_message_ttl):
            if not 1 <= _whole_ms(value) <= _MAX_INT32_MS:
                raise InvalidBrokerConfig(
                    "consumer timeout and dead-letter TTL must be 1 ms to the broker's 32-bit "
                    "maximum"
                )

    def queue_arguments(self) -> dict[str, str | int]:
        """The work queue: quorum, dead-lettering, an explicit delivery limit, a maximum length
        that rejects further publishes, a consumer timeout."""
        return {
            "x-queue-type": "quorum",
            "x-dead-letter-exchange": self.names.dead_letter_exchange,
            "x-delivery-limit": self.limits.delivery_limit,
            "x-max-length": self.limits.queue_max_length,
            "x-overflow": "reject-publish",
            "x-consumer-timeout": _whole_ms(self.limits.consumer_timeout),
        }

    def dead_letter_arguments(self) -> dict[str, str | int]:
        """The dead-letter queue: quorum, bounded length and message lifetime."""
        return {
            "x-queue-type": "quorum",
            "x-max-length": self.limits.dlq_max_length,
            "x-message-ttl": _whole_ms(self.limits.dlq_message_ttl),
        }


@dataclass(frozen=True, slots=True)
class ExchangeTopology:
    """An exchange the process only publishes to; ``limits`` bound the queue of its alternate
    exchange, which is kept like a dead-letter queue (evidence, no consumer)."""

    names: ExchangeNames
    limits: QueueLimits

    def validate(self) -> None:
        """A valid name and type, and, for an owned topic exchange, a positive bound and lifetime
        for its alternate queue (``InvalidBrokerConfig``)."""
        if not _valid_name(self.names.name) or len(self.names.name) > MAX_NAME_LEN - len(
            UNROUTED_SUFFIX
        ):
            raise InvalidBrokerConfig("exchange names must be printable without spaces")
        if self.names.exchange_type not in ("direct", "topic"):
            raise InvalidBrokerConfig("the exchange type must be direct or topic")
        if (
            self.limits.dlq_max_length < 1
            or not 1 <= _whole_ms(self.limits.dlq_message_ttl) <= _MAX_INT32_MS
        ):
            raise InvalidBrokerConfig("the unrouted queue's length and lifetime must be positive")

    def unrouted_arguments(self) -> dict[str, str | int]:
        """The alternate exchange's queue: quorum, bounded length and message lifetime."""
        return {
            "x-queue-type": "quorum",
            "x-max-length": self.limits.dlq_max_length,
            "x-message-ttl": _whole_ms(self.limits.dlq_message_ttl),
        }


@dataclass(frozen=True, slots=True)
class BrokerConfig:
    """Every value required (decision 0005 section 7)."""

    url: str = field(repr=False)  # RABBITMQ_URL, amqp:// or amqps://; a secret
    heartbeat: timedelta  # RABBITMQ_HEARTBEAT, at least one second
    dial_timeout: timedelta  # RABBITMQ_DIAL_TIMEOUT
    reconnect_base: timedelta  # RABBITMQ_RECONNECT_BACKOFF_BASE
    reconnect_max: timedelta  # RABBITMQ_RECONNECT_BACKOFF_MAX
    declare_topology: bool  # RABBITMQ_DECLARE_TOPOLOGY: declare, or only check passively
    topologies: tuple[QueueTopology, ...]  # the queues this process consumes or publishes to
    publish_exchanges: tuple[ExchangeTopology, ...]  # further exchanges it only publishes to
    max_in_flight_publishes: int  # RABBITMQ_MAX_IN_FLIGHT_PUBLISHES
    connection_name: str  # fixed per process; printable, no spaces

    def validate(self) -> None:
        """``InvalidBrokerConfig`` for a missing or inconsistent value; never quotes the URL."""
        try:
            parts = urlsplit(self.url)
            host = parts.hostname
        except ValueError:
            # The parse error is not chained: it would quote the URL, which holds the password.
            raise InvalidBrokerConfig("the broker URL must be an amqp or amqps URL") from None
        if parts.scheme not in ("amqp", "amqps") or not host:
            raise InvalidBrokerConfig("the broker URL must be an amqp or amqps URL with a host")
        if self.heartbeat < timedelta(seconds=1):
            raise InvalidBrokerConfig("heartbeat must be at least one second")
        if self.dial_timeout <= timedelta(0):
            raise InvalidBrokerConfig("dial timeout must be positive")
        if self.reconnect_base <= timedelta(0) or self.reconnect_max < self.reconnect_base:
            raise InvalidBrokerConfig(
                "reconnect backoff base must be positive and its maximum at least the base"
            )
        if self.max_in_flight_publishes < 1:
            raise InvalidBrokerConfig("in-flight publish limit must be positive")
        if not _valid_name(self.connection_name) or len(self.connection_name) > 200:
            raise InvalidBrokerConfig(
                "connection name must be 1 to 200 printable characters without spaces"
            )
        if not self.topologies and not self.publish_exchanges:
            raise InvalidBrokerConfig("the client needs a queue topology or an exchange")
        for exchange in self.publish_exchanges:
            exchange.validate()
        for topology in self.topologies:
            topology.validate()

    def __str__(self) -> str:
        return f"BrokerConfig(connection_name={self.connection_name}, url=<redacted>)"
