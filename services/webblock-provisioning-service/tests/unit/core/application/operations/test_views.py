"""The result views of the operations (module views.py) and the fields of their commands and
queries. Views carry revision 19 names; the transport maps them to the contract's field names
later, so the tests compare them with the expected revision 19 field names."""

from __future__ import annotations

import dataclasses
import types
import typing
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provisioning.core.application import (
    MAX_CORRELATION_ID_LEN,
    MAX_RELEASE_STEPS,
    AcceptedCommand,
    AdminListCommandsQuery,
    BuildReleaseCommand,
    BuildStarted,
    CommandAccepted,
    CommandPage,
    CommandSummaryView,
    CommandView,
    Deployment,
    DeploymentView,
    DeployReleaseCommand,
    DiagnosticsView,
    GetCommandQuery,
    GetDeploymentQuery,
    GetReleaseDiagnosticsQuery,
    GetReleaseQuery,
    ListCommandsQuery,
    ListReleasesQuery,
    PromoteReleaseCommand,
    RedeployReleaseCommand,
    ReleasePage,
    ReleaseStepView,
    ReleaseSummaryView,
    ReleaseView,
    ReleaseWithSteps,
    ResumeWebsiteCommand,
    RollbackReleaseCommand,
    SourceEventView,
    SuspendWebsiteCommand,
    new_command_accepted,
    new_command_view,
    new_deployment_view,
    new_release_view,
)
from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    HostingStatus,
    ProjectHosting,
    Release,
    ReleaseStatus,
    ReleaseStep,
    ReleaseStepName,
    ReleaseStepStatus,
    RoutingStatus,
    new_command,
    new_project_hosting,
)

# ---- fixtures ----

T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)

ID_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
ID_RELEASE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
ID_ACTIVE = UUID("0190a1b2-0000-7000-8000-0000000000b1")
ID_COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
ID_REQUEST = UUID("0190a1b2-0000-7000-8000-0000000000d1")
ID_STEP = UUID("0190a1b2-0000-7000-8000-0000000000e1")

SHA40 = "0123456789abcdef0123456789abcdef01234567"
DIGEST = "sha256:" + "a" * 64
REQUESTER = "user-1"


def _deploy_request() -> CommandRequest:
    return CommandRequest(
        project_id=ID_PROJECT,
        kind=CommandKind.DEPLOY,
        release_id=ID_RELEASE,
        idempotency_key="key-1",
    )


def _stored_command(command_number: int) -> Command:
    """A deploy command as the store would return it, accepted at T0 and issued by REQUESTER."""
    return new_command(
        command_id=ID_COMMAND,
        request=_deploy_request(),
        requested_by_subject=REQUESTER,
        command_number=command_number,
        now=T0,
    )


def _field_names(cls: type) -> tuple[str, ...]:
    return tuple(f.name for f in dataclasses.fields(cls))


# ---- field names ----

# The fields of each result view, in declaration order, with their revision 19 names.
_VIEW_FIELDS: dict[type, tuple[str, ...]] = {
    CommandView: (
        "command_id",
        "project_id",
        "release_id",
        "kind",
        "command_number",
        "status",
        "failure_code",
        "created_at",
        "updated_at",
    ),
    CommandSummaryView: ("command_id", "kind", "status"),
    ReleaseStepView: (
        "step_name",
        "attempt",
        "status",
        "started_at",
        "finished_at",
        "deadline_at",
        "failure_code",
    ),
    ReleaseView: (
        "release_id",
        "project_id",
        "generation_request_id",
        "repository_id",
        "git_commit_sha",
        "container_image_digest",
        "status",
        "failure_code",
        "failure_summary",
        "steps",
        "created_at",
        "updated_at",
        "promoted_at",
    ),
    DeploymentView: (
        "project_id",
        "live_release_id",
        "last_command_number",
        "routing_status",
        "hosting_status",
        "in_flight_command",
        "updated_at",
    ),
    DiagnosticsView: (
        "release_id",
        "project_id",
        "release_status",
        "release_failure_code",
        "release_created_at",
        "release_updated_at",
        "release_promoted_at",
        "steps",
        "steps_truncated",
        "commands",
        "commands_truncated",
        "source_event",
    ),
    SourceEventView: (
        "status",
        "outcome_code",
        "attempts",
        "mismatched_repeat_count",
        "received_at",
        "finished_at",
    ),
    ReleaseSummaryView: (
        "release_id",
        "project_id",
        "git_commit_sha",
        "container_image_digest",
        "status",
        "failure_code",
        "created_at",
        "updated_at",
        "promoted_at",
    ),
    ReleasePage: ("items", "next_cursor"),
    CommandPage: ("items", "next_after"),
}


