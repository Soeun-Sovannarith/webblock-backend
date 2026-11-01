"""The pure decide function of an acceptance (``new_acceptance_decider``, decision 0004 section 4)
and its precedence."""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from tests.unit.core.application.operations.fakes import (
    ID_ACTIVE,
    ID_COMMAND,
    ID_COMMAND2,
    ID_PROJECT,
    ID_PROJECT2,
    ID_RELEASE,
    REQUESTER,
    T0,
    T1,
    assert_internal,
    command_for,
    project_hosting,
    release_in,
    stored_command,
)

from provisioning.core.application import (
    AcceptanceFacts,
    AcceptCommandParams,
    NotFound,
    OperationInProgress,
    in_flight_command_id,
    insert_decision,
    new_acceptance_decider,
)
from provisioning.core.domain import (
    MAX_COMMAND_NUMBER,
    Command,
    CommandKind,
    CommandNumberOverflow,
    CommandRequest,
    CommandStatus,
    HostingOffline,
    HostingStatus,
    IdempotencyConflict,
    IllegalTransition,
    InvalidRequester,
    OfflineReason,
    OwnerStatusOutdated,
    ReleaseOutdated,
    ReleaseStatus,
    RouteNotStable,
    RoutingStatus,
)

# ---- fixtures ----

T2 = T0 + timedelta(minutes=2)


# The decide function applies, under the project lock, the precedence of decision 0004 section 4:
# replay or conflict by key, then the command in flight, then the missing target release, then
# check_command_preconditions (whose own precedence is decision 0001 section 4), then the command
# number advance. Each case below arranges facts where a later rule would also fail, so that only
# the precedence decides the answer.


def _accept_params(kind: CommandKind = CommandKind.DEPLOY) -> AcceptCommandParams:
    """A command of ``kind`` on ID_RELEASE in ID_PROJECT under key-1, new ID ID_COMMAND2."""
    return AcceptCommandParams(
        request=CommandRequest(
            project_id=ID_PROJECT, kind=kind, release_id=ID_RELEASE, idempotency_key="key-1"
        ),
        command_id=ID_COMMAND2,
        requested_by_subject=REQUESTER,
        now=T1,
        correlation_id="corr-1",
        deadline_at=T2,
    )


def _clean_deploy_facts() -> AcceptanceFacts:
    return AcceptanceFacts(
        hosting=project_hosting(4), target=release_in(ID_RELEASE, ReleaseStatus.BUILT, T1)
    )


def _live_active(facts: AcceptanceFacts, created: datetime) -> AcceptanceFacts:
    """The facts with a live release ID_ACTIVE created at ``created`` as the project's live
    release."""
    live = release_in(ID_ACTIVE, ReleaseStatus.LIVE, created)
    return dataclasses.replace(
        facts, hosting=dataclasses.replace(facts.hosting, live_release_id=live.id), live=live
    )


def _with_target_status(facts: AcceptanceFacts, status: ReleaseStatus) -> AcceptanceFacts:
    assert facts.target is not None
    return dataclasses.replace(facts, target=dataclasses.replace(facts.target, status=status))


def _routing(facts: AcceptanceFacts, status: RoutingStatus) -> AcceptanceFacts:
    return dataclasses.replace(
        facts, hosting=dataclasses.replace(facts.hosting, routing_status=status)
    )


def _offline(facts: AcceptanceFacts) -> AcceptanceFacts:
    return dataclasses.replace(
        facts, hosting=dataclasses.replace(facts.hosting, hosting_status=HostingStatus.OFFLINE)
    )


def _of_project2(facts: AcceptanceFacts) -> AcceptanceFacts:
    return dataclasses.replace(
        facts, hosting=dataclasses.replace(facts.hosting, project_id=ID_PROJECT2)
    )


def test_decide_inserts_at_the_advanced_command_number() -> None:
    facts = _live_active(_clean_deploy_facts(), T0)  # the target is newer than the live release
    decision = new_acceptance_decider(_accept_params())(facts)
    assert decision.insert, "decide asked for a replay, want an insert"
    cmd = decision.command
    assert cmd.command_number == 5
    assert cmd.id == ID_COMMAND2
    assert cmd.requested_by_subject == REQUESTER


def _same_key() -> Command:
    return stored_command(4)  # deploy of ID_RELEASE under key-1, same request


def _other_request() -> Command:
    return command_for(CommandKind.PROMOTE, ID_COMMAND, "key-1", REQUESTER, 4)


def _in_flight() -> Command:
    return command_for(CommandKind.PROMOTE, ID_COMMAND, "other-key", REQUESTER, 4)


