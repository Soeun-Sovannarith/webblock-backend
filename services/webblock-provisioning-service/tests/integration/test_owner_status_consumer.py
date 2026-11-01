"""The worker's owner-status consumer on PostgreSQL at the latest migration (decision 0021 D9,
decision 0022 Task 5): v1 bodies decoded by ``EventCodecV1``, the real ``AcceptOwnerStatusHandler``
and ``HostingIntakeStore`` on a login of ``provisioning_hosting_intake`` alone; each delivery is
settled only after the database answered."""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration._pg import ROLE_HOSTING_INTAKE, Database, q, seed
from tests.integration.test_store_support import (
    HOSTING,
    TIMEOUTS,
    hosting_row,
    role_engine,
    row_of,
    scalar,
    seed_rows,
)
from tests.unit._buses import intake_bus
from tests.unit._loop_fakes import FakeDelivery
from tests.unit.core.application.routing.background.test_routing_support import FakeIds

from provisioning.core.application import AcceptOwnerStatusHandler, NullAuditLog, OwnerStatus
from provisioning.core.application.hosting.background.retry import RetryResult
from provisioning.core.application.shared.backoff import Backoff
from provisioning.infrastructure.postgres import HostingIntakeStore
from provisioning.presentation.messaging import EventCodecV1
from provisioning.presentation.messaging.consumers.messages import EventConsumerConfig
from provisioning.presentation.messaging.consumers.payment_events import OwnerStatusConsumer

pytestmark = pytest.mark.asyncio

MS = timedelta(milliseconds=1)
CODEC = EventCodecV1(max_body_bytes=4096)


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _NoRetries:
    async def retry(self, project_id: UUID) -> RetryResult:
        raise AssertionError(f"no hosting retry expected for {project_id}")


@pytest_asyncio.fixture
async def engine(db: Database) -> AsyncIterator[AsyncEngine]:
    async with role_engine(db, ROLE_HOSTING_INTAKE) as found:
        yield found


@pytest.fixture
def consumer(engine: AsyncEngine) -> OwnerStatusConsumer:
    clock = _Clock()
    return OwnerStatusConsumer(
        EventConsumerConfig(
            concurrency=1, store_hold=Backoff(base=MS, max=2 * MS), store_failure_limit=5
        ),
        decoder=CODEC,
        commands=intake_bus(
            owner_status_handler=AcceptOwnerStatusHandler(
                store=HostingIntakeStore(engine, TIMEOUTS),
                clock=clock,
                ids=FakeIds(),
                command_deadline=timedelta(hours=1),
                audit=NullAuditLog(),
                logger=logging.getLogger("test.owner_status_consumer"),
            )
        ),
        retries=_NoRetries(),
        audit=NullAuditLog(),
        clock=clock,
        logger=logging.getLogger("test.owner_status_consumer"),
        retry_backoff=Backoff(base=MS, max=4 * MS),
        max_retries=3,
    )


def delivery(project: UUID, status: OwnerStatus, version: int, subdomain: str) -> FakeDelivery:
    message = CODEC.encode_owner_status_changed(
        message_id=uuid.uuid7(),
        project_id=project,
        status=status,
        status_version=version,
        subdomain=subdomain,
        occurred_at=datetime.now(UTC),
    )
    return FakeDelivery(
        message.body,
        content_type=message.content_type,
        message_type=message.message_type,
        message_id=message.message_id,
    )


def hosting_commands(db: Database, project: UUID) -> list[tuple[object, ...]]:
    with db.admin() as conn:
        found = conn.execute(
            q(
                "SELECT kind::text, idempotency_key, hosting_reason::text, status::text"
                " FROM provisioning.commands WHERE project_id = %s ORDER BY command_number"
            ),
            (project,),
        ).fetchall()
    return [tuple(row) for row in found]


