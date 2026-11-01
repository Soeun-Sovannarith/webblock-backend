"""The go-live tries on PostgreSQL 18 (TR-57; decision 0027).

Each try is one claim of the promote command through the real ``PgJobStore``; the real
``Routing`` writes through the real ``PgRouteCommandStore`` on a login that is a member of
``provisioning_worker`` alone, against the fake cluster of the unit tests. A try that fails gives
its claim back as the consumer does: ``release_claim`` with the ``RetryLater``'s code and wait. A
test moves ``retry_after`` into the past through the admin connection instead of waiting.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration._pg import ROLE_API, ROLE_WORKER, Database
from tests.integration.test_store_support import (
    COMMANDS,
    DISPATCH,
    HOSTING,
    RELEASES,
    TIMEOUTS,
    Row,
    command_row,
    dispatch_row,
    hosting_row,
    release_row,
    role_engine,
    row_of,
    scalar,
    seed_rows,
)
from tests.unit.core.application.routing.background.test_routing_support import (
    OFFLINE,
    PROJECT,
    SUBDOMAIN,
    FakeCluster,
    FakeEntitlement,
    Route,
    settings,
    to_release,
)

from provisioning.core.application import (
    Claim,
    ClaimLost,
    ClaimOutcome,
    ClaimRequest,
    ReleaseKey,
    RetryLater,
    WorkRef,
)
from provisioning.core.application.routing.background.routing import Routing
from provisioning.core.domain import CommandKind, ReleaseStatus, confirm_live, start_route_change
from provisioning.infrastructure.postgres import PgJobStore, PgReadStore
from provisioning.infrastructure.postgres.routing_store import PgRouteCommandStore

pytestmark = pytest.mark.asyncio

NUMBER = 1
TTL = timedelta(minutes=5)
# The generic backoff the consumer uses for a RetryLater without a wait (none is raised here).
BACKOFF = timedelta(seconds=1)


def _now() -> datetime:
    return datetime.now(UTC)


class _Clock:
    def now(self) -> datetime:
        return _now()


class Go:
    """One project at command number 1 with an accepted promote of its candidate, the worker's
    stores, and the routing use case on them."""

    def __init__(self, db: Database, engine: AsyncEngine, *, live: bool = True) -> None:
        self.db = db
        self.live: UUID | None = uuid4() if live else None
        self.candidate = uuid4()
        self.command = uuid4()
        rows: list[tuple[str, Row]] = []
        if self.live is not None:
            rows.append((RELEASES, release_row(PROJECT, "live", release_id=self.live, name="live")))
        rows += [
            (
                RELEASES,
                release_row(
                    PROJECT, "candidate_ready", release_id=self.candidate, name="candidate"
                ),
            ),
            (HOSTING, hosting_row(PROJECT, number=NUMBER, live=self.live, subdomain=SUBDOMAIN)),
            (
                COMMANDS,
                command_row(
                    PROJECT,
                    CommandKind.PROMOTE,
                    NUMBER,
                    "accepted",
                    release=self.candidate,
                    command_id=self.command,
                ),
            ),
            (DISPATCH, dispatch_row(self.command)),
        ]
        seed_rows(db, *rows)
        self.ref = WorkRef(
            command_id=self.command,
            project_id=PROJECT,
            kind=CommandKind.PROMOTE,
            command_number=NUMBER,
            release_id=self.candidate,
        )
        self.log: list[str] = []
        serving = {self.candidate} | ({self.live} if self.live is not None else set())
        self.cluster = FakeCluster(self.log, serving=serving, answering={self.candidate})
        if self.live is not None:
            self.cluster.route = Route(to_release(self.live), 0)
        self.jobs = PgJobStore(engine, TIMEOUTS)
        self.routes = PgRouteCommandStore(engine, TIMEOUTS)
        self.routing = Routing(
            store=self.routes,
            cluster=self.cluster,
            entitlement=FakeEntitlement(self.log),
            clock=_Clock(),
            settings=settings(),
        )

    def site_stops_answering(self) -> None:
        self.cluster.preview_answering = {self.candidate}
        self.cluster.answering = set()

    async def claim(self) -> Claim:
        result = await self.jobs.claim(ClaimRequest(self.ref, "worker-1", TTL))
        assert result.outcome == ClaimOutcome.CLAIMED, result.outcome
        assert result.claim is not None
        return result.claim

    async def run(self) -> RetryLater | None:
        """One claim and its try; a failed try 1 or 2 gives the claim back as the consumer does."""
        claim = await self.claim()
        try:
            await self.routing.promote(claim)
        except RetryLater as raised:
            await self.jobs.release_claim(claim, raised.after or BACKOFF, raised.code)
            return raised
        return None

    def wait_is_over(self) -> None:
        with self.db.admin() as conn:
            conn.execute(
                "UPDATE provisioning_ops.command_dispatch"
                " SET retry_after = now() - interval '1 second'"
            )

    async def tries(self, count: int) -> list[RetryLater | None]:
        out: list[RetryLater | None] = []
        for _ in range(count):
            out.append(await self.run())
            self.wait_is_over()
        return out

    def release(self, release_id: UUID | None) -> tuple[object, ...]:
        return row_of(
            self.db,
            "SELECT status::text, failure_code FROM provisioning.releases WHERE release_id = %s",
            release_id,
        )

    def hosting(self) -> tuple[object, ...]:
        return row_of(
            self.db,
            "SELECT routing_status::text, live_release_id FROM provisioning.project_hosting"
            " WHERE project_id = %s",
            PROJECT,
        )

    def command_row(self) -> tuple[object, ...]:
        return row_of(
            self.db,
            "SELECT c.status::text, c.failure_code, d.execution_attempts,"
            " d.finished_at IS NOT NULL, d.last_failure_code"
            " FROM provisioning.commands AS c"
            " JOIN provisioning_ops.command_dispatch AS d USING (command_id)",
        )

    def waiting(self) -> tuple[object, ...]:
        """The dispatch row while a try waits: holder, expiry, seconds until ``retry_after``,
        whether ``next_publish_at`` equals it, and the last failure code."""
        return row_of(
            self.db,
            "SELECT claimed_by_worker, claim_expires_at,"
            " EXTRACT(EPOCH FROM retry_after - now())::float8,"
            " next_publish_at = retry_after, last_failure_code"
            " FROM provisioning_ops.command_dispatch",
        )

    def usage_rows(self) -> object:
        return scalar(self.db, "SELECT count(*) FROM provisioning_ops.usage_outbox")


@pytest_asyncio.fixture
async def worker_engine(db: Database) -> AsyncIterator[AsyncEngine]:
    async with role_engine(db, ROLE_WORKER) as engine:
        yield engine


async def _api_read(db: Database, release_id: UUID) -> tuple[object, ...]:
    """FR-37.3: the release as ``get_release`` reads it, on a login of ``provisioning_api``."""
    async with role_engine(db, ROLE_API) as engine:
        read = await PgReadStore(engine, TIMEOUTS).read_release(ReleaseKey(PROJECT, release_id))
    return read.release.status, read.release.failure_code, read.release.failure_summary


def _wait_between(waiting: tuple[object, ...], seconds: float, code: str) -> None:
    holder, expires, wait, publish_at_wait, last = waiting
    assert (holder, expires, publish_at_wait, last) == (None, None, True, code)
    assert isinstance(wait, float)
    assert seconds - 5.0 < wait <= seconds


async def test_fr21_1_three_failed_tries_roll_back_with_try_3s_code(
    db: Database, worker_engine: AsyncEngine
) -> None:
    """FR-21.1, FR-37.3: three failed tries; after try 3 the candidate is rolled_back with its
    code, the command failed and finished after 3 tries, the live release still live, routing
    stable, no usage row; the API reads rolled_back and the code."""
    go = Go(db, worker_engine)
    go.site_stops_answering()

    results = await go.tries(3)

    assert [r.code if r else None for r in results] == [
        "site_not_answering",
        "site_not_answering",
        None,
    ]
    assert go.release(go.candidate) == ("rolled_back", "site_not_answering")
    assert go.release(go.live) == ("live", None)
    assert go.hosting() == ("stable", go.live)
    assert go.command_row()[:4] == ("failed", "site_not_answering", 3, True)
    assert go.usage_rows() == 0
    assert go.live is not None
    assert go.cluster.route == Route(to_release(go.live), NUMBER)
    assert await _api_read(db, go.candidate) == (
        ReleaseStatus.ROLLED_BACK,
        "site_not_answering",
        None,
    )


async def test_fr21_2_after_try_1_the_row_waits_30_s_and_the_release_stays_promoting(
    db: Database, worker_engine: AsyncEngine
) -> None:
    """FR-21.2, FR-37.3: after a failed try 1 the dispatch row holds no claim and waits 30 s
    with the try's code; the command runs, the release is promoting, the live release unchanged;
    the API reads promoting with no failure code or summary."""
    go = Go(db, worker_engine)
    go.site_stops_answering()

    raised = await go.run()

    assert raised is not None and raised.after == timedelta(seconds=30)
    _wait_between(go.waiting(), 30.0, "site_not_answering")
    assert go.command_row()[:4] == ("running", None, 1, False)
    assert go.release(go.candidate) == ("promoting", None)
    assert go.hosting() == ("changing", go.live)
    assert await _api_read(db, go.candidate) == (ReleaseStatus.PROMOTING, None, None)
    # A duplicate delivery during the wait is BACKOFF and counts no try.
    early = await go.jobs.claim(ClaimRequest(go.ref, "worker-2", TTL))
    assert early.outcome == ClaimOutcome.BACKOFF
    assert go.command_row()[2] == 1


async def test_fr21_3_claim_2_passing_goes_live_with_one_usage_row(
    db: Database, worker_engine: AsyncEngine
) -> None:
    """FR-21.3: try 1 fails, try 2 passes: the candidate live, the old live superseded, the
    command succeeded after 2 tries, one usage row."""
    go = Go(db, worker_engine)
    go.site_stops_answering()
    await go.tries(1)

    go.cluster.answering = {go.candidate}
    assert await go.run() is None

    assert go.release(go.candidate) == ("live", None)
    assert go.release(go.live) == ("superseded", None)
    assert go.hosting() == ("stable", go.candidate)
    assert go.command_row()[:4] == ("succeeded", None, 2, True)
    assert go.usage_rows() == 1


async def test_fr21_4_after_try_2_the_row_waits_60_s(
    db: Database, worker_engine: AsyncEngine
) -> None:
    """FR-21.4: after a failed try 2 the row waits 60 s with try 2's code."""
    go = Go(db, worker_engine)
    go.site_stops_answering()
    await go.tries(1)
    go.cluster.serving.discard(go.candidate)  # try 2's preview route is never accepted

    raised = await go.run()

    assert raised is not None
    assert (raised.code, raised.after) == ("route_not_accepted", timedelta(seconds=60))
    _wait_between(go.waiting(), 60.0, "route_not_accepted")
    assert go.command_row()[:3] == ("running", None, 2)
    assert go.release(go.candidate) == ("promoting", None)


