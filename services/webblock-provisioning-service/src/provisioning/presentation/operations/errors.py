"""Error classification (decision 0006 section 3, amended by decision 0023 G5, G6, G8a and G14).

``classify`` checks an ordered table against the error and each explicit cause (``raise ... from``,
never the implicit ``__context__``): the first row whose class matches anything in the chain wins.
Order matters where one error is raised from another: application errors come before domain errors
(a corrupt row may come from a domain parse error; project access unavailable from a dependency
error or a timeout), and the built-in timeout comes last.

Codes are one UPPER_SNAKE enum (``Code``) on Notion's status table (the audit's table C.1.3 plus
``REDEPLOY_NOT_ALLOWED`` and ``RESOURCE_NOT_FOUND``). Messages are fixed text per code; they never
carry request content, provider text or anything that depends on which project, release or command
was named, so the concealment 404s are byte-identical. A field error names the input and one of
four fixed codes, never the value.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from provisioning.core.application import (
    ActionDenied,
    BuildInProgress,
    ClaimLost,
    Contention,
    CorruptRow,
    DependencyNotConfigured,
    DependencyUnavailable,
    IdempotencyKeyTooLong,
    InvalidCorrelationId,
    LimitReached,
    NotFound,
    OperationDisabled,
    OperationInProgress,
    OperationNotAvailable,
    Overloaded,
    PermanentFailure,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
    RedeployNotAllowed,
    ReleaseNotBuildable,
    RetryLater,
    StoreUnreachable,
    SubdomainTaken,
    Timeout,
    Unauthenticated,
)
from provisioning.core.application.ports.build_system import (
    BuildSystemRejected,
    BuildSystemUnavailable,
)
from provisioning.core.application.ports.site_cluster import (
    ApplyConflict,
    ClusterRefused,
    ClusterUnavailable,
    FenceRejected,
    SiteDependencyUnavailable,
    TenantNotReady,
)
from provisioning.core.domain import (
    CommandNumberOverflow,
    HostingOffline,
    IdempotencyConflict,
    IllegalTransition,
    ImmutableField,
    InvalidAttempt,
    InvalidCommandNumber,
    InvalidCommandTarget,
    InvalidCommitSha,
    InvalidFailureCode,
    InvalidFailureSummary,
    InvalidId,
    InvalidIdempotencyKey,
    InvalidImageDigest,
    InvalidLogRef,
    InvalidRepositoryId,
    InvalidRequester,
    InvalidRequestFingerprint,
    InvalidSubdomain,
    MissingDeadline,
    MissingFailureCode,
    MissingImageDigest,
    OwnerStatusOutdated,
    ProjectMismatch,
    ReleaseOutdated,
    ResultMismatch,
    RouteNotStable,
    StaleFence,
    UnknownEnumValue,
    error_chain,
)


class Code(StrEnum):
    """Every error code this API answers (decision 0023 G6, G10), in the order of the status
    table."""

    VALIDATION_FAILED = "VALIDATION_FAILED"
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    ACCESS_DENIED = "ACCESS_DENIED"
    PROJECT_NOT_FOUND = "PROJECT_NOT_FOUND"
    RELEASE_NOT_FOUND = "RELEASE_NOT_FOUND"
    COMMAND_NOT_FOUND = "COMMAND_NOT_FOUND"
    DEPLOYMENT_NOT_FOUND = "DEPLOYMENT_NOT_FOUND"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    COMMAND_IN_PROGRESS = "COMMAND_IN_PROGRESS"
    BUILD_IN_PROGRESS = "BUILD_IN_PROGRESS"
    CONCURRENT_CHANGE = "CONCURRENT_CHANGE"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    ROUTE_NOT_STABLE = "ROUTE_NOT_STABLE"
    HOSTING_OFFLINE = "HOSTING_OFFLINE"
    RELEASE_STATE_INVALID = "RELEASE_STATE_INVALID"
    RELEASE_OUTDATED = "RELEASE_OUTDATED"
    RELEASE_NOT_BUILDABLE = "RELEASE_NOT_BUILDABLE"
    COMMAND_NUMBER_EXHAUSTED = "COMMAND_NUMBER_EXHAUSTED"
    PUBLISH_LIMIT_REACHED = "PUBLISH_LIMIT_REACHED"
    REDEPLOY_NOT_ALLOWED = "REDEPLOY_NOT_ALLOWED"
    SERVICE_OVERLOADED = "SERVICE_OVERLOADED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    PROJECT_ACCESS_UNAVAILABLE = "PROJECT_ACCESS_UNAVAILABLE"
    IDENTITY_UNAVAILABLE = "IDENTITY_UNAVAILABLE"
    OPERATION_UNAVAILABLE = "OPERATION_UNAVAILABLE"
    REQUEST_TIMEOUT = "REQUEST_TIMEOUT"
    DEPENDENCY_TIMEOUT = "DEPENDENCY_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


_STATUS: Final[Mapping[Code, int]] = MappingProxyType(
    {
        Code.VALIDATION_FAILED: 400,
        Code.REQUEST_TOO_LARGE: 413,
        Code.UNAUTHENTICATED: 401,
        Code.ACCESS_DENIED: 403,
        Code.PROJECT_NOT_FOUND: 404,
        Code.RELEASE_NOT_FOUND: 404,
        Code.COMMAND_NOT_FOUND: 404,
        Code.DEPLOYMENT_NOT_FOUND: 404,
        Code.RESOURCE_NOT_FOUND: 404,
        Code.COMMAND_IN_PROGRESS: 409,
        Code.BUILD_IN_PROGRESS: 409,
        Code.CONCURRENT_CHANGE: 409,
        Code.IDEMPOTENCY_KEY_REUSED: 422,
        Code.ROUTE_NOT_STABLE: 422,
        Code.HOSTING_OFFLINE: 422,
        Code.RELEASE_STATE_INVALID: 422,
        Code.RELEASE_OUTDATED: 422,
        Code.RELEASE_NOT_BUILDABLE: 422,
        Code.COMMAND_NUMBER_EXHAUSTED: 422,
        Code.PUBLISH_LIMIT_REACHED: 422,
        Code.REDEPLOY_NOT_ALLOWED: 422,
        Code.SERVICE_OVERLOADED: 503,
        Code.DEPENDENCY_UNAVAILABLE: 503,
        Code.PROJECT_ACCESS_UNAVAILABLE: 503,
        Code.IDENTITY_UNAVAILABLE: 503,
        Code.OPERATION_UNAVAILABLE: 503,
        Code.REQUEST_TIMEOUT: 504,
        Code.DEPENDENCY_TIMEOUT: 504,
        Code.INTERNAL_ERROR: 500,
    }
)


def status_of(code: Code) -> int:
    """The HTTP status of a code (Notion 2.3; the same number on MCP)."""
    return _STATUS[code]


_MESSAGES: Final[Mapping[str, str]] = MappingProxyType(
    {
        Code.VALIDATION_FAILED: "The request is invalid.",
        Code.REQUEST_TOO_LARGE: "The request body is larger than the limit for the operation.",
        Code.UNAUTHENTICATED: "Authentication is missing or invalid.",
        Code.ACCESS_DENIED: "The token does not permit this action.",
        Code.PROJECT_NOT_FOUND: "The project was not found.",
        Code.RELEASE_NOT_FOUND: "The release was not found in the project.",
        Code.COMMAND_NOT_FOUND: "The command was not found in the project.",
        Code.DEPLOYMENT_NOT_FOUND: "The project has no deployment.",
        Code.RESOURCE_NOT_FOUND: "No resource is at this path.",
        Code.COMMAND_IN_PROGRESS: "Another command of the project is in progress.",
        Code.BUILD_IN_PROGRESS: "A build attempt of the release is running.",
        Code.CONCURRENT_CHANGE: "The request met a concurrent change; retry later.",
        Code.IDEMPOTENCY_KEY_REUSED: "The idempotency key was used with a different request.",
        Code.ROUTE_NOT_STABLE: "The route is changing or uncertain.",
        Code.HOSTING_OFFLINE: "Hosting is offline.",
        Code.RELEASE_STATE_INVALID: (
            "The release or route is in the wrong state for this operation."
        ),
        Code.RELEASE_OUTDATED: "The release was created before the live release.",
        Code.RELEASE_NOT_BUILDABLE: "The release has no build attempt that can start.",
        Code.COMMAND_NUMBER_EXHAUSTED: "The project's command number has reached its maximum.",
        Code.PUBLISH_LIMIT_REACHED: "A limit of the plan was reached.",
        Code.REDEPLOY_NOT_ALLOWED: "The site has no live release online to redeploy.",
        Code.SERVICE_OVERLOADED: "The service is at its in-flight limit; retry later.",
        Code.DEPENDENCY_UNAVAILABLE: "A required dependency is unavailable.",
        Code.PROJECT_ACCESS_UNAVAILABLE: "Project access cannot be checked now.",
        Code.IDENTITY_UNAVAILABLE: "Identity keys cannot be fetched now.",
        Code.OPERATION_UNAVAILABLE: "The operation is not available.",
        Code.REQUEST_TIMEOUT: "The request timed out.",
        Code.DEPENDENCY_TIMEOUT: "A required dependency did not answer in time.",
        Code.INTERNAL_ERROR: "An unexpected failure occurred.",
    }
)


def message(code: str) -> str:
    """The fixed message of a code; the internal message for an unknown code."""
    return _MESSAGES.get(code, _MESSAGES[Code.INTERNAL_ERROR])


class FieldCode(StrEnum):
    """What is wrong with one input (Notion 2.2): one fixed list."""

    REQUIRED = "REQUIRED"
    INVALID_FORMAT = "INVALID_FORMAT"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    TOO_LONG = "TOO_LONG"


_FIELD_MESSAGES: Final[Mapping[FieldCode, str]] = MappingProxyType(
    {
        FieldCode.REQUIRED: "The value is required.",
        FieldCode.INVALID_FORMAT: "The value does not have the expected format.",
        FieldCode.OUT_OF_RANGE: "The value is outside the allowed range.",
        FieldCode.TOO_LONG: "The value is longer than allowed.",
    }
)


def field_message(code: FieldCode) -> str:
    """The fixed message of a field code."""
    return _FIELD_MESSAGES[code]


@dataclass(frozen=True, slots=True)
class FieldError:
    """One bad input of a 400: the camelCase input name the client sent it under (``projectId``,
    ``Idempotency-Key``, ``body``, ``query``) and what is wrong. The message comes from a fixed
    table by code; the value is never kept."""

    field: str
    code: FieldCode

    def __post_init__(self) -> None:
        if not isinstance(self.code, FieldCode):
            raise TypeError("ops: a field error's code is a FieldCode")
        if not self.field:
            raise ValueError("ops: a field error names its field")

    def as_json(self) -> dict[str, str]:
        return {"field": self.field, "code": self.code.value, "message": field_message(self.code)}


class TransportError(Exception):
    """Base of the errors the transports add to those of the core."""


class MalformedRequest(TransportError):
    """400, VALIDATION_FAILED: an invalid input, with the fields it names in ``errors`` (empty
    when the problem is not one input's: a wrong content type, a repeated header other than
    Idempotency-Key, an Origin or Host outside the MCP allow-lists, a JSON-RPC batch)."""

    def __init__(self, *args: object, errors: tuple[FieldError, ...] = ()) -> None:
        super().__init__(*args)
        self.errors = errors


class RequestTooLarge(TransportError):
    """413, REQUEST_TOO_LARGE: the body is over the operation's limit."""


