"""The acceptance preconditions of a command: hosting, routing, target and live release (decision
0001 section 4; rule; cut from the commands row module, decision 0025 CQ1)."""

from __future__ import annotations

from provisioning.core.domain.entities.project_hosting import ProjectHosting
from provisioning.core.domain.entities.release import Release
from provisioning.core.domain.enums.states import (
    CommandKind,
    HostingStatus,
    ReleaseStatus,
    RoutingStatus,
    parse_command_kind,
)
from provisioning.core.domain.errors import (
    HostingOffline,
    IllegalTransition,
    InvalidCommandTarget,
    InvalidId,
    ProjectMismatch,
    ReleaseOutdated,
    RouteNotStable,
)


def check_command_preconditions(
    kind: CommandKind,
    hosting: ProjectHosting,
    target: Release | None,
    live: Release | None,
) -> None:
    """Decide whether a command of ``kind`` may be accepted for the project whose locked hosting
    row is ``hosting`` (decision 0001 section 4). ``target`` is the release the command names
    (None for a hosting kind); ``live`` is the release ``hosting.live_release_id`` names. The
    precedence is: project mismatch, command target, routing status, hosting status, release
    status, older release.

    An operator rollback (decision 0014 section 13, amending 0001) is accepted for any uncertain
    route, whatever the hosting status: it names the promoting release, the live release, or,
    without a live release, any release of the project (the one that failed)."""
    kind = parse_command_kind(kind)
    if hosting.project_id.int == 0 or (target is not None and target.id.int == 0):
        raise InvalidId("project and release IDs are required")
    if target is not None and target.project_id != hosting.project_id:
        raise ProjectMismatch(f"release {target.id}")
    if live is not None and live.project_id != hosting.project_id:
        raise ProjectMismatch(f"live release {live.id}")
    if kind.names_no_release() != (target is None):
        raise InvalidCommandTarget(str(kind))
    check_live_release(hosting, live)
    if kind == CommandKind.REDEPLOY:
        # The admin's redeploy of the live release (decision 0023 G9, G12): the API accepts it
        # since Phase C, and the store takes it (0021 D10 lifted).
        _check_redeploy(kind, hosting, target, live)
        return
    if kind == CommandKind.ROLLBACK:
        if hosting.routing_status != RoutingStatus.UNCERTAIN:
            raise IllegalTransition(f"rollback while routing is {hosting.routing_status}")
    elif hosting.routing_status != RoutingStatus.STABLE:
        raise RouteNotStable(f"{kind} while routing is {hosting.routing_status}")
    if target is None:
        # A hosting command checks hosting_status when it runs. A sync_domains command applies
        # hostnames to the route, so the site must be online when it is accepted (decision 0014
        # section 16 item 1).
        if kind == CommandKind.SYNC_DOMAINS and hosting.hosting_status != HostingStatus.ONLINE:
            raise HostingOffline(f"{kind} while hosting is not online")
        return
    if kind == CommandKind.ROLLBACK:
        # Either hosting status is accepted; it picks where the route goes. Fail closed on an empty
        # or unknown one (VR-01), as for deploy and promote.
        if hosting.hosting_status not in (HostingStatus.ONLINE, HostingStatus.OFFLINE):
            raise HostingOffline("rollback while hosting status is unknown")
        _check_rollback_target(target, live)
        return
    # Deploy and promote require online. Fail closed: an empty or unknown hosting status
    # is refused like offline, as an unknown routing status is refused like one not stable.
    if hosting.hosting_status != HostingStatus.ONLINE:
        raise HostingOffline(f"{kind} while hosting is not online")
    if target.status != RELEASE_STATUS_FOR[kind]:
        raise IllegalTransition(f"{kind} command on release {target.id} in status {target.status}")
    if live is not None and _created_before(target, live):
        raise ReleaseOutdated(f"release {target.id} was created before live release {live.id}")


def _check_redeploy(
    kind: CommandKind, hosting: ProjectHosting, target: Release | None, live: Release | None
) -> None:
    """Notion "redeploy" (decision 0021): routing stable, hosting online, and the target is the
    project's live release (``InvalidCommandTarget`` otherwise, also when there is none)."""
    if hosting.routing_status != RoutingStatus.STABLE:
        raise RouteNotStable(f"{kind} while routing is {hosting.routing_status}")
    if hosting.hosting_status != HostingStatus.ONLINE:
        raise HostingOffline(f"{kind} while hosting is not online")
    if live is None or target is None or target.id != hosting.live_release_id:
        raise InvalidCommandTarget(f"{kind} names a release other than the live release")


# The release status deploy and promote require at acceptance. A rollback has no single status
# (decision 0014 section 13; ``_check_rollback_target``).
RELEASE_STATUS_FOR: dict[str, ReleaseStatus] = {
    CommandKind.DEPLOY: ReleaseStatus.BUILT,
    CommandKind.PROMOTE: ReleaseStatus.CANDIDATE_READY,
}


def _check_rollback_target(target: Release, live: Release | None) -> None:
    """A rollback names the promoting release, the live release, or, when the project has no live
    release, any of its releases (``IllegalTransition`` otherwise). The project was checked."""
    if target.status == ReleaseStatus.PROMOTING or live is None or target.id == live.id:
        return
    raise IllegalTransition(
        f"rollback names release {target.id} in status {target.status}, "
        "neither the promoting nor the live release"
    )


def check_live_release(hosting: ProjectHosting, live: Release | None) -> None:
    """The live release passed in is the live release ``hosting.live_release_id`` names, or both
    are absent (``IllegalTransition``)."""
    if (hosting.live_release_id is None) != (live is None) or (
        live is not None
        and (live.id != hosting.live_release_id or live.status != ReleaseStatus.LIVE)
    ):
        raise IllegalTransition(
            f"live release does not match the hosting row of project {hosting.project_id}"
        )


def _created_before(a: Release, b: Release) -> bool:
    """Provisioning's acceptance order: created_at, then release ID."""
    if a.created_at != b.created_at:
        return a.created_at < b.created_at
    return a.id.bytes < b.id.bytes
