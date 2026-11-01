"""The build/API split (decision 0016 section 5 item 2, task H2).

- ``RequestBuild``, the API's build_release: the pending build step that the intake or a failed
  attempt wrote is the durable wish; the API checks it with its read grant, sends the hint and
  writes nothing. A release that cannot start is refused as before; a hint that cannot be sent
  is logged and the request still succeeds (the grace scan starts the attempt).
- The hint payload ``<project>/<release>``: canonical lowercase UUIDs only.
- ``WantedBuilds``, the build worker's half: a bounded queue of hints, each started through
  ``StartBuild``; malformed hints and hints beyond the bound are dropped with a log line, and a
  start that is no longer possible is not an error.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from provisioning.core.application import (
    BuildDispatch,
    BuildInProgress,
    BuildStarted,
    BuildTarget,
    Contention,
    NotFound,
    ReleaseNotBuildable,
)
from provisioning.core.application.builds.background.start_build import (
    BUILD_WANTED_CHANNEL,
    MAX_BUILD_HINT_BYTES,
    WantedBuilds,
    build_hint,
    parse_build_hint,
)
from provisioning.core.application.builds.request_build import RequestBuild
from provisioning.core.domain import (
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    new_release,
    new_release_step,
)

T0 = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
STEP = UUID("0190a1b2-0000-7000-8000-0000000000e1")
REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
SHA = "0123456789abcdef0123456789abcdef01234567"


def _release(status: ReleaseStatus = ReleaseStatus.ACCEPTED) -> Release:
    release = new_release(
        release_id=RELEASE,
        project_id=PROJECT,
        generation_request_id=REQUEST,
        repository_id=4242,
        git_commit_sha=SHA,
        now=T0,
    )
    return release if status is ReleaseStatus.ACCEPTED else replace(release, status=status)


def _step(status: ReleaseStepStatus = ReleaseStepStatus.PENDING) -> ReleaseStep:
    step = new_release_step(step_id=STEP, release_id=RELEASE, name=ReleaseStepName.BUILD)
    return replace(step, status=status)


class _Requests:
    """The API's build request store: one target, the hints it was asked to send."""

    def __init__(self, target: BuildTarget | Exception, hint_error: Exception | None = None):
        self.target = target
        self.hint_error = hint_error
        self.hints: list[tuple[UUID, UUID]] = []

    async def load_build_target(self, project_id: UUID, release_id: UUID) -> BuildTarget:
        if isinstance(self.target, Exception):
            raise self.target
        return self.target

    async def build_wanted(self, project_id: UUID, release_id: UUID) -> None:
        if self.hint_error is not None:
            raise self.hint_error
        self.hints.append((project_id, release_id))


def test_the_hint_is_project_slash_release_on_a_fixed_channel() -> None:
    assert BUILD_WANTED_CHANNEL == "provisioning_build_wanted"
    hint = build_hint(PROJECT, RELEASE)
    assert hint == f"{PROJECT}/{RELEASE}"
    assert len(hint.encode()) <= MAX_BUILD_HINT_BYTES
    assert parse_build_hint(hint) == (PROJECT, RELEASE)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        f"{PROJECT}",
        f"{PROJECT}/",
        f"/{RELEASE}",
        f"{PROJECT}/{RELEASE}/",
        f"{PROJECT}/{RELEASE}/{RELEASE}",
        f"{PROJECT} /{RELEASE}",
        f"{str(PROJECT).upper()}/{RELEASE}",
        f"{PROJECT.hex}/{RELEASE}",
        f"{{{PROJECT}}}/{RELEASE}",
        f"00000000-0000-0000-0000-000000000000/{RELEASE}",
        f"{PROJECT}/00000000-0000-0000-0000-000000000000",
        f"{PROJECT}\n/{RELEASE}",
        "x" * 10_000,
        "drop table releases; --",
    ],
)
def test_a_malformed_hint_is_refused(payload: str) -> None:
    assert parse_build_hint(payload) is None


@pytest.mark.asyncio
async def test_request_build_checks_the_pending_step_sends_the_hint_and_writes_nothing() -> None:
    requests = _Requests(BuildTarget(release=_release(), step=_step()))
    started = await RequestBuild(store=requests, logger=logging.getLogger("t")).start(
        PROJECT, RELEASE
    )
    assert started == BuildStarted(
        project_id=PROJECT,
        release_id=RELEASE,
        step_id=STEP,
        attempt=1,
        dispatch=BuildDispatch.PENDING,
    )
    assert requests.hints == [(PROJECT, RELEASE)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target", "error"),
    [
        (BuildTarget(release=_release(), step=_step(ReleaseStepStatus.RUNNING)), BuildInProgress),
        (BuildTarget(release=_release(ReleaseStatus.BUILT), step=None), ReleaseNotBuildable),
        (
            BuildTarget(release=_release(), step=_step(ReleaseStepStatus.FAILED)),
            ReleaseNotBuildable,
        ),
        (NotFound("release"), NotFound),
    ],
)
async def test_request_build_refuses_a_release_that_cannot_start_and_sends_no_hint(
    target: BuildTarget | Exception, error: type[Exception]
) -> None:
    requests = _Requests(target)
    with pytest.raises(error):
        await RequestBuild(store=requests, logger=logging.getLogger("t")).start(PROJECT, RELEASE)
    assert requests.hints == []