class IdentityUnavailable(TransportError):
    """503, IDENTITY_UNAVAILABLE: the token could not be verified."""


class UnknownRoute(TransportError):
    """404, RESOURCE_NOT_FOUND: no route at this path, or not with this method. It names no
    project, so it reveals nothing."""


def field_errors(exc: BaseException, *, key_field: str) -> tuple[FieldError, ...]:
    """The field errors of the first ``MalformedRequest`` in ``exc``'s cause chain; for an admin
    key that is too long (``IdempotencyKeyTooLong``, decision 0023 G11), ``TOO_LONG`` on
    ``key_field``; else none. ``key_field`` is the name the caller's transport gives the key
    (REST's ``Idempotency-Key`` header, MCP's ``idempotencyKey`` argument) and has no default, so
    neither transport can fall into the other's name."""
    for err in error_chain(exc):
        if isinstance(err, MalformedRequest):
            return err.errors
        if isinstance(err, IdempotencyKeyTooLong):
            return (FieldError(key_field, FieldCode.TOO_LONG),)
    return ()


@dataclass(frozen=True, slots=True)
class ErrorClass:
    """One row: an error class, its code and that code's status."""

    error: type[BaseException]
    code: Code

    @property
    def status(self) -> int:
        return status_of(self.code)


def _internal(error: type[BaseException]) -> ErrorClass:
    return ErrorClass(error, Code.INTERNAL_ERROR)


