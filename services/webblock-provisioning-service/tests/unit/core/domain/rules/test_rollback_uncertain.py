"""Operator rollback of any uncertain route (decision 0014 section 13, amending decision 0001
sections 4 and 7; task F2 follow-up 1).

A rollback is accepted whenever the route is uncertain, with or without a promoting release and
whatever the hosting status. It names the promoting release, the live release, or, without a live
release, any release of the project. Its verified outcome sets the route stable and the command
succeeded, and rolls back the project's promoting release when one is recorded (failed without a
live release, decision 0001 section 2).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from provisioning.core.domain import (
    Command,
    CommandKind,
    CommandRequest,
    CommandStatus,
    Failure,
    HostingStatus,
    IllegalTransition,
    OutcomeInput,
    ProjectHosting,
    ProjectMismatch,
    Release,
    ReleaseStatus,
    RoutingStatus,
    check_command_preconditions,
    fail_without_route_change,
    new_command,
    rollback_verified,
    route_uncertain,
    start_route_change,
)

T0 = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
NOW = T0 + timedelta(hours=1)
PROJECT = UUID("0190a1b2-0000-7000-8000-000000000001")
OTHER_PROJECT = UUID("0190a1b2-0000-7000-8000-000000000002")
LIVE = UUID("0190a1b2-0000-7000-8000-0000000000a1")
PROMOTING = UUID("0190a1b2-0000-7000-8000-0000000000a2")
FAILED = UUID("0190a1b2-0000-7000-8000-0000000000a3")
BUILT = UUID("0190a1b2-0000-7000-8000-0000000000a4")
COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")
OPERATOR = Failure(code="operator_rollback")
UNCONFIRMED = Failure(code="route_restore_unconfirmed")


def release(release_id: UUID, status: ReleaseStatus, project: UUID = PROJECT) -> Release:
    created = T0 + timedelta(minutes=release_id.int % 100)
    failed = status in (ReleaseStatus.FAILED, ReleaseStatus.ROLLED_BACK)
    return Release(
        id=release_id,
        project_id=project,
        generation_request_id=UUID(int=release_id.int + 1000),
        repository_id=4242,
        git_commit_sha="0123456789abcdef0123456789abcdef01234567",
        status=status,
        created_at=created,
        updated_at=created,
        container_image_digest="sha256:" + "a" * 64,
        failure_code="site_not_answering" if failed else None,
        promoted_at=created if status is ReleaseStatus.LIVE else None,
    )


def hosting(
    *,
    routing: RoutingStatus = RoutingStatus.UNCERTAIN,
    status: HostingStatus = HostingStatus.ONLINE,
    live: UUID | None = LIVE,
) -> ProjectHosting:
    return ProjectHosting(
        project_id=PROJECT,
        last_command_number=7,
        routing_status=routing,
        hosting_status=status,
        updated_at=T0,
        live_release_id=live,
        subdomain="shop",
    )


def rollback(release_id: UUID, status: CommandStatus = CommandStatus.RUNNING) -> Command:
    command = new_command(
        command_id=COMMAND,
        request=CommandRequest(
            project_id=PROJECT,
            kind=CommandKind.ROLLBACK,
            release_id=release_id,
            idempotency_key="op-1",
        ),
        requested_by_subject="operator@example.test",
        command_number=7,
        now=T0,
    )
    return replace(command, status=status)


# --- acceptance -----------------------------------------------------------------------------------


@pytest.mark.parametrize("status", list(HostingStatus))
def test_a_rollback_naming_the_live_release_is_accepted_for_any_uncertain_route(
    status: HostingStatus,
) -> None:
    """After a failed suspend (online) or a failed resume (offline): no promoting release."""
    live = release(LIVE, ReleaseStatus.LIVE)
    check_command_preconditions(CommandKind.ROLLBACK, hosting(status=status), live, live)


def test_a_rollback_naming_the_promoting_release_is_still_accepted() -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    check_command_preconditions(
        CommandKind.ROLLBACK, hosting(), release(PROMOTING, ReleaseStatus.PROMOTING), live
    )


@pytest.mark.parametrize("status", [ReleaseStatus.FAILED, ReleaseStatus.PROMOTING])
def test_without_a_live_release_a_rollback_names_the_release_that_failed(
    status: ReleaseStatus,
) -> None:
    check_command_preconditions(
        CommandKind.ROLLBACK, hosting(live=None), release(FAILED, status), None
    )


@pytest.mark.parametrize(
    "status", [ReleaseStatus.BUILT, ReleaseStatus.FAILED, ReleaseStatus.SUPERSEDED]
)
def test_with_a_live_release_a_rollback_naming_another_idle_release_is_refused(
    status: ReleaseStatus,
) -> None:
    with pytest.raises(IllegalTransition):
        check_command_preconditions(
            CommandKind.ROLLBACK,
            hosting(),
            release(BUILT, status),
            release(LIVE, ReleaseStatus.LIVE),
        )


@pytest.mark.parametrize("routing", [RoutingStatus.STABLE, RoutingStatus.CHANGING])
def test_a_rollback_of_a_route_that_is_not_uncertain_is_still_refused(
    routing: RoutingStatus,
) -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    with pytest.raises(IllegalTransition):
        check_command_preconditions(CommandKind.ROLLBACK, hosting(routing=routing), live, live)


def test_a_rollback_naming_a_release_of_another_project_is_refused() -> None:
    with pytest.raises(ProjectMismatch):
        check_command_preconditions(
            CommandKind.ROLLBACK,
            hosting(live=None),
            release(FAILED, ReleaseStatus.FAILED, project=OTHER_PROJECT),
            None,
        )


def test_deploy_and_promote_still_need_an_online_project() -> None:
    from provisioning.core.domain import HostingOffline

    live = release(LIVE, ReleaseStatus.LIVE)
    offline = hosting(routing=RoutingStatus.STABLE, status=HostingStatus.OFFLINE)
    with pytest.raises(HostingOffline):
        check_command_preconditions(
            CommandKind.DEPLOY, offline, release(BUILT, ReleaseStatus.BUILT), live
        )


# --- outcomes -------------------------------------------------------------------------------------


def test_the_intent_of_a_rollback_naming_the_live_release_moves_uncertain_to_changing() -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    out = start_route_change(
        OutcomeInput(hosting=hosting(), command=rollback(LIVE), release=live, live_release=live),
        NOW,
    )
    assert out.hosting.routing_status is RoutingStatus.CHANGING
    assert out.release is None
    assert out.command.status is CommandStatus.RUNNING


def test_a_verified_rollback_without_a_promoting_release_only_stabilises_the_route() -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    inp = OutcomeInput(
        hosting=hosting(routing=RoutingStatus.CHANGING, status=HostingStatus.OFFLINE),
        command=rollback(LIVE),
        release=live,
        live_release=live,
    )
    out = rollback_verified(inp, OPERATOR, NOW)
    assert out.hosting.routing_status is RoutingStatus.STABLE
    assert out.hosting.hosting_status is HostingStatus.OFFLINE  # unchanged
    assert out.hosting.live_release_id == LIVE
    assert out.command.status is CommandStatus.SUCCEEDED
    assert out.command.failure_code is None
    assert out.release is None
    assert out.superseded_release is None


def test_a_verified_rollback_naming_the_promoting_release_rolls_it_back() -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    promoting = release(PROMOTING, ReleaseStatus.PROMOTING)
    out = rollback_verified(
        OutcomeInput(
            hosting=hosting(routing=RoutingStatus.CHANGING),
            command=rollback(PROMOTING),
            release=promoting,
            live_release=live,
            promoting_release=promoting,
        ),
        OPERATOR,
        NOW,
    )
    assert out.release is not None
    assert out.release.id == PROMOTING
    assert out.release.status is ReleaseStatus.ROLLED_BACK
    assert out.release.failure_code == OPERATOR.code
    assert out.command.status is CommandStatus.SUCCEEDED


def test_a_rollback_naming_the_live_release_still_rolls_back_a_recorded_promoting_release() -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    promoting = release(PROMOTING, ReleaseStatus.PROMOTING)
    out = rollback_verified(
        OutcomeInput(
            hosting=hosting(routing=RoutingStatus.CHANGING),
            command=rollback(LIVE),
            release=live,
            live_release=live,
            promoting_release=promoting,
        ),
        OPERATOR,
        NOW,
    )
    assert out.release is not None
    assert out.release.id == PROMOTING
    assert out.release.status is ReleaseStatus.ROLLED_BACK


def test_a_verified_rollback_of_a_first_publish_fails_its_promoting_release() -> None:
    promoting = release(PROMOTING, ReleaseStatus.PROMOTING)
    out = rollback_verified(
        OutcomeInput(
            hosting=hosting(routing=RoutingStatus.CHANGING, live=None),
            command=rollback(PROMOTING),
            release=promoting,
            promoting_release=promoting,
        ),
        OPERATOR,
        NOW,
    )
    assert out.release is not None
    assert out.release.status is ReleaseStatus.FAILED
    assert out.hosting.routing_status is RoutingStatus.STABLE


def test_a_rollback_whose_route_was_not_changing_is_refused() -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    with pytest.raises(IllegalTransition):
        rollback_verified(
            OutcomeInput(
                hosting=hosting(), command=rollback(LIVE), release=live, live_release=live
            ),
            OPERATOR,
            NOW,
        )


def test_a_promoting_release_of_another_project_is_refused() -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    with pytest.raises(ProjectMismatch):
        rollback_verified(
            OutcomeInput(
                hosting=hosting(routing=RoutingStatus.CHANGING),
                command=rollback(LIVE),
                release=live,
                live_release=live,
                promoting_release=release(PROMOTING, ReleaseStatus.PROMOTING, OTHER_PROJECT),
            ),
            OPERATOR,
            NOW,
        )


def test_a_promoting_release_that_is_not_promoting_is_refused() -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    with pytest.raises(IllegalTransition):
        rollback_verified(
            OutcomeInput(
                hosting=hosting(routing=RoutingStatus.CHANGING),
                command=rollback(LIVE),
                release=live,
                live_release=live,
                promoting_release=release(PROMOTING, ReleaseStatus.BUILT),
            ),
            OPERATOR,
            NOW,
        )


def test_an_unconfirmed_rollback_naming_the_live_release_leaves_the_route_uncertain() -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    out = route_uncertain(
        OutcomeInput(
            hosting=hosting(routing=RoutingStatus.CHANGING),
            command=rollback(LIVE),
            release=live,
            live_release=live,
        ),
        UNCONFIRMED,
        NOW,
    )
    assert out.hosting.routing_status is RoutingStatus.UNCERTAIN
    assert out.command.status is CommandStatus.FAILED


def test_a_rollback_naming_the_live_release_can_fail_before_the_route_changes() -> None:
    live = release(LIVE, ReleaseStatus.LIVE)
    out = fail_without_route_change(
        OutcomeInput(hosting=hosting(), command=rollback(LIVE), release=live, live_release=live),
        Failure(code="operation_unavailable"),
        False,
        NOW,
    )
    assert out.command.status is CommandStatus.FAILED
    assert out.hosting.routing_status is RoutingStatus.UNCERTAIN
