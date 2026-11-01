"""The build worker process built from its configuration with constructors only (task G;
decisions 0008 and 0014 sections 3, 10.2, 10.3 and 10.5).

It runs:
- E2's "source published" consumer, which stores each event in the inbox (``provisioning_intake``)
  and wakes the processor;
- E2's inbox processor over B1's release intake, which confirms the repository with the prototype
  repository file, accepts the release and starts its build (``StartBuild``) on Jenkins;
- E2's build poller over B1's ``PollBuilds``, ``StartPendingBuilds`` (the pending attempts nothing
  started, QA-10) and ``BuildAttempts`` sweep, which cancels an overdue attempt's build
  (``provisioning_build``).

- the build hint listener (task H2, decision 0016 section 5 item 2): the API's build_release
  sends a PostgreSQL notification, and ``HintLoop`` starts that release at once through the same
  ``StartBuild``; a lost hint waits for ``StartPendingBuilds``.

Every build mutation is audited through the ``AuditLog`` the host passes, as actor
``build-worker``. It holds no cluster credential. Only this process holds the Jenkins token and
the ``provisioning_build`` login.

Production (task H2), decided per part:
- the "source published" consumer and the inbox processor are not run: Project's event (OD-02,
  its producer identity) and the repository confirmation (OD-01) are open, the event schema is a
  draft (decision 0022 E2) and the repository file is a prototype stand-in; no broker
  connection is opened;
- no build is started: neither ``StartPendingBuilds`` nor the hint listener runs
  (``NoPendingStarts``), because a build needs the repository's clone URL from Project (OD-01);
  the closed ``ClosedRepositories`` answers ``ContractNotDecided`` if anything asked it;
- the poll of running builds and the deadline sweep run as in the prototype: they need no open
  contract (Jenkins is decided, decision 0016 section 2), and with nothing started they find
  nothing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Final

import httpx2
from sqlalchemy.ext.asyncio import AsyncEngine

from provisioning.core.application import (
    AuditLog,
    BuildAttempts,
    CommandBus,
    PollBuilds,
    PollRoundParams,
    ReceiveSourcePublishedCommand,
    ReceiveSourcePublishedHandler,
    ReleaseIntake,
    StartBuild,
    SweepRoundParams,
)
from provisioning.core.application.builds.background.build_poller import (
    BuildPoller,
    BuildPollerConfig,
)
from provisioning.core.application.builds.background.hints import HintLoop
from provisioning.core.application.builds.background.poll_builds import (
    PendingStartResult,
    StartPendingBuilds,
)
from provisioning.core.application.builds.background.start_build import WantedBuilds
from provisioning.core.application.intake.background.inbox_processor import (
    InboxProcessor,
    InboxProcessorConfig,
)
from provisioning.core.application.ports.build_system import BuildSystem
from provisioning.core.application.ports.metrics import BuildRoundMetrics
from provisioning.core.application.shared.profile import Profile
from provisioning.core.domain import BuildAttemptPolicy
from provisioning.hosts.shared.buses import require_handlers
from provisioning.hosts.shared.messaging import backoff, broker_config
from provisioning.hosts.shared.runtime import (
    Loop,
    SystemClock,
    UUIDv7,
    run_until_stopped,
    settings_refused,
    timeouts_of,
)
from provisioning.hosts.shared.stand_ins import Repositories
from provisioning.infrastructure.config import JenkinsConfig
from provisioning.infrastructure.config.build_worker import BuildWorkerConfig
from provisioning.infrastructure.jenkins.build_system import ApiToken, JenkinsBuildSystem
from provisioning.infrastructure.postgres import PgReleaseIntakeStore
from provisioning.infrastructure.postgres.build_store import PgBuildHintListener, PgBuildStore
from provisioning.infrastructure.rabbitmq import SOURCE_PUBLISHED_NAMES, BrokerClient, QueueNames
from provisioning.presentation.messaging import EventCodecV1
from provisioning.presentation.messaging.consumers.inbox import SourcePublishedConsumer
from provisioning.presentation.messaging.consumers.messages import EventConsumerConfig

CONNECTION_NAME = "provisioning-build-worker"
# The queue the build worker consumes: Project's "source published" v1 (decision 0022 E6).
BROKER_QUEUES: Final[tuple[QueueNames, ...]] = (SOURCE_PUBLISHED_NAMES,)
AUDIT_ACTOR = "build-worker"
BUILD_WORKER_COMMANDS: Final[tuple[type, ...]] = (ReceiveSourcePublishedCommand,)
"""The commands the build worker's consumers send; each needs a handler at start."""


