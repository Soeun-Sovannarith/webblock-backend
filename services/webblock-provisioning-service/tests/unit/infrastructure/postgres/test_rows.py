"""The row mappers judge a stored value by its type, never by its text: a column value of the
wrong type is ``CorruptRow`` (TR-12), not coerced with ``str()`` into a value that may pass."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import RowMapping
from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData

from provisioning.core.application import CorruptRow
from provisioning.core.domain import (
    CommandKind,
    CommandRequest,
    CommandStatus,
    OfflineReason,
    compute_request_fingerprint,
)
from provisioning.infrastructure.postgres.rows import (
    command_of,
    hosting_intent_of,
    hosting_of,
    source_event_outcome_of,
)

PROJECT = UUID("0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11")
COMMAND = UUID("c4a5d674-2f90-4d9e-8c43-7e5f1a2b3c04")
REQUEST = UUID("5f0a3c9e-2b1d-4c6e-8f7a-9d0e1b2c3d4e")
AT = datetime(2026, 10, 8, 9, 30, tzinfo=UTC)


def row(values: dict[str, object]) -> RowMapping:
    """A real SQLAlchemy row mapping of ``values``, built without a database."""
    result: IteratorResult[tuple[object, ...]] = IteratorResult(
        SimpleResultMetaData(list(values)), iter([tuple(values.values())])
    )
    return result.mappings().one()


def command_row(**changes: object) -> RowMapping:
    values: dict[str, object] = {
        "command_id": COMMAND,
        "project_id": PROJECT,
        "release_id": None,
        "kind": "suspend_hosting",
        "idempotency_key": "key-1",
        "request_fingerprint": compute_request_fingerprint(
            CommandRequest(
                project_id=PROJECT,
                kind=CommandKind.SUSPEND_HOSTING,
                release_id=None,
                idempotency_key="key-1",
            )
        ),
        "command_number": 3,
        "status": "accepted",
        "created_at": AT,
        "updated_at": AT,
        "failure_code": None,
        "requested_by_subject": None,
        "hosting_reason": "billing",
    }
    return row(values | changes)


def hosting_row(**changes: object) -> RowMapping:
    values: dict[str, object] = {
        "project_id": PROJECT,
        "last_command_number": 3,
        "routing_status": "stable",
        "hosting_status": "offline",
        "updated_at": AT,
        "live_release_id": None,
        "offline_reasons": ["billing"],
        "owner_status_version": 1,
        "subdomain": "shop",
    }
    return row(values | changes)


def source_event_row(**changes: object) -> RowMapping:
    values: dict[str, object] = {
        "project_id": PROJECT,
        "generation_request_id": REQUEST,
        "status": "rejected",
        "outcome_code": "REPOSITORY_MISMATCH",
        "release_id": None,
        "attempts": 1,
        "mismatched_repeat_count": 0,
        "received_at": AT,
        "finished_at": AT,
    }
    return row(values | changes)


def intent_row(**changes: object) -> RowMapping:
    values: dict[str, object] = {
        "kind": "suspend_hosting",
        "idempotency_key": "key-1",
        "status": "succeeded",
        "hosting_reason": "billing",
        "offline_reasons": ["billing"],
        "owner_status_version": 1,
    }
    return row(values | changes)


def test_tr12_valid_rows_map() -> None:
    """TR-12: the rows the corrupt cases start from are valid."""
    assert command_of(command_row()).kind is CommandKind.SUSPEND_HOSTING
    assert hosting_of(hosting_row()).offline_reasons == frozenset({OfflineReason.BILLING})
    assert source_event_outcome_of(source_event_row()).outcome_code == "REPOSITORY_MISMATCH"
    intent = hosting_intent_of(intent_row(), PROJECT)
    assert intent.status is CommandStatus.SUCCEEDED
    assert intent.hosting_reason is OfflineReason.BILLING


@pytest.mark.parametrize("change", [{"kind": 5}, {"hosting_reason": 5}])
def test_tr12_a_command_row_with_a_non_string_enum_is_corrupt(change: dict[str, object]) -> None:
    """TR-12: an integer kind or hosting reason in a command row is a corrupt row."""
    with pytest.raises(CorruptRow):
        command_of(command_row(**change))


@pytest.mark.parametrize("reasons", [[5], [OfflineReason.BILLING.value, 5]])
def test_tr12_a_hosting_row_with_a_non_string_reason_is_corrupt(reasons: list[object]) -> None:
    """TR-12: an offline reason that is no string in a hosting row is a corrupt row."""
    with pytest.raises(CorruptRow):
        hosting_of(hosting_row(offline_reasons=reasons))


@pytest.mark.parametrize("change", [{"outcome_code": 5}, {"status": 5}])
def test_tr12_a_source_event_row_with_a_non_string_text_is_corrupt(
    change: dict[str, object],
) -> None:
    """TR-12: an integer outcome code or status in a source event row is a corrupt row, never
    the text ``"5"``."""
    with pytest.raises(CorruptRow):
        source_event_outcome_of(source_event_row(**change))


@pytest.mark.parametrize(
    "change",
    [
        {"hosting_reason": None},
        {"hosting_reason": "no-such-reason"},
        {"hosting_reason": 5},
        {"offline_reasons": ["no-such-reason"]},
        {"kind": "no-such-kind"},
        {"status": "no-such-status"},
    ],
)
def test_tr12_a_hosting_intent_row_with_a_bad_value_is_corrupt(change: dict[str, object]) -> None:
    """TR-12: a hosting intent row with no reason, or an offline reason, kind or status that is not
    valid, is a corrupt row, as in the other row mappers, never ``UnknownEnumValue``."""
    with pytest.raises(CorruptRow):
        hosting_intent_of(intent_row(**change), PROJECT)