CLASSES: Final[tuple[ErrorClass, ...]] = (
    # Transport errors.
    ErrorClass(MalformedRequest, Code.VALIDATION_FAILED),
    ErrorClass(RequestTooLarge, Code.REQUEST_TOO_LARGE),
    ErrorClass(IdentityUnavailable, Code.IDENTITY_UNAVAILABLE),
    ErrorClass(UnknownRoute, Code.RESOURCE_NOT_FOUND),
    # Application errors.
    ErrorClass(Unauthenticated, Code.UNAUTHENTICATED),
    ErrorClass(ActionDenied, Code.ACCESS_DENIED),
    ErrorClass(ProjectAccessDenied, Code.PROJECT_NOT_FOUND),
    # The operation layer names the release, command or deployment once access passed (G6).
    ErrorClass(NotFound, Code.PROJECT_NOT_FOUND),
    _internal(CorruptRow),
    # A lost database is an unavailable dependency (decision 0023 G8a).
    ErrorClass(StoreUnreachable, Code.DEPENDENCY_UNAVAILABLE),
    # Worker-internal errors never reach a caller; if one did, it is internal (0006 section 3).
    _internal(ClaimLost),
    _internal(InvalidCorrelationId),
    _internal(PermanentFailure),
    _internal(RetryLater),
    _internal(SubdomainTaken),
    _internal(ClusterUnavailable),
    _internal(TenantNotReady),
    _internal(ApplyConflict),
    _internal(FenceRejected),
    _internal(ClusterRefused),
    _internal(SiteDependencyUnavailable),  # the promotion probe's; a failed run, never a reply
    # StartBuild turns the build system's errors into a dispatch result; one reaching a caller is
    # a defect.
    _internal(BuildSystemUnavailable),
    _internal(BuildSystemRejected),
    ErrorClass(ProjectAccessUnavailable, Code.PROJECT_ACCESS_UNAVAILABLE),
    ErrorClass(OperationNotAvailable, Code.OPERATION_UNAVAILABLE),
    ErrorClass(OperationDisabled, Code.OPERATION_UNAVAILABLE),
    ErrorClass(OperationInProgress, Code.COMMAND_IN_PROGRESS),
    ErrorClass(ReleaseNotBuildable, Code.RELEASE_NOT_BUILDABLE),
    ErrorClass(BuildInProgress, Code.BUILD_IN_PROGRESS),
    ErrorClass(LimitReached, Code.PUBLISH_LIMIT_REACHED),
    ErrorClass(RedeployNotAllowed, Code.REDEPLOY_NOT_ALLOWED),
    ErrorClass(IdempotencyKeyTooLong, Code.VALIDATION_FAILED),
    ErrorClass(Contention, Code.CONCURRENT_CHANGE),
    ErrorClass(Timeout, Code.REQUEST_TIMEOUT),
    ErrorClass(Overloaded, Code.SERVICE_OVERLOADED),
    ErrorClass(DependencyUnavailable, Code.DEPENDENCY_UNAVAILABLE),
    ErrorClass(DependencyNotConfigured, Code.DEPENDENCY_UNAVAILABLE),
    # Domain errors a caller's input or the stored state can cause.
    ErrorClass(InvalidId, Code.VALIDATION_FAILED),
    ErrorClass(InvalidIdempotencyKey, Code.VALIDATION_FAILED),
    # The principal's subject is stored as the requester; one that fails the requester rule is an
    # invalid token (decision 0006 section 5), whichever layer notices it.
    ErrorClass(InvalidRequester, Code.UNAUTHENTICATED),
    ErrorClass(IdempotencyConflict, Code.IDEMPOTENCY_KEY_REUSED),
    ErrorClass(RouteNotStable, Code.ROUTE_NOT_STABLE),
    ErrorClass(HostingOffline, Code.HOSTING_OFFLINE),
    ErrorClass(IllegalTransition, Code.RELEASE_STATE_INVALID),
    ErrorClass(ReleaseOutdated, Code.RELEASE_OUTDATED),
    ErrorClass(CommandNumberOverflow, Code.COMMAND_NUMBER_EXHAUSTED),
    # Domain errors no caller input can cause: the transports parse every identifier, and nothing
    # else a caller sends reaches these checks.
    _internal(MissingImageDigest),
    _internal(InvalidImageDigest),
    _internal(MissingFailureCode),
    _internal(ImmutableField),
    _internal(ProjectMismatch),
    _internal(InvalidCommandNumber),
    _internal(UnknownEnumValue),
    _internal(InvalidAttempt),
    _internal(InvalidFailureCode),
    _internal(InvalidFailureSummary),
    # Only the hosting intake sends owner-status keys; an outdated one is skipped there.
    _internal(OwnerStatusOutdated),
    _internal(StaleFence),
    _internal(InvalidCommandTarget),
    _internal(ResultMismatch),
    _internal(InvalidCommitSha),
    _internal(InvalidRepositoryId),
    # A sub-domain comes from the repository lookup or Project's event, never from a caller.
    _internal(InvalidSubdomain),
    _internal(InvalidRequestFingerprint),
    _internal(MissingDeadline),
    _internal(InvalidLogRef),
    # The request timeout applied by the operation layer (asyncio.timeout raises TimeoutError).
    ErrorClass(TimeoutError, Code.REQUEST_TIMEOUT),
)