def _replay_before_busy(f: AcceptanceFacts) -> AcceptanceFacts:
    f = dataclasses.replace(f, by_key=_same_key(), in_flight=_same_key(), target=None)
    return _routing(f, RoutingStatus.UNCERTAIN)


def _conflict_before_busy(f: AcceptanceFacts) -> AcceptanceFacts:
    f = dataclasses.replace(f, by_key=_other_request(), in_flight=_in_flight())
    return _offline(f)


def _busy_before_missing_target(f: AcceptanceFacts) -> AcceptanceFacts:
    return dataclasses.replace(f, in_flight=_in_flight(), target=None)


def _busy_before_preconditions(f: AcceptanceFacts) -> AcceptanceFacts:
    f = dataclasses.replace(f, in_flight=_in_flight())
    return _routing(f, RoutingStatus.CHANGING)


def _missing_target_before_preconditions(f: AcceptanceFacts) -> AcceptanceFacts:
    f = dataclasses.replace(f, target=None)
    return _routing(f, RoutingStatus.CHANGING)


def _route_not_stable_before_offline(f: AcceptanceFacts) -> AcceptanceFacts:
    return _offline(_routing(f, RoutingStatus.CHANGING))


def _offline_before_release_status(f: AcceptanceFacts) -> AcceptanceFacts:
    return _with_target_status(_offline(f), ReleaseStatus.LIVE)


def _release_status_before_older_release(f: AcceptanceFacts) -> AcceptanceFacts:
    return _with_target_status(_live_active(f, T2), ReleaseStatus.CANDIDATE_READY)


def _older_release(f: AcceptanceFacts) -> AcceptanceFacts:
    return _live_active(f, T2)


def _older_release_promote(f: AcceptanceFacts) -> AcceptanceFacts:
    return _live_active(_with_target_status(f, ReleaseStatus.CANDIDATE_READY), T2)


def _rollback_while_route_stable(f: AcceptanceFacts) -> AcceptanceFacts:
    return _with_target_status(f, ReleaseStatus.PROMOTING)


def _rollback_while_offline(f: AcceptanceFacts) -> AcceptanceFacts:
    f = _with_target_status(f, ReleaseStatus.PROMOTING)
    return _offline(_routing(f, RoutingStatus.UNCERTAIN))


def _command_number_exhausted(f: AcceptanceFacts) -> AcceptanceFacts:
    return dataclasses.replace(
        f, hosting=dataclasses.replace(f.hosting, last_command_number=MAX_COMMAND_NUMBER)
    )


# (name, kind, arrange, wanted error; None is a replay of the command under the key)
_PRECEDENCE: tuple[
    tuple[str, CommandKind, Callable[[AcceptanceFacts], AcceptanceFacts], type[Exception] | None],
    ...,
] = (
    (
        "replay before busy, missing target and preconditions",
        CommandKind.DEPLOY,
        _replay_before_busy,
        None,
    ),
    (
        "conflict before busy and preconditions",
        CommandKind.DEPLOY,
        _conflict_before_busy,
        IdempotencyConflict,
    ),
    (
        "busy before missing target",
        CommandKind.DEPLOY,
        _busy_before_missing_target,
        OperationInProgress,
    ),
    (
        "busy before preconditions",
        CommandKind.DEPLOY,
        _busy_before_preconditions,
        OperationInProgress,
    ),
    (
        "missing target before preconditions",
        CommandKind.DEPLOY,
        _missing_target_before_preconditions,
        NotFound,
    ),
    (
        "route not stable before hosting offline",
        CommandKind.DEPLOY,
        _route_not_stable_before_offline,
        RouteNotStable,
    ),
    (
        "hosting offline before release status",
        CommandKind.DEPLOY,
        _offline_before_release_status,
        HostingOffline,
    ),
    (
        "release status before older release",
        CommandKind.DEPLOY,
        _release_status_before_older_release,
        IllegalTransition,
    ),
    ("older release", CommandKind.DEPLOY, _older_release, ReleaseOutdated),
    ("older release refuses promote", CommandKind.PROMOTE, _older_release_promote, ReleaseOutdated),
    (
        "rollback while route stable",
        CommandKind.ROLLBACK,
        _rollback_while_route_stable,
        IllegalTransition,
    ),
    (
        "command number exhausted last",
        CommandKind.DEPLOY,
        _command_number_exhausted,
        CommandNumberOverflow,
    ),
)