@pytest.mark.parametrize(
    ("view", "want"), list(_VIEW_FIELDS.items()), ids=[c.__name__ for c in _VIEW_FIELDS]
)
def test_views_carry_exactly_the_contract_properties(view: type, want: tuple[str, ...]) -> None:
    assert _field_names(view) == want


# CommandAccepted feeds the 202 body: command_id and status, plus the project ID from which the
# transport builds status_url (never from the Host header) and whether this was a replay, which is
# for the logs.
def test_command_accepted_carries_what_the_202_body_needs() -> None:
    assert _field_names(CommandAccepted) == ("command_id", "project_id", "status", "replayed")


def _all_field_names(cls: type, prefix: str) -> list[str]:
    """``Type.field`` for every field, descending into the dataclasses of the field types."""
    out: list[str] = []
    hints = typing.get_type_hints(cls)
    for f in dataclasses.fields(cls):
        out.append(f"{prefix}.{f.name}")
        for nested in _dataclasses_in(hints[f.name]):
            out.extend(_all_field_names(nested, f"{prefix}.{f.name}"))
    return out


def _dataclasses_in(hint: object) -> list[type]:
    if isinstance(hint, type) and dataclasses.is_dataclass(hint):
        return [hint]
    origin = typing.get_origin(hint)
    if origin is None and not isinstance(hint, types.UnionType):
        return []
    found: list[type] = []
    for arg in typing.get_args(hint):
        found.extend(_dataclasses_in(arg))
    return found


# No result type names a command's idempotency key, request fingerprint or requester, in any
# spelling (decision 0006 section 2). The check walks nested dataclasses too.
@pytest.mark.parametrize(
    "result",
    [
        CommandAccepted,
        CommandView,
        CommandSummaryView,
        ReleaseView,
        ReleaseStepView,
        DeploymentView,
        DiagnosticsView,
        SourceEventView,
        ReleaseSummaryView,
        ReleasePage,
        CommandPage,
        BuildStarted,
    ],
    ids=lambda c: c.__name__,
)
def test_results_never_expose_key_hash_or_requester(result: type) -> None:
    """FR-39.1: results never expose key hash or requester."""
    forbidden = (
        "idempoten",
        "requesthash",
        "hash",
        "fingerprint",
        "requestedby",
        "requester",
        "subject",
    )
    for field in _all_field_names(result, result.__name__):
        flat = field.replace("_", "").lower()
        for word in forbidden:
            assert word not in flat, f"result field {field} must not exist"


def test_new_command_view_copies_the_row_without_key_or_hash() -> None:
    cmd = (
        _stored_command(3)
        .transition(CommandStatus.RUNNING, "", T1)
        .transition(CommandStatus.FAILED, "executor_unavailable", T2)
    )

    view = new_command_view(cmd)

    assert view == CommandView(
        command_id=cmd.id,
        project_id=cmd.project_id,
        release_id=ID_RELEASE,
        kind=CommandKind.DEPLOY,
        command_number=3,
        status=CommandStatus.FAILED,
        failure_code="executor_unavailable",
        created_at=T0,
        updated_at=T2,
    )
    # The row has a requester; nothing of it reaches the view (decision 0006 section 2). The field
    # test pins the names; this pins the value in any field.
    assert cmd.requested_by_subject == REQUESTER
    assert REQUESTER not in repr(view)
    assert REQUESTER not in repr(new_command_accepted(AcceptedCommand(command=cmd, replayed=False)))
    hosting = ProjectHosting(
        project_id=cmd.project_id,
        last_command_number=3,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        updated_at=T0,
        subdomain="shop",
    )
    deployment = new_deployment_view(Deployment(hosting=hosting, in_flight=cmd))
    assert deployment.in_flight_command is not None
    assert REQUESTER not in repr(deployment.in_flight_command)