class NoPendingStarts:
    """The production build worker's pending-start half (task H2): it starts nothing, because a
    build needs Project's repository confirmation (OD-01)."""

    async def start_round(self, params: PollRoundParams) -> list[PendingStartResult]:
        del params  # the port's signature; nothing is started, so no round is read
        return []


def jenkins_build_system(j: JenkinsConfig) -> JenkinsBuildSystem:
    """C's Jenkins adapter from the JENKINS_* keys. Built here, in the one host that holds the
    Jenkins token, and not in ``hosts.shared.runtime``, which the API imports (finding F-6 of
    H-V)."""
    return JenkinsBuildSystem(
        base_url=j.url,
        user=j.user,
        api_token=ApiToken(j.api_token.reveal()),
        job=j.job,
        timeout=httpx2.Timeout(j.request_timeout.total_seconds()),
        operation_timeout=j.operation_timeout.total_seconds(),
        queue_polls=j.queue_polls,
        queue_poll_interval=j.queue_poll_interval.total_seconds(),
        recent_builds=j.recent_builds,
    )


@dataclass(frozen=True, slots=True)
class BuildWorkerProcess:
    """A built build worker; ``serve`` it once. ``first`` are the connection loops (the broker
    client, prototype only), ``then`` the work that uses them; ``parts`` names each part of
    ``then`` in order, for the log and the tests."""

    first: tuple[Loop, ...]
    then: tuple[Loop, ...]
    parts: tuple[str, ...]
    logger: logging.Logger

    async def serve(self, stop: asyncio.Event, *, also: tuple[Loop, ...] = ()) -> None:
        """Run ``first`` (and ``also``, the operational listener of task H4), then ``then``,
        until ``stop`` is set."""
        self.logger.info("build worker running (parts=%s)", ",".join(self.parts))
        await run_until_stopped(
            stop, first=(*self.first, *also), then=self.then, logger=self.logger
        )


