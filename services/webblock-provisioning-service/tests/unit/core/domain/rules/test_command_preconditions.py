"""The acceptance preconditions of a command (decision 0001 section 4), cut from
entities/test_command.py when the rule moved to core/domain/rules/command_preconditions.py (decision
0025 section 4)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

import pytest
from tests.unit.core.domain.builders import (
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
    NIL_ID,
    CommandKind,
    DomainError,
    HostingOffline,
    HostingStatus,
    IllegalTransition,
    InvalidCommandTarget,
    InvalidId,
    OfflineReason,
    ProjectHosting,
    ProjectMismatch,
    Release,
    ReleaseOutdated,
    ReleaseStatus,
    RouteNotStable,
    RoutingStatus,
    UnknownEnumValue,
    check_command_preconditions,
)

# ---- fixtures ----


DIGEST_A = "sha256:" + "a" * 64

# The hosting kinds, by hand (decision 0001 section 4): the test does not take them from the code
# under test.
HOSTING_KINDS = frozenset({CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING})

ALL_KINDS = list(CommandKind)

# The release status each release kind accepts, by hand from the table.
REQUIRED_STATUS: dict[CommandKind, ReleaseStatus] = {
    CommandKind.DEPLOY: ReleaseStatus.BUILT,
    CommandKind.PROMOTE: ReleaseStatus.CANDIDATE_READY,
    CommandKind.ROLLBACK: ReleaseStatus.PROMOTING,
}


# Kinds that name no release and have no requester: the hosting kinds and sync_domains (decision
# 0014 section 16 item 1). Only the hosting kinds may end skipped.
NO_RELEASE_KINDS = HOSTING_KINDS | {CommandKind.SYNC_DOMAINS}


def _no_release(kind: CommandKind) -> bool:
    return kind in NO_RELEASE_KINDS


def _live_release() -> Release:
    """The project's live release ID_PREV, created at T0."""
    return replace(valid_row(ReleaseStatus.LIVE), id=ID_PREV)


def _base_hosting() -> ProjectHosting:
    """The hosting row at command number 3, stable and online."""
    return ProjectHosting(
        project_id=ID_PROJECT,
        last_command_number=3,
        routing_status=RoutingStatus.STABLE,
        hosting_status=HostingStatus.ONLINE,
        updated_at=T0,
        subdomain="shop",
    )


def _hosting_with(routing: RoutingStatus, hosting: HostingStatus) -> ProjectHosting:
    """The base hosting row with the routing and hosting statuses."""
    return replace(_base_hosting(), routing_status=routing, hosting_status=hosting)


# ---- preconditions (decision 0001 section 4) ----


def _expected_precondition(
    kind: CommandKind, status: ReleaseStatus, routing: RoutingStatus, hosting: HostingStatus
) -> type[DomainError] | None:
    """The table and its precedence, written out by hand, for a project with no live release and
    a target (when the kind takes one) of the same project."""
    if kind == CommandKind.REDEPLOY:
        # Decision 0021, Notion: routing stable, hosting online, then the live release (none here).
        if routing != RoutingStatus.STABLE:
            return RouteNotStable
        if hosting == HostingStatus.OFFLINE:
            return HostingOffline
        return InvalidCommandTarget
    if kind == CommandKind.ROLLBACK:
        # Decision 0014 section 13: any uncertain route, either hosting status, and without a live
        # release any release of the project.
        return None if routing == RoutingStatus.UNCERTAIN else IllegalTransition
    if routing != RoutingStatus.STABLE:
        return RouteNotStable
    if kind == CommandKind.SYNC_DOMAINS:
        # No release, but the site must be online (decision 0014 section 16 item 1).
        return HostingOffline if hosting == HostingStatus.OFFLINE else None
    if _no_release(kind):
        return None
    if hosting == HostingStatus.OFFLINE:
        return HostingOffline
    if status != REQUIRED_STATUS[kind]:
        return IllegalTransition
    return None


_MATRIX = [
    (kind, status, routing, hosting)
    for kind in ALL_KINDS
    for status in ReleaseStatus
    for routing in RoutingStatus
    for hosting in HostingStatus
    # A hosting kind takes no release; one pass per routing and hosting status.
    if not (_no_release(kind) and status != ReleaseStatus.ACCEPTED)
]


