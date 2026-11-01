"""The routing store's fenced writes and the hosting intake on PostgreSQL 18 (prototype task F2).

The route command store runs on a login that is a member of ``provisioning_worker`` alone; the
hosting intake on one that is a member of ``provisioning_hosting_intake`` alone. Every outcome is a
real domain function, as the routing and hosting use cases pass them. The last tests run the real
``Routing`` and ``Hosting`` use cases on the real store with the fake cluster of the unit tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration._pg import ROLE_HOSTING_INTAKE, ROLE_WORKER, Database
from tests.integration.test_store_support import (
    COMMANDS,
    DISPATCH,
    HOSTING,
    RELEASES,
    TIMEOUTS,
    Row,
    at,
    command_row,
    dispatch_row,
    hosting_row,
    release_row,
    role_engine,
    row_of,
    scalar,
    seed_rows,
    subdomain_for,
)
from tests.unit.core.application.routing.background.test_routing_support import (
    OFFLINE,
    PROJECT,
    SUBDOMAIN,
    FakeCluster,
    FakeEntitlement,
    FakeHostingEntitlement,
    FakeIds,
    Route,
    settings,
    to_release,
    unavailable,
)

from provisioning.core.application import (
    AcceptHostingEventCommand,
    Claim,
    ClaimLost,
    Fence,
    OperationInProgress,
    WorkRef,
)
from provisioning.core.application.deploys.background.redeploy import Redeploy, RedeploySettings
from provisioning.core.application.hosting.background.hosting_job import DELETION_EVENT_KEY, Hosting
from provisioning.core.application.hosting.commands.accept_hosting_event import (
    AcceptHostingEventHandler,
)
from provisioning.core.application.hosting.ports import HostingCommandStore, HostingIntent
from provisioning.core.application.ports.site_cluster import (
    ReleaseWorkload,
    WorkloadState,
    WorkloadStatus,
)
from provisioning.core.application.routing.background.routing import Routing
from provisioning.core.application.routing.ports import RouteCommandStore
from provisioning.core.domain import (
    CommandKind,
    CommandStatus,
    Failure,
    IdempotencyConflict,
    OfflineReason,
    ReleaseStatus,
    StaleFence,
    confirm_live,
    fail_without_route_change,
    hosting_verified,
    owner_status_key,
    redeploy_succeeded,
    rollback_verified,
    route_uncertain,
    start_route_change,
)
from provisioning.infrastructure.postgres import DatabaseError, HostingIntakeStore
from provisioning.infrastructure.postgres.hosting_intake import PgHostingHistory
from provisioning.infrastructure.postgres.routing_store import PgRouteCommandStore
from provisioning.infrastructure.postgres.usage_outbox import PgUsageOutbox
from provisioning.infrastructure.standins.entitlement_record import PrototypeHostingRecord

pytestmark = pytest.mark.asyncio

NUMBER = 5


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Scene:
    project: UUID
    live: UUID | None
    candidate: UUID | None
    command: UUID
    fence: Fence


def scene(
    db: Database,
    kind: CommandKind,
    *,
    live: bool = True,
    routing: str = "stable",
    hosting: str = "online",
    candidate_status: str | None = "candidate_ready",
    names: str = "candidate",
    project: UUID = PROJECT,
    reasons: str | None = None,
    key: str | None = None,
) -> Scene:
    """A project at command number NUMBER with a running command of ``kind`` claimed once.
    ``names`` is which release a rollback names: ``candidate`` or ``live``. ``reasons`` is the
    ``offline_reasons`` literal (``hosting_row``'s default when None); ``key`` the command's
    idempotency key (a random one, so ``billing`` for a hosting kind, when None)."""
    live_id = uuid4() if live else None
    candidate_id = uuid4() if candidate_status is not None else None
    command_id = uuid4()
    rows: list[tuple[str, Row]] = []
    if live_id is not None:
        rows.append((RELEASES, release_row(project, "live", release_id=live_id, name="live")))
    if candidate_id is not None and candidate_status is not None:
        rows.append(
            (
                RELEASES,
                release_row(
                    project,
                    candidate_status,
                    release_id=candidate_id,
                    name="candidate",
                    created_at=datetime(2026, 10, 6, 10, tzinfo=UTC),
                    updated_at=datetime(2026, 10, 6, 10, tzinfo=UTC),
                ),
            )
        )
    rows.append(
        (
            HOSTING,
            hosting_row(
                project,
                number=NUMBER,
                live=live_id,
                routing=routing,
                hosting=hosting,
                reasons=reasons,
                # The fake cluster's site is <SUBDOMAIN>.<site domain> (decision 0023 G18); any
                # other project has its own, as FR-54 requires.
                subdomain=SUBDOMAIN if project == PROJECT else subdomain_for(project),
            ),
        )
    )
    named = None
    if not kind.is_hosting():
        named = live_id if names == "live" else candidate_id
    rows.append(
        (
            COMMANDS,
            command_row(
                project, kind, NUMBER, "running", release=named, key=key, command_id=command_id
            ),
        )
    )
    rows.append(
        (
            DISPATCH,
            dispatch_row(
                command_id,
                claim_number=1,
                execution_attempts=1,
                claimed_by_worker="worker-1",
                claim_expires_at=now() + timedelta(minutes=5),
            ),
        )
    )
    seed_rows(db, *rows)
    fence = Fence(project_id=project, command_id=command_id, command_number=NUMBER, claim_number=1)
    return Scene(project, live_id, candidate_id, command_id, fence)


def claim_of(s: Scene, kind: CommandKind, release: UUID | None) -> Claim:
    return Claim(
        ref=WorkRef(
            command_id=s.command,
            project_id=s.project,
            kind=kind,
            command_number=NUMBER,
            release_id=release,
        ),
        claimed_by_worker="worker-1",
        claim_number=1,
        execution_attempt=1,
        expires_at=now() + timedelta(minutes=5),
        deadline_at=now() + timedelta(hours=1),
    )


def release_state(db: Database, release_id: UUID | None) -> tuple[object, ...]:
    return row_of(
        db,
        "SELECT status::text, failure_code, promoted_at IS NOT NULL "
        "FROM provisioning.releases WHERE release_id = %s",
        release_id,
    )


def hosting_state(db: Database, project: UUID) -> tuple[object, ...]:
    return row_of(
        db,
        "SELECT routing_status::text, hosting_status::text, live_release_id, last_command_number "
        "FROM provisioning.project_hosting WHERE project_id = %s",
        project,
    )


def command_state(db: Database, command_id: UUID) -> tuple[object, ...]:
    return row_of(
        db,
        "SELECT c.status::text, c.failure_code, d.finished_at IS NOT NULL, d.last_failure_code "
        "FROM provisioning.commands AS c "
        "JOIN provisioning_ops.command_dispatch AS d USING (command_id) WHERE c.command_id = %s",
        command_id,
    )


@pytest_asyncio.fixture
async def worker_engine(db: Database) -> AsyncIterator[AsyncEngine]:
    async with role_engine(db, ROLE_WORKER) as engine:
        yield engine


@pytest.fixture
def store(worker_engine: AsyncEngine) -> PgRouteCommandStore:
    return PgRouteCommandStore(worker_engine, TIMEOUTS)


# --- the store's writes -----------------------------------------------------------------------


async def test_a_promotion_writes_the_old_live_superseded_and_the_candidate_live(
    db: Database, store: PgRouteCommandStore
) -> None:
    """FR-19.1: a promotion writes the old live superseded and the candidate live."""
    s = scene(db, CommandKind.PROMOTE)
    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    assert release_state(db, s.candidate) == ("promoting", None, False)
    assert hosting_state(db, s.project)[:3] == ("changing", "online", s.live)
    assert command_state(db, s.command)[:3] == ("running", None, False)

    # The one-live index: this transaction succeeds only if the old live release is written first.
    out = await store.apply_route_outcome(s.fence, lambda i: confirm_live(i, now()))

    assert out.release is not None and out.release.status is ReleaseStatus.LIVE
    assert release_state(db, s.live) == ("superseded", None, True)
    assert release_state(db, s.candidate) == ("live", None, True)
    assert hosting_state(db, s.project) == ("stable", "online", s.candidate, NUMBER)
    assert command_state(db, s.command) == ("succeeded", None, True, None)


async def test_a_first_publish_goes_live_without_a_superseded_release(
    db: Database, store: PgRouteCommandStore
) -> None:
    s = scene(db, CommandKind.PROMOTE, live=False)
    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    await store.apply_route_outcome(s.fence, lambda i: confirm_live(i, now()))
    assert release_state(db, s.candidate) == ("live", None, True)
    assert hosting_state(db, s.project)[:3] == ("stable", "online", s.candidate)


async def test_a_restore_that_fails_leaves_the_route_uncertain(
    db: Database, store: PgRouteCommandStore
) -> None:
    """FR-23.1: a restore that fails leaves the route uncertain."""
    s = scene(db, CommandKind.PROMOTE)
    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    failure = Failure("route_restore_unconfirmed")
    await store.apply_route_outcome(s.fence, lambda i: route_uncertain(i, failure, now()))

    assert hosting_state(db, s.project)[:3] == ("uncertain", "online", s.live)
    assert release_state(db, s.candidate) == ("promoting", None, False)
    assert command_state(db, s.command) == (
        "failed",
        "route_restore_unconfirmed",
        True,
        "route_restore_unconfirmed",
    )


async def test_an_operator_rollback_clears_the_uncertain_route(
    db: Database, store: PgRouteCommandStore
) -> None:
    s = scene(db, CommandKind.ROLLBACK, routing="uncertain", candidate_status="promoting")
    loaded = await store.load_route_command(s.fence)
    assert loaded.promoting_release is not None and loaded.promoting_release.id == s.candidate

    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    assert hosting_state(db, s.project)[0] == "changing"
    await store.apply_route_outcome(
        s.fence, lambda i: rollback_verified(i, Failure("operator_rollback"), now())
    )

    assert hosting_state(db, s.project)[:3] == ("stable", "online", s.live)
    assert release_state(db, s.candidate) == ("rolled_back", "operator_rollback", False)
    assert release_state(db, s.live) == ("live", None, True)
    assert command_state(db, s.command) == ("succeeded", None, True, None)


async def test_a_rollback_naming_the_live_release_rolls_back_the_promoting_one(
    db: Database, store: PgRouteCommandStore
) -> None:
    s = scene(
        db, CommandKind.ROLLBACK, routing="uncertain", candidate_status="promoting", names="live"
    )
    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    await store.apply_route_outcome(
        s.fence, lambda i: rollback_verified(i, Failure("operator_rollback"), now())
    )
    assert release_state(db, s.candidate) == ("rolled_back", "operator_rollback", False)
    assert release_state(db, s.live) == ("live", None, True)
    assert hosting_state(db, s.project)[0] == "stable"


async def test_suspend_and_resume_write_the_hosting_status(
    db: Database, store: PgRouteCommandStore
) -> None:
    s = scene(db, CommandKind.SUSPEND_HOSTING, candidate_status=None)
    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    await store.apply_route_outcome(s.fence, lambda i: hosting_verified(i, now()))
    assert hosting_state(db, s.project) == ("stable", "offline", s.live, NUMBER)
    assert command_state(db, s.command) == ("succeeded", None, True, None)

    r = scene(
        db,
        CommandKind.RESUME_HOSTING,
        hosting="offline",
        candidate_status=None,
        project=uuid4(),
    )
    await store.apply_route_outcome(r.fence, lambda i: start_route_change(i, now()))
    await store.apply_route_outcome(r.fence, lambda i: hosting_verified(i, now()))
    assert hosting_state(db, r.project) == ("stable", "online", r.live, NUMBER)


def reasons_state(db: Database, project: UUID) -> tuple[object, ...]:
    return row_of(
        db,
        "SELECT hosting_status::text, offline_reasons::text[] "
        "FROM provisioning.project_hosting WHERE project_id = %s",
        project,
    )


async def test_verified_suspend_writes_its_reason(db: Database, store: PgRouteCommandStore) -> None:
    """A verified billing suspend writes ``{billing}`` with ``offline``; the resume that removes
    the last reason writes ``{}`` with ``online`` (decision 0021)."""
    s = scene(db, CommandKind.SUSPEND_HOSTING, candidate_status=None)
    assert reasons_state(db, s.project) == ("online", [])
    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    await store.apply_route_outcome(s.fence, lambda i: hosting_verified(i, now()))
    assert reasons_state(db, s.project) == ("offline", ["billing"])

    r = scene(
        db, CommandKind.RESUME_HOSTING, hosting="offline", candidate_status=None, project=uuid4()
    )
    assert reasons_state(db, r.project) == ("offline", ["billing"])
    await store.apply_route_outcome(r.fence, lambda i: start_route_change(i, now()))
    await store.apply_route_outcome(r.fence, lambda i: hosting_verified(i, now()))
    assert reasons_state(db, r.project) == ("online", [])


async def test_a_stale_fence_is_refused_and_writes_nothing(
    db: Database, store: PgRouteCommandStore
) -> None:
    s = scene(db, CommandKind.PROMOTE)
    stale = Fence(
        project_id=s.project, command_id=s.command, command_number=NUMBER - 1, claim_number=1
    )
    with pytest.raises(StaleFence):
        await store.apply_route_outcome(stale, lambda i: start_route_change(i, now()))
    with pytest.raises(StaleFence):
        await store.load_route_command(stale)
    assert hosting_state(db, s.project)[0] == "stable"
    assert release_state(db, s.candidate) == ("candidate_ready", None, False)


async def test_a_lost_claim_is_refused_and_writes_nothing(
    db: Database, store: PgRouteCommandStore
) -> None:
    s = scene(db, CommandKind.PROMOTE)
    lost = Fence(project_id=s.project, command_id=s.command, command_number=NUMBER, claim_number=2)
    with pytest.raises(ClaimLost):
        await store.apply_route_outcome(lost, lambda i: start_route_change(i, now()))
    assert hosting_state(db, s.project)[0] == "stable"


async def test_a_decision_that_raises_rolls_back_everything(
    db: Database, store: PgRouteCommandStore
) -> None:
    s = scene(db, CommandKind.PROMOTE)

    def refuse(_: object) -> object:
        raise RuntimeError("decide refused")

    with pytest.raises(RuntimeError):
        await store.apply_route_outcome(s.fence, refuse)  # type: ignore[arg-type]  # a decide that never returns an outcome
    assert hosting_state(db, s.project)[0] == "stable"
    assert scalar(db, "SELECT count(*) FROM provisioning.releases WHERE status = 'promoting'") == 0


# --- the use cases on the real store ----------------------------------------------------------


async def test_promote_with_restore_runs_on_the_store(
    db: Database, store: PgRouteCommandStore
) -> None:
    """FR-21.1: the last go-live try (claim 3) fails, so the restore runs on the store and the
    candidate is rolled_back with the try's code."""
    s = scene(db, CommandKind.PROMOTE)
    assert s.live is not None and s.candidate is not None
    log: list[str] = []
    cluster = FakeCluster(
        log, serving={s.live, s.candidate}, answering=set(), preview_answering={s.candidate}
    )
    cluster.route = Route(to_release(s.live), 0)
    port: RouteCommandStore = store
    routing = Routing(
        store=port,
        cluster=cluster,
        entitlement=FakeEntitlement(log),
        clock=_Clock(),
        settings=settings(),
    )
    last_try = replace(claim_of(s, CommandKind.PROMOTE, s.candidate), execution_attempt=3)
    await routing.promote(last_try)

    assert cluster.route == Route(to_release(s.live), NUMBER)
    assert release_state(db, s.candidate) == ("rolled_back", "site_not_answering", False)
    assert hosting_state(db, s.project)[:3] == ("stable", "online", s.live)
    assert command_state(db, s.command)[:2] == ("failed", "site_not_answering")


async def test_a_suspend_whose_route_is_unconfirmed_leaves_it_uncertain_on_the_store(
    db: Database, store: PgRouteCommandStore
) -> None:
    """FR-23.2: a suspend whose route is unconfirmed leaves it uncertain on the store."""
    s = scene(db, CommandKind.SUSPEND_HOSTING, candidate_status=None)
    assert s.live is not None
    log: list[str] = []
    cluster = FakeCluster(log, serving={s.live})
    cluster.fail_apply[OFFLINE] = unavailable()
    hosting = Hosting(
        store=store,
        cluster=cluster,
        entitlement=FakeHostingEntitlement(log),
        clock=_Clock(),
        settings=settings(),
    )
    await hosting.suspend(claim_of(s, CommandKind.SUSPEND_HOSTING, None))
    assert hosting_state(db, s.project)[:2] == ("uncertain", "online")
    assert command_state(db, s.command)[:2] == ("failed", "route_change_unconfirmed")


async def test_suspend_then_resume_on_the_store(db: Database, store: PgRouteCommandStore) -> None:
    s = scene(db, CommandKind.SUSPEND_HOSTING, candidate_status=None)
    assert s.live is not None
    log: list[str] = []
    cluster = FakeCluster(log, serving={s.live})
    hosting = Hosting(
        store=store,
        cluster=cluster,
        entitlement=FakeHostingEntitlement(log),
        clock=_Clock(),
        settings=settings(),
    )
    await hosting.suspend(claim_of(s, CommandKind.SUSPEND_HOSTING, None))
    assert hosting_state(db, s.project)[:2] == ("stable", "offline")
    assert command_state(db, s.command)[0] == "succeeded"


def reason_hosting(log: list[str], cluster: FakeCluster, store: PgRouteCommandStore) -> Hosting:
    return Hosting(
        store=store,
        cluster=cluster,
        entitlement=FakeHostingEntitlement(log),
        clock=_Clock(),
        settings=settings(),
    )


async def test_a_billing_resume_while_user_suspended_only_removes_billing_on_the_store(
    db: Database, store: PgRouteCommandStore
) -> None:
    """Decision 0021 D7 on the worker's login: the resume removes only ``billing``, ends
    ``succeeded`` without a route call, and the site stays offline."""
    s = scene(
        db,
        CommandKind.RESUME_HOSTING,
        candidate_status=None,
        hosting="offline",
        reasons="{billing,user_suspended}",
    )
    log: list[str] = []
    cluster = FakeCluster(log, serving=set() if s.live is None else {s.live})
    await reason_hosting(log, cluster, store).resume(claim_of(s, CommandKind.RESUME_HOSTING, None))
    assert command_state(db, s.command)[:2] == ("succeeded", None)
    assert reasons_state(db, s.project) == ("offline", ["user_suspended"])
    assert hosting_state(db, s.project)[0] == "stable"
    assert cluster.applied_targets() == []


async def test_an_owner_suspend_of_an_offline_site_adds_its_reason_on_the_store(
    db: Database, store: PgRouteCommandStore
) -> None:
    s = scene(
        db,
        CommandKind.SUSPEND_HOSTING,
        candidate_status=None,
        hosting="offline",
        key=owner_status_key(4),
    )
    log: list[str] = []
    cluster = FakeCluster(log, serving=set() if s.live is None else {s.live})
    await reason_hosting(log, cluster, store).suspend(
        claim_of(s, CommandKind.SUSPEND_HOSTING, None)
    )
    assert command_state(db, s.command)[:2] == ("succeeded", None)
    assert reasons_state(db, s.project) == ("offline", ["billing", "user_suspended"])
    assert cluster.applied_targets() == []


async def test_a_never_routed_suspend_and_resume_change_only_reasons_on_the_store(
    db: Database, store: PgRouteCommandStore
) -> None:
    """Decision 0021 D6: no live release, so the hosting status follows the reasons and the
    cluster is never called."""
    s = scene(db, CommandKind.SUSPEND_HOSTING, live=False, candidate_status=None)
    log: list[str] = []
    cluster = FakeCluster(log)
    await reason_hosting(log, cluster, store).suspend(
        claim_of(s, CommandKind.SUSPEND_HOSTING, None)
    )
    assert command_state(db, s.command)[:2] == ("succeeded", None)
    assert reasons_state(db, s.project) == ("offline", ["billing"])
    assert hosting_state(db, s.project)[0] == "stable"

    r = scene(
        db,
        CommandKind.RESUME_HOSTING,
        live=False,
        candidate_status=None,
        hosting="offline",
        project=uuid4(),
    )
    await reason_hosting(log, cluster, store).resume(claim_of(r, CommandKind.RESUME_HOSTING, None))
    assert command_state(db, r.command)[:2] == ("succeeded", None)
    assert reasons_state(db, r.project) == ("online", [])
    assert log == ["hosting entitlement"] * 2  # the billing recheck only; no cluster call


# --- the hosting intake as provisioning_hosting_intake -----------------------------------------


@pytest_asyncio.fixture
async def intake(db: Database) -> AsyncIterator[AcceptHostingEventHandler]:
    async with role_engine(db, ROLE_HOSTING_INTAKE) as engine:
        port: HostingCommandStore = HostingIntakeStore(engine, TIMEOUTS)
        yield AcceptHostingEventHandler(
            store=port, clock=_Clock(), ids=FakeIds(), command_deadline=timedelta(hours=1)
        )


async def test_a_payment_event_is_accepted_once_as_the_hosting_intake_role(
    db: Database, intake: AcceptHostingEventHandler
) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, number=3)))

    first = await intake.handle(
        AcceptHostingEventCommand("payment-evt-1", project, CommandKind.SUSPEND_HOSTING)
    )
    again = await intake.handle(
        AcceptHostingEventCommand("payment-evt-1", project, CommandKind.SUSPEND_HOSTING)
    )

    assert not first.replayed and again.replayed
    assert again.command.id == first.command.id
    assert first.command.command_number == 4
    assert scalar(db, "SELECT count(*) FROM provisioning.commands") == 1
    assert scalar(db, "SELECT count(*) FROM provisioning_ops.command_dispatch") == 1
    assert hosting_state(db, project)[3] == 4
    with pytest.raises(IdempotencyConflict):
        await intake.handle(
            AcceptHostingEventCommand("payment-evt-1", project, CommandKind.RESUME_HOSTING)
        )


