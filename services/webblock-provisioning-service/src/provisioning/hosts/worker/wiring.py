"""The release worker process built from its configuration with constructors only (task G;
decisions 0005, 0013 section 3, 0014 sections 3, 10.5 and 13).

One process runs:
- the dispatcher, which publishes due command rows (SQL first, the broker only notifies);
- the command consumer, with the deploy executor (F1) for ``deploy``, the routing executor (F2)
  for ``promote`` and ``rollback``, the hosting executor (F2) for ``suspend_hosting`` and
  ``resume_hosting``, and the redeploy executor (decision 0023 G12) for ``redeploy``, a rolling
  restart of the live release; every kind but ``sync_domains`` is enabled in the prototype
  (decision 0014; custom domains are removed, section 23, so a ``sync_domains`` command fails
  ``operation_unavailable`` without any executor). The routing and hosting executors carry the
  abandon hook (``RouteAbandoner``) and routing the inline cleanup (``ReleaseCleanup``) of
  decision 0014 section 21 (W6-B);
- the reconciler loop (W6-B): re-applies a missing or drifted live workload or route, never
  forced, sweeps finished workloads, and reports orphans, foreign managers and fences ahead;
- the event consumers of ``hosts.worker.events``: today the Payment-event consumer (E2) over the
  hosting intake (F2), which records each event in the in-process entitlement record before it
  turns the event into a command.

Production (task H2): the worker enables no command kind (``open_contracts.worker_kinds``), so a
command that appears anyway is claimed and fails ``operation_unavailable`` without any executor
running; Routing's entitlement is closed and the hosting executor reads the closed hosting
entitlement. The event side (Payment's and Project's v1 events, the hosting retry and the usage
publisher) is not built at all, because the event schemas are drafts that wait for their owners
(decision 0022 E2) and the contracts are still open (OD-02, OD-04, OD-42, OD-52). The dispatcher
and the reconciler run as in the prototype: they depend on no open contract.

Two database logins: ``provisioning_worker`` (jobs, fenced writes) and
``provisioning_hosting_intake`` (Payment-event commands). One cluster identity: the worker
kubeconfig. This module never imports the bootstrap adapter (import-linter contract
``worker-no-bootstrap`` and a test), so the worker can never construct a client that creates
namespaces or RoleBindings.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Final

from sqlalchemy.ext.asyncio import AsyncEngine

from provisioning.core.application.deploys.background.deploy import DeployCandidate, DeployParams
from provisioning.core.application.deploys.background.deploy_executor import (
    AsyncioPauser,
    DeployExecutor,
)
from provisioning.core.application.deploys.background.redeploy import Redeploy, RedeploySettings
from provisioning.core.application.deploys.background.redeploy_executor import RedeployExecutor
from provisioning.core.application.hosting.background.hosting_executor import HostingExecutor
from provisioning.core.application.hosting.background.hosting_job import Hosting
from provisioning.core.application.hosting.ports import HostingEntitlement
from provisioning.core.application.jobs.audited import (
    AuditedDeployStore,
    AuditedJobStore,
    AuditedRouteCommandStore,
)
from provisioning.core.application.jobs.background.dispatcher import Dispatcher, DispatcherConfig
from provisioning.core.application.ports.dependencies import Entitlement
from provisioning.core.application.ports.metrics import CommandMetrics
from provisioning.core.application.ports.site_cluster import SiteCluster
from provisioning.core.application.reconcile.background.reconcile import (
    Reconciler,
    ReconcileSettings,
)
from provisioning.core.application.reconcile.background.reconciler import (
    ReconcilerLoop,
    ReconcilerLoopConfig,
)
from provisioning.core.application.routing.background.recovery import ReleaseCleanup, RouteAbandoner
from provisioning.core.application.routing.background.routing import Routing, RoutingSettings
from provisioning.core.application.routing.background.routing_executor import RoutingExecutor
from provisioning.core.application.shared.audit import AuditLog
from provisioning.core.application.shared.profile import Profile
from provisioning.core.domain import CommandKind
from provisioning.hosts.shared import open_contracts
from provisioning.hosts.shared.messaging import backoff, broker_config
from provisioning.hosts.shared.runtime import (
    InvalidSettings,
    SystemClock,
    UUIDv7,
    run_until_stopped,
    settings_refused,
    timeouts_of,
)
from provisioning.hosts.worker.events import EventParts, Loop, build_events
from provisioning.infrastructure.config.api import PAYMENT_GRPC
from provisioning.infrastructure.config.worker import WorkerProcessConfig
from provisioning.infrastructure.kubernetes.render import SiteSettings
from provisioning.infrastructure.kubernetes.site_cluster import SiteClusterSettings
from provisioning.infrastructure.payment.grpc_entitlement import GrpcEntitlement
from provisioning.infrastructure.postgres import PgJobStore
from provisioning.infrastructure.postgres.deploy_store import PgDeployStore
from provisioning.infrastructure.postgres.reconcile_store import PgReconcileStore
from provisioning.infrastructure.postgres.routing_store import PgRouteCommandStore
from provisioning.infrastructure.rabbitmq import (
    COMMAND_JOB_NAMES,
    BrokerClient,
    Subscription,
)
from provisioning.infrastructure.telemetry import Logger
from provisioning.presentation.messaging import CommandJobCodec, CommandJobPublisher
from provisioning.presentation.messaging.consumers.consumer import CommandConsumer, WorkerConfig

PROTOTYPE_KINDS: Final[tuple[CommandKind, ...]] = open_contracts.PROTOTYPE_KINDS
"""The kinds the prototype executes (``hosts.shared.open_contracts``); none in production."""

CONNECTION_NAME: Final = "provisioning-worker"


def site_cluster_settings(config: WorkerProcessConfig) -> SiteClusterSettings:
    """D's settings of the per-site objects and the probe; the readiness deadline of a release's
    pods is the deploy's ready timeout. ``InvalidSettings`` when they are refused."""
    site = config.site
    with settings_refused():
        return SiteClusterSettings(
            site=SiteSettings(
                cpu_millicores=site.cpu_millicores,
                memory_mib=site.memory_mib,
                progress_deadline_seconds=int(site.progress_deadline.total_seconds()),
                readiness_deadline_seconds=int(config.deploy.ready_timeout.total_seconds()),
                offline_service_port=site.offline_service_port,
            ),
            gateway_url=site.gateway_url,
            probe_path=site.probe_path,
            probe_timeout_seconds=site.probe_timeout.total_seconds(),
            delete_retry_base_seconds=site.delete_retry_base.total_seconds(),
            site_domain=site.domain,
            preview_token=site.preview_token.reveal(),
            probe_successes=site.probe_successes,
            probe_max_body_bytes=site.probe_max_body_bytes,
            probe_success_gap_seconds=site.probe_gap.total_seconds(),
        )