def test_command_preconditions_matrix_size() -> None:
    assert len(_MATRIX) == 4 * 9 * 3 * 2 + 3 * 3 * 2


@pytest.mark.parametrize(
    ("kind", "status", "routing", "hosting"),
    _MATRIX,
    ids=[f"{k.value}/{s.value}/{r.value}/{h.value}" for k, s, r, h in _MATRIX],
)
def test_command_preconditions_matrix(
    kind: CommandKind, status: ReleaseStatus, routing: RoutingStatus, hosting: HostingStatus
) -> None:
    """FR-14.4, FR-23.3: command preconditions matrix."""
    target = None if _no_release(kind) else valid_row(status)
    want = _expected_precondition(kind, status, routing, hosting)
    if want is None:
        check_command_preconditions(kind, _hosting_with(routing, hosting), target, None)
        return
    with pytest.raises(want):
        check_command_preconditions(kind, _hosting_with(routing, hosting), target, None)


def test_command_preconditions_target() -> None:
    stable = _hosting_with(RoutingStatus.STABLE, HostingStatus.ONLINE)
    uncertain = _hosting_with(RoutingStatus.UNCERTAIN, HostingStatus.OFFLINE)
    for kind in (CommandKind.SUSPEND_HOSTING, CommandKind.RESUME_HOSTING):
        # A hosting command that names a release, even with the route not stable (target first).
        for st in (stable, uncertain):
            with pytest.raises(InvalidCommandTarget):
                check_command_preconditions(kind, st, valid_row(ReleaseStatus.LIVE), None)
    for kind in REQUIRED_STATUS:
        for st in (stable, uncertain):
            with pytest.raises(InvalidCommandTarget):
                check_command_preconditions(kind, st, None, None)


@pytest.mark.parametrize("kind", ALL_KINDS, ids=[k.value for k in ALL_KINDS])
def test_command_preconditions_project_mismatch(kind: CommandKind) -> None:
    # Project mismatch comes before every other check, including the target check of a hosting
    # kind.
    target = replace(valid_row(ReleaseStatus.FAILED), project_id=ID_PROJECT2)
    st = _hosting_with(RoutingStatus.CHANGING, HostingStatus.OFFLINE)
    with pytest.raises(ProjectMismatch):
        check_command_preconditions(kind, st, target, None)


def test_command_preconditions_project_mismatch_live_release() -> None:
    # A live release of another project is a mismatch too.
    st = replace(_base_hosting(), live_release_id=ID_PREV)
    live = replace(_live_release(), project_id=ID_PROJECT2)
    with pytest.raises(ProjectMismatch):
        check_command_preconditions(CommandKind.DEPLOY, st, valid_row(ReleaseStatus.BUILT), live)


_ACCEPTED = ReleaseStatus.ACCEPTED
_BUILT = ReleaseStatus.BUILT
_CANDIDATE = ReleaseStatus.CANDIDATE_READY
_PROMOTING = ReleaseStatus.PROMOTING
_LIVE = ReleaseStatus.LIVE
_STABLE = RoutingStatus.STABLE
_CHANGING = RoutingStatus.CHANGING
_UNCERTAIN = RoutingStatus.UNCERTAIN
_ONLINE = HostingStatus.ONLINE
_OFFLINE = HostingStatus.OFFLINE
_EMPTY_HOSTING = cast(HostingStatus, "")
_UNKNOWN_HOSTING = cast(HostingStatus, "paused")
_UNKNOWN_ROUTING = cast(RoutingStatus, "moving")

