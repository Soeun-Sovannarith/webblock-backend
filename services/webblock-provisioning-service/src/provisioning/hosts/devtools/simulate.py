"""Publishes the prototype's simulated Project and Payment events to RabbitMQ (decision 0014
section 3, "Simulated events"; decision 0022 E4 and E6).

    python -m provisioning.hosts.devtools.simulate source-published --project P --repository-id N \\
        --commit SHA [--generation-request G] [--subdomain S]
    python -m provisioning.hosts.devtools.simulate entitlement-ended --project P [--event-key K] \\
        [--reason R]
    python -m provisioning.hosts.devtools.simulate entitlement-restored --project P [--event-key K]
    python -m provisioning.hosts.devtools.simulate project-deleted --project P
    python -m provisioning.hosts.devtools.simulate owner-status --project P \\
        --status active|suspended --version N --subdomain S
    python -m provisioning.hosts.devtools.simulate declare-producers

The broker URL is read from the environment variable ``WEBBLOCK_SIMULATOR_AMQP_URL`` and never
printed. The events are the v1 events of ``src/contracts/shared/events``, published as their owners
would: Project's on ``project.events`` and Payment's on ``payment.events`` (topic exchanges with
the alternate exchanges ``project.events.unrouted`` and ``payment.events.unrouted``), which the
tool declares, as Project and Payment would. It also declares Payment's queue
``payment.publish_completed.v1``, bound to Provisioning's ``provisioning.events`` with the key
``publish_completed.v1``; that exchange is Provisioning's and is only checked, so the worker must
have started once (until then the tool says so and still publishes). An event no queue is bound
for yet is kept in the exchange's ``.unrouted`` queue, not returned: start the consuming worker
first. ``declare-producers`` publishes nothing: the prototype's run script calls it before the
processes start, so their passive checks find the producers' exchanges, and again once they run,
so Payment's queue exists before a release goes live. On success the tool prints
``event_key=<key>`` (``declared`` for ``declare-producers``): the generation request for "source
published", ``project-deleted`` for "project deleted" (the fixed key of the suspend it becomes,
decision 0014 section 21 answer 4), ``owner-status:<N>`` for "owner status changed" (decision 0021
D9), the Payment event key otherwise. Exit status: 0 when
the broker confirmed the event, 1 when it was not published (no broker or a negative
acknowledgment), 2 for a usage error.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta

import aio_pika
from aio_pika.exceptions import ChannelNotFoundEntity

from provisioning.core.application.hosting.background.hosting_job import DELETION_EVENT_KEY
from provisioning.core.application.hosting.ports import OwnerStatus
from provisioning.core.domain import CommandKind, DomainError, owner_status_key, parse_id
from provisioning.infrastructure.rabbitmq import (
    ENTITLEMENT_ENDED_NAMES,
    ENTITLEMENT_RESTORED_NAMES,
    OWNER_STATUS_NAMES,
    PAYMENT_EVENTS,
    PROJECT_DELETED_NAMES,
    PROJECT_EVENTS,
    PROVISIONING_EVENTS,
    PUBLISH_COMPLETED_ROUTING_KEY,
    SOURCE_PUBLISHED_NAMES,
    BrokerClient,
    BrokerConfig,
    ExchangeNames,
    ExchangeTopology,
    InvalidBrokerConfig,
    PublishFailed,
    PublishReturned,
    QueueLimits,
)
from provisioning.presentation.messaging import EncodedMessage, EventCodecV1
from provisioning.presentation.messaging.codec import publish_encoded

ENV_URL = "WEBBLOCK_SIMULATOR_AMQP_URL"
CONNECT_TIMEOUT_SECONDS = 15.0
CONFIRM_TIMEOUT_SECONDS = 15.0

_EXIT_PUBLISHED = 0
_EXIT_NOT_PUBLISHED = 1
_EXIT_USAGE = 2

_HOSTING_EVENTS = {
    "entitlement-ended": CommandKind.SUSPEND_HOSTING,
    "entitlement-restored": CommandKind.RESUME_HOSTING,
}
_ROUTING_KEYS = {
    CommandKind.SUSPEND_HOSTING: ENTITLEMENT_ENDED_NAMES.routing_keys[0],
    CommandKind.RESUME_HOSTING: ENTITLEMENT_RESTORED_NAMES.routing_keys[0],
}
# The reasons Payment's draft schema allows for "hosting entitlement ended".
ENDED_REASONS = ("trial_ended", "grace_expired", "plan_canceled", "over_website_limit")

# Payment's queue for Provisioning's usage event, as Payment would declare it (decision 0022 E6):
# quorum and bounded, bound to ``provisioning.events`` with ``publish_completed.v1``.
PAYMENT_USAGE_QUEUE = "payment.publish_completed.v1"
PAYMENT_USAGE_QUEUE_MAX_LENGTH = 100_000
_LIMITS = QueueLimits(
    queue_max_length=100_000,
    delivery_limit=5,
    consumer_timeout=timedelta(minutes=30),
    dlq_max_length=10_000,
    dlq_message_ttl=timedelta(days=7),
)


def _uuid(text: str) -> uuid.UUID:
    try:
        return parse_id(text)
    except DomainError:
        raise argparse.ArgumentTypeError("must be a canonical lowercase UUID, not nil") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m provisioning.hosts.devtools.simulate",
        description="Publish a simulated Project or Payment event (prototype stand-ins).",
        epilog=f"The broker URL is read from the environment variable {ENV_URL}.",
    )
    events = parser.add_subparsers(dest="event", required=True)
    source = events.add_parser("source-published", help='Project\'s "source published" event')
    source.add_argument("--project", type=_uuid, required=True)
    source.add_argument("--repository-id", type=int, required=True)
    source.add_argument("--commit", required=True, help="40 or 64 lowercase hexadecimal")
    source.add_argument("--generation-request", type=_uuid, help="default: a new UUIDv7")
    source.add_argument("--subdomain", help="the site's DNS label; default: left out")
    for name in _HOSTING_EVENTS:
        hosting = events.add_parser(name, help=f'Payment\'s "{name.replace("-", " ")}" event')
        hosting.add_argument("--project", type=_uuid, required=True)
        hosting.add_argument("--event-key", help="default: sim-<new UUIDv7>")
        if _HOSTING_EVENTS[name] is CommandKind.SUSPEND_HOSTING:
            hosting.add_argument("--reason", choices=ENDED_REASONS, default="trial_ended")
    deleted = events.add_parser("project-deleted", help='Project\'s "project deleted" event')
    deleted.add_argument("--project", type=_uuid, required=True)
    owner = events.add_parser("owner-status", help='Project\'s "owner status changed" event')
    owner.add_argument("--project", type=_uuid, required=True)
    owner.add_argument("--status", choices=[s.value for s in OwnerStatus], required=True)
    owner.add_argument("--version", type=int, required=True, help="the status version, from 1")
    owner.add_argument("--subdomain", required=True, help="the site's DNS label")
    events.add_parser(
        "declare-producers",
        help="declare project.events, payment.events and Payment's usage queue only",
    )
    return parser


def _build(args: argparse.Namespace, codec: EventCodecV1) -> tuple[str, str, str, EncodedMessage]:
    """(event key, exchange, routing key, message); ``ValueError`` for an invalid event."""
    now = datetime.now(UTC)
    if args.event == "source-published":
        request: uuid.UUID = args.generation_request or uuid.uuid7()
        message = codec.encode_source_published(
            message_id=uuid.uuid7(),
            project_id=args.project,
            generation_request_id=request,
            repository_id=args.repository_id,
            git_commit_sha=args.commit,
            subdomain=args.subdomain,
            occurred_at=now,
        )
        return (
            str(request),
            SOURCE_PUBLISHED_NAMES.exchange,
            SOURCE_PUBLISHED_NAMES.routing_keys[0],
            message,
        )
    if args.event == "project-deleted":
        message = codec.encode_project_deleted(
            message_id=uuid.uuid7(), project_id=args.project, occurred_at=now
        )
        return (
            DELETION_EVENT_KEY,
            PROJECT_DELETED_NAMES.exchange,
            PROJECT_DELETED_NAMES.routing_keys[0],
            message,
        )
    if args.event == "owner-status":
        message = codec.encode_owner_status_changed(
            message_id=uuid.uuid7(),
            project_id=args.project,
            status=OwnerStatus(args.status),
            status_version=args.version,
            subdomain=args.subdomain,
            occurred_at=now,
        )
        return (
            owner_status_key(args.version),
            OWNER_STATUS_NAMES.exchange,
            OWNER_STATUS_NAMES.routing_keys[0],
            message,
        )
    kind = _HOSTING_EVENTS[args.event]
    key: str = args.event_key or f"sim-{uuid.uuid7()}"
    message = codec.encode_hosting_event(
        message_id=uuid.uuid7(),
        event_key=key,
        project_id=args.project,
        kind=kind,
        reason=getattr(args, "reason", None),
        occurred_at=now,
    )
    return key, PAYMENT_EVENTS, _ROUTING_KEYS[kind], message


def producer_config(url: str) -> BrokerConfig:
    """The simulator's client: it declares ``project.events`` and ``payment.events`` (topic, with
    their alternate exchanges) as Project and Payment would, and publishes to them."""
    return BrokerConfig(
        url=url,
        heartbeat=timedelta(seconds=10),
        dial_timeout=timedelta(seconds=10),
        reconnect_base=timedelta(milliseconds=200),
        reconnect_max=timedelta(seconds=2),
        declare_topology=True,
        topologies=(),
        publish_exchanges=tuple(
            ExchangeTopology(names=ExchangeNames(name, "topic", True), limits=_LIMITS)
            for name in (PROJECT_EVENTS, PAYMENT_EVENTS)
        ),
        max_in_flight_publishes=1,
        connection_name="provisioning-simulator",
    )


async def declare_payment_queue(url: str) -> bool:
    """Declare Payment's usage queue and bind it to ``provisioning.events``, as Payment would.
    That exchange is Provisioning's and is only checked: False, and nothing declared, while the
    worker has not declared it yet. Raises on a broker failure."""
    connection = await aio_pika.connect(url, timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        channel = await connection.channel()
        try:
            exchange = await channel.declare_exchange(PROVISIONING_EVENTS, passive=True)
        except ChannelNotFoundEntity:  # the broker closed this channel
            return False
        # The check succeeded, so the same channel stays open for the declarations.
        queue = await channel.declare_queue(
            PAYMENT_USAGE_QUEUE,
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                "x-max-length": PAYMENT_USAGE_QUEUE_MAX_LENGTH,
            },
        )
        await queue.bind(exchange.name, PUBLISH_COMPLETED_ROUTING_KEY)
        return True
    finally:
        await connection.close()


async def _publish(config: BrokerConfig, event: tuple[str, str, EncodedMessage] | None) -> int:
    """Declare the producers' topology, then publish ``event`` (exchange, routing key, message)
    when given."""
    client = BrokerClient(config, logger=logging.getLogger("provisioning.hosts.devtools.simulate"))
    running = asyncio.create_task(client.run())
    try:
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                await client.wait_connected()
        except TimeoutError:
            print("not published: the broker could not be reached", file=sys.stderr)
            return _EXIT_NOT_PUBLISHED
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS):
                if not await declare_payment_queue(config.url):
                    print(
                        "note: provisioning.events does not exist yet, so Payment's queue "
                        f"{PAYMENT_USAGE_QUEUE} is not declared; start the worker first",
                        file=sys.stderr,
                    )
        except Exception as exc:  # the event is still published; the queue waits for a rerun
            print(f"note: Payment's queue not declared ({type(exc).__name__})", file=sys.stderr)
        if event is None:
            return _EXIT_PUBLISHED
        exchange, routing_key, message = event
        try:
            async with asyncio.timeout(CONFIRM_TIMEOUT_SECONDS):
                await publish_encoded(client, exchange, routing_key, message)
        except PublishReturned:  # not expected: the exchange has an alternate exchange
            print("not published: the broker returned the event", file=sys.stderr)
            return _EXIT_NOT_PUBLISHED
        except (PublishFailed, TimeoutError) as exc:
            print(f"not published: {type(exc).__name__}", file=sys.stderr)
            return _EXIT_NOT_PUBLISHED
        return _EXIT_PUBLISHED
    finally:
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    url = os.environ.get(ENV_URL, "")
    if not url:
        parser.error(f"set {ENV_URL} to the broker's amqp:// URL")
    try:
        config = producer_config(url)
        config.validate()
    except InvalidBrokerConfig as exc:
        parser.error(f"{ENV_URL}: {exc}")
    if args.event == "declare-producers":
        status = asyncio.run(_publish(config, None))
        if status == _EXIT_PUBLISHED:
            print("declared")
        return status
    try:
        key, exchange, routing_key, message = _build(args, EventCodecV1(max_body_bytes=4096))
    except ValueError as exc:
        parser.error(str(exc))
    status = asyncio.run(_publish(config, (exchange, routing_key, message)))
    if status == _EXIT_PUBLISHED:
        print(f"event_key={key}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