INTERNAL: Final = _internal(BaseException)


def classify(exc: BaseException) -> tuple[ErrorClass, bool]:
    """The first row whose class matches ``exc`` or one of its explicit causes, and True; the
    internal class and False when no row matches (anything else, including bugs)."""
    chain = tuple(error_chain(exc))
    for row in CLASSES:
        if any(isinstance(err, row.error) for err in chain):
            return row, True
    return INTERNAL, False


_UPPER_SNAKE: Final = re.compile(r"[A-Z][A-Z0-9_]{0,99}", re.ASCII)
_CONCEALED_SUFFIX: Final = "NOT_FOUND"
"""An upstream reason ending so (``PROJECT_NOT_FOUND``, ``RELEASE_NOT_FOUND``,
``COMMAND_NOT_FOUND``, ``DEPLOYMENT_NOT_FOUND``, Project's own ``NOT_FOUND``) would say whether a
project exists: it is never passed through (G6 wins over G14)."""
_CODE_VALUES: Final = frozenset(Code)


def _passable(reason: str | None) -> str | None:
    """``reason`` when it may be a code: UPPER_SNAKE and not ending in ``NOT_FOUND``."""
    if reason is None or not _UPPER_SNAKE.fullmatch(reason):
        return None
    return None if reason.endswith(_CONCEALED_SUFFIX) else reason