def test_new_command_view_of_a_hosting_command_has_no_release() -> None:
    cmd = new_command(
        command_id=ID_COMMAND,
        request=CommandRequest(
            project_id=ID_PROJECT,
            kind=CommandKind.SUSPEND_HOSTING,
            release_id=None,
            idempotency_key="evt-1",
        ),
        requested_by_subject=None,
        command_number=1,
        now=T0,
    )
    view = new_command_view(cmd)
    assert view.release_id is None
    assert view.failure_code is None


@pytest.mark.parametrize("replayed", [False, True])
def test_new_command_accepted(replayed: bool) -> None:
    cmd = _stored_command(3)
    got = new_command_accepted(AcceptedCommand(command=cmd, replayed=replayed))
    assert got == CommandAccepted(
        command_id=cmd.id,
        project_id=cmd.project_id,
        status=CommandStatus.ACCEPTED,
        replayed=replayed,
    )


def _rolled_back_release() -> Release:
    return Release(
        id=ID_RELEASE,
        project_id=ID_PROJECT,
        generation_request_id=ID_REQUEST,
        repository_id=4242,
        git_commit_sha=SHA40,
        container_image_digest=DIGEST,
        status=ReleaseStatus.ROLLED_BACK,
        failure_code="candidate_unhealthy",
        failure_summary="candidate failed its check",
        created_at=T0,
        updated_at=T2,
        promoted_at=T1,
    )


def test_new_release_view_copies_release_and_steps_in_order() -> None:
    r = _rolled_back_release()
    steps = (
        ReleaseStep(
            id=ID_STEP,
            release_id=r.id,
            name=ReleaseStepName.BUILD,
            attempt=1,
            status=ReleaseStepStatus.FAILED,
            started_at=T0,
            finished_at=T1,
            deadline_at=T2,
            failure_code="build_timeout",
        ),
        ReleaseStep(
            id=ID_COMMAND,
            release_id=r.id,
            name=ReleaseStepName.BUILD,
            attempt=2,
            status=ReleaseStepStatus.PENDING,
        ),
    )

    view = new_release_view(ReleaseWithSteps(release=r, steps=steps))

    assert view == ReleaseView(
        release_id=r.id,
        project_id=r.project_id,
        generation_request_id=r.generation_request_id,
        repository_id=4242,
        git_commit_sha=SHA40,
        container_image_digest=DIGEST,
        status=ReleaseStatus.ROLLED_BACK,
        failure_code="candidate_unhealthy",
        failure_summary="candidate failed its check",
        steps=(
            ReleaseStepView(
                step_name=ReleaseStepName.BUILD,
                attempt=1,
                status=ReleaseStepStatus.FAILED,
                started_at=T0,
                finished_at=T1,
                deadline_at=T2,
                failure_code="build_timeout",
            ),
            ReleaseStepView(
                step_name=ReleaseStepName.BUILD,
                attempt=2,
                status=ReleaseStepStatus.PENDING,
                started_at=None,
                finished_at=None,
                deadline_at=None,
                failure_code=None,
            ),
        ),
        created_at=T0,
        updated_at=T2,
        promoted_at=T1,
    )