@pytest.mark.parametrize(
    ("kind", "arrange", "want"),
    [(kind, arrange, want) for _, kind, arrange, want in _PRECEDENCE],
    ids=[name for name, _, _, _ in _PRECEDENCE],
)
def test_decide_precedence(
    kind: CommandKind,
    arrange: Callable[[AcceptanceFacts], AcceptanceFacts],
    want: type[Exception] | None,
) -> None:
    facts = arrange(_clean_deploy_facts())
    decide = new_acceptance_decider(_accept_params(kind))
    if want is None:
        decision = decide(facts)
        assert not decision.insert
        assert decision.command.id == _same_key().id
        return
    with pytest.raises(want):
        decide(facts)


# A replay returns the stored command whatever its status, with its own requester.
def test_decide_replays_a_finished_command_of_another_requester() -> None:
    stored = dataclasses.replace(
        command_for(CommandKind.DEPLOY, ID_COMMAND, "key-1", "another-member", 3),
        status=CommandStatus.FAILED,
        failure_code="candidate_failed",
    )
    facts = dataclasses.replace(_clean_deploy_facts(), by_key=stored)
    decision = new_acceptance_decider(_accept_params())(facts)
    assert not decision.insert
    assert decision.command == stored


# The busy refusal names the command in flight.
def test_decide_busy_names_the_command_in_flight() -> None:
    in_flight = _in_flight()
    facts = dataclasses.replace(_clean_deploy_facts(), in_flight=in_flight)
    with pytest.raises(OperationInProgress) as exc_info:
        new_acceptance_decider(_accept_params())(facts)
    assert in_flight_command_id(exc_info.value) == in_flight.id


def _state_of_another_project(f: AcceptanceFacts) -> AcceptanceFacts:
    return _of_project2(f)


def _state_of_another_project_with_replay(f: AcceptanceFacts) -> AcceptanceFacts:
    return _of_project2(dataclasses.replace(f, by_key=stored_command(4)))


def _state_of_another_project_with_in_flight(f: AcceptanceFacts) -> AcceptanceFacts:
    return _of_project2(dataclasses.replace(f, in_flight=_in_flight()))


def _target_of_another_project(f: AcceptanceFacts) -> AcceptanceFacts:
    assert f.target is not None
    return dataclasses.replace(f, target=dataclasses.replace(f.target, project_id=ID_PROJECT2))


def _target_with_another_id(f: AcceptanceFacts) -> AcceptanceFacts:
    assert f.target is not None
    return dataclasses.replace(f, target=dataclasses.replace(f.target, id=ID_ACTIVE))


def _key_holder_of_another_project(f: AcceptanceFacts) -> AcceptanceFacts:
    holder = dataclasses.replace(stored_command(4), project_id=ID_PROJECT2)
    return dataclasses.replace(f, by_key=holder)


# Facts that do not belong to the request are a store fault, never an answer: an internal error
# that matches none of the typed classes (the domain's ProjectMismatch is internal).
@pytest.mark.parametrize(
    "arrange",
    [
        _state_of_another_project,
        _state_of_another_project_with_replay,
        _state_of_another_project_with_in_flight,
        _target_of_another_project,
        _target_with_another_id,
        _key_holder_of_another_project,
    ],
    ids=[
        "state of another project",
        "state of another project with a replay under the key",
        "state of another project with a command in flight",
        "target of another project",
        "target with another ID",
        "key holder of another project",
    ],
)
def test_decide_refuses_facts_of_another_project_or_release(
    arrange: Callable[[AcceptanceFacts], AcceptanceFacts],
) -> None:
    facts = arrange(_clean_deploy_facts())
    with pytest.raises(Exception) as exc_info:  # internal: any untyped error
        new_acceptance_decider(_accept_params())(facts)
    assert_internal(exc_info.value)


# The decide function is pure: it may be called twice for one acceptance (decision 0004 section 4,
# raced violation) and gives the same answer; the facts it reads are frozen values.
def test_decide_is_pure() -> None:
    decide = new_acceptance_decider(_accept_params())
    facts = _clean_deploy_facts()
    a = decide(facts)
    b = decide(facts)
    assert a.insert
    assert b.insert
    assert a.command == b.command
    assert facts == _clean_deploy_facts()


# The requester in the parameters reaches the domain: a malformed one is the domain's
# InvalidRequester from insert_decision, the last step.
def test_decide_passes_the_requester_to_the_domain() -> None:
    params = dataclasses.replace(_accept_params(), requested_by_subject=" padded")
    with pytest.raises(InvalidRequester):
        new_acceptance_decider(params)(_clean_deploy_facts())