_PRECEDENCE: dict[
    str,
    tuple[
        CommandKind, ReleaseStatus | None, RoutingStatus, HostingStatus, type[DomainError] | None
    ],
] = {
    "route before hosting": (CommandKind.DEPLOY, _BUILT, _CHANGING, _OFFLINE, RouteNotStable),
    "route before status": (CommandKind.PROMOTE, _ACCEPTED, _UNCERTAIN, _ONLINE, RouteNotStable),
    "hosting before status": (CommandKind.DEPLOY, _ACCEPTED, _STABLE, _OFFLINE, HostingOffline),
    "promote offline": (CommandKind.PROMOTE, _CANDIDATE, _STABLE, _OFFLINE, HostingOffline),
    "rollback route not uncertain": (
        CommandKind.ROLLBACK,
        _PROMOTING,
        _STABLE,
        _ONLINE,
        IllegalTransition,
    ),
    "rollback while changing": (
        CommandKind.ROLLBACK,
        _PROMOTING,
        _CHANGING,
        _ONLINE,
        IllegalTransition,
    ),
    # Decision 0014 section 13 (amending 0001 section 4): a rollback is accepted while hosting is
    # offline (after a failed resume); the hosting status picks where the route goes.
    "rollback offline": (CommandKind.ROLLBACK, _PROMOTING, _UNCERTAIN, _OFFLINE, None),
    "rollback serving": (CommandKind.ROLLBACK, _PROMOTING, _UNCERTAIN, _ONLINE, None),
    "rollback route before hosting": (
        CommandKind.ROLLBACK,
        _PROMOTING,
        _STABLE,
        _OFFLINE,
        IllegalTransition,
    ),
    # Without a live release, a rollback names any release of the project (0014 section 13).
    "rollback of the live-named release, offline": (
        CommandKind.ROLLBACK,
        _LIVE,
        _UNCERTAIN,
        _OFFLINE,
        None,
    ),
    "rollback of another release without a live one": (
        CommandKind.ROLLBACK,
        _CANDIDATE,
        _UNCERTAIN,
        _ONLINE,
        None,
    ),
    # Fail closed (VR-01): only online admits deploy, promote and rollback; an empty or unknown
    # hosting status is refused like offline, as an unknown routing status is refused like a route
    # that is not stable.
    "deploy empty hosting": (CommandKind.DEPLOY, _BUILT, _STABLE, _EMPTY_HOSTING, HostingOffline),
    "deploy unknown hosting": (
        CommandKind.DEPLOY,
        _BUILT,
        _STABLE,
        _UNKNOWN_HOSTING,
        HostingOffline,
    ),
    "promote empty hosting": (
        CommandKind.PROMOTE,
        _CANDIDATE,
        _STABLE,
        _EMPTY_HOSTING,
        HostingOffline,
    ),
    "promote unknown hosting": (
        CommandKind.PROMOTE,
        _CANDIDATE,
        _STABLE,
        _UNKNOWN_HOSTING,
        HostingOffline,
    ),
    "rollback empty hosting": (
        CommandKind.ROLLBACK,
        _PROMOTING,
        _UNCERTAIN,
        _EMPTY_HOSTING,
        HostingOffline,
    ),
    "rollback unknown hosting": (
        CommandKind.ROLLBACK,
        _PROMOTING,
        _UNCERTAIN,
        _UNKNOWN_HOSTING,
        HostingOffline,
    ),
    "deploy unknown route": (CommandKind.DEPLOY, _BUILT, _UNKNOWN_ROUTING, _ONLINE, RouteNotStable),
    "rollback of no promoting": (
        CommandKind.ROLLBACK,
        _LIVE,
        _UNCERTAIN,
        _ONLINE,
        None,
    ),
    "suspend while uncertain": (
        CommandKind.SUSPEND_HOSTING,
        None,
        _UNCERTAIN,
        _ONLINE,
        RouteNotStable,
    ),
    "resume while changing": (
        CommandKind.RESUME_HOSTING,
        None,
        _CHANGING,
        _OFFLINE,
        RouteNotStable,
    ),
    "suspend when offline": (CommandKind.SUSPEND_HOSTING, None, _STABLE, _OFFLINE, None),
    "resume when serving": (CommandKind.RESUME_HOSTING, None, _STABLE, _ONLINE, None),
}