async def test_fr21_5_a_lapsed_claim_is_try_2_and_its_writes_are_refused(
    db: Database, worker_engine: AsyncEngine
) -> None:
    """FR-21.5, FR-25: claim 1 recorded the intent and its switch landed, then its worker died.
    Once the claim lapses the next claim is try 2; claim 1's late write is refused, and try 2
    puts the route back before its preview, then goes live."""
    go = Go(db, worker_engine)
    first = await go.claim()
    await go.routes.apply_route_outcome(first.fence(), lambda i: start_route_change(i, _now()))
    go.cluster.route = Route(to_release(go.candidate), NUMBER)  # claim 1's switch landed
    with db.admin() as conn:
        conn.execute(
            "UPDATE provisioning_ops.command_dispatch"
            " SET claim_expires_at = now() - interval '1 second'"
        )

    second = await go.claim()
    assert (second.execution_attempt, second.claim_number) == (2, 2)
    with pytest.raises(ClaimLost):
        await go.routes.apply_route_outcome(first.fence(), lambda i: confirm_live(i, _now()))

    go.log.clear()
    await go.routing.promote(second)

    assert go.log.index(f"apply_route release {go.live} #{NUMBER}") < go.log.index(
        f"apply_preview release {go.candidate} #{NUMBER}"
    )
    assert go.release(go.candidate) == ("live", None)
    assert go.command_row()[:3] == ("succeeded", None, 2)