@pytest.mark.asyncio
async def test_a_hint_that_cannot_be_sent_is_logged_and_the_request_still_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    requests = _Requests(
        BuildTarget(release=_release(), step=_step()), hint_error=OSError("secret text")
    )
    with caplog.at_level(logging.WARNING, logger="t"):
        started = await RequestBuild(store=requests, logger=logging.getLogger("t")).start(
            PROJECT, RELEASE
        )
    assert started.dispatch is BuildDispatch.PENDING
    assert "build hint not sent" in caplog.text
    assert "OSError" in caplog.text
    assert "secret text" not in caplog.text


class _Starter:
    def __init__(self, errors: dict[UUID, Exception] | None = None) -> None:
        self.errors = errors or {}
        self.starts: list[tuple[UUID, UUID]] = []
        self.done = asyncio.Event()
        self.expect = 0

    async def start(self, project_id: UUID, release_id: UUID) -> BuildStarted:
        self.starts.append((project_id, release_id))
        if len(self.starts) >= self.expect:
            self.done.set()
        error = self.errors.get(release_id)
        if error is not None:
            raise error
        return BuildStarted(
            project_id=project_id,
            release_id=release_id,
            step_id=STEP,
            attempt=1,
            dispatch=BuildDispatch.DISPATCHED,
        )


def _release_id(n: int) -> UUID:
    return UUID(f"0190a1b2-0000-7000-8000-{n:012x}")


async def _run_until(wanted: WantedBuilds, starter: _Starter, count: int) -> None:
    starter.expect = count
    async with asyncio.TaskGroup() as tg:
        task = tg.create_task(wanted.run())
        async with asyncio.timeout(5):
            await starter.done.wait()
        task.cancel()


@pytest.mark.asyncio
async def test_wanted_builds_starts_each_valid_hint_and_drops_a_malformed_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    starter = _Starter()
    wanted = WantedBuilds(starter=starter, queue_size=4, logger=logging.getLogger("w"))
    with caplog.at_level(logging.INFO, logger="w"):
        assert not wanted.offer("not a hint; secret-ish text")
        assert wanted.offer(build_hint(PROJECT, _release_id(1)))
        await _run_until(wanted, starter, 1)
    assert starter.starts == [(PROJECT, _release_id(1))]
    assert "malformed build hint dropped" in caplog.text
    assert "secret-ish" not in caplog.text


@pytest.mark.asyncio
async def test_wanted_builds_drops_hints_beyond_its_bound(
    caplog: pytest.LogCaptureFixture,
) -> None:
    starter = _Starter()
    wanted = WantedBuilds(starter=starter, queue_size=2, logger=logging.getLogger("w"))
    with caplog.at_level(logging.WARNING, logger="w"):
        offered = [wanted.offer(build_hint(PROJECT, _release_id(n))) for n in (1, 2, 3)]
    assert offered == [True, True, False]
    assert "build hint dropped: the queue is full" in caplog.text
    await _run_until(wanted, starter, 2)
    assert starter.starts == [(PROJECT, _release_id(1)), (PROJECT, _release_id(2))]


@pytest.mark.asyncio
async def test_a_start_that_fails_or_is_stale_does_not_stop_the_loop() -> None:
    starter = _Starter(
        errors={
            _release_id(1): BuildInProgress("x"),
            _release_id(2): ReleaseNotBuildable("x"),
            _release_id(3): Contention("x"),
            _release_id(4): NotFound("x"),
            _release_id(5): RuntimeError("store down"),
        }
    )
    wanted = WantedBuilds(starter=starter, queue_size=8, logger=logging.getLogger("w"))
    for n in range(1, 7):
        assert wanted.offer(build_hint(PROJECT, _release_id(n)))
    await _run_until(wanted, starter, 6)
    assert [r for _, r in starter.starts] == [_release_id(n) for n in range(1, 7)]


def test_the_queue_bound_must_be_positive() -> None:
    with pytest.raises(ValueError, match="queue size"):
        WantedBuilds(starter=_Starter(), queue_size=0, logger=logging.getLogger("w"))