@pytest.mark.parametrize(
    ("kind", "status", "routing", "hosting", "want"),
    list(_PRECEDENCE.values()),
    ids=list(_PRECEDENCE),
)
def test_command_preconditions_precedence(
    kind: CommandKind,
    status: ReleaseStatus | None,
    routing: RoutingStatus,
    hosting: HostingStatus,
    want: type[DomainError] | None,
) -> None:
    target = None if status is None else valid_row(status)
    if want is None:
        check_command_preconditions(kind, _hosting_with(routing, hosting), target, None)
        return
    with pytest.raises(want):
        check_command_preconditions(kind, _hosting_with(routing, hosting), target, None)


_OLDER: dict[str, tuple[datetime, UUID, type[DomainError] | None]] = {
    "created before": (T0 - timedelta(seconds=1), ID_RELEASE, ReleaseOutdated),
    "created after": (T1, ID_RELEASE, None),
    "same instant, smaller id": (T0, ID_RELEASE, ReleaseOutdated),
    "same instant, larger id": (T0, UUID("0190a1b2-0000-7000-8000-0000000000c9"), None),
    "one microsecond before": (
        T0 - timedelta(microseconds=1),
        UUID("0190a1b2-0000-7000-8000-0000000000c9"),
        ReleaseOutdated,
    ),
    "one microsecond after": (T0 + timedelta(microseconds=1), ID_RELEASE, None),
}
_OLDER_CASES = [
    (name, kind, *case)
    for name, case in _OLDER.items()
    for kind in (CommandKind.DEPLOY, CommandKind.PROMOTE)
]


@pytest.mark.parametrize(
    ("kind", "created", "release_id", "want"),
    [c[1:] for c in _OLDER_CASES],
    ids=[f"{c[0]}/{c[1].value}" for c in _OLDER_CASES],
)
def test_command_preconditions_older_release(
    kind: CommandKind,
    created: datetime,
    release_id: UUID,
    want: type[DomainError] | None,
) -> None:
    # ID_PREV (...b1) sorts after ID_RELEASE (...a1).
    st = replace(_base_hosting(), live_release_id=ID_PREV)
    target = replace(valid_row(REQUIRED_STATUS[kind]), id=release_id, created_at=created)
    if want is None:
        check_command_preconditions(kind, st, target, _live_release())
        return
    with pytest.raises(want):
        check_command_preconditions(kind, st, target, _live_release())


def test_command_preconditions_older_release_order_and_exemptions() -> None:
    st = replace(_base_hosting(), live_release_id=ID_PREV)
    live = _live_release()
    # Release status comes before the older-release rule.
    old = replace(valid_row(ReleaseStatus.ACCEPTED), created_at=T0 - timedelta(hours=1))
    with pytest.raises(IllegalTransition):
        check_command_preconditions(CommandKind.DEPLOY, st, old, live)
    # A rollback is never refused as outdated.
    ust = replace(st, routing_status=RoutingStatus.UNCERTAIN)
    p = replace(valid_row(ReleaseStatus.PROMOTING), created_at=T0 - timedelta(hours=1))
    check_command_preconditions(CommandKind.ROLLBACK, ust, p, live)
    # Without a live release nothing is outdated.
    no_live = _base_hosting()
    with pytest.raises(IllegalTransition):
        check_command_preconditions(CommandKind.DEPLOY, no_live, old, None)
    built = replace(valid_row(ReleaseStatus.BUILT), created_at=T0 - timedelta(hours=1))
    check_command_preconditions(CommandKind.DEPLOY, no_live, built, None)


def _live_must_match_cases() -> dict[str, tuple[ProjectHosting, Release | None]]:
    with_live = replace(_base_hosting(), live_release_id=ID_PREV)
    live = _live_release()
    return {
        "state names one, none passed": (with_live, None),
        "state names none, one passed": (_base_hosting(), live),
        "different release passed": (with_live, replace(live, id=ID_RELEASE2)),
        "active not live": (with_live, replace(live, status=ReleaseStatus.SUPERSEDED)),
    }


_LIVE_MUST_MATCH = _live_must_match_cases()


