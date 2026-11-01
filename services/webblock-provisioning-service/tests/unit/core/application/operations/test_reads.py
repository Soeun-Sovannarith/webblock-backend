"""The three reads and get_release_diagnostics (decision 0004 section 6) sent through the query bus,
against recording doubles."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable

import pytest
from tests.unit.core.application.operations.fakes import (
    CALL_ACCEPT,
    CALL_ACCESS,
    CALL_BUILD,
    CALL_CLOCK,
    CALL_ENTITLEMENT,
    CALL_FIND,
    CALL_ID,
    CALL_POLICY,
    CALL_READ_COMMAND,
    CALL_READ_DEPLOYMENT,
    CALL_READ_DIAGNOSTICS,
    CALL_READ_RELEASE,
    ID_ACTIVE,
    ID_COMMAND,
    ID_COMMAND2,
    ID_PROJECT,
    ID_PROJECT2,
    ID_RELEASE,
    ID_STEP,
    T0,
    T1,
    OperationsFixture,
    assert_internal,
    project_hosting,
    release_in,
    requester_principal,
    stored_command,
)

from provisioning.core.application import (
    MAX_RELEASE_STEPS,
    Action,
    BuildInProgress,
    BuildReleaseCommand,
    CommandKey,
    Contention,
    CorruptRow,
    Deployment,
    GetCommandQuery,
    GetDeploymentQuery,
    GetReleaseDiagnosticsQuery,
    GetReleaseQuery,
    NotFound,
    OperationNotAvailable,
    ProjectAccessDenied,
    ReleaseKey,
    ReleaseNotBuildable,
    ReleaseWithSteps,
    new_command_view,
    new_deployment_view,
    new_release_view,
)
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    is_error,
    new_command,
)

# ---- fixtures ----


def _release_with_steps(steps: int) -> ReleaseWithSteps:
    release = release_in(ID_RELEASE, ReleaseStatus.BUILT, T0)
    return ReleaseWithSteps(
        release=release,
        steps=tuple(
            ReleaseStep(
                id=ID_STEP,
                release_id=release.id,
                name=ReleaseStepName.BUILD,
                attempt=i + 1,
                status=ReleaseStepStatus.SUCCEEDED,
                started_at=T0,
                finished_at=T1,
            )
            for i in range(steps)
        ),
    )


# get_release asks permission, project access and the read store, in that order, with the
# request's key, and returns the view of what the store returned. A read writes nothing: no
# acceptance store, no clock, no ID source.
@pytest.mark.asyncio
async def test_get_release_returns_the_stored_release() -> None:
    """FR-37: get release returns the stored release."""
    f = OperationsFixture(with_history=True)
    f.reads.release = _release_with_steps(2)
    req = GetReleaseQuery(requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE)
    got = await f.queries.send(req)
    assert got == new_release_view(f.reads.release)
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS, CALL_READ_RELEASE]
    assert f.reads.release_keys == [ReleaseKey(project_id=ID_PROJECT, release_id=ID_RELEASE)]


@pytest.mark.asyncio
async def test_get_command_returns_the_stored_command() -> None:
    """FR-39: get command returns the stored command."""
    f = OperationsFixture(with_history=True)
    f.reads.command = stored_command(3)
    req = GetCommandQuery(requester_principal(), project_id=ID_PROJECT, command_id=ID_COMMAND)
    got = await f.queries.send(req)
    assert got == new_command_view(f.reads.command)
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS, CALL_READ_COMMAND]
    assert f.reads.command_keys == [CommandKey(project_id=ID_PROJECT, command_id=ID_COMMAND)]


# get_command returns hosting commands too; their release_id is absent.
@pytest.mark.asyncio
async def test_get_command_returns_a_hosting_command() -> None:
    f = OperationsFixture(with_history=True)
    hosting = new_command(
        command_id=ID_COMMAND,
        request=CommandRequest(
            project_id=ID_PROJECT,
            kind=CommandKind.SUSPEND_HOSTING,
            release_id=None,
            idempotency_key="payment-event-1",
        ),
        requested_by_subject=None,
        command_number=2,
        now=T0,
    )
    f.reads.command = hosting
    got = await f.queries.send(
        GetCommandQuery(requester_principal(), project_id=ID_PROJECT, command_id=hosting.id)
    )
    assert got.release_id is None
    assert got.kind is CommandKind.SUSPEND_HOSTING


@pytest.mark.asyncio
async def test_get_deployment_returns_the_stored_state() -> None:
    """FR-40: get deployment returns the stored state."""
    f = OperationsFixture(with_history=True)
    f.reads.deployment = Deployment(hosting=project_hosting(4), in_flight=stored_command(4))
    got = await f.queries.send(GetDeploymentQuery(requester_principal(), project_id=ID_PROJECT))
    assert got == new_deployment_view(f.reads.deployment)
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS, CALL_READ_DEPLOYMENT]
    assert f.reads.projects == [ID_PROJECT]


# Store errors of the reads come back as the store reports them: not found, corrupt row,
# contention.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error", [NotFound, CorruptRow, Contention], ids=["not found", "corrupt row", "contention"]
)
async def test_read_store_errors_pass_through(error: type[Exception]) -> None:
    f = OperationsFixture(with_history=True)
    f.reads.err = error("store")
    with pytest.raises(error):
        await f.queries.send(
            GetReleaseQuery(requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE)
        )
    with pytest.raises(error):
        await f.queries.send(
            GetCommandQuery(requester_principal(), project_id=ID_PROJECT, command_id=ID_COMMAND)
        )
    with pytest.raises(error):
        await f.queries.send(GetDeploymentQuery(requester_principal(), project_id=ID_PROJECT))


def _release_of_another_project(r: ReleaseWithSteps) -> ReleaseWithSteps:
    return dataclasses.replace(r, release=dataclasses.replace(r.release, project_id=ID_PROJECT2))


def _another_release(r: ReleaseWithSteps) -> ReleaseWithSteps:
    return dataclasses.replace(r, release=dataclasses.replace(r.release, id=ID_ACTIVE))


def _step_of_another_release(r: ReleaseWithSteps) -> ReleaseWithSteps:
    return dataclasses.replace(
        r, steps=(dataclasses.replace(r.steps[0], release_id=ID_ACTIVE), *r.steps[1:])
    )


# A read store that returns rows of another project, another release or command, or too many
# steps, is a fault: the caller gets an error, never those rows.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arrange",
    [_release_of_another_project, _another_release, _step_of_another_release],
    ids=["release of another project", "another release", "step of another release"],
)
async def test_reads_refuse_releases_that_do_not_match_the_request(
    arrange: Callable[[ReleaseWithSteps], ReleaseWithSteps],
) -> None:
    f = OperationsFixture(with_history=True)
    f.reads.release = arrange(_release_with_steps(1))
    with pytest.raises(Exception) as exc_info:  # internal: any untyped error
        await f.queries.send(
            GetReleaseQuery(requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE)
        )
    assert_internal(exc_info.value)


@pytest.mark.asyncio
async def test_reads_refuse_more_than_the_maximum_steps() -> None:
    f = OperationsFixture(with_history=True)
    f.reads.release = _release_with_steps(MAX_RELEASE_STEPS + 1)
    with pytest.raises(CorruptRow):
        await f.queries.send(
            GetReleaseQuery(requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE)
        )


@pytest.mark.asyncio
async def test_reads_accept_the_maximum_steps() -> None:
    f = OperationsFixture(with_history=True)
    f.reads.release = _release_with_steps(MAX_RELEASE_STEPS)
    got = await f.queries.send(
        GetReleaseQuery(requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE)
    )
    assert len(got.steps) == MAX_RELEASE_STEPS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        lambda: dataclasses.replace(stored_command(3), project_id=ID_PROJECT2),
        lambda: dataclasses.replace(stored_command(3), id=ID_COMMAND2),
    ],
    ids=["command of another project", "another command"],
)
async def test_reads_refuse_commands_that_do_not_match_the_request(
    command: Callable[[], Command],
) -> None:
    f = OperationsFixture(with_history=True)
    f.reads.command = command()
    with pytest.raises(Exception) as exc_info:  # internal: any untyped error
        await f.queries.send(
            GetCommandQuery(requester_principal(), project_id=ID_PROJECT, command_id=ID_COMMAND)
        )
    assert_internal(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deployment",
    [
        lambda: Deployment(hosting=dataclasses.replace(project_hosting(4), project_id=ID_PROJECT2)),
        lambda: Deployment(
            hosting=project_hosting(4),
            in_flight=dataclasses.replace(stored_command(4), project_id=ID_PROJECT2),
        ),
    ],
    ids=["state of another project", "in-flight command of another project"],
)
async def test_reads_refuse_deployments_that_do_not_match_the_request(
    deployment: Callable[[], Deployment],
) -> None:
    f = OperationsFixture(with_history=True)
    f.reads.deployment = deployment()
    with pytest.raises(Exception) as exc_info:  # internal: any untyped error
        await f.queries.send(GetDeploymentQuery(requester_principal(), project_id=ID_PROJECT))
    assert_internal(exc_info.value)


# Reads never write: across every read, no acceptance store call, no clock, no ID source, no
# entitlement and no build.
@pytest.mark.asyncio
async def test_reads_never_write() -> None:
    f = OperationsFixture(with_history=True)
    f.reads.release = _release_with_steps(1)
    f.reads.command = stored_command(3)
    f.reads.deployment = Deployment(hosting=project_hosting(4))
    await f.queries.send(
        GetReleaseQuery(requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE)
    )
    await f.queries.send(
        GetCommandQuery(requester_principal(), project_id=ID_PROJECT, command_id=ID_COMMAND)
    )
    await f.queries.send(GetDeploymentQuery(requester_principal(), project_id=ID_PROJECT))
    await f.queries.send(
        GetReleaseDiagnosticsQuery(
            requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE
        )
    )
    writes = {CALL_FIND, CALL_ACCEPT, CALL_CLOCK, CALL_ID, CALL_ENTITLEMENT, CALL_BUILD}
    assert not writes & set(f.log.calls), f"a read made a writing call: {f.log.calls}"


# get_release_diagnostics authorizes first and then reads the rows (decision 0014 section 21
# ruling 6); a refusal reads nothing. Its bounds and redaction: test_bus_surface.py.
@pytest.mark.asyncio
async def test_diagnostics_authorizes_then_reads() -> None:
    req = GetReleaseDiagnosticsQuery(
        requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE
    )

    f = OperationsFixture(with_history=True)
    f.reads.release = _release_with_steps(1)
    view = await f.queries.send(req)
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS, CALL_READ_DIAGNOSTICS]
    assert view.release_id == ID_RELEASE and len(view.steps) == 1

    f = OperationsFixture(with_history=True)
    f.access.err = ProjectAccessDenied()
    with pytest.raises(ProjectAccessDenied) as exc_info:
        await f.queries.send(req)
    assert not is_error(exc_info.value, OperationNotAvailable)
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS]


# build_release (decision 0014 section 3, "Build") starts the build and answers with the release
# read after the start, under the build_release permission alone: the caller is never asked for
# get_release, so a build never starts before a read refusal (E1 report, concern 2).
@pytest.mark.asyncio
async def test_build_release_starts_the_build_then_returns_the_release() -> None:
    f = OperationsFixture(with_history=True)
    f.reads.release = _release_with_steps(1)
    got = await f.commands.send(
        BuildReleaseCommand(requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE)
    )
    assert got == new_release_view(f.reads.release)
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS, CALL_BUILD, CALL_READ_RELEASE]
    assert f.policy.actions == [Action.BUILD_RELEASE]
    assert f.access.actions == [Action.BUILD_RELEASE]
    assert f.builds.started == [(ID_PROJECT, ID_RELEASE)]
    assert f.reads.release_keys == [ReleaseKey(project_id=ID_PROJECT, release_id=ID_RELEASE)]


# A refused start reads nothing.
@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ReleaseNotBuildable, BuildInProgress, NotFound])
async def test_build_release_reads_nothing_when_the_start_is_refused(
    error: type[Exception],
) -> None:
    f = OperationsFixture(with_history=True)
    f.reads.release = _release_with_steps(1)
    f.builds.err = error()
    with pytest.raises(error):
        await f.commands.send(
            BuildReleaseCommand(requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE)
        )
    assert f.log.calls == [CALL_POLICY, CALL_ACCESS, CALL_BUILD]


# The release read after the start passes the same checks as get_release: a row of another
# project or release is a fault, never an answer.
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arrange",
    [_release_of_another_project, _another_release, _step_of_another_release],
    ids=["release of another project", "another release", "step of another release"],
)
async def test_build_release_refuses_a_release_that_does_not_match_the_request(
    arrange: Callable[[ReleaseWithSteps], ReleaseWithSteps],
) -> None:
    f = OperationsFixture(with_history=True)
    f.reads.release = arrange(_release_with_steps(1))
    with pytest.raises(Exception) as exc_info:  # internal: any untyped error
        await f.commands.send(
            BuildReleaseCommand(requester_principal(), project_id=ID_PROJECT, release_id=ID_RELEASE)
        )
    assert_internal(exc_info.value)