async def test_fr21_6_a_preview_failure_leaves_the_release_promoting(
    db: Database, worker_engine: AsyncEngine
) -> None:
    """FR-21.6: the candidate never answers its preview on try 1: the intent is recorded, the
    site's route never moves, the release is promoting and the row waits 30 s."""
    go = Go(db, worker_engine)
    go.cluster.answering = set()

    raised = await go.run()

    assert raised is not None
    assert (raised.code, raised.after) == ("candidate_not_answering", timedelta(seconds=30))
    assert go.cluster.applied_targets() == []
    assert go.release(go.candidate) == ("promoting", None)
    assert go.hosting() == ("changing", go.live)
    _wait_between(go.waiting(), 30.0, "candidate_not_answering")


async def test_fr22_1_three_failed_tries_of_a_first_deployment_fail_the_release(
    db: Database, worker_engine: AsyncEngine
) -> None:
    """FR-22.1: a first deployment whose three tries fail: the release failed with try 3's
    code, the route on the offline page, routing stable, no live release."""
    go = Go(db, worker_engine, live=False)
    go.site_stops_answering()

    await go.tries(3)

    assert go.release(go.candidate) == ("failed", "site_not_answering")
    assert go.hosting() == ("stable", None)
    assert go.command_row()[:4] == ("failed", "site_not_answering", 3, True)
    assert go.cluster.route == Route(OFFLINE, NUMBER)
    assert go.usage_rows() == 0


async def test_fr22_2_between_tries_of_a_first_deployment_the_release_stays_promoting(
    db: Database, worker_engine: AsyncEngine
) -> None:
    """FR-22.2: after a failed try 1 of a first deployment the site shows the offline page, the
    release is promoting and the row waits 30 s."""
    go = Go(db, worker_engine, live=False)
    go.cluster.answering = set()

    raised = await go.run()

    assert raised is not None and raised.after == timedelta(seconds=30)
    assert go.cluster.route == Route(OFFLINE, NUMBER)
    assert go.release(go.candidate) == ("promoting", None)
    assert go.hosting() == ("changing", None)
    _wait_between(go.waiting(), 30.0, "candidate_not_answering")