def _everything(_status: int, _code: str) -> bool:
    return True


def status_and_code(
    row: ErrorClass, exc: BaseException, answers: Callable[[int, str], bool] = _everything
) -> tuple[int, str]:
    """The status and code of an error ``classify`` put in ``row`` (decision 0023 G14 and the
    lead's precision of the Task 2 review).

    A project access failure (``ProjectAccessUnavailable``) never passes an upstream reason or a
    deadline on: it is always 503 ``PROJECT_ACCESS_UNAVAILABLE``, so an access outage looks the
    same for every project. For ``DependencyUnavailable`` the first one in the cause chain that
    knows more decides, its reason kept only when UPPER_SNAKE and not ending in ``NOT_FOUND``:

    - a reason that is one of our codes takes that code's status from our table;
    - a passed deadline is 504, with the reason as the code, else ``DEPENDENCY_TIMEOUT``;
    - any other reason is 503 with the reason as the code.

    When ``answers`` (the operation's ``Operation.answers``: a declared status, and on a read only
    ``CONCURRENT_CHANGE`` at 409) refuses the status and code, the answer falls back to our own
    code: 504 ``DEPENDENCY_TIMEOUT`` for a deadline, else 503
    ``DEPENDENCY_UNAVAILABLE``, never a remap to internal."""
    if row.error is not DependencyUnavailable:
        return row.status, row.code
    for err in error_chain(exc):
        if not isinstance(err, DependencyUnavailable):
            continue
        reason = _passable(err.upstream_reason)
        if reason is None and not err.deadline:
            continue
        if reason is not None and reason in _CODE_VALUES:
            status, code = status_of(Code(reason)), reason
        elif err.deadline:
            status, code = status_of(Code.DEPENDENCY_TIMEOUT), reason or Code.DEPENDENCY_TIMEOUT
        else:
            status, code = row.status, str(reason)
        if answers(status, code):
            return status, code
        own = Code.DEPENDENCY_TIMEOUT if err.deadline else Code.DEPENDENCY_UNAVAILABLE
        return status_of(own), own
    return row.status, row.code
