"""The build hint on PostgreSQL 18 (task H2; decision 0016 section 5 item 2).

The API's build_release (``RequestBuild`` over ``PgBuildRequests``) runs as a login of
``provisioning_api`` alone: it reads the pending build step and sends ``pg_notify``, which needs
no grant, and writes nothing. The build worker listens as a login of ``provisioning_build``
(``PgBuildHintListener``) and starts the hinted release through ``StartBuild`` at once:

- the hint starts the build long before the grace period (one hour here) would;
- a hint sent while nobody listens is lost, and the grace scan still starts the attempt;
- a malformed payload is ignored, and the listener goes on to start the next valid hint.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine
from tests.integration._pg import ROLE_API, ROLE_BUILD, Database
from tests.integration.test_store_support import (
    HOSTING,
    RELEASES,
    STEPS,
    TIMEOUTS,
    hosting_row,
    release_row,
    role_engine,
    seed_rows,
    step_row,
)

from provisioning.core.application import (
    BuildDispatch,
    BuildStepKey,
    PollRoundParams,
    StartBuild,
)
from provisioning.core.application.builds.background.poll_builds import (
    PendingOutcome,
    StartPendingBuilds,
)
from provisioning.core.application.builds.background.start_build import (
    BUILD_WANTED_CHANNEL,
    WantedBuilds,
)
from provisioning.core.application.builds.request_build import RequestBuild
from provisioning.core.application.ports.build_system import (
    BuildHandle,
    BuildRequest,
    BuildStatus,
)
from provisioning.core.domain import BuildAttemptPolicy, ReleaseStepStatus
from provisioning.infrastructure.postgres.build_requests import PgBuildRequests
from provisioning.infrastructure.postgres.build_store import PgBuildHintListener, PgBuildStore

pytestmark = pytest.mark.asyncio

GRACE = timedelta(hours=1)
LOG = logging.getLogger("tests.build_hints")


class _Jenkins:
    """A build system that holds every build it is asked to start."""

    def __init__(self) -> None:
        self.started: list[UUID] = []
        self.called = asyncio.Event()

    async def find(self, request: BuildRequest) -> BuildHandle | None:
        return None

    async def start(self, request: BuildRequest) -> BuildHandle:
        self.started.append(request.release_id)
        self.called.set()
        return BuildHandle(ref=f"job/site/{len(self.started)}")

    async def status(self, handle: BuildHandle) -> BuildStatus:
        raise AssertionError("not polled here")

    async def cancel(self, handle: BuildHandle) -> None:
        raise AssertionError("not cancelled here")


class _Repositories:
    async def repository_url(self, project_id: UUID, repository_id: int) -> str:
        return f"http://git.test/{project_id}.git"


class _Ids:
    def new_id(self) -> UUID:
        return uuid4()


def _fresh_release(db: Database, *, accepted_ago: timedelta = timedelta(0)) -> BuildStepKey:
    """An accepted release whose pending first attempt is ``accepted_ago`` old."""
    key = BuildStepKey(uuid4(), uuid4(), uuid4())
    seed_rows(
        db,
        (
            RELEASES,
            release_row(
                key.project_id,
                "accepted",
                release_id=key.release_id,
                updated_at=datetime.now(UTC) - accepted_ago,
            ),
        ),
        (HOSTING, hosting_row(key.project_id)),
        (STEPS, step_row(key.release_id, 1, "pending", release_step_id=key.step_id)),
    )
    return key


def _starter(build_engine: AsyncEngine, jenkins: _Jenkins) -> StartBuild:
    return StartBuild(
        store=PgBuildStore(build_engine, TIMEOUTS),
        build_system=jenkins,
        repositories=_Repositories(),
        ids=_Ids(),
        logger=LOG,
        policy=BuildAttemptPolicy(max_attempts=3),
        image_registry="registry.test:5000",
        build_duration=timedelta(minutes=20),
        call_timeout=timedelta(seconds=5),
        actor="build-worker",
    )


async def _step_status(build_engine: AsyncEngine, key: BuildStepKey) -> ReleaseStepStatus:
    target = await PgBuildStore(build_engine, TIMEOUTS).load_build_target(
        key.project_id, key.release_id
    )
    assert target.step is not None
    return target.step.status


async def _until_started(build_engine: AsyncEngine, jenkins: _Jenkins, key: BuildStepKey) -> None:
    """The build system was asked to start a build, so the step went running first."""
    async with asyncio.timeout(10):
        await jenkins.called.wait()
    assert await _step_status(build_engine, key) is ReleaseStepStatus.RUNNING


@asynccontextmanager
async def _listening(build_engine: AsyncEngine, wanted: WantedBuilds) -> AsyncIterator[None]:
    """The build worker's listener and its start loop, with the LISTEN in force."""
    ready = asyncio.Event()
    listener = PgBuildHintListener(build_engine, TIMEOUTS, liveness=timedelta(seconds=1))
    async with asyncio.TaskGroup() as tg:
        listen = tg.create_task(listener.listen(wanted.offer, ready.set))
        run = tg.create_task(wanted.run())
        async with asyncio.timeout(10):
            await ready.wait()
        try:
            yield
        finally:
            listen.cancel()
            run.cancel()


