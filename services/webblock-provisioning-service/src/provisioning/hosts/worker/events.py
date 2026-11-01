"""The worker's event side (task G; moved out of the wiring in wave 6 task W6-S; wave 6 task W6-E):

- Payment's hosting events, "hosting entitlement ended" and "restored" (two queues, one handler),
  through the in-process entitlement record and the hosting intake (E2, F2);
- Project's "project deleted" event, a ``suspend_hosting`` command with the fixed key
  ``project-deleted`` through the same intake (decision 0014 section 21 answer 4);
- Project's "owner status changed" event, a hosting command with the key
  ``owner-status:<version>`` through the owner-status intake on the same login (decision 0021 D9,
  decision 0022 Task 5);
- the hosting retry sweep, which retries a failed suspend or resume within its budget (QA-03),
  every ``RECONCILE_INTERVAL`` for at most ``RECONCILE_BATCH`` projects;
- the usage publisher, which sends the "publish completed" event of each release that went live
  from the outbox (section 21 answer 2), every ``USAGE_PUBLISH_INTERVAL`` for at most
  ``USAGE_BATCH_SIZE`` events, with the dispatcher's confirm and visibility timeouts and backoff.

``build_events`` builds them before the broker client exists, because the client must know every
queue it declares: ``EventParts.queue_names`` (and ``publish_exchanges``) go into the client's
configuration, and ``EventParts.loops(client)`` subscribes the consumers and returns the loops the
process runs after the client's connection loop. The events are v1 on Notion's broker names
(decision 0022 E4 and E6): the worker declares ``provisioning.events`` with its alternate exchange
and publishes the usage event there; Payment owns and declares its queue, so the worker declares
no Payment queue.

Audit: the consumers, the retry and the usage publisher write audit events through the
structured logger (``LoggerAuditLog``), built here on the process's standard error at the
configured level, as ``__main__`` builds its own.

Two logins: the hosting intake's (``provisioning_hosting_intake``: the commands of the events, the
retry and the deletion lookup) and the worker's (``provisioning_worker``: the usage outbox).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy.ext.asyncio import AsyncEngine

from provisioning.core.application import (
    AcceptHostingEventCommand,
    AcceptHostingEventHandler,
    AcceptOwnerStatusCommand,
    AcceptOwnerStatusHandler,
    Clock,
    CommandBus,
    IdSource,
)
from provisioning.core.application.hosting.background.retry import HostingRetry
from provisioning.core.application.hosting.background.retry_sweeper import (
    HostingRetrySweeper,
    RetrySweepConfig,
)
from provisioning.core.application.usage.background.usage_publisher import (
    UsagePublisher,
    UsagePublisherConfig,
)
from provisioning.hosts.shared.buses import require_handlers
from provisioning.hosts.shared.messaging import backoff
from provisioning.hosts.shared.stand_ins import PaymentEventRecord, build_payment_event_record
from provisioning.hosts.worker.payments import RecordingHostingEvents
from provisioning.infrastructure.config.worker import PROTOTYPE_PAYMENT_EVENTS, WorkerProcessConfig
from provisioning.infrastructure.postgres import HostingIntakeStore, Timeouts
from provisioning.infrastructure.postgres.hosting_intake import PgHostingHistory
from provisioning.infrastructure.postgres.usage_outbox import PgUsageOutbox
from provisioning.infrastructure.rabbitmq import (
    ENTITLEMENT_ENDED_NAMES,
    ENTITLEMENT_RESTORED_NAMES,
    OWNER_STATUS_NAMES,
    PROJECT_DELETED_NAMES,
    PROVISIONING_EVENTS_EXCHANGE,
    PUBLISH_COMPLETED_ROUTING_KEY,
    BrokerClient,
    ExchangeNames,
    QueueNames,
)
from provisioning.infrastructure.telemetry import LoggerAuditLog, new_logger
from provisioning.presentation.messaging import EventCodecV1, UsageEventPublisher
from provisioning.presentation.messaging.consumers.messages import EventConsumerConfig
from provisioning.presentation.messaging.consumers.payment_events import (
    OwnerStatusConsumer,
    PaymentEventConsumer,
    ProjectDeletionConsumer,
)

# The queues the event side consumes and the exchange it publishes to (decision 0022 E6).
EVENT_QUEUES: Final[tuple[QueueNames, ...]] = (
    PROJECT_DELETED_NAMES,
    OWNER_STATUS_NAMES,
    ENTITLEMENT_ENDED_NAMES,
    ENTITLEMENT_RESTORED_NAMES,
)
EVENT_PUBLISH_EXCHANGES: Final[tuple[ExchangeNames, ...]] = (PROVISIONING_EVENTS_EXCHANGE,)

# The in-process entitlement record remembers at most this many projects (oldest forgotten).
MAX_RECORDED_PROJECTS: Final = 100_000

WORKER_COMMANDS: Final[tuple[type, ...]] = (AcceptHostingEventCommand, AcceptOwnerStatusCommand)
"""The commands the worker's event consumers send; each needs a handler at start."""

type Loop = Callable[[], Coroutine[Any, Any, None]]
"""One of the process's long-running loops, as ``run_until_stopped`` takes them."""


@dataclass(frozen=True, slots=True)
class EventParts:
    """The event side of the worker process.

    ``queue_names`` are the queues the broker client declares (or checks), all consumed;
    ``publish_exchanges`` the exchanges it only publishes to. ``loops(client)`` subscribes each
    consumer on ``client`` and returns, in order, each subscription's loop and then its
    consumer's loop, then the retry sweep and the usage publisher. ``record`` is the entitlement
    record that the hosting executor reads; it knows deleted projects from the database."""

    queue_names: tuple[QueueNames, ...]
    publish_exchanges: tuple[ExchangeNames, ...]
    loops: Callable[[BrokerClient], tuple[Loop, ...]]
    record: PaymentEventRecord