@pytest.mark.parametrize(
    ("hosting", "live"), list(_LIVE_MUST_MATCH.values()), ids=list(_LIVE_MUST_MATCH)
)
def test_command_preconditions_active_release_must_match_state(
    hosting: ProjectHosting, live: Release | None
) -> None:
    """The live release passed in must be the one the hosting row names, and live."""
    target = replace(valid_row(ReleaseStatus.BUILT), created_at=T1)
    # Control: consistent input is accepted.
    check_command_preconditions(
        CommandKind.DEPLOY,
        replace(_base_hosting(), live_release_id=ID_PREV),
        target,
        _live_release(),
    )
    for kind in ALL_KINDS:
        tg = None if _no_release(kind) else target
        with pytest.raises(IllegalTransition):
            check_command_preconditions(kind, hosting, tg, live)


def test_command_preconditions_invalid_input() -> None:
    st = _base_hosting()
    with pytest.raises(UnknownEnumValue):
        check_command_preconditions(
            cast(CommandKind, "bogus"), st, valid_row(ReleaseStatus.BUILT), None
        )
    zero_project = replace(st, project_id=NIL_ID)
    zero_target_project = replace(valid_row(ReleaseStatus.BUILT), project_id=NIL_ID)
    with pytest.raises(InvalidId):
        check_command_preconditions(CommandKind.DEPLOY, zero_project, zero_target_project, None)
    with pytest.raises(InvalidId):
        check_command_preconditions(CommandKind.SUSPEND_HOSTING, zero_project, None, None)
    zero_target = replace(valid_row(ReleaseStatus.BUILT), id=NIL_ID)
    with pytest.raises(InvalidId):
        check_command_preconditions(CommandKind.DEPLOY, st, zero_target, None)


def test_sync_domains_is_accepted_only_for_a_stable_route_and_online_hosting() -> None:
    online = _hosting_with(RoutingStatus.STABLE, HostingStatus.ONLINE)
    check_command_preconditions(CommandKind.SYNC_DOMAINS, online, None, None)
    with pytest.raises(HostingOffline):
        check_command_preconditions(
            CommandKind.SYNC_DOMAINS,
            _hosting_with(RoutingStatus.STABLE, HostingStatus.OFFLINE),
            None,
            None,
        )
    for routing in (RoutingStatus.CHANGING, RoutingStatus.UNCERTAIN):
        with pytest.raises(RouteNotStable):
            check_command_preconditions(
                CommandKind.SYNC_DOMAINS,
                _hosting_with(routing, HostingStatus.ONLINE),
                None,
                None,
            )
    with pytest.raises(InvalidCommandTarget):
        check_command_preconditions(
            CommandKind.SYNC_DOMAINS, online, valid_row(ReleaseStatus.LIVE), None
        )


def test_redeploy_needs_the_live_release_online_and_stable() -> None:
    """Decision 0021, Notion "redeploy": accepted only while routing is stable and hosting online,
    and only naming the project's live release. The API store still refuses it until Phase C
    (D10).

    FR-14.4, FR-23.3, FR-35.2."""
    live = _live_release()
    online = replace(
        _hosting_with(RoutingStatus.STABLE, HostingStatus.ONLINE), live_release_id=ID_PREV
    )
    check_command_preconditions(CommandKind.REDEPLOY, online, live, live)
    # Another release than the live one.
    other = replace(valid_row(ReleaseStatus.BUILT), id=ID_RELEASE2)
    with pytest.raises(InvalidCommandTarget):
        check_command_preconditions(CommandKind.REDEPLOY, online, other, live)
    # Offline (a reason is held).
    offline = replace(
        online,
        hosting_status=HostingStatus.OFFLINE,
        offline_reasons=frozenset({OfflineReason.ADMIN_SUSPENDED}),
    )
    with pytest.raises(HostingOffline):
        check_command_preconditions(CommandKind.REDEPLOY, offline, live, live)
    # A route that is not stable.
    for routing in (RoutingStatus.CHANGING, RoutingStatus.UNCERTAIN):
        with pytest.raises(RouteNotStable):
            check_command_preconditions(
                CommandKind.REDEPLOY, replace(online, routing_status=routing), live, live
            )
    # No live release at all.
    never = _hosting_with(RoutingStatus.STABLE, HostingStatus.ONLINE)
    with pytest.raises(InvalidCommandTarget):
        check_command_preconditions(
            CommandKind.REDEPLOY, never, valid_row(ReleaseStatus.BUILT), None
        )
