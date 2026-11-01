"""The typed application errors (decision 0006 section 3) and the command an ``OperationInProgress``
names."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

import pytest

from provisioning.core.application import (
    ActionDenied,
    ApplicationError,
    BuildInProgress,
    Contention,
    CorruptRow,
    DependencyNotConfigured,
    DependencyUnavailable,
    LimitReached,
    NotFound,
    OperationDisabled,
    OperationInProgress,
    OperationNotAvailable,
    Overloaded,
    ProjectAccessDenied,
    ProjectAccessUnavailable,
    ReleaseNotBuildable,
    Timeout,
    Unauthenticated,
    in_flight_command_id,
)
from provisioning.core.domain import NIL_ID, IdempotencyConflict, is_error

ID_COMMAND = UUID("0190a1b2-0000-7000-8000-0000000000c1")

# The typed application errors of decision 0006 section 3, plus the two errors of the
# prototype's build endpoint.
_CLASSES: tuple[type[ApplicationError], ...] = (
    Unauthenticated,
    ActionDenied,
    ProjectAccessDenied,
    NotFound,
    LimitReached,
    DependencyUnavailable,
    DependencyNotConfigured,
    ProjectAccessUnavailable,
    OperationNotAvailable,
    OperationDisabled,
    OperationInProgress,
    Contention,
    CorruptRow,
    Timeout,
    Overloaded,
    ReleaseNotBuildable,
    BuildInProgress,
)


def _wrapped(cause: BaseException, text: str = "accept deploy") -> RuntimeError:
    """An error raised ``from`` cause, as a caller that adds context would raise it."""
    try:
        raise RuntimeError(text) from cause
    except RuntimeError as exc:
        return exc


# A classifier matches by class along the cause chain, so no class may match another, and each needs
# its own text for the logs.
def test_classes_are_distinct() -> None:
    texts: dict[str, str] = {}
    for cls in _CLASSES:
        doc = (cls.__doc__ or "").strip()
        assert doc, f"{cls.__name__} has no description"
        assert doc not in texts, f"{cls.__name__} and {texts[doc]} share the description"
        texts[doc] = cls.__name__
        for other in _CLASSES:
            if other is not cls:
                assert not issubclass(cls, other), f"{cls.__name__} is a {other.__name__}"


# The "no access" and "missing" cases share one response (decision 0006 section 3), but they are
# different facts for the logs, so neither is the other.
def test_project_access_denied_is_not_not_found() -> None:
    assert not issubclass(ProjectAccessDenied, NotFound)
    assert not issubclass(NotFound, ProjectAccessDenied)
    assert not is_error(ProjectAccessDenied(), NotFound)
    assert not is_error(NotFound(), ProjectAccessDenied)


def test_operation_in_progress_carries_the_command_in_flight() -> None:
    err = OperationInProgress(ID_COMMAND)

    assert err.command_id == ID_COMMAND
    assert in_flight_command_id(err) == ID_COMMAND
    assert str(ID_COMMAND) in str(err), "the message does not name the command in flight"

    wrapped = _wrapped(err)
    assert in_flight_command_id(wrapped) == ID_COMMAND
    assert is_error(wrapped, OperationInProgress)


# Only an OperationInProgress that names a command names one. Other errors and None name none, so a
# transport never puts an unrelated or nil ID into an error body.
# The errors are built inside the test: OperationInProgress's constructor is part of the code under
# test.
@pytest.mark.parametrize(
    "make",
    [
        lambda: None,
        lambda: OperationInProgress(),
        lambda: _wrapped(OperationInProgress(), "x"),
        lambda: Contention(),
        lambda: IdempotencyConflict(),
    ],
    ids=["nil", "bare sentinel", "wrapped bare", "other sentinel", "domain error"],
)
def test_in_flight_command_id_only_from_the_typed_error(
    make: Callable[[], BaseException | None],
) -> None:
    assert in_flight_command_id(make()) is None


def test_operation_in_progress_without_a_command() -> None:
    err = OperationInProgress()
    assert err.command_id is None
    # An error built without arguments still has a text, so a log line has something to show.
    assert str(err).strip() != ""


# An error built with the nil UUID names no command either: the nil UUID is not an identity.
def test_operation_in_progress_with_nil_id_names_no_command() -> None:
    err = OperationInProgress(NIL_ID)
    assert isinstance(err, OperationInProgress)
    assert in_flight_command_id(err) is None