def build_events(
    config: WorkerProcessConfig,
    *,
    hosting_engine: AsyncEngine,
    worker_engine: AsyncEngine,
    timeouts: Timeouts,
    clock: Clock,
    ids: IdSource,
) -> EventParts:
    """Build the event consumers, the retry sweep and the usage publisher. Opens no socket and no
    connection; a component that refuses its settings raises its own error, which the caller
    turns into ``InvalidSettings``. Prototype only: in production (task H2) the worker builds
    none of them, and ``StandinNotAvailable`` is raised here if it were asked to."""
    history = PgHostingHistory(hosting_engine, timeouts)
    record = build_payment_event_record(
        config.profile,
        PROTOTYPE_PAYMENT_EVENTS,
        max_projects=MAX_RECORDED_PROJECTS,
        deletions=history,
    )
    hosting_store = HostingIntakeStore(hosting_engine, timeouts)
    intake = AcceptHostingEventHandler(
        store=hosting_store,
        clock=clock,
        ids=ids,
        command_deadline=config.command_deadline,
    )
    audit = LoggerAuditLog(new_logger(sys.stderr, config.log_level))
    owner_status_logger = logging.getLogger("provisioning.owner_status_events")
    # The worker's command bus: the commands its event consumers send (decision 0025 CQ11);
    # checked before anything starts.
    commands = CommandBus()
    commands.register(AcceptHostingEventCommand, intake)
    commands.register(
        AcceptOwnerStatusCommand,
        AcceptOwnerStatusHandler(
            store=hosting_store,
            clock=clock,
            ids=ids,
            command_deadline=config.command_deadline,
            audit=audit,
            logger=owner_status_logger,
        ),
    )
    require_handlers(commands, WORKER_COMMANDS, process="worker")
    retry = HostingRetry(
        history=history, intake=intake, logger=logging.getLogger("provisioning.hosting_retry")
    )
    codec = EventCodecV1(max_body_bytes=config.broker.max_message_bytes)
    c, p = config.consumer, config.payment
    consumer_config = EventConsumerConfig(
        concurrency=p.concurrency,
        store_hold=backoff(c.store_hold),
        store_failure_limit=c.store_failure_limit,
    )
    payments = PaymentEventConsumer(
        consumer_config,
        decoder=codec,
        commands=RecordingHostingEvents(commands, record),
        retries=retry,
        audit=audit,
        clock=clock,
        logger=logging.getLogger("provisioning.payment_events"),
        retry_backoff=backoff(p.retry_backoff),
        max_retries=p.max_retries,
    )
    deletions = ProjectDeletionConsumer(
        consumer_config,
        decoder=codec,
        commands=commands,
        retries=retry,
        audit=audit,
        clock=clock,
        logger=logging.getLogger("provisioning.project_events"),
        retry_backoff=backoff(p.retry_backoff),
        max_retries=p.max_retries,
    )
    owner_statuses = OwnerStatusConsumer(
        consumer_config,
        decoder=codec,
        commands=commands,
        retries=retry,
        audit=audit,
        clock=clock,
        logger=owner_status_logger,
        retry_backoff=backoff(p.retry_backoff),
        max_retries=p.max_retries,
    )
    sweeper = HostingRetrySweeper(
        RetrySweepConfig(
            interval=config.reconcile.interval, batch_size=config.reconcile.batch_size
        ),
        retries=retry,
        audit=audit,
        clock=clock,
        logger=logging.getLogger("provisioning.hosting_retry"),
    )
    d = config.dispatch
    usage_config = UsagePublisherConfig(
        interval=config.usage.publish_interval,
        batch_size=config.usage.batch_size,
        confirm_timeout=d.confirm_timeout,
        visibility_timeout=d.visibility_timeout,
        backoff=backoff(d.backoff),
    )
    usage_config.validate()
    outbox = PgUsageOutbox(worker_engine, timeouts)

    def loops(client: BrokerClient) -> tuple[Loop, ...]:
        ended_events = client.subscribe(ENTITLEMENT_ENDED_NAMES.queue, prefetch=p.concurrency)
        restored_events = client.subscribe(ENTITLEMENT_RESTORED_NAMES.queue, prefetch=p.concurrency)
        deleted_events = client.subscribe(PROJECT_DELETED_NAMES.queue, prefetch=p.concurrency)
        owner_status_events = client.subscribe(OWNER_STATUS_NAMES.queue, prefetch=p.concurrency)
        usage = UsagePublisher(
            usage_config,
            outbox=outbox,
            sender=UsageEventPublisher(
                codec=codec,
                raw=client,
                exchange=PROVISIONING_EVENTS_EXCHANGE.name,
                routing_key=PUBLISH_COMPLETED_ROUTING_KEY,
            ),
            ids=ids,
            audit=audit,
            clock=clock,
            logger=logging.getLogger("provisioning.usage_publisher"),
        )

        # One handler for both Payment queues: the AMQP type picks the kind.
        async def consume_ended() -> None:
            await payments.run(ended_events)

        async def consume_restored() -> None:
            await payments.run(restored_events)

        async def consume_deletions() -> None:
            await deletions.run(deleted_events)

        async def consume_owner_statuses() -> None:
            await owner_statuses.run(owner_status_events)

        return (
            ended_events.run,
            consume_ended,
            restored_events.run,
            consume_restored,
            deleted_events.run,
            consume_deletions,
            owner_status_events.run,
            consume_owner_statuses,
            sweeper.run,
            usage.run,
        )

    return EventParts(
        queue_names=EVENT_QUEUES,
        publish_exchanges=EVENT_PUBLISH_EXCHANGES,
        loops=loops,
        record=record,
    )