async def test_suspend_then_stale_then_resume_on_a_serving_project(
    db: Database, consumer: OwnerStatusConsumer
) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, number=0)))

    suspended = delivery(project, OwnerStatus.SUSPENDED, 1, "shop")
    await consumer.handle(suspended)
    assert suspended.actions == ["ack"]
    assert hosting_commands(db, project) == [
        ("suspend_hosting", "owner-status:1", "user_suspended", "accepted")
    ]

    # The worker runs the suspend: the site goes offline for the owner's suspension.
    seed(
        db,
        (
            "UPDATE provisioning.commands SET status = 'succeeded' WHERE project_id = %s",
            [project],
        ),
        (
            "UPDATE provisioning.project_hosting SET hosting_status = 'offline',"
            " offline_reasons = '{user_suspended}' WHERE project_id = %s",
            [project],
        ),
    )

    # The same event redelivered is acked and adds no command.
    redelivered = delivery(project, OwnerStatus.SUSPENDED, 1, "shop")
    await consumer.handle(redelivered)
    assert redelivered.actions == ["ack"]
    assert len(hosting_commands(db, project)) == 1

    # The same version again, now "active": not newer than the row's version 1, so it changes
    # nothing and is acked (decision 0023 G17; Notion: the newest wins). Phase B dead-lettered it.
    same_version = delivery(project, OwnerStatus.ACTIVE, 1, "shop")
    await consumer.handle(same_version)
    assert same_version.actions == ["ack"]
    assert len(hosting_commands(db, project)) == 1
    assert (
        scalar(
            db,
            "SELECT owner_status_version FROM provisioning.project_hosting WHERE project_id = %s",
            project,
        )
        == 1
    )

    active = delivery(project, OwnerStatus.ACTIVE, 2, "shop")
    await consumer.handle(active)
    assert active.actions == ["ack"]
    assert hosting_commands(db, project)[1] == (
        "resume_hosting",
        "owner-status:2",
        "user_suspended",
        "accepted",
    )
    assert (
        scalar(
            db,
            "SELECT owner_status_version FROM provisioning.project_hosting WHERE project_id = %s",
            project,
        )
        == 2
    )


async def test_an_older_version_is_stale_and_acked_without_a_command(
    db: Database, consumer: OwnerStatusConsumer
) -> None:
    project = uuid4()
    seed_rows(db, (HOSTING, hosting_row(project, number=0, owner_status_version=5)))
    stale = delivery(project, OwnerStatus.SUSPENDED, 3, "shop")
    await consumer.handle(stale)
    assert stale.actions == ["ack"]
    assert hosting_commands(db, project) == []
    assert (
        scalar(
            db,
            "SELECT owner_status_version FROM provisioning.project_hosting WHERE project_id = %s",
            project,
        )
        == 5
    )


async def test_an_event_for_a_project_without_a_hosting_row_inserts_it(
    db: Database, consumer: OwnerStatusConsumer
) -> None:
    project = uuid4()
    event = delivery(project, OwnerStatus.SUSPENDED, 4, "sunny-bakery")
    await consumer.handle(event)
    assert event.actions == ["ack"]
    assert row_of(
        db,
        "SELECT hosting_status::text, offline_reasons::text[], owner_status_version, subdomain"
        " FROM provisioning.project_hosting WHERE project_id = %s",
        project,
    ) == ("offline", ["user_suspended"], 4, "sunny-bakery")
    assert hosting_commands(db, project) == []


async def test_s2_a_taken_subdomain_is_dead_lettered_at_once_and_writes_nothing(
    db: Database, consumer: OwnerStatusConsumer
) -> None:
    """FR-54.1 (TR-53; Task 12 security review S2): an event that would give a second project a
    sub-domain another project holds is a permanent refusal: dead-lettered on its first delivery,
    never held or requeued, and no row is written."""
    owner, intruder = uuid4(), uuid4()
    first = delivery(owner, OwnerStatus.ACTIVE, 1, "victim-shop")
    await consumer.handle(first)
    assert first.actions == ["ack"]
    taken = delivery(intruder, OwnerStatus.ACTIVE, 1, "victim-shop")
    await consumer.handle(taken)
    assert taken.actions == ["dead_letter"]
    with db.admin() as conn:
        found = conn.execute(
            q("SELECT count(*) FROM provisioning.project_hosting WHERE project_id = %s"),
            (intruder,),
        ).fetchone()
    assert found is not None and found[0] == 0
