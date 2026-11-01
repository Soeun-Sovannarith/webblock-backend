"""The reconciler's reads on PostgreSQL 18 (task W6-B; decision 0014 section 21 ruling 9), on a
login that is a member of ``provisioning_worker`` alone. The last test runs the real
``Reconciler`` on the real store with the in-memory cluster of the unit tests.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration._pg import ROLE_WORKER, Database
from tests.integration.test_store_support import (
    COMMANDS,
    DISPATCH,
    HOSTING,
    RELEASES,
    TIMEOUTS,
    at,
    command_row,
    dispatch_row,
    hosting_row,
    release_row,
    role_engine,
    seed_rows,
)
from tests.unit.core.application.reconcile.background.test_reconcile import Clock, Cluster, to

from provisioning.core.application.reconcile.background.reconcile import (
    Reconciler,
    ReconcileSettings,
)
from provisioning.core.application.routing.background.recovery import ReleaseCleanup
from provisioning.core.application.shared.audit import NullAuditLog
from provisioning.core.domain import CommandKind, HostingStatus, ReleaseStatus, RoutingStatus
from provisioning.infrastructure.postgres.errors import InvalidArgument
from provisioning.infrastructure.postgres.reconcile_store import PgReconcileStore

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def worker_engine(db: Database) -> AsyncIterator[AsyncEngine]:
    async with role_engine(db, ROLE_WORKER) as engine:
        yield engine


def ids(n: int) -> list[UUID]:
    return sorted(uuid4() for _ in range(n))


def project(
    db: Database,
    project_id: UUID,
    *,
    live: bool = True,
    routing: str = "stable",
    hosting: str = "online",
    number: int = 4,
    command: str | None = None,
) -> UUID | None:
    """A hosting row at ``number``, with a live release when ``live``, and a command of the last
    number in status ``command`` when given."""
    live_id = uuid4() if live else None
    rows = []
    if live_id is not None:
        rows.append((RELEASES, release_row(project_id, "live", release_id=live_id, name="live")))
    rows.append(
        (
            HOSTING,
            hosting_row(project_id, number=number, live=live_id, routing=routing, hosting=hosting),
        )
    )
    if command is not None:
        command_id = uuid4()
        rows.append(
            (
                COMMANDS,
                command_row(
                    project_id, CommandKind.SUSPEND_HOSTING, number, command, command_id=command_id
                ),
            )
        )
        finished = None if command in ("accepted", "running") else at(30)
        rows.append((DISPATCH, dispatch_row(command_id, finished_at=finished)))
    seed_rows(db, *rows)
    return live_id


async def test_projects_are_read_in_id_order_in_pages(
    db: Database, worker_engine: AsyncEngine
) -> None:
    p = ids(3)
    for project_id in p:
        project(db, project_id)
    store = PgReconcileStore(worker_engine, TIMEOUTS)

    first = await store.projects_after(None, 2)
    rest = await store.projects_after(first[-1].project_id, 2)

    assert [r.project_id for r in first] == p[:2]
    assert [r.project_id for r in rest] == p[2:]


async def test_a_record_carries_the_hosting_row_the_live_digest_and_the_unfinished_command(
    db: Database, worker_engine: AsyncEngine
) -> None:
    a, b, c = ids(3)
    live = project(db, a, number=7, hosting="offline")
    project(db, b, live=False, routing="uncertain")
    project(db, c, command="running")
    store = PgReconcileStore(worker_engine, TIMEOUTS)

    records = {r.project_id: r for r in await store.projects_after(None, 10)}

    ra, rb, rc = records[a], records[b], records[c]
    assert (ra.last_command_number, ra.hosting_status, ra.live_release_id) == (
        7,
        HostingStatus.OFFLINE,
        live,
    )
    assert ra.live_release_digest is not None and ra.live_release_digest.startswith("sha256:")
    assert not ra.unfinished_command
    assert (rb.routing_status, rb.live_release_id, rb.live_release_digest) == (
        RoutingStatus.UNCERTAIN,
        None,
        None,
    )
    assert rc.unfinished_command


async def test_a_finished_command_is_not_unfinished(
    db: Database, worker_engine: AsyncEngine
) -> None:
    (a,) = ids(1)
    project(db, a, command="succeeded")
    (record,) = await PgReconcileStore(worker_engine, TIMEOUTS).projects_after(None, 10)
    assert not record.unfinished_command


async def test_known_projects_and_release_statuses_are_scoped(
    db: Database, worker_engine: AsyncEngine
) -> None:
    a, b, orphan = ids(3)
    live_a = project(db, a)
    live_b = project(db, b)
    assert live_a is not None and live_b is not None
    old = uuid4()
    seed_rows(db, (RELEASES, release_row(a, "superseded", release_id=old, name="old")))
    store = PgReconcileStore(worker_engine, TIMEOUTS)

    assert await store.known_projects([a, orphan, b]) == frozenset({a, b})
    assert await store.known_projects([]) == frozenset()
    statuses = await store.release_statuses(a, [live_a, old, live_b, uuid4()])
    assert statuses == {live_a: ReleaseStatus.LIVE, old: ReleaseStatus.SUPERSEDED}


async def test_still_idle_needs_stable_the_same_number_and_no_unfinished_command(
    db: Database, worker_engine: AsyncEngine
) -> None:
    a, b, c = ids(3)
    project(db, a, number=4)
    project(db, b, number=4, routing="changing")
    project(db, c, number=4, command="accepted")
    store = PgReconcileStore(worker_engine, TIMEOUTS)

    assert await store.still_idle(a, 4)
    assert not await store.still_idle(a, 3)
    assert not await store.still_idle(b, 4)
    assert not await store.still_idle(c, 4)
    assert not await store.still_idle(uuid4(), 0)


async def test_out_of_range_batches_are_refused(worker_engine: AsyncEngine) -> None:
    store = PgReconcileStore(worker_engine, TIMEOUTS)
    for limit in (0, 1001):
        with pytest.raises(InvalidArgument):
            await store.projects_after(None, limit)


async def test_the_reconciler_on_the_real_store_reapplies_and_sweeps(
    db: Database, worker_engine: AsyncEngine
) -> None:
    a, busy = ids(2)
    live = project(db, a, number=9)
    project(db, busy, number=3, command="running")
    assert live is not None
    old = uuid4()
    seed_rows(db, (RELEASES, release_row(a, "superseded", release_id=old, name="old")))
    log: list[str] = []
    cluster = Cluster(log)
    cluster.workload(a, old)
    cluster.route(a, to(live))
    cluster.workload(busy, uuid4())
    cluster.extra_tenants = (orphan := uuid4(),)
    reconciler = Reconciler(
        store=PgReconcileStore(worker_engine, TIMEOUTS),
        cluster=cluster,
        cleanup=ReleaseCleanup(
            cluster=cluster,
            audit=NullAuditLog(),
            clock=Clock(),
            delete_timeout=timedelta(seconds=5),
            logger=logging.getLogger("test.cleanup"),
        ),
        settings=ReconcileSettings(
            site_domain="sites.webblock.test",
            image_registry="registry.webblock.test:5000",
            batch_size=10,
            call_timeout=timedelta(seconds=5),
        ),
        logger=logging.getLogger("test.reconcile"),
    )

    report = await reconciler.round()

    assert report.skipped == (busy,)
    assert {name for _, _, name in report.reapplied} == {f"rel-{live}"}
    assert report.deleted == ((a, old),)
    assert report.orphans == (orphan,)
    assert all("#9" in line for line in log if line.startswith(("apply", "delete")))
    assert cluster.forced == []
