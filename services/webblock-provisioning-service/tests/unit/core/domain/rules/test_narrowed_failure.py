"""Decision 0011 sections 3 and 12: a deploy's release goes built -> failed only with a code that
classify_deploy_failure marks fails_release, in the domain as well as in the store."""

import dataclasses

import pytest
from tests.unit.core.domain.builders import (
    ID_COMMAND,
    ID_PREV,
    ID_PROJECT,
    ID_RELEASE,
    T0,
    T1,
    valid_row,
)

from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    Failure,
    HostingStatus,
    InvalidFailureCode,
    OutcomeInput,
    ProjectHosting,
    Release,
    ReleaseStatus,
    RoutingStatus,
    deploy_failures,
    fail_without_route_change,
    new_command,
)

# --- fixtures -----------------------------------------------------------------------------------


_HOSTING_KINDS = frozenset({CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING})

S = ReleaseStatus


def _new_command(kind: CommandKind) -> Command:
    """An accepted command of the kind with command number 4."""
    if kind in _HOSTING_KINDS:
        request = CommandRequest(
            project_id=ID_PROJECT, kind=kind, release_id=None, idempotency_key="pay-evt-42"
        )
    else:
        request = CommandRequest(
            project_id=ID_PROJECT, kind=kind, release_id=ID_RELEASE, idempotency_key="key-1"
        )
    return new_command(
        command_id=ID_COMMAND,
        request=request,
        requested_by_subject=None if kind in _HOSTING_KINDS else "user-7f3a",
        command_number=4,
        now=T0,
    )


def _running(
    kind: CommandKind,
    status: ReleaseStatus,
    routing: RoutingStatus,
    hosting: HostingStatus,
    with_live: bool,
) -> OutcomeInput:
    """The hosting row at command number 4 with the routing and
    hosting statuses, a running command of the kind fenced at 4, its release in the status (none
    for a hosting kind) and, when with_live, the live release ID_PREV."""
    hosting_row = ProjectHosting(
        project_id=ID_PROJECT,
        last_command_number=4,
        routing_status=routing,
        hosting_status=hosting,
        updated_at=T0,
        live_release_id=ID_PREV if with_live else None,
        subdomain="shop",
    )
    cmd = dataclasses.replace(_new_command(kind), status=CommandStatus.RUNNING)
    release = None if kind in _HOSTING_KINDS else valid_row(status)
    live = dataclasses.replace(valid_row(S.LIVE), id=ID_PREV) if with_live else None
    return OutcomeInput(hosting=hosting_row, command=cmd, release=release, live_release=live)


def _expect_release(r: Release | None, status: ReleaseStatus) -> None:
    assert r is not None, f"no release in the outcome, want {status}"
    assert r.status == status
    assert r.updated_at == T1


# --- tests ------------------------------------------------------------------------------------


def test_fail_without_route_change_narrows_the_deploy_release_failure() -> None:
    def deploy() -> OutcomeInput:
        return _running(
            CommandKind.DEPLOY, S.BUILT, RoutingStatus.STABLE, HostingStatus.ONLINE, False
        )

    failures = deploy_failures()
    assert failures
    for f in failures:
        if f.fails_release:
            out = fail_without_route_change(deploy(), Failure(code=f.code), True, T1)
            _expect_release(out.release, S.FAILED)
        else:
            with pytest.raises(InvalidFailureCode):
                fail_without_route_change(deploy(), Failure(code=f.code), True, T1)
        # The same code without a release failure ends the command and keeps the release.
        kept = fail_without_route_change(deploy(), Failure(code=f.code), False, T1)
        assert kept.command.status == CommandStatus.FAILED
    for code in ("candidate_failed", "promotion_failed"):
        with pytest.raises(InvalidFailureCode):
            fail_without_route_change(deploy(), Failure(code=code), True, T1)
    # A promote's candidate_ready -> failed (the recheck at its start) is not a deploy failure;
    # its codes come with Phase 5 and are not narrowed here.
    promote = _running(
        CommandKind.PROMOTE, S.CANDIDATE_READY, RoutingStatus.STABLE, HostingStatus.ONLINE, False
    )
    out = fail_without_route_change(promote, Failure(code="promotion_failed"), True, T1)
    _expect_release(out.release, S.FAILED)