def test_decide_accepts_a_rollback_while_hosting_offline() -> None:
    """Decision 0014 section 13 (amending 0001 section 4): an operator rollback clears an
    uncertain route whatever the hosting status (a failed resume leaves hosting offline)."""
    decision = new_acceptance_decider(_accept_params(CommandKind.ROLLBACK))(
        _rollback_while_offline(_clean_deploy_facts())
    )
    assert decision.insert
    assert decision.command.kind is CommandKind.ROLLBACK


@pytest.mark.parametrize(
    ("kind", "key", "want"),
    [
        (CommandKind.SUSPEND_HOSTING, "project-deleted", OfflineReason.PROJECT_DELETED),
        (CommandKind.SUSPEND_HOSTING, "owner-status:9", OfflineReason.USER_SUSPENDED),
        (CommandKind.RESUME_HOSTING, "owner-status:10", OfflineReason.USER_SUSPENDED),
        (CommandKind.SUSPEND_HOSTING, "evt-1", OfflineReason.BILLING),
        (CommandKind.RESUME_HOSTING, "evt-2.retry-1", OfflineReason.BILLING),
    ],
)
def test_insert_decision_sets_the_hosting_reason(
    kind: CommandKind, key: str, want: OfflineReason
) -> None:
    params = dataclasses.replace(
        _accept_params(kind),
        request=CommandRequest(
            project_id=ID_PROJECT, kind=kind, release_id=None, idempotency_key=key
        ),
        requested_by_subject="",
    )
    command = insert_decision(params, project_hosting(3)).command
    assert command.hosting_reason is want
    command.validate()


def test_insert_decision_leaves_the_reason_of_a_deploy_empty() -> None:
    command = insert_decision(_accept_params(), project_hosting(3)).command
    assert command.hosting_reason is None


def _hosting_params(kind: CommandKind, key: str) -> AcceptCommandParams:
    return dataclasses.replace(
        _accept_params(kind),
        request=CommandRequest(
            project_id=ID_PROJECT, kind=kind, release_id=None, idempotency_key=key
        ),
        requested_by_subject="",
    )


@pytest.mark.parametrize(
    ("key", "version", "accepted"),
    [
        ("owner-status:4", 4, True),
        ("owner-status:4.retry-1", 4, True),
        ("owner-status:3", 4, False),  # a newer owner status replaced it
        ("owner-status:3.retry-1", 4, False),
        ("owner-status:5", 4, False),  # the intake records the version before it accepts
        ("evt-1", 4, True),  # billing does not follow the owner status version
        ("project-deleted", 4, True),
    ],
)
def test_decide_accepts_a_user_suspended_key_only_at_the_row_version(
    key: str, version: int, accepted: bool
) -> None:
    """Notion: a user_suspended retry only while its version equals owner_status_version. The
    decision reads the locked row, so a version recorded after the retry read its history still
    refuses the retry (decision 0021)."""
    facts = AcceptanceFacts(
        hosting=dataclasses.replace(project_hosting(3), owner_status_version=version)
    )
    decide = new_acceptance_decider(_hosting_params(CommandKind.SUSPEND_HOSTING, key))
    if accepted:
        assert decide(facts).insert
    else:
        with pytest.raises(OwnerStatusOutdated):
            decide(facts)


def test_decide_replays_a_stored_owner_status_key_of_an_older_version() -> None:
    """A stored command is a replay whatever the row's version: the outdated check guards only
    a new insert."""
    params = _hosting_params(CommandKind.SUSPEND_HOSTING, "owner-status:3")
    stored = insert_decision(params, project_hosting(3)).command
    facts = AcceptanceFacts(
        hosting=dataclasses.replace(project_hosting(4), owner_status_version=4), by_key=stored
    )
    decision = new_acceptance_decider(params)(facts)
    assert not decision.insert and decision.command.id == stored.id


def test_decide_refuses_an_outdated_owner_status_before_the_command_in_flight() -> None:
    """Outdated is permanent, busy is temporary: the permanent answer wins, so the event or the
    retry is not redelivered for nothing."""
    busy = insert_decision(
        _hosting_params(CommandKind.SUSPEND_HOSTING, "evt-9"), project_hosting(3)
    )
    facts = AcceptanceFacts(
        hosting=dataclasses.replace(project_hosting(4), owner_status_version=4),
        in_flight=busy.command,
    )
    with pytest.raises(OwnerStatusOutdated):
        new_acceptance_decider(_hosting_params(CommandKind.RESUME_HOSTING, "owner-status:3"))(facts)
