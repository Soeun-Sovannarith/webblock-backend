"""The typed v1 events (decision 0022 E11): every contract example goes through the JSON schema and
the pydantic model, and the two must agree."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from provisioning.presentation.messaging import (
    CONTENT_TYPE_JSON,
    HOSTING_ENTITLEMENT_ENDED_V1_SCHEMA,
    HOSTING_ENTITLEMENT_RESTORED_V1_SCHEMA,
    OWNER_STATUS_CHANGED_V1_SCHEMA,
    PROJECT_DELETED_V1,
    PROJECT_DELETED_V1_SCHEMA,
    PUBLISH_COMPLETED_V1_SCHEMA,
    SOURCE_PUBLISHED_V1,
    SOURCE_PUBLISHED_V1_SCHEMA,
    EventCodecV1,
    JsonSchema,
    MessageRejected,
    Reason,
)
from provisioning.presentation.messaging.models import (
    EVENT_MODELS,
    HostingEntitlementEndedV1,
    OwnerStatusChangedV1,
    ProjectDeletedV1,
    SourcePublishedV1,
)

SHARED = Path(__file__).resolve().parents[4] / "src" / "contracts" / "shared" / "events"
VALID = sorted((SHARED / "examples" / "valid").glob("*.json"))
INVALID = sorted((SHARED / "examples" / "invalid").glob("*.json"))
SCHEMAS = {
    "project.source_published.v1": SOURCE_PUBLISHED_V1_SCHEMA,
    "project.project_deleted.v1": PROJECT_DELETED_V1_SCHEMA,
    "project.owner_status_changed.v1": OWNER_STATUS_CHANGED_V1_SCHEMA,
    "payment.hosting_entitlement_ended.v1": HOSTING_ENTITLEMENT_ENDED_V1_SCHEMA,
    "payment.hosting_entitlement_restored.v1": HOSTING_ENTITLEMENT_RESTORED_V1_SCHEMA,
    "provisioning.publish_completed.v1": PUBLISH_COMPLETED_V1_SCHEMA,
}


def _model_accepts(path: Path) -> bool:
    doc = json.loads(path.read_text())
    model = EVENT_MODELS.get(doc.get("message_type"))
    if model is None:
        return False
    try:
        model.model_validate_json(path.read_bytes())
    except ValidationError:
        return False
    return True


def _schema_accepts(path: Path) -> bool:
    doc = json.loads(path.read_text())
    schema = SCHEMAS.get(doc.get("message_type"))
    if schema is None:
        return False
    return not JsonSchema(json.loads(schema)).failures(doc)


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_every_valid_example_parses_into_its_model(path: Path) -> None:
    assert _model_accepts(path)


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_every_invalid_example_is_refused_by_its_model(path: Path) -> None:
    assert not _model_accepts(path)


@pytest.mark.parametrize("path", VALID + INVALID, ids=lambda p: p.stem)
def test_model_and_schema_agree_on_every_example(path: Path) -> None:
    assert _model_accepts(path) == _schema_accepts(path)


def _doc(name: str) -> dict[str, object]:
    return json.loads((SHARED / "examples" / "valid" / name).read_text())  # type: ignore[no-any-return]


def _refused(model: type, doc: dict[str, object]) -> bool:
    try:
        model.model_validate_json(json.dumps(doc))  # type: ignore[attr-defined]
    except ValidationError:
        return True
    return False


def test_nil_uuid_refused() -> None:
    doc = _doc("project-deleted.json")
    doc["project_id"] = "00000000-0000-0000-0000-000000000000"
    assert _refused(ProjectDeletedV1, doc)


def test_uppercase_uuid_refused() -> None:
    doc = _doc("project-deleted.json")
    doc["project_id"] = str(doc["project_id"]).upper()
    assert _refused(ProjectDeletedV1, doc)


def test_naive_occurred_at_refused() -> None:
    doc = _doc("project-deleted.json")
    doc["occurred_at"] = "2026-10-24T09:30:00"
    assert _refused(ProjectDeletedV1, doc)


def test_extra_field_refused() -> None:
    doc = _doc("project-deleted.json")
    doc["extra"] = 1
    assert _refused(ProjectDeletedV1, doc)


def test_status_version_above_2_pow_53_minus_1_refused() -> None:
    doc = _doc("owner-status-changed.json")
    doc["status_version"] = 2**53
    assert _refused(OwnerStatusChangedV1, doc)
    doc["status_version"] = 2**53 - 1
    assert not _refused(OwnerStatusChangedV1, doc)


def test_bool_is_not_an_int() -> None:
    doc = _doc("owner-status-changed.json")
    doc["status_version"] = True
    assert _refused(OwnerStatusChangedV1, doc)
    doc = _doc("source-published.json")
    doc["repository_id"] = True
    assert _refused(SourcePublishedV1, doc)


def test_a_model_is_frozen() -> None:
    event = ProjectDeletedV1.model_validate_json(
        (SHARED / "examples" / "valid" / "project-deleted.json").read_bytes()
    )
    with pytest.raises(ValidationError):
        event.correlation_id = "x"


def test_validation_error_text_never_contains_the_input() -> None:
    secret = "SECRET-" + "token-value-9f8e7d"
    doc = _doc("source-published.json")
    doc["git_commit_sha"] = secret
    doc["correlation_id"] = secret + " bad"
    doc["repository_id"] = secret
    body = json.dumps(doc).encode()
    with pytest.raises(MessageRejected) as info:
        EventCodecV1(max_body_bytes=4096).decode_source_published(
            body=body,
            content_type=CONTENT_TYPE_JSON,
            message_type=SOURCE_PUBLISHED_V1,
            message_id=str(doc["message_id"]),
        )
    err = info.value
    assert err.reason is Reason.SCHEMA_INVALID
    assert "SECRET" not in str(err) + repr(err.schema_locations)
    assert "/git_commit_sha" in err.schema_locations
    assert "/repository_id" in err.schema_locations


def test_models_cover_every_event_type() -> None:
    assert set(EVENT_MODELS) == set(SCHEMAS)
    assert EVENT_MODELS[PROJECT_DELETED_V1] is ProjectDeletedV1


@pytest.mark.parametrize(
    ("name", "model", "field", "bad"),
    [
        ("project-deleted.json", ProjectDeletedV1, "schema_version", True),
        ("project-deleted.json", ProjectDeletedV1, "schema_version", 1.0),
        ("project-deleted.json", ProjectDeletedV1, "schema_version", 2),
        ("project-deleted.json", ProjectDeletedV1, "schema_version", "1"),
        ("project-deleted.json", ProjectDeletedV1, "message_type", True),
        ("project-deleted.json", ProjectDeletedV1, "message_type", 1),
        ("owner-status-changed.json", OwnerStatusChangedV1, "status", True),
        ("owner-status-changed.json", OwnerStatusChangedV1, "status", 1),
        ("hosting-entitlement-ended.json", HostingEntitlementEndedV1, "reason", True),
        ("hosting-entitlement-ended.json", HostingEntitlementEndedV1, "reason", 0),
    ],
)
def test_literal_fields_take_no_bool_or_number(
    name: str, model: type, field: str, bad: object
) -> None:
    doc = _doc(name)
    doc[field] = bad
    assert _refused(model, doc)


@pytest.mark.parametrize("bad", [True, 1.0, 2])
def test_codec_refuses_a_bad_schema_version(bad: object) -> None:
    doc = _doc("project-deleted.json")
    doc["schema_version"] = bad
    with pytest.raises(MessageRejected) as info:
        EventCodecV1(max_body_bytes=4096).decode_project_deleted(
            body=json.dumps(doc).encode(),
            content_type=CONTENT_TYPE_JSON,
            message_type=PROJECT_DELETED_V1,
            message_id=str(doc["message_id"]),
        )
    assert info.value.reason is Reason.SCHEMA_INVALID