async def test_a_payment_event_while_a_deploy_is_in_flight_is_refused_as_busy(
    db: Database, intake: AcceptHostingEventHandler
) -> None:
    project, built = uuid4(), uuid4()
    deploy = uuid4()
    seed_rows(
        db,
        (RELEASES, release_row(project, "built", release_id=built)),
        (HOSTING, hosting_row(project, number=1)),
        (
            COMMANDS,
            command_row(
                project, CommandKind.DEPLOY, 1, "running", release=built, command_id=deploy
            ),
        ),
        (DISPATCH, dispatch_row(deploy)),
    )
    with pytest.raises(OperationInProgress) as raised:
        await intake.handle(
            AcceptHostingEventCommand("payment-evt-2", project, CommandKind.SUSPEND_HOSTING)
        )
    assert raised.value.command_id == deploy
    assert scalar(db, "SELECT count(*) FROM provisioning.commands") == 1


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


# --- wave 6 (task W6-E): the usage outbox written with confirm_live ------------------------------


def usage_rows(db: Database) -> list[tuple[object, ...]]:
    with db.admin() as conn:
        return [
            tuple(r)
            for r in conn.execute(
                "SELECT release_id, project_id, event_key, status::text, attempts,"
                " completed_at = (SELECT promoted_at FROM provisioning.releases AS r"
                " WHERE r.release_id = o.release_id)"
                " FROM provisioning_ops.usage_outbox AS o ORDER BY created_at"
            ).fetchall()
        ]


