"""Typed application errors: one class per failure a use case reports, and the two errors of the
prototype's build endpoint (decision 0014 section 3, "Build").

The transports map each class to the status and code of decision 0023 G6 (the audit's table
C.1.3), named in each docstring. Messages carry no request content. Classification walks
``__cause__`` only (``provisioning.core.domain.is_error``).
"""

from __future__ import annotations

from uuid import UUID

from provisioning.core.domain import error_chain


class ApplicationError(Exception):
    """Base class of every application error."""


class Unauthenticated(ApplicationError):
    """unauthenticated, 401: the principal's subject fails the requester rule."""


class ActionDenied(ApplicationError):
    """403, ACCESS_DENIED: the token does not carry the action."""


class ProjectAccessDenied(ApplicationError):
    """404, PROJECT_NOT_FOUND: the caller has no access to the project (concealed as not found)."""


class NotFound(ApplicationError):
    """not found, 404: the release or command does not exist in the project, or the project has
    no stored hosting row."""


class LimitReached(ApplicationError):
    """422, PUBLISH_LIMIT_REACHED: a limit owned by Payment refuses a deploy or promote."""


class _UpstreamError(ApplicationError):
    """An error a call to another service can cause (decision 0023 G14). ``upstream_reason`` is
    the ``ErrorInfo.reason`` the service answered, or None; ``deadline`` is True when the call's
    deadline passed. Both are optional and default to nothing known."""

    def __init__(
        self, *args: object, upstream_reason: str | None = None, deadline: bool = False
    ) -> None:
        super().__init__(*args)
        self.upstream_reason = upstream_reason
        self.deadline = deadline


class DependencyUnavailable(_UpstreamError):
    """503, DEPENDENCY_UNAVAILABLE: a required dependency is down; 504, DEPENDENCY_TIMEOUT when
    ``deadline`` is set."""


class DependencyNotConfigured(ApplicationError):
    """503, DEPENDENCY_UNAVAILABLE: no real adapter for an external port."""


class ProjectAccessUnavailable(_UpstreamError):
    """503, PROJECT_ACCESS_UNAVAILABLE: project access could not be checked (504,
    DEPENDENCY_TIMEOUT when ``deadline`` is set). Raised ``from`` the ProjectAccess error, which
    stays in the cause chain."""


class OperationNotAvailable(ApplicationError):
    """503, OPERATION_UNAVAILABLE: the operation has no implementation yet."""


class OperationDisabled(ApplicationError):
    """503, OPERATION_UNAVAILABLE: the operation is switched off in this build."""


class OperationInProgress(ApplicationError):
    """409, COMMAND_IN_PROGRESS: another command of the project is in flight.

    ``command_id`` names that command for the response's ``commandId``, or is None when unknown."""

    def __init__(self, command_id: UUID | None = None) -> None:
        if command_id is None:
            super().__init__("another command of the project is in progress")
        else:
            super().__init__(f"another command of the project is in progress: command {command_id}")
        self.command_id = command_id


class Contention(ApplicationError):
    """409, CONCURRENT_CHANGE: lock timeout, deadlock, serialization failure, or an
    acceptance that lost a race twice."""


class StoreUnreachable(ApplicationError):
    """503, DEPENDENCY_UNAVAILABLE (decision 0023 G8a): the database could not be reached; the
    connection failed or was lost. The message consumers hold and requeue a delivery after it and
    count it towards the message's 5 tries, like any failure (decision 0023 G15)."""


class SubdomainTaken(ApplicationError):
    """internal, 500 (never reaches a transport today): the sub-domain an event carries already
    belongs to another project's hosting row (FR-54, TR-53: the store's check under the
    sub-domain's lock; no index holds it). Permanent: retrying cannot help, so the
    owner-status consumer dead-letters the event at once for an operator (Task 12 security review
    S2). The message never names the sub-domain."""


class CorruptRow(ApplicationError):
    """internal, 500: a stored row failed validation on load, or rows that must agree do not."""


class Timeout(ApplicationError):
    """504, REQUEST_TIMEOUT: the deadline passed before a store or service answered."""


class Overloaded(ApplicationError):
    """503, SERVICE_OVERLOADED: the in-flight limit is reached."""


class ReleaseNotBuildable(ApplicationError):
    """422, RELEASE_NOT_BUILDABLE: the release is neither accepted nor building with a pending
    next attempt, so no build can start (decision 0014 section 3, "Build")."""


class BuildInProgress(ApplicationError):
    """409, BUILD_IN_PROGRESS: the release's current build attempt is already running."""


class RedeployNotAllowed(ApplicationError):
    """422, REDEPLOY_NOT_ALLOWED (decision 0023 G10): the project has no live release, its
    hosting is offline, or it has no hosting row, so there is nothing online to redeploy."""


class IdempotencyKeyTooLong(ApplicationError):
    """400, VALIDATION_FAILED with the field error ``TOO_LONG`` on the key (decision 0023 G11):
    an admin's key is stored as ``admin:<key>``, so it may be at most ``MAX_ADMIN_KEY_BYTES``
    bytes for the stored key to keep the 255 byte rule."""


def in_flight_command_id(exc: BaseException | None) -> UUID | None:
    """The command named by an ``OperationInProgress`` in ``exc``'s cause chain, or None for any
    other error, for None, and when the named ID is the nil UUID."""
    for err in error_chain(exc):
        if isinstance(err, OperationInProgress):
            if err.command_id is None or err.command_id.int == 0:
                return None
            return err.command_id
    return None