# A release without steps has an empty list, so the response carries [] and not null: the contract
# requires steps.
def test_new_release_view_without_steps_has_an_empty_list() -> None:
    r = dataclasses.replace(
        _rolled_back_release(),
        status=ReleaseStatus.ACCEPTED,
        failure_code=None,
        failure_summary=None,
        promoted_at=None,
        container_image_digest=None,
    )
    view = new_release_view(ReleaseWithSteps(release=r, steps=()))
    assert view.steps == ()
    assert view.container_image_digest is None
    assert view.failure_code is None
    assert view.failure_summary is None
    assert view.promoted_at is None


def test_new_deployment_view_with_in_flight_command() -> None:
    hosting = ProjectHosting(
        project_id=ID_PROJECT,
        live_release_id=ID_ACTIVE,
        last_command_number=5,
        routing_status=RoutingStatus.CHANGING,
        hosting_status=HostingStatus.ONLINE,
        updated_at=T1,
        subdomain="shop",
    )
    cmd = _stored_command(5).transition(CommandStatus.RUNNING, "", T1)

    view = new_deployment_view(Deployment(hosting=hosting, in_flight=cmd))

    assert view == DeploymentView(
        project_id=ID_PROJECT,
        live_release_id=ID_ACTIVE,
        last_command_number=5,
        routing_status=RoutingStatus.CHANGING,
        hosting_status=HostingStatus.ONLINE,
        in_flight_command=CommandSummaryView(
            command_id=cmd.id, kind=CommandKind.DEPLOY, status=CommandStatus.RUNNING
        ),
        updated_at=T1,
    )


def test_new_deployment_view_without_live_release_or_command() -> None:
    hosting = new_project_hosting(ID_PROJECT, T0, subdomain="shop")
    view = new_deployment_view(Deployment(hosting=hosting))
    assert view.live_release_id is None
    assert view.in_flight_command is None
    assert view.last_command_number == 0
    assert view.routing_status is RoutingStatus.STABLE
    assert view.hosting_status is HostingStatus.ONLINE
    assert view.updated_at == T0


# The commands and queries hold what the contract takes and nothing a client may not send: no
# command number, no body fields, and no requester; the verified principal is their first field and
# a mutation's ``actor_kind`` names the transport's caller (decision 0025 CQ6, TR-54).
_MUTATION = ("principal", "project_id", "release_id", "idempotency_key", "correlation_id")
_ADMIN = ("principal", "project_id", "idempotency_key", "correlation_id", "actor_kind")
_INTENT_FIELDS: tuple[tuple[type, tuple[str, ...]], ...] = (
    (DeployReleaseCommand, (*_MUTATION, "actor_kind")),
    (PromoteReleaseCommand, (*_MUTATION, "actor_kind")),
    (RollbackReleaseCommand, (*_MUTATION, "actor_kind")),
    (SuspendWebsiteCommand, _ADMIN),
    (ResumeWebsiteCommand, _ADMIN),
    (RedeployReleaseCommand, _ADMIN),
    (
        BuildReleaseCommand,
        ("principal", "project_id", "release_id", "correlation_id", "actor_kind"),
    ),
    (GetReleaseQuery, ("principal", "project_id", "release_id")),
    (GetCommandQuery, ("principal", "project_id", "command_id")),
    (GetDeploymentQuery, ("principal", "project_id")),
    (GetReleaseDiagnosticsQuery, ("principal", "project_id", "release_id")),
    (ListReleasesQuery, ("principal", "project_id", "page_size", "after")),
    (ListCommandsQuery, ("principal", "project_id", "page_size", "after")),
    (AdminListCommandsQuery, ("principal", "project_id", "limit", "cursor")),
)


@pytest.mark.parametrize(
    ("intent_type", "want"), _INTENT_FIELDS, ids=[t.__name__ for t, _ in _INTENT_FIELDS]
)
def test_commands_and_queries_carry_only_contract_inputs(
    intent_type: type, want: tuple[str, ...]
) -> None:
    assert _field_names(intent_type) == want


def test_limits() -> None:
    assert MAX_CORRELATION_ID_LEN == 128  # the work row's column and the envelope
    assert MAX_RELEASE_STEPS == 100  # the contract's steps maxItems