def seed_usage_event(db: Database, release_id: UUID, project: UUID) -> None:
    """A row the guards would refuse (its release is not live yet), through the seeding path."""
    with db.admin() as conn, conn.transaction():
        conn.execute("SET LOCAL session_replication_role = replica")
        conn.execute(
            "INSERT INTO provisioning_ops.usage_outbox (release_id, project_id, event_key,"
            " completed_at, status, attempts, next_attempt_at, last_failure_code, sent_at,"
            " created_at, updated_at) VALUES (%s, %s, %s, now(), 'pending', 0, now(), NULL, NULL,"
            " now(), now())",
            (release_id, project, f"publish-completed-{release_id}"),
        )


async def test_confirm_live_writes_one_pending_usage_event_in_its_transaction(
    db: Database, store: PgRouteCommandStore
) -> None:
    """FR-19.1: confirm live writes one pending usage event in its transaction."""
    s = scene(db, CommandKind.PROMOTE)
    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    assert usage_rows(db) == []
    await store.apply_route_outcome(s.fence, lambda i: confirm_live(i, now()))
    assert usage_rows(db) == [
        (s.candidate, s.project, f"publish-completed-{s.candidate}", "pending", 0, True)
    ]


async def test_a_usage_event_that_cannot_be_written_rolls_the_promotion_back(
    db: Database, store: PgRouteCommandStore
) -> None:
    """The outbox row is in the confirm_live transaction: when it cannot be inserted (here a row
    for the release already exists), the release does not go live either."""
    s = scene(db, CommandKind.PROMOTE)
    assert s.candidate is not None
    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    seed_usage_event(db, s.candidate, s.project)
    with pytest.raises(DatabaseError) as raised:
        await store.apply_route_outcome(s.fence, lambda i: confirm_live(i, now()))
    assert raised.value.constraint == "usage_outbox_pkey"
    assert release_state(db, s.candidate)[0] == "promoting"
    assert release_state(db, s.live)[0] == "live"
    assert hosting_state(db, s.project)[:3] == ("changing", "online", s.live)
    assert command_state(db, s.command)[0] == "running"


