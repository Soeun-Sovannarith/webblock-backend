"""The worker in the production profile (task H2).

- Its configuration refuses each stand-in key by name, and without PROVISIONING_PROFILE it
  refuses to start.
- Built in production, it enables no command kind, runs no prototype event loop and reads the
  closed hosting entitlement.
- A command that appears anyway (a planted row, claimed through a fake job store) is refused
  explicitly: it fails ``operation_unavailable``, its delivery is acknowledged, and no executor
  runs. It is never silently dropped.
"""

from __future__ import annotations

import io
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from tests.unit._loop_fakes import (
    MS,
    FakeDelivery,
    FakeJobStore,
    FakeWorkerStore,
    FixedClock,
    claim_of,
    logger_with_handler,
    ref,
)
from tests.unit.core.application.routing.background.test_recovery import RecordingAudit
from tests.unit.core.application.routing.background.test_routing_support import FakeCluster
from tests.unit.hosts.envs import production, worker_env

from provisioning.core.application import (
    FAILURE_CODE_OPERATION_UNAVAILABLE,
    Claim,
    ClaimOutcome,
    ClaimResult,
    WorkRef,
)
from provisioning.core.application.shared.backoff import Backoff
from provisioning.core.application.shared.profile import ContractNotDecided, Profile
from provisioning.core.domain import CommandKind, CommandStatus
from provisioning.hosts.shared.open_contracts import ClosedHostingEntitlement, worker_kinds
from provisioning.infrastructure.config import LoadError
from provisioning.infrastructure.config.worker import load_worker
from provisioning.infrastructure.telemetry import new_logger
from provisioning.presentation.messaging.consumers.consumer import CommandConsumer, WorkerConfig

if TYPE_CHECKING:
    from provisioning.hosts.worker.wiring import WorkerProcess

KUBECONFIG = Path("/project/bin/kind-state/proto/worker.kubeconfig")
PROJECT_ID = ref().project_id


def _build(env: dict[str, str]) -> WorkerProcess:
    # Imported here, not at collection (see test_wiring.py): the wiring imports kubernetes_asyncio.
    from provisioning.hosts.worker.wiring import build_worker

    config = load_worker(env.get)
    engine = create_async_engine("postgresql+psycopg://unused@127.0.0.1:1/unused")
    return build_worker(
        config,
        worker_engine=engine,
        hosting_engine=engine,
        cluster=FakeCluster([]),
        logger=new_logger(io.StringIO(), config.log_level),
        audit=RecordingAudit(),
    )


def test_the_production_worker_loads_without_any_stand_in_key() -> None:
    config = load_worker(production(worker_env(KUBECONFIG)).get)
    assert config.profile is Profile.PRODUCTION
    assert (config.entitlement, config.entitlement_file, config.hosting_entitlement) == (
        "",
        None,
        "",
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ENTITLEMENT_MODE", "prototype-allow"),
        ("ENTITLEMENT_FILE", "/etc/webblock/entitlement.json"),
        ("HOSTING_ENTITLEMENT_MODE", "prototype-payment-events"),
    ],
)
def test_production_refuses_each_stand_in_key_by_name(key: str, value: str) -> None:
    with pytest.raises(LoadError) as caught:
        load_worker((production(worker_env(KUBECONFIG)) | {key: value}).get)
    assert caught.value.keys() == (key,)
    assert caught.value.problems[0].reason.startswith("must be unset in production: ")


def test_without_a_profile_the_worker_refuses_to_start() -> None:
    env = {k: v for k, v in worker_env(KUBECONFIG).items() if k != "PROVISIONING_PROFILE"}
    with pytest.raises(LoadError) as caught:
        load_worker(env.get)
    assert [(p.key, p.reason) for p in caught.value.problems] == [
        ("PROVISIONING_PROFILE", "is required")
    ]


def test_the_production_worker_enables_no_kind_and_runs_no_prototype_event() -> None:
    process = _build(production(worker_env(KUBECONFIG)))
    assert process.kinds == ()
    assert process.event_loops == ()
    assert isinstance(process.record, ClosedHostingEntitlement)


def test_the_prototype_worker_still_runs_every_kind_but_sync_domains() -> None:
    """Redeploy runs in the prototype since its executor exists (decision 0023 G12, G13)."""
    process = _build(worker_env(KUBECONFIG))
    assert CommandKind.SYNC_DOMAINS not in process.kinds
    assert CommandKind.REDEPLOY in process.kinds
    assert set(process.kinds) == set(CommandKind) - {CommandKind.SYNC_DOMAINS}
    assert len(process.event_loops) == 10


@pytest.mark.asyncio
async def test_the_closed_hosting_entitlement_names_its_contract() -> None:
    with pytest.raises(ContractNotDecided) as caught:
        await ClosedHostingEntitlement().entitlement_after(PROJECT_ID, "pay-1")
    assert caught.value.contract == "OD-04"


class _Executor:
    def __init__(self) -> None:
        self.claims: list[Claim] = []

    async def execute(self, claim: Claim) -> None:
        self.claims.append(claim)


class _Decoder:
    def __init__(self, work: WorkRef) -> None:
        self.work = work

    def decode_command_job(
        self, *, body: bytes, content_type: str | None, message_type: str | None
    ) -> WorkRef:
        return self.work


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", list(CommandKind))
async def test_a_planted_command_is_refused_in_production_never_dropped(kind: CommandKind) -> None:
    work = ref(kind)
    jobs = FakeJobStore(claims=[ClaimResult(outcome=ClaimOutcome.CLAIMED, claim=claim_of(work))])
    worker = FakeWorkerStore(jobs=jobs)
    executor = _Executor()
    logger, _ = logger_with_handler("production-consumer")
    consumer = CommandConsumer(
        WorkerConfig(
            claimed_by_worker="worker-1",
            concurrency=1,
            claim_ttl=90 * MS,
            heartbeat_interval=10 * MS,
            attempt_timeout=2000 * MS,
            broker_consumer_timeout=timedelta(seconds=60),
            max_attempts=3,
            retry_backoff=Backoff(base=10 * MS, max=40 * MS),
            store_hold=Backoff(base=1 * MS, max=2 * MS),
            store_failure_limit=3,
        ),
        jobs=jobs,
        worker=worker,
        executors=dict.fromkeys(CommandKind, executor),
        kinds=worker_kinds(Profile.PRODUCTION),
        decoder=_Decoder(work),
        clock=FixedClock(),
        logger=logger,
    )
    delivery = FakeDelivery()
    await consumer.handle(delivery)
    assert [(f.failure_code, f.status) for _, f in worker.finishes] == [
        (FAILURE_CODE_OPERATION_UNAVAILABLE, CommandStatus.FAILED)
    ]
    assert executor.claims == []
    assert delivery.actions == ["ack"]
