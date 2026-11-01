"""Typed domain errors: one class per rule a domain check refuses.

Callers match them with ``isinstance`` on the exception or on any exception of its ``__cause__``
chain (``is_error``). Messages carry statuses, kinds and IDs only, never request content. Names
follow revision 19 (decision 0012 section 3): command number, request fingerprint and
``log_ref``.
"""

from __future__ import annotations

from collections.abc import Iterator


class DomainError(Exception):
    """Base class of every domain error."""


class IllegalTransition(DomainError):
    """illegal transition"""


class IdempotencyConflict(DomainError):
    """idempotency key reused with a different request"""


class MissingImageDigest(DomainError):
    """image digest required"""


class InvalidImageDigest(DomainError):
    """invalid image digest"""


class MissingFailureCode(DomainError):
    """failure code required"""


class ImmutableField(DomainError):
    """immutable field already recorded"""


class ProjectMismatch(DomainError):
    """project mismatch"""


class InvalidIdempotencyKey(DomainError):
    """invalid idempotency key"""


class CommandNumberOverflow(DomainError):
    """command number overflow"""


class InvalidCommandNumber(DomainError):
    """invalid command number"""


class InvalidId(DomainError):
    """invalid id"""


class UnknownEnumValue(DomainError):
    """unknown enum value"""


class InvalidAttempt(DomainError):
    """invalid step attempt"""


class InvalidFailureCode(DomainError):
    """invalid failure code"""


class InvalidFailureSummary(DomainError):
    """invalid failure summary"""


class StaleFence(DomainError):
    """stale command number: the command is not the project's last accepted command"""


class RouteNotStable(DomainError):
    """route is not stable"""


class HostingOffline(DomainError):
    """hosting is offline"""


class ReleaseOutdated(DomainError):
    """release was created before the live release"""


class OwnerStatusOutdated(DomainError):
    """a user_suspended hosting command names an owner status version other than the row's"""


class InvalidCommandTarget(DomainError):
    """invalid command target"""


class ResultMismatch(DomainError):
    """result does not match the release"""


class InvalidCommitSha(DomainError):
    """invalid commit sha"""


class InvalidRepositoryId(DomainError):
    """invalid repository id"""


class InvalidSubdomain(DomainError):
    """invalid sub-domain: not a DNS label of 1 to 63 characters"""


class InvalidRequestFingerprint(DomainError):
    """invalid request fingerprint"""


class InvalidRequester(DomainError):
    """invalid requester"""


class MissingDeadline(DomainError):
    """step deadline required"""


class InvalidLogRef(DomainError):
    """invalid log reference"""


def error_chain(exc: BaseException | None) -> Iterator[BaseException]:
    """Yield ``exc`` and then each explicit cause (``raise ... from``), never the implicit
    ``__context__``: an exception raised while handling another one does not take its class."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__


def is_error(exc: BaseException | None, cls: type[BaseException]) -> bool:
    """Report whether ``exc`` or an exception in its cause chain is a ``cls``."""
    return any(isinstance(e, cls) for e in error_chain(exc))