async def test_no_usage_event_for_a_hosting_change(
    db: Database, store: PgRouteCommandStore
) -> None:
    s = scene(db, CommandKind.SUSPEND_HOSTING, candidate_status=None)
    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    await store.apply_route_outcome(s.fence, lambda i: hosting_verified(i, now()))
    assert usage_rows(db) == []


# --- the usage outbox store ----------------------------------------------------------------------


async def promoted(db: Database, store: PgRouteCommandStore) -> Scene:
    s = scene(db, CommandKind.PROMOTE, project=uuid4())
    await store.apply_route_outcome(s.fence, lambda i: start_route_change(i, now()))
    await store.apply_route_outcome(s.fence, lambda i: confirm_live(i, now()))
    return s


async def test_the_outbox_take_counts_an_attempt_and_hides_the_row(
    db: Database, store: PgRouteCommandStore, worker_engine: AsyncEngine
) -> None:
    s = await promoted(db, store)
    outbox = PgUsageOutbox(worker_engine, TIMEOUTS)
    (event,) = await outbox.take_due(10, timedelta(minutes=5))
    assert (event.release_id, event.project_id, event.attempts) == (s.candidate, s.project, 1)
    assert event.event_key == f"publish-completed-{s.candidate}"
    assert await outbox.take_due(10, timedelta(minutes=5)) == ()  # hidden by the visibility
    assert await outbox.mark_sent(event)
    assert usage_rows(db)[0][3:5] == ("sent", 1)
    assert not await outbox.mark_sent(event)  # sent is final; a second mark changes nothing