def route_abandon_timeout(config: WorkerProcessConfig) -> timedelta:
    """The bound of one route abandon (W6-B): its restore deadline plus its two database
    transactions (the load and the outcome), each bounded by DB_TX_TIMEOUT."""
    return config.route.restore_deadline + 2 * config.database.tx_timeout


def reconcile_round_timeout(config: WorkerProcessConfig) -> timedelta:
    """A safety bound of one reconcile round; every call in it is bounded already. Per project:
    up to four cluster calls (DEPLOY_CALL_TIMEOUT), a route read and two deletes per finished
    release are bounded by DEPLOY_DELETE_TIMEOUT (counted as two), and up to six short reads
    (DB_TX_TIMEOUT); plus the tenant list and its read."""
    dep, database = config.deploy, config.database
    per_project = 4 * dep.call_timeout + 3 * dep.delete_timeout + 6 * database.tx_timeout
    return config.reconcile.batch_size * per_project + dep.call_timeout + database.tx_timeout


@dataclass(frozen=True, slots=True)
class WorkerProcess:
    """A built worker process; ``serve`` it once."""

    client: BrokerClient
    dispatcher: Dispatcher
    consumer: CommandConsumer
    commands: Subscription
    reconciler: ReconcilerLoop
    event_loops: tuple[Loop, ...]  # from ``hosts.worker.events``, after the command consumer
    record: HostingEntitlement  # the Payment event record; closed in production
    kinds: tuple[CommandKind, ...]
    logger: logging.Logger

    async def serve(self, stop: asyncio.Event, *, also: tuple[Loop, ...] = ()) -> None:
        """Run the broker connection (and ``also``, the operational listener of task H4), then
        the dispatcher and every consumer, until ``stop`` is set; the consumers stop first, so a
        claim in progress is released over the open connection. ``LoopEnded`` when a loop ends
        without a stop request."""

        async def commands() -> None:
            await self.consumer.run(self.commands)

        self.logger.info("worker running (kinds=%s)", ",".join(k.value for k in self.kinds))
        await run_until_stopped(
            stop,
            first=(self.client.run, *also),
            then=(
                self.dispatcher.run,
                self.commands.run,
                commands,
                self.reconciler.run,
                *self.event_loops,
            ),
            logger=self.logger,
        )