def build_build_worker(
    config: BuildWorkerConfig,
    *,
    intake_engine: AsyncEngine,
    build_engine: AsyncEngine,
    build_system: BuildSystem,
    repositories: Repositories,
    audit: AuditLog,
    metrics: BuildRoundMetrics | None = None,
) -> BuildWorkerProcess:
    """Build the process; ``InvalidSettings`` when a component refuses its settings. Opens no
    socket and no connection. ``repositories`` is the prototype's repository file, or
    ``ClosedRepositories`` in production. ``metrics`` counts the build rounds' results
    (task H4)."""
    production = config.profile is Profile.PRODUCTION
    ids = UUIDv7()
    timeouts = timeouts_of(config.database)
    inbox, poll = config.inbox, config.poller
    with settings_refused():
        policy = BuildAttemptPolicy(max_attempts=config.build.max_attempts)
        policy.validate()
        builds = PgBuildStore(build_engine, timeouts)
        start = StartBuild(
            store=builds,
            build_system=build_system,
            repositories=repositories,
            ids=ids,
            logger=logging.getLogger("provisioning.build"),
            policy=policy,
            image_registry=config.build.image_registry,
            build_duration=config.build.duration,
            call_timeout=config.build.call_timeout,
            audit=audit,
            actor=AUDIT_ACTOR,
        )
        intake_store = PgReleaseIntakeStore(
            intake_engine, timeouts, max_attempts=inbox.max_attempts
        )
        intake = ReleaseIntake(
            store=intake_store,
            registry=repositories,
            ids=ids,
            builds=start,
            audit=audit,
        )
        # The build worker's command bus: only the "source published" intake (decision 0025
        # CQ11); checked before anything starts.
        commands = CommandBus()
        commands.register(
            ReceiveSourcePublishedCommand,
            ReceiveSourcePublishedHandler(
                store=intake_store, max_pending_per_project=inbox.max_pending_per_project
            ),
        )
        require_handlers(commands, BUILD_WORKER_COMMANDS, process="build worker")
        processor = InboxProcessor(
            InboxProcessorConfig(
                poll_interval=inbox.poll_interval,
                batch_size=inbox.batch_size,
                max_per_project=inbox.max_per_project,
                visibility=inbox.visibility,
                concurrency=inbox.concurrency,
                registry_timeout=inbox.registry_timeout,
                transaction_timeout=inbox.transaction_timeout,
                max_attempts=inbox.max_attempts,
                deadline=inbox.deadline,
                backoff=backoff(inbox.backoff),
            ),
            intake=intake,
            logger=logging.getLogger("provisioning.inbox"),
        )
        client = BrokerClient(
            broker_config(config.broker, BROKER_QUEUES, CONNECTION_NAME),
            logger=logging.getLogger("provisioning.broker"),
        )
        consumer = SourcePublishedConsumer(
            EventConsumerConfig(
                concurrency=inbox.consumer_concurrency,
                store_hold=backoff(inbox.store_hold),
                store_failure_limit=inbox.store_failure_limit,
            ),
            decoder=EventCodecV1(max_body_bytes=config.broker.max_message_bytes),
            commands=commands,
            logger=logging.getLogger("provisioning.source_published"),
            on_stored=processor.wake,
        )
        poller = BuildPoller(
            BuildPollerConfig(
                poll_interval=poll.poll_interval,
                poll=PollRoundParams(limit=poll.poll_limit, concurrency=poll.poll_concurrency),
                sweep_interval=poll.sweep_interval,
                sweep=SweepRoundParams(
                    limit=poll.sweep_limit,
                    concurrency=poll.sweep_concurrency,
                    transaction_timeout=poll.sweep_transaction_timeout,
                ),
            ),
            polls=PollBuilds(
                store=builds,
                build_system=build_system,
                repositories=repositories,
                ids=ids,
                logger=logging.getLogger("provisioning.build_poll"),
                policy=policy,
                image_registry=config.build.image_registry,
                call_timeout=config.build.call_timeout,
                dispatch_grace=poll.dispatch_grace,
                audit=audit,
                actor=AUDIT_ACTOR,
            ),
            sweeps=BuildAttempts(
                store=builds,
                ids=ids,
                logger=logging.getLogger("provisioning.build_sweep"),
                policy=policy,
                builds=build_system,
                clock=SystemClock(),
                audit=audit,
                actor=AUDIT_ACTOR,
                cancel_timeout=config.build.call_timeout,
            ),
            pending=NoPendingStarts()
            if production
            else StartPendingBuilds(store=builds, starter=start, grace=poll.pending_grace),
            logger=logging.getLogger("provisioning.build_poller"),
            metrics=metrics,
        )
        log = logging.getLogger("provisioning.build_worker")
        if production:
            return BuildWorkerProcess(first=(), then=(poller.run,), parts=("poller",), logger=log)
        hints = HintLoop(
            listener=PgBuildHintListener(build_engine, timeouts, liveness=config.hints.liveness),
            wanted=WantedBuilds(
                starter=start,
                queue_size=config.hints.queue_size,
                logger=logging.getLogger("provisioning.build_hints"),
            ),
            reconnect=backoff(config.hints.reconnect),
            logger=logging.getLogger("provisioning.build_hints"),
        )
        events = client.subscribe(SOURCE_PUBLISHED_NAMES.queue, prefetch=inbox.consumer_concurrency)

        async def consume() -> None:
            await consumer.run(events)

        return BuildWorkerProcess(
            first=(client.run,),
            then=(events.run, consume, processor.run, poller.run, hints.run),
            parts=("events", "consumer", "processor", "poller", "hints"),
            logger=log,
        )