async def test_a_failure_makes_the_row_due_after_its_backoff_and_a_stale_take_writes_nothing(
    db: Database, store: PgRouteCommandStore, worker_engine: AsyncEngine
) -> None:
    await promoted(db, store)
    outbox = PgUsageOutbox(worker_engine, TIMEOUTS)
    (first,) = await outbox.take_due(10, timedelta(microseconds=1))
    assert await outbox.record_failure(first, timedelta(microseconds=1), "publish_failed")
    (second,) = await outbox.take_due(10, timedelta(minutes=5))
    assert second.attempts == 2
    # The first take's attempt is stale: its mark and its failure change nothing.
    assert not await outbox.mark_sent(first)
    assert not await outbox.record_failure(first, timedelta(seconds=1), "publish_failed")
    assert await outbox.mark_sent(second)
    with db.admin() as conn:
        found = conn.execute("SELECT last_failure_code FROM provisioning_ops.usage_outbox")
        assert found.fetchall() == [(None,)]


async def test_two_publishers_take_disjoint_rows(
    db: Database, store: PgRouteCommandStore, worker_engine: AsyncEngine
) -> None:
    for _ in range(3):
        await promoted(db, store)
    a, b = PgUsageOutbox(worker_engine, TIMEOUTS), PgUsageOutbox(worker_engine, TIMEOUTS)
    left, right = await asyncio.gather(
        a.take_due(2, timedelta(minutes=5)), b.take_due(2, timedelta(minutes=5))
    )
    taken = [e.release_id for e in (*left, *right)]
    assert len(taken) == 3 and len(set(taken)) == 3


