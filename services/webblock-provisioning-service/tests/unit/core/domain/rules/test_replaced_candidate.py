"""A newer deploy returns an idle candidate of its project to built (decision 0011 section 4)."""

import dataclasses
from collections.abc import Callable

import pytest
from tests.unit.core.domain.builders import (
    ID_COMMAND,
    ID_PREV,
    ID_PROJECT,
    ID_PROJECT2,
    ID_RELEASE,
    ID_RELEASE2,
    T0,
    T1,
    valid_row,
)

from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    DomainError,
    HostingStatus,
    IllegalTransition,
    InvalidCommandTarget,
    ProjectHosting,
    ProjectMismatch,
    ReleaseStatus,
    ReleaseTransition,
    ReplacedCandidateInput,
    RoutingStatus,
    StaleFence,
    new_command,
    return_replaced_candidate,
)

# --- fixtures -----------------------------------------------------------------------------------


_HOSTING_KINDS = frozenset(  # kinds with no release and no requester (0014 s16)
    {CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING, CommandKind.SYNC_DOMAINS}
)

S = ReleaseStatus


def _request_for(kind: CommandKind) -> CommandRequest:
    if kind in _HOSTING_KINDS:
        return CommandRequest(
            project_id=ID_PROJECT, kind=kind, release_id=None, idempotency_key="pay-evt-42"
        )
    return CommandRequest(
        project_id=ID_PROJECT, kind=kind, release_id=ID_RELEASE, idempotency_key="key-1"
    )


def _command_in(kind: CommandKind, status: CommandStatus) -> Command:
    """An accepted command of the kind with command number 4, moved to the status, with a failure
    code where the status needs one."""
    c = new_command(
        command_id=ID_COMMAND,
        request=_request_for(kind),
        requested_by_subject=None if kind in _HOSTING_KINDS else "user-7f3a",
        command_number=4,
        now=T0,
    )
    c = dataclasses.replace(c, status=status)
    if status in (CommandStatus.FAILED, CommandStatus.SKIPPED):
        c = dataclasses.replace(c, failure_code="earlier_failure")
    return c


def _replaced_input() -> ReplacedCandidateInput:
    """The hosting row at command number 4 with live release ID_PREV, a running deploy fenced at
    4 for release ID_RELEASE2, and the project's idle candidate ID_RELEASE in candidate_ready."""
    hosting = ProjectHosting(
        project_id=ID_PROJECT,
        last_command_number=4,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        updated_at=T0,
        live_release_id=ID_PREV,
        subdomain="shop",
    )
    cmd = dataclasses.replace(
        _command_in(CommandKind.DEPLOY, CommandStatus.RUNNING), release_id=ID_RELEASE2
    )
    return ReplacedCandidateInput(
        hosting=hosting, command=cmd, release=valid_row(S.CANDIDATE_READY)
    )


# --- tests ------------------------------------------------------------------------------------


def test_return_replaced_candidate() -> None:
    """A newer deploy returns an idle candidate of its project to built: the image digest is
    kept, so the release can be deployed again, and no failure is recorded."""
    inp = _replaced_input()
    before = dataclasses.replace(inp)
    out = return_replaced_candidate(inp, T1)
    assert out.status == S.BUILT
    assert out.updated_at == T1
    assert out.container_image_digest is not None
    assert out.container_image_digest == inp.release.container_image_digest
    assert out.failure_code is None
    assert out.failure_summary is None
    assert out.promoted_at is None
    out.validate()
    assert inp == before
    # Without a live release (a project that never went live) the rule is the same.
    first = dataclasses.replace(inp, hosting=dataclasses.replace(inp.hosting, live_release_id=None))
    assert return_replaced_candidate(first, T1).status == S.BUILT