def build_worker(
    config: WorkerProcessConfig,
    *,
    worker_engine: AsyncEngine,
    hosting_engine: AsyncEngine,
    cluster: SiteCluster,
    logger: Logger,
    audit: AuditLog,
    metrics: CommandMetrics | None = None,
    payment: GrpcEntitlement | None = None,
) -> WorkerProcess:
    """Build the process from its configuration and its connections; ``InvalidSettings`` when a
    component refuses its settings. Opens no socket and no connection. ``audit`` receives the
    workload cleanups, the operator rollback's reclaim and the uncertain-route alert;
    ``metrics`` measures each command execution (task H4)."""
    log = logging.getLogger("provisioning.worker")
    clock, ids = SystemClock(), UUIDv7()
    timeouts = timeouts_of(config.database)
    with settings_refused():
        # W6-D's audited wrappers: the same AuditedJobStore is jobs= and worker=, and one
        # wrapped `routes` serves Routing, Hosting and the abandoner, so the uncertain-route
        # alert has a single source.
        jobs = AuditedJobStore(PgJobStore(worker_engine, timeouts), audit, clock)
        events: EventParts | None = None
        hosting_record: HostingEntitlement = open_contracts.ClosedHostingEntitlement()
        if config.profile is Profile.PROTOTYPE:
            events = build_events(
                config,
                hosting_engine=hosting_engine,
                worker_engine=worker_engine,
                timeouts=timeouts,
                clock=clock,
                ids=ids,
            )
            hosting_record = events.record
        if PAYMENT_GRPC in (config.entitlement, config.hosting_entitlement) and payment is None:
            raise InvalidSettings(
                "ENTITLEMENT_MODE or HOSTING_ENTITLEMENT_MODE payment-grpc needs the adapter "
                "built from PAYMENT_GRPC_TARGET"
            )
        # The Payment-event consumer still writes the in-process record (its write-through sink and
        # deletion lookup); with payment-grpc the executors ask Payment's current answer instead.
        run_entitlement: Entitlement
        if config.entitlement == PAYMENT_GRPC and payment is not None:
            run_entitlement = payment
        else:
            run_entitlement = open_contracts.entitlement(
                config.profile, config.entitlement, config.entitlement_file, logger
            )
        executor_hosting: HostingEntitlement = hosting_record
        if config.hosting_entitlement == PAYMENT_GRPC and payment is not None:
            executor_hosting = payment
        client = BrokerClient(
            broker_config(
                config.broker,
                (COMMAND_JOB_NAMES, *(events.queue_names if events else ())),
                CONNECTION_NAME,
                publish_exchanges=events.publish_exchanges if events else (),
            ),
            logger=logging.getLogger("provisioning.broker"),
        )
        codec = CommandJobCodec(max_body_bytes=config.broker.max_message_bytes)
        d = config.dispatch
        dispatcher = Dispatcher(
            DispatcherConfig(
                poll_interval=d.poll_interval,
                batch_size=d.batch_size,
                publish_concurrency=d.publish_concurrency,
                confirm_timeout=d.confirm_timeout,
                visibility_timeout=d.visibility_timeout,
                redelivery_interval=d.redelivery_interval,
                backoff=backoff(d.backoff),
            ),
            jobs=jobs,
            worker=jobs,
            publisher=CommandJobPublisher(
                codec=codec,
                raw=client,
                exchange=COMMAND_JOB_NAMES.exchange,
                routing_key=COMMAND_JOB_NAMES.routing_keys[0],
            ),
            ids=ids,
            clock=clock,
            logger=logging.getLogger("provisioning.dispatcher"),
        )
        c, dep = config.consumer, config.deploy
        kinds = open_contracts.worker_kinds(config.profile)
        deploy = DeployCandidate(
            store=AuditedDeployStore(PgDeployStore(worker_engine, timeouts), audit, clock),
            cluster=cluster,
            clock=clock,
            ids=ids,
            pauser=AsyncioPauser(),
            params=DeployParams(
                image_registry=dep.image_registry,
                ready_timeout=dep.ready_timeout,
                poll_interval=dep.poll_interval,
                call_timeout=dep.call_timeout,
                delete_timeout=dep.delete_timeout,
                finish_margin=dep.finish_margin,
                claim_ttl=c.lease_ttl,
            ),
            logger=logging.getLogger("provisioning.deploy"),
        )
        routes = AuditedRouteCommandStore(
            PgRouteCommandStore(worker_engine, timeouts), audit, clock
        )
        settings = RoutingSettings(
            site_domain=config.site.domain,
            switch_deadline=config.route.switch_deadline,
            restore_deadline=config.route.restore_deadline,
            poll_interval=config.route.poll_interval,
        )
        cleanup = ReleaseCleanup(
            cluster=cluster,
            audit=audit,
            clock=clock,
            delete_timeout=dep.delete_timeout,
            logger=logging.getLogger("provisioning.cleanup"),
        )
        abandoner = RouteAbandoner(
            store=routes,
            cluster=cluster,
            clock=clock,
            settings=settings,
            cleanup=cleanup,
        )
        route_abandon = route_abandon_timeout(config)
        routing = RoutingExecutor(
            Routing(
                store=routes,
                cluster=cluster,
                entitlement=run_entitlement,
                clock=clock,
                settings=settings,
                audit=audit,
                cleanup=cleanup,
            ),
            abandoner,
            abandon_timeout=route_abandon,
        )
        hosting = HostingExecutor(
            Hosting(
                store=routes,
                cluster=cluster,
                entitlement=executor_hosting,
                clock=clock,
                settings=settings,
            ),
            abandoner,
            abandon_timeout=route_abandon,
        )
        redeploy = RedeployExecutor(
            Redeploy(
                store=routes,
                cluster=cluster,
                clock=clock,
                pauser=AsyncioPauser(),
                settings=RedeploySettings(
                    image_registry=dep.image_registry,
                    poll_interval=dep.poll_interval,
                    call_timeout=dep.call_timeout,
                    finish_margin=dep.finish_margin,
                ),
                logger=logging.getLogger("provisioning.redeploy"),
            )
        )
        reconcile = config.reconcile
        reconciler = ReconcilerLoop(
            Reconciler(
                store=PgReconcileStore(worker_engine, timeouts),
                cluster=cluster,
                cleanup=cleanup,
                settings=ReconcileSettings(
                    site_domain=config.site.domain,
                    image_registry=dep.image_registry,
                    batch_size=reconcile.batch_size,
                    call_timeout=dep.call_timeout,
                ),
                logger=logging.getLogger("provisioning.reconcile"),
            ),
            ReconcilerLoopConfig(
                interval=reconcile.interval, round_timeout=reconcile_round_timeout(config)
            ),
            logger=logging.getLogger("provisioning.reconciler"),
        )
        consumer = CommandConsumer(
            WorkerConfig(
                claimed_by_worker=c.worker_id,
                concurrency=c.concurrency,
                claim_ttl=c.lease_ttl,
                heartbeat_interval=c.heartbeat_interval,
                attempt_timeout=c.attempt_timeout,
                broker_consumer_timeout=config.broker.consumer_timeout,
                max_attempts=c.max_attempts,
                retry_backoff=backoff(c.retry_backoff),
                store_hold=backoff(c.store_hold),
                store_failure_limit=c.store_failure_limit,
            ),
            jobs=jobs,
            worker=jobs,
            executors={
                CommandKind.DEPLOY: DeployExecutor(deploy, abandon_timeout=dep.abandon_timeout),
                CommandKind.PROMOTE: routing,
                CommandKind.ROLLBACK: routing,
                CommandKind.SUSPEND_HOSTING: hosting,
                CommandKind.RESUME_HOSTING: hosting,
                CommandKind.REDEPLOY: redeploy,
            },
            kinds=kinds,
            decoder=codec,
            clock=clock,
            logger=logging.getLogger("provisioning.consumer"),
            metrics=metrics,
        )
        return WorkerProcess(
            client=client,
            dispatcher=dispatcher,
            consumer=consumer,
            commands=client.subscribe(COMMAND_JOB_NAMES.queue, prefetch=c.concurrency),
            reconciler=reconciler,
            event_loops=events.loops(client) if events else (),
            record=hosting_record,
            kinds=kinds.kinds(),
            logger=log,
        )