# --- the hosting history, the retry and project deletion -----------------------------------------


@pytest_asyncio.fixture
async def history(db: Database) -> AsyncIterator[PgHostingHistory]:
    async with role_engine(db, ROLE_HOSTING_INTAKE) as engine:
        yield PgHostingHistory(engine, TIMEOUTS)


def hosting_command(
    project: UUID,
    kind: CommandKind,
    number: int,
    status: str,
    key: str,
    *,
    updated_at: datetime | None = None,
) -> list[tuple[str, Row]]:
    command_id = uuid4()
    finished = None if status in ("accepted", "running") else at(1)
    row = command_row(project, kind, number, status, key=key, command_id=command_id)
    if updated_at is not None:
        row["updated_at"] = updated_at
    return [
        (COMMANDS, row),
        (DISPATCH, dispatch_row(command_id, finished_at=finished)),
    ]


async def test_the_latest_hosting_intents_are_one_per_reason_oldest_first(
    db: Database, history: PgHostingHistory
) -> None:
    project = uuid4()
    assert await history.latest_hosting_intents(project) == ()
    seed_rows(
        db,
        (
            HOSTING,
            hosting_row(
                project,
                number=4,
                reasons="{user_suspended}",
                hosting="offline",
                owner_status_version=3,
            ),
        ),
        *hosting_command(project, CommandKind.SUSPEND_HOSTING, 1, "failed", "evt-1"),
        *hosting_command(project, CommandKind.SUSPEND_HOSTING, 2, "succeeded", "owner-status:3"),
        *hosting_command(project, CommandKind.RESUME_HOSTING, 3, "succeeded", "evt-2"),
        *hosting_command(project, CommandKind.SUSPEND_HOSTING, 4, "failed", "evt-3.retry-1"),
    )
    reasons = frozenset({OfflineReason.USER_SUSPENDED})
    assert await history.latest_hosting_intents(project) == (
        HostingIntent(
            project_id=project,
            kind=CommandKind.SUSPEND_HOSTING,
            idempotency_key="owner-status:3",
            status=CommandStatus.SUCCEEDED,
            hosting_reason=OfflineReason.USER_SUSPENDED,
            offline_reasons=reasons,
            owner_status_version=3,
        ),
        HostingIntent(
            project_id=project,
            kind=CommandKind.SUSPEND_HOSTING,
            idempotency_key="evt-3.retry-1",
            status=CommandStatus.FAILED,
            hosting_reason=OfflineReason.BILLING,
            offline_reasons=reasons,
            owner_status_version=3,
        ),
    )


async def test_the_retry_list_holds_only_projects_a_retry_can_help(
    db: Database, history: PgHostingHistory
) -> None:
    due, spent, met, newer, busy, uncertain = (uuid4() for _ in range(6))
    deploying = uuid4()
    seed_rows(
        db,
        (HOSTING, hosting_row(due, number=1)),
        *hosting_command(due, CommandKind.SUSPEND_HOSTING, 1, "failed", "evt-due.retry-2"),
        (HOSTING, hosting_row(spent, number=1)),
        *hosting_command(spent, CommandKind.SUSPEND_HOSTING, 1, "failed", "evt-spent.retry-3"),
        (HOSTING, hosting_row(met, number=1, hosting="offline")),
        *hosting_command(met, CommandKind.SUSPEND_HOSTING, 1, "failed", "evt-met"),
        (HOSTING, hosting_row(newer, number=2, hosting="offline")),
        *hosting_command(newer, CommandKind.RESUME_HOSTING, 1, "failed", "evt-newer-1"),
        *hosting_command(newer, CommandKind.SUSPEND_HOSTING, 2, "succeeded", "evt-newer-2"),
        (RELEASES, release_row(busy, "built", release_id=deploying)),
        (HOSTING, hosting_row(busy, number=2)),
        *hosting_command(busy, CommandKind.SUSPEND_HOSTING, 1, "failed", "evt-busy"),
        (COMMANDS, command_row(busy, CommandKind.DEPLOY, 2, "running", release=deploying)),
        (HOSTING, hosting_row(uncertain, number=1, routing="uncertain")),
        *hosting_command(uncertain, CommandKind.SUSPEND_HOSTING, 1, "failed", "evt-uncertain"),
    )
    assert await history.projects_to_retry(limit=10, budget=3) == (due,)
    # A larger budget makes the spent project eligible again; the limit bounds the list.
    assert set(await history.projects_to_retry(limit=10, budget=4)) == {due, spent}
    assert len(await history.projects_to_retry(limit=1, budget=4)) == 1