def test_candidate_ready_to_built_only_through_replacement() -> None:
    """candidate_ready -> built is reached only through return_replaced_candidate, so its
    conditions cannot be skipped by a direct transition."""
    r = valid_row(S.CANDIDATE_READY)
    before = dataclasses.replace(r)
    with pytest.raises(IllegalTransition):
        r.transition(S.BUILT, ReleaseTransition(), T1)
    assert r == before


_Change = Callable[[ReplacedCandidateInput], ReplacedCandidateInput]


def _stale_fence(i: ReplacedCandidateInput) -> ReplacedCandidateInput:
    return dataclasses.replace(i, hosting=dataclasses.replace(i.hosting, last_command_number=5))


def _own_release(i: ReplacedCandidateInput) -> ReplacedCandidateInput:
    return dataclasses.replace(i, command=dataclasses.replace(i.command, release_id=i.release.id))


def _live_release(i: ReplacedCandidateInput) -> ReplacedCandidateInput:
    return dataclasses.replace(
        i, hosting=dataclasses.replace(i.hosting, live_release_id=i.release.id)
    )


def _release_of_another_project(i: ReplacedCandidateInput) -> ReplacedCandidateInput:
    return dataclasses.replace(i, release=dataclasses.replace(i.release, project_id=ID_PROJECT2))


def _command_of_another_project(i: ReplacedCandidateInput) -> ReplacedCandidateInput:
    return dataclasses.replace(i, command=dataclasses.replace(i.command, project_id=ID_PROJECT2))


def _deploy_without_a_release(i: ReplacedCandidateInput) -> ReplacedCandidateInput:
    return dataclasses.replace(i, command=dataclasses.replace(i.command, release_id=None))


def _command_status(status: CommandStatus) -> _Change:
    def change(i: ReplacedCandidateInput) -> ReplacedCandidateInput:
        c = dataclasses.replace(
            _command_in(CommandKind.DEPLOY, status), release_id=i.command.release_id
        )
        return dataclasses.replace(i, command=c)

    return change


def _command_kind(kind: CommandKind) -> _Change:
    def change(i: ReplacedCandidateInput) -> ReplacedCandidateInput:
        c = _command_in(kind, CommandStatus.RUNNING)
        if not kind.is_hosting():
            c = dataclasses.replace(c, release_id=i.command.release_id)
        return dataclasses.replace(i, command=c)

    return change


def _release_status(status: ReleaseStatus) -> _Change:
    def change(i: ReplacedCandidateInput) -> ReplacedCandidateInput:
        return dataclasses.replace(i, release=valid_row(status))

    return change


_REFUSALS: list[tuple[str, _Change, type[DomainError]]] = [
    ("stale fence", _stale_fence, StaleFence),
    ("own release", _own_release, IllegalTransition),
    ("active release", _live_release, IllegalTransition),
    ("release of another project", _release_of_another_project, ProjectMismatch),
    ("command of another project", _command_of_another_project, ProjectMismatch),
    ("deploy without a release", _deploy_without_a_release, InvalidCommandTarget),
]
_REFUSALS += [
    (f"command {s}", _command_status(s), IllegalTransition)
    for s in CommandStatus
    if s != CommandStatus.RUNNING
]
_REFUSALS += [
    (f"command kind {k}", _command_kind(k), IllegalTransition)
    for k in CommandKind
    if k != CommandKind.DEPLOY
]
_REFUSALS += [
    (f"release {s}", _release_status(s), IllegalTransition)
    for s in ReleaseStatus
    if s != S.CANDIDATE_READY
]


@pytest.mark.parametrize(
    ("change", "want"),
    [(c, w) for _, c, w in _REFUSALS],
    ids=[name for name, _, _ in _REFUSALS],
)
def test_return_replaced_candidate_refusals(change: _Change, want: type[DomainError]) -> None:
    """Every condition of decision 0011 section 4 refuses on its own, with the error the domain
    uses elsewhere, and nothing changes."""
    inp = change(_replaced_input())
    before = dataclasses.replace(inp)
    with pytest.raises(want):
        return_replaced_candidate(inp, T1)
    assert inp == before