async def test_the_hint_starts_the_build_long_before_the_grace(db: Database) -> None:
    key = _fresh_release(db)
    jenkins = _Jenkins()
    async with role_engine(db, ROLE_API) as api, role_engine(db, ROLE_BUILD) as build:
        starter = _starter(build, jenkins)
        # Nothing is older than the grace, so the scan alone would not start it for an hour.
        pending = StartPendingBuilds(
            store=PgBuildStore(build, TIMEOUTS), starter=starter, grace=GRACE
        )
        assert await pending.start_round(PollRoundParams(limit=10, concurrency=1)) == []
        wanted = WantedBuilds(starter=starter, queue_size=8, logger=LOG)
        async with _listening(build, wanted):
            started = await RequestBuild(store=PgBuildRequests(api, TIMEOUTS), logger=LOG).start(
                key.project_id, key.release_id
            )
            assert started.dispatch is BuildDispatch.PENDING
            assert started.step_id == key.step_id
            await _until_started(build, jenkins, key)
    assert jenkins.started == [key.release_id]


async def test_the_api_writes_nothing_when_it_requests_a_build(db: Database) -> None:
    key = _fresh_release(db)
    async with role_engine(db, ROLE_API) as api, role_engine(db, ROLE_BUILD) as build:
        await RequestBuild(store=PgBuildRequests(api, TIMEOUTS), logger=LOG).start(
            key.project_id, key.release_id
        )
        assert await _step_status(build, key) is ReleaseStepStatus.PENDING


async def test_a_lost_hint_is_still_started_by_the_grace_scan(db: Database) -> None:
    key = _fresh_release(db, accepted_ago=timedelta(seconds=10))
    jenkins = _Jenkins()
    async with role_engine(db, ROLE_API) as api, role_engine(db, ROLE_BUILD) as build:
        # Nobody listens: the notification is lost.
        await RequestBuild(store=PgBuildRequests(api, TIMEOUTS), logger=LOG).start(
            key.project_id, key.release_id
        )
        assert await _step_status(build, key) is ReleaseStepStatus.PENDING
        assert jenkins.started == []
        starter = _starter(build, jenkins)
        pending = StartPendingBuilds(
            store=PgBuildStore(build, TIMEOUTS), starter=starter, grace=timedelta(seconds=1)
        )
        results = await pending.start_round(PollRoundParams(limit=10, concurrency=1))
        assert [(r.build.step, r.outcome) for r in results] == [(key, PendingOutcome.STARTED)]
        assert await _step_status(build, key) is ReleaseStepStatus.RUNNING
    assert jenkins.started == [key.release_id]


async def test_a_malformed_hint_is_ignored_and_the_next_valid_one_starts(db: Database) -> None:
    named = _fresh_release(db)  # named only by malformed payloads
    valid = _fresh_release(db)
    jenkins = _Jenkins()
    async with role_engine(db, ROLE_API) as api, role_engine(db, ROLE_BUILD) as build:
        wanted = WantedBuilds(starter=_starter(build, jenkins), queue_size=8, logger=LOG)
        async with _listening(build, wanted):
            async with api.begin() as conn:
                for payload in (
                    "garbage",
                    f"{str(named.project_id).upper()}/{named.release_id}",
                    f"{named.project_id}/{named.release_id}/extra",
                    f"{named.project_id} {named.release_id}",
                ):
                    await conn.execute(select(func.pg_notify(BUILD_WANTED_CHANNEL, payload)))
            await RequestBuild(store=PgBuildRequests(api, TIMEOUTS), logger=LOG).start(
                valid.project_id, valid.release_id
            )
            await _until_started(build, jenkins, valid)
        assert await _step_status(build, named) is ReleaseStepStatus.PENDING
    assert jenkins.started == [valid.release_id]