async def test_projects_to_retry_partitions_by_reason(
    db: Database, history: PgHostingHistory
) -> None:
    """The latest command is per reason, "met" reads ``offline_reasons``, and a failed owner-status
    command counts only while its version is the row's ``owner_status_version``."""
    billing_under_owner, stale_owner, current_owner, current_retry, met_by_reason, deleted = (
        uuid4() for _ in range(6)
    )
    seed_rows(
        db,
        # A failed billing suspend, then a newer owner suspend that succeeded: billing is retried.
        (
            HOSTING,
            hosting_row(
                billing_under_owner,
                number=2,
                hosting="offline",
                reasons="{user_suspended}",
                owner_status_version=3,
            ),
        ),
        *hosting_command(
            billing_under_owner, CommandKind.SUSPEND_HOSTING, 1, "failed", "evt-1", updated_at=at(9)
        ),
        *hosting_command(
            billing_under_owner, CommandKind.SUSPEND_HOSTING, 2, "succeeded", "owner-status:3"
        ),
        # A failed owner suspend of version 3 after version 4 arrived: stale, not retried.
        (HOSTING, hosting_row(stale_owner, number=1, owner_status_version=4)),
        *hosting_command(
            stale_owner, CommandKind.SUSPEND_HOSTING, 1, "failed", "owner-status:3.retry-1"
        ),
        # A failed owner suspend of the current version: retried.
        (HOSTING, hosting_row(current_owner, number=1, owner_status_version=4)),
        *hosting_command(
            current_owner,
            CommandKind.SUSPEND_HOSTING,
            1,
            "failed",
            "owner-status:4",
            updated_at=at(1),
        ),
        # A failed retry of the current owner suspend: retried again.
        (HOSTING, hosting_row(current_retry, number=1, owner_status_version=4)),
        *hosting_command(
            current_retry, CommandKind.SUSPEND_HOSTING, 1, "failed", "owner-status:4.retry-1"
        ),
        # A failed billing resume while only user_suspended is held: billing is gone, so met.
        (
            HOSTING,
            hosting_row(
                met_by_reason,
                number=1,
                hosting="offline",
                reasons="{user_suspended}",
                owner_status_version=2,
            ),
        ),
        *hosting_command(met_by_reason, CommandKind.RESUME_HOSTING, 1, "failed", "evt-r"),
        # A failed deletion suspend after a billing suspend: project_deleted is not held.
        (HOSTING, hosting_row(deleted, number=2, hosting="offline")),
        *hosting_command(deleted, CommandKind.SUSPEND_HOSTING, 1, "succeeded", "evt-b"),
        *hosting_command(deleted, CommandKind.SUSPEND_HOSTING, 2, "failed", DELETION_EVENT_KEY),
    )
    found = await history.projects_to_retry(limit=10, budget=3)
    assert sorted(found) == sorted([billing_under_owner, current_owner, current_retry, deleted])
    assert len(set(found)) == len(found)  # one entry per project, also with several reasons
    # The oldest failure first: current_owner failed at 1 s, billing_under_owner at 9 s.
    assert found.index(current_owner) < found.index(billing_under_owner)


async def test_projects_to_retry_leaves_out_a_retry_key_that_would_be_too_long(
    db: Database, history: PgHostingHistory
) -> None:
    """The retry key is the root key plus ``.retry-<n>``: a project whose next key would pass
    255 bytes (``MAX_IDEMPOTENCY_KEY_LEN``, UTF-8 bytes as in the domain) is never listed, so it
    cannot take a place of the sweep every time and come back unretryable."""
    fits, fits_retry, too_long, too_long_retry, multibyte = (uuid4() for _ in range(5))
    cases = (
        (fits, "k" * 247),  # + ".retry-1" is 255 bytes
        (fits_retry, "k" * 246 + ".retry-9"),  # + ".retry-10" is 255 bytes
        (too_long, "k" * 248),  # 256 bytes
        (too_long_retry, "k" * 247 + ".retry-9"),  # 256 bytes
        (multibyte, "\u00e9" * 124),  # 248 bytes in 124 characters; the retry key is 256 bytes
    )
    rows: list[tuple[str, Row]] = []
    for project, key in cases:
        rows.append((HOSTING, hosting_row(project, number=1)))
        rows.extend(hosting_command(project, CommandKind.SUSPEND_HOSTING, 1, "failed", key))
    seed_rows(db, *rows)
    found = await history.projects_to_retry(limit=10, budget=10)
    assert sorted(found) == sorted([fits, fits_retry])


async def test_a_deleted_project_is_known_by_its_fixed_key(
    db: Database, history: PgHostingHistory, intake: AcceptHostingEventHandler
) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, number=0)))
    assert not await history.project_deleted(project)
    deleted = await intake.handle(
        AcceptHostingEventCommand(DELETION_EVENT_KEY, project, CommandKind.SUSPEND_HOSTING)
    )
    assert deleted.command.idempotency_key == DELETION_EVENT_KEY
    assert await history.project_deleted(project)
    again = await intake.handle(
        AcceptHostingEventCommand(DELETION_EVENT_KEY, project, CommandKind.SUSPEND_HOSTING)
    )
    assert again.replayed and again.command.id == deleted.command.id
    other = uuid4()
    seed_rows(db, (HOSTING, hosting_row(other, number=0)))
    await intake.handle(
        AcceptHostingEventCommand("payment-evt-x", other, CommandKind.SUSPEND_HOSTING)
    )
    assert not await history.project_deleted(other)  # a Payment suspend is not a deletion


