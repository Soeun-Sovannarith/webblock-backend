"""The PostgreSQL enums of the business tables (revision 19 names, decision 0012 section 3).

Members are declared in the declaration order of the PostgreSQL enum, so iterating a class gives
the enum order. ``parse_*`` matches a text exactly and raises ``UnknownEnumValue`` naming the enum,
never the input.
"""

from __future__ import annotations

from enum import StrEnum

from provisioning.core.domain.errors import UnknownEnumValue


class ReleaseStatus(StrEnum):
    """provisioning.release_status."""

    ACCEPTED = "accepted"
    BUILDING = "building"
    BUILT = "built"
    CANDIDATE_READY = "candidate_ready"
    PROMOTING = "promoting"
    LIVE = "live"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"

    def unfinished(self) -> bool:
        """Whether the release is still in flight (accepted to promoting). Descriptive only: no
        rule limits how many unfinished releases a project has."""
        return self in _UNFINISHED_RELEASE


class ReleaseStepName(StrEnum):
    """provisioning.release_step_name, in Notion's order (migration 00022)."""

    BUILD = "build"
    CANDIDATE = "candidate"
    PROMOTION = "promotion"
    ROLLBACK = "rollback"
    ACCESS_SETUP = "access_setup"
    OBSERVATION = "observation"


class ReleaseStepStatus(StrEnum):
    """provisioning.release_step_status."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RoutingStatus(StrEnum):
    """provisioning.routing_status."""

    STABLE = "stable"
    CHANGING = "changing"
    UNCERTAIN = "uncertain"


class HostingStatus(StrEnum):
    """provisioning.hosting_status."""

    ONLINE = "online"
    OFFLINE = "offline"


class CommandKind(StrEnum):
    """provisioning.command_kind."""

    DEPLOY = "deploy"
    PROMOTE = "promote"
    ROLLBACK = "rollback"
    SUSPEND_HOSTING = "suspend_hosting"
    RESUME_HOSTING = "resume_hosting"
    # Notion lists redeploy before sync_domains, and that list is the enum order (decision 0021).
    # Phase A stores the kind but accepts and runs none (decision 0021 D10).
    REDEPLOY = "redeploy"
    SYNC_DOMAINS = "sync_domains"

    def is_hosting(self) -> bool:
        """Whether the kind is suspend_hosting or resume_hosting, the kinds of a Payment event."""
        return self is CommandKind.SUSPEND_HOSTING or self is CommandKind.RESUME_HOSTING

    def names_no_release(self) -> bool:
        """Whether the kind names no release and has no requester: the hosting kinds and
        sync_domains (decision 0014 section 16 item 1)."""
        return self.is_hosting() or self is CommandKind.SYNC_DOMAINS


class CommandStatus(StrEnum):
    """provisioning.command_status."""

    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"

    def unfinished(self) -> bool:
        """Whether the command is still accepted or running."""
        return self is CommandStatus.ACCEPTED or self is CommandStatus.RUNNING


class OfflineReason(StrEnum):
    """provisioning.offline_reason: why a site is offline (decision 0021). A site is online
    exactly when it holds none."""

    BILLING = "billing"
    USER_SUSPENDED = "user_suspended"
    PROJECT_DELETED = "project_deleted"
    # An admin suspended this one website (00019). No key gives it until 00020 adds admin keys.
    ADMIN_SUSPENDED = "admin_suspended"


def parse_release_status(text: object) -> ReleaseStatus:
    return _parse(ReleaseStatus, "release status", text)


def parse_release_step_name(text: object) -> ReleaseStepName:
    return _parse(ReleaseStepName, "release step name", text)


def parse_release_step_status(text: object) -> ReleaseStepStatus:
    return _parse(ReleaseStepStatus, "release step status", text)


def parse_routing_status(text: object) -> RoutingStatus:
    return _parse(RoutingStatus, "routing status", text)


def parse_hosting_status(text: object) -> HostingStatus:
    return _parse(HostingStatus, "hosting status", text)


def parse_command_kind(text: object) -> CommandKind:
    return _parse(CommandKind, "command kind", text)


def parse_command_status(text: object) -> CommandStatus:
    return _parse(CommandStatus, "command status", text)


def parse_offline_reason(value: object) -> OfflineReason:
    return _parse(OfflineReason, "offline reason", value)


_UNFINISHED_RELEASE = frozenset(
    {
        ReleaseStatus.ACCEPTED,
        ReleaseStatus.BUILDING,
        ReleaseStatus.BUILT,
        ReleaseStatus.CANDIDATE_READY,
        ReleaseStatus.PROMOTING,
    }
)


def _parse[E: StrEnum](enum: type[E], label: str, text: object) -> E:
    """Match ``text`` exactly against the enum's values; the error names the enum, never the
    input."""
    if isinstance(text, str):
        for member in enum:
            if member.value == text:
                return member
    raise UnknownEnumValue(label) from None