async def test_a_resume_after_deletion_ends_skipped_on_the_store(
    db: Database, store: PgRouteCommandStore, history: PgHostingHistory
) -> None:
    """Answer 4: a later "entitlement restored" never resumes a deleted project, also after a
    worker restart (the record knows no event; the deletion is read from the database).

    FR-28.1."""
    s = scene(
        db,
        CommandKind.RESUME_HOSTING,
        candidate_status=None,
        hosting="offline",
        reasons="{billing,project_deleted}",
    )
    deletion = uuid4()
    seed_rows(
        db,
        (
            COMMANDS,
            command_row(
                s.project,
                CommandKind.SUSPEND_HOSTING,
                NUMBER - 1,
                "succeeded",
                key=DELETION_EVENT_KEY,
                command_id=deletion,
            ),
        ),
        (DISPATCH, dispatch_row(deletion, finished_at=at(1))),
    )
    log: list[str] = []
    cluster = FakeCluster(log, serving=set() if s.live is None else {s.live})
    record = PrototypeHostingRecord(max_projects=10, deletions=history)
    hosting = Hosting(
        store=store, cluster=cluster, entitlement=record, clock=_Clock(), settings=settings()
    )
    await hosting.resume(claim_of(s, CommandKind.RESUME_HOSTING, None))
    assert command_state(db, s.command)[:2] == ("skipped", "entitlement_ended")
    assert hosting_state(db, s.project)[:2] == ("stable", "offline")
    assert cluster.applied_targets() == []  # the route was never touched


# --- the redeploy's writes (decision 0023 G12) ----------------------------------------------------


def hosting_updated_at(db: Database, project: UUID) -> object:
    return scalar(
        db, "SELECT updated_at FROM provisioning.project_hosting WHERE project_id = %s", project
    )


async def test_a_redeploy_that_succeeds_writes_only_its_command(
    db: Database, store: PgRouteCommandStore
) -> None:
    """Notion "redeploy": no new release, the release and the route state unchanged, not a
    publish. The one change is the command's end; no usage event is written."""
    s = scene(db, CommandKind.REDEPLOY, names="live", candidate_status=None)
    hosting, updated = hosting_state(db, s.project), hosting_updated_at(db, s.project)
    release = release_state(db, s.live)

    await store.apply_route_outcome(s.fence, lambda i: redeploy_succeeded(i, now()))

    assert command_state(db, s.command) == ("succeeded", None, True, None)
    assert hosting_state(db, s.project) == hosting == ("stable", "online", s.live, NUMBER)
    assert hosting_updated_at(db, s.project) == updated
    assert release_state(db, s.live) == release == ("live", None, True)
    assert usage_rows(db) == []


@pytest.mark.parametrize("code", ["redeploy_unready", "redeploy_target_changed"])
async def test_a_redeploy_that_fails_writes_only_its_command(
    db: Database, store: PgRouteCommandStore, code: str
) -> None:
    s = scene(db, CommandKind.REDEPLOY, names="live", candidate_status=None)
    hosting = hosting_state(db, s.project)

    await store.apply_route_outcome(
        s.fence, lambda i: fail_without_route_change(i, Failure(code), False, now())
    )

    assert command_state(db, s.command) == ("failed", code, True, code)
    assert hosting_state(db, s.project) == hosting
    assert release_state(db, s.live) == ("live", None, True)
    assert usage_rows(db) == []


@dataclass
class _RolloutCluster(FakeCluster):
    """The unit tests' cluster fake with a restart that lands and a rollout that is ready."""

    restarted: list[ReleaseWorkload] = field(default_factory=list)

    async def restart_release(self, workload: ReleaseWorkload) -> None:
        self.log.append(f"restart_release #{workload.command_number}")
        self.restarted.append(workload)

    async def release_status(self, workload: ReleaseWorkload) -> WorkloadStatus:
        self.log.append(f"release_status #{workload.command_number}")
        return WorkloadStatus(WorkloadState.READY)


class _NoPause:
    async def pause(self, delay: timedelta) -> None:
        return None


def redeploy_use_case(store: PgRouteCommandStore, cluster: FakeCluster) -> Redeploy:
    return Redeploy(
        store=store,
        cluster=cluster,
        clock=_Clock(),
        pauser=_NoPause(),
        settings=RedeploySettings(
            image_registry="registry.webblock.test:5000",
            poll_interval=timedelta(milliseconds=1),
            call_timeout=timedelta(seconds=5),
            finish_margin=timedelta(seconds=5),
        ),
    )


async def test_the_redeploy_use_case_on_the_store_restarts_the_live_release(
    db: Database, store: PgRouteCommandStore
) -> None:
    s = scene(db, CommandKind.REDEPLOY, names="live", candidate_status=None)
    cluster = _RolloutCluster([])

    await redeploy_use_case(store, cluster).run(claim_of(s, CommandKind.REDEPLOY, s.live))

    assert [(w.release_id, w.command_number) for w in cluster.restarted] == [(s.live, NUMBER)]
    assert command_state(db, s.command) == ("succeeded", None, True, None)
    assert hosting_state(db, s.project) == ("stable", "online", s.live, NUMBER)
    assert cluster.applied_targets() == []
    assert usage_rows(db) == []


async def test_the_redeploy_use_case_on_the_store_refuses_a_release_no_longer_live(
    db: Database, store: PgRouteCommandStore
) -> None:
    """Review Focus 3 on PostgreSQL: the command names a release that is no longer the live one
    (here the candidate, as if the live release had moved): it fails ``redeploy_target_changed``
    and nothing is restarted."""
    s = scene(db, CommandKind.REDEPLOY, names="candidate", candidate_status="superseded")
    cluster = _RolloutCluster([])

    await redeploy_use_case(store, cluster).run(claim_of(s, CommandKind.REDEPLOY, s.candidate))

    assert cluster.restarted == []
    assert cluster.log == []
    assert command_state(db, s.command) == (
        "failed",
        "redeploy_target_changed",
        True,
        "redeploy_target_changed",
    )
    assert hosting_state(db, s.project) == ("stable", "online", s.live, NUMBER)
