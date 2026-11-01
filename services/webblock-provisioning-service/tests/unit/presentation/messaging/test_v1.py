"""The v1 event codec (decision 0022 E4 and E5): the pinned schemas and examples of
src/contracts/shared/events, the AMQP envelope checks and the encoders' round trips."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from provisioning.core.application.hosting.ports import OwnerStatus
from provisioning.core.domain import CommandKind, DomainError, SourcePublishedFields
from provisioning.presentation.messaging import (
    CONTENT_TYPE_JSON,
    ENVELOPE_V1_SCHEMA,
    HOSTING_ENTITLEMENT_ENDED_V1,
    HOSTING_ENTITLEMENT_ENDED_V1_SCHEMA,
    HOSTING_ENTITLEMENT_RESTORED_V1,
    HOSTING_ENTITLEMENT_RESTORED_V1_SCHEMA,
    OWNER_STATUS_CHANGED_V1,
    OWNER_STATUS_CHANGED_V1_SCHEMA,
    PROJECT_DELETED_V1,
    PROJECT_DELETED_V1_SCHEMA,
    PUBLISH_COMPLETED_V1,
    PUBLISH_COMPLETED_V1_SCHEMA,
    SOURCE_PUBLISHED_V1,
    SOURCE_PUBLISHED_V1_SCHEMA,
    EventCodecV1,
    HostingEvent,
    MessageRejected,
    OwnerStatusChanged,
    ProjectDeleted,
    PublishCompleted,
    Reason,
    v1,
)

SHARED = Path(__file__).resolve().parents[4] / "src" / "contracts" / "shared" / "events"
VALID = sorted((SHARED / "examples" / "valid").glob("*.json"))
INVALID = sorted((SHARED / "examples" / "invalid").glob("*.json"))

MID = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1eff")
OTHER_MID = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e00")
PID = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01")
REQ = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e02")
REL = UUID("0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e03")
SHA = "3f9a1c7e5b2d4f6a8c0e1b3d5f7a9c1e3b5d7f9a"
AT = datetime(2026, 10, 24, 9, 30, tzinfo=UTC)


@pytest.fixture
def codec() -> EventCodecV1:
    return EventCodecV1(max_body_bytes=4096)


_FROM_BODY = "from-body"


def decode(
    codec: EventCodecV1, body: bytes, message_type: str, message_id: str | None = _FROM_BODY
) -> object:
    """Decode as the consumer does; the AMQP message ID is the body's unless one is given."""
    if message_id == _FROM_BODY:
        message_id = str(json.loads(body)["message_id"])
    name = {
        SOURCE_PUBLISHED_V1: "source_published",
        PROJECT_DELETED_V1: "project_deleted",
        OWNER_STATUS_CHANGED_V1: "owner_status_changed",
        HOSTING_ENTITLEMENT_ENDED_V1: "hosting_event",
        HOSTING_ENTITLEMENT_RESTORED_V1: "hosting_event",
        PUBLISH_COMPLETED_V1: "publish_completed",
    }[message_type]
    return getattr(codec, f"decode_{name}")(
        body=body,
        content_type=CONTENT_TYPE_JSON,
        message_type=message_type,
        message_id=message_id,
    )


@pytest.mark.parametrize(
    ("embedded", "relative"),
    [
        (ENVELOPE_V1_SCHEMA, "envelope.v1.schema.json"),
        (SOURCE_PUBLISHED_V1_SCHEMA, "project/source_published.v1.schema.json"),
        (PROJECT_DELETED_V1_SCHEMA, "project/project_deleted.v1.schema.json"),
        (OWNER_STATUS_CHANGED_V1_SCHEMA, "project/owner_status_changed.v1.schema.json"),
        (HOSTING_ENTITLEMENT_ENDED_V1_SCHEMA, "payment/hosting_entitlement_ended.v1.schema.json"),
        (
            HOSTING_ENTITLEMENT_RESTORED_V1_SCHEMA,
            "payment/hosting_entitlement_restored.v1.schema.json",
        ),
        (PUBLISH_COMPLETED_V1_SCHEMA, "provisioning/publish_completed.v1.schema.json"),
    ],
)
def test_the_embedded_v1_schemas_are_the_contract_files(embedded: str, relative: str) -> None:
    assert embedded == (SHARED / relative).read_text()


def test_source_published_round_trip(codec: EventCodecV1) -> None:
    msg = codec.encode_source_published(
        message_id=MID,
        project_id=PID,
        generation_request_id=REQ,
        repository_id=42,
        git_commit_sha=SHA,
        subdomain="sunny-bakery",
        occurred_at=AT,
    )
    assert msg.correlation_id == str(REQ)
    assert msg.message_type == SOURCE_PUBLISHED_V1
    fields = decode(codec, msg.body, msg.message_type, msg.message_id)
    assert fields == SourcePublishedFields(
        project_id=str(PID),
        generation_request_id=str(REQ),
        repository_id=42,
        git_commit_sha=SHA,
    )


def test_source_published_without_a_subdomain(codec: EventCodecV1) -> None:
    msg = codec.encode_source_published(
        message_id=MID,
        project_id=PID,
        generation_request_id=REQ,
        repository_id=42,
        git_commit_sha=SHA,
        subdomain=None,
        occurred_at=AT,
    )
    assert "subdomain" not in json.loads(msg.body)


def test_project_deleted_round_trip(codec: EventCodecV1) -> None:
    msg = codec.encode_project_deleted(message_id=MID, project_id=PID, occurred_at=AT)
    assert msg.correlation_id == str(PID)
    assert decode(codec, msg.body, msg.message_type, msg.message_id) == ProjectDeleted(PID)


def test_owner_status_round_trip(codec: EventCodecV1) -> None:
    msg = codec.encode_owner_status_changed(
        message_id=MID,
        project_id=PID,
        status=OwnerStatus.SUSPENDED,
        status_version=7,
        subdomain="sunny-bakery",
        occurred_at=AT,
    )
    assert msg.correlation_id == "owner-status:7"
    assert decode(codec, msg.body, msg.message_type, msg.message_id) == OwnerStatusChanged(
        project_id=PID, status=OwnerStatus.SUSPENDED, status_version=7, subdomain="sunny-bakery"
    )


@pytest.mark.parametrize(
    ("kind", "reason"),
    [(CommandKind.SUSPEND_HOSTING, "trial_ended"), (CommandKind.RESUME_HOSTING, None)],
)
def test_hosting_event_round_trip(
    codec: EventCodecV1, kind: CommandKind, reason: str | None
) -> None:
    msg = codec.encode_hosting_event(
        message_id=MID,
        event_key="pay-evt-1",
        project_id=PID,
        kind=kind,
        reason=reason,
        occurred_at=AT,
    )
    assert msg.correlation_id == "pay-evt-1"
    assert ("reason" in json.loads(msg.body)) is (reason is not None)
    assert decode(codec, msg.body, msg.message_type, msg.message_id) == HostingEvent(
        event_key="pay-evt-1", project_id=PID, kind=kind
    )


def test_resume_never_writes_a_reason(codec: EventCodecV1) -> None:
    msg = codec.encode_hosting_event(
        message_id=MID,
        event_key="k",
        project_id=PID,
        kind=CommandKind.RESUME_HOSTING,
        reason="trial_ended",
        occurred_at=AT,
    )
    assert "reason" not in json.loads(msg.body)


def test_ended_event_needs_a_known_reason(codec: EventCodecV1) -> None:
    for reason in (None, "fraud"):
        with pytest.raises(ValueError, match="hosting event"):
            codec.encode_hosting_event(
                message_id=MID,
                event_key="k",
                project_id=PID,
                kind=CommandKind.SUSPEND_HOSTING,
                reason=reason,
                occurred_at=AT,
            )


def test_event_key_outside_the_correlation_pattern_uses_the_fallback(
    codec: EventCodecV1,
) -> None:
    msg = codec.encode_hosting_event(
        message_id=MID,
        event_key="has space/slash",
        project_id=PID,
        kind=CommandKind.RESUME_HOSTING,
        reason=None,
        occurred_at=AT,
    )
    assert msg.correlation_id == "payment-event"


def test_hosting_encoder_refuses_another_kind(codec: EventCodecV1) -> None:
    with pytest.raises(ValueError, match="not a hosting kind"):
        codec.encode_hosting_event(
            message_id=MID,
            event_key="k",
            project_id=PID,
            kind=CommandKind.DEPLOY,
            reason=None,
            occurred_at=AT,
        )


def test_publish_completed_round_trip(codec: EventCodecV1) -> None:
    msg = codec.encode_publish_completed(
        message_id=MID, project_id=PID, release_id=REL, occurred_at=AT
    )
    assert msg.correlation_id == str(REL)
    assert decode(codec, msg.body, msg.message_type, msg.message_id) == PublishCompleted(
        event_key=f"publish-completed-{REL}", project_id=PID, release_id=REL, occurred_at=AT
    )


def test_amqp_type_must_equal_body_type(codec: EventCodecV1) -> None:
    msg = codec.encode_project_deleted(message_id=MID, project_id=PID, occurred_at=AT)
    with pytest.raises(MessageRejected):
        codec.decode_project_deleted(
            body=msg.body,
            content_type="application/json",
            message_type=SOURCE_PUBLISHED_V1,
            message_id=msg.message_id,
        )


def test_amqp_message_id_must_equal_body_message_id(codec: EventCodecV1) -> None:
    msg = codec.encode_project_deleted(message_id=MID, project_id=PID, occurred_at=AT)
    with pytest.raises(MessageRejected) as info:
        codec.decode_project_deleted(
            body=msg.body,
            content_type="application/json",
            message_type=msg.message_type,
            message_id=str(OTHER_MID),
        )
    assert info.value.reason is Reason.ENVELOPE_MISMATCH


def test_a_missing_amqp_message_id_is_refused(codec: EventCodecV1) -> None:
    msg = codec.encode_project_deleted(message_id=MID, project_id=PID, occurred_at=AT)
    with pytest.raises(MessageRejected) as info:
        decode(codec, msg.body, msg.message_type, None)
    assert info.value.reason is Reason.ENVELOPE_MISMATCH


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_every_valid_example_decodes(codec: EventCodecV1, path: Path) -> None:
    doc = json.loads(path.read_text())
    decode(codec, path.read_bytes(), doc["message_type"], doc["message_id"])


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_every_invalid_example_is_rejected(codec: EventCodecV1, path: Path) -> None:
    doc = json.loads(path.read_text())
    with pytest.raises(MessageRejected):
        decode(codec, path.read_bytes(), doc["message_type"], doc["message_id"])


def test_owner_status_with_bad_subdomain_is_rejected(codec: EventCodecV1) -> None:
    # A label the schema accepts but the domain refuses is not possible today; the schema stops it.
    doc = json.loads((SHARED / "examples/valid/owner-status-changed.json").read_text())
    doc["subdomain"] = "Sunny"
    with pytest.raises(MessageRejected) as info:
        decode(codec, json.dumps(doc).encode(), OWNER_STATUS_CHANGED_V1)
    assert info.value.reason is Reason.SCHEMA_INVALID


def _refuse(*_: object) -> None:
    raise DomainError("refused by the domain")


def test_owner_status_the_domain_refuses_is_domain_invalid(
    codec: EventCodecV1, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(v1, "validate_subdomain", _refuse)
    body = (SHARED / "examples/valid/owner-status-changed.json").read_bytes()
    with pytest.raises(MessageRejected) as info:
        decode(codec, body, OWNER_STATUS_CHANGED_V1)
    assert info.value.reason is Reason.DOMAIN_INVALID


def test_source_published_the_domain_refuses_is_domain_invalid(
    codec: EventCodecV1, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(v1, "parse_source_published", _refuse)
    body = (SHARED / "examples/valid/source-published.json").read_bytes()
    with pytest.raises(MessageRejected) as info:
        decode(codec, body, SOURCE_PUBLISHED_V1)
    assert info.value.reason is Reason.DOMAIN_INVALID


# Ported from the prototype codec's tests (decision 0022 E4: the prototype events are deleted).


def test_size_and_content_type_are_checked_first(codec: EventCodecV1) -> None:
    body = (SHARED / "examples/valid/source-published.json").read_bytes()
    with pytest.raises(MessageRejected) as info:
        EventCodecV1(max_body_bytes=10).decode_source_published(
            body=body,
            content_type=CONTENT_TYPE_JSON,
            message_type=SOURCE_PUBLISHED_V1,
            message_id=None,
        )
    assert info.value.reason is Reason.TOO_LARGE
    with pytest.raises(MessageRejected) as info:
        codec.decode_hosting_event(
            body=(SHARED / "examples/valid/hosting-entitlement-ended.json").read_bytes(),
            content_type="application/xml",
            message_type=HOSTING_ENTITLEMENT_ENDED_V1,
            message_id=None,
        )
    assert info.value.reason is Reason.CONTENT_TYPE


def test_a_hosting_event_needs_its_own_amqp_type(codec: EventCodecV1) -> None:
    body = (SHARED / "examples/valid/hosting-entitlement-ended.json").read_bytes()
    for message_type in (HOSTING_ENTITLEMENT_RESTORED_V1, SOURCE_PUBLISHED_V1, None):
        with pytest.raises(MessageRejected) as info:
            codec.decode_hosting_event(
                body=body,
                content_type=CONTENT_TYPE_JSON,
                message_type=message_type,
                message_id=None,
            )
        assert info.value.reason in (Reason.MESSAGE_TYPE, Reason.SCHEMA_INVALID)


def test_the_deletion_decoder_takes_only_its_own_type(codec: EventCodecV1) -> None:
    body = (SHARED / "examples/valid/project-deleted.json").read_bytes()
    for message_type in (HOSTING_ENTITLEMENT_ENDED_V1, SOURCE_PUBLISHED_V1, None):
        with pytest.raises(MessageRejected) as info:
            codec.decode_project_deleted(
                body=body,
                content_type=CONTENT_TYPE_JSON,
                message_type=message_type,
                message_id=None,
            )
        assert info.value.reason is Reason.MESSAGE_TYPE
    with pytest.raises(MessageRejected) as info:
        codec.decode_hosting_event(
            body=body,
            content_type=CONTENT_TYPE_JSON,
            message_type=PROJECT_DELETED_V1,
            message_id=None,
        )
    assert info.value.reason is Reason.MESSAGE_TYPE


@pytest.mark.parametrize("key", [" leading-space", "trailing-space ", "tab\tkey", "é" * 128])
def test_an_event_key_the_domain_refuses_is_domain_invalid(codec: EventCodecV1, key: str) -> None:
    doc = json.loads((SHARED / "examples/valid/hosting-entitlement-ended.json").read_text())
    doc["event_key"] = key
    with pytest.raises(MessageRejected) as info:
        decode(codec, json.dumps(doc).encode(), HOSTING_ENTITLEMENT_ENDED_V1)
    assert info.value.reason is Reason.DOMAIN_INVALID
    assert key not in str(info.value)


def test_encoders_refuse_what_the_decoders_would_refuse(codec: EventCodecV1) -> None:
    with pytest.raises(ValueError, match="source published"):
        codec.encode_source_published(
            message_id=MID,
            project_id=PID,
            generation_request_id=REQ,
            repository_id=0,
            git_commit_sha=SHA,
            subdomain=None,
            occurred_at=AT,
        )
    with pytest.raises(ValueError, match="hosting event"):
        codec.encode_hosting_event(
            message_id=MID,
            event_key=" bad",
            project_id=PID,
            kind=CommandKind.SUSPEND_HOSTING,
            reason="trial_ended",
            occurred_at=AT,
        )


def test_wrong_schema_version_is_rejected(codec: EventCodecV1) -> None:
    doc = json.loads((SHARED / "examples/valid/project-deleted.json").read_text())
    doc["schema_version"] = 2
    with pytest.raises(MessageRejected) as info:
        decode(codec, json.dumps(doc).encode(), PROJECT_DELETED_V1)
    assert info.value.reason is Reason.SCHEMA_INVALID


def test_nil_message_id_is_rejected(codec: EventCodecV1) -> None:
    doc = json.loads((SHARED / "examples/valid/project-deleted.json").read_text())
    doc["message_id"] = "00000000-0000-0000-0000-000000000000"
    with pytest.raises(MessageRejected) as info:
        decode(codec, json.dumps(doc).encode(), PROJECT_DELETED_V1)
    assert info.value.reason is Reason.SCHEMA_INVALID


def test_publish_completed_key_must_name_the_release(codec: EventCodecV1) -> None:
    doc = json.loads((SHARED / "examples/valid/publish-completed.json").read_text())
    doc["event_key"] = f"publish-completed-{OTHER_MID}"
    with pytest.raises(MessageRejected) as info:
        decode(codec, json.dumps(doc).encode(), PUBLISH_COMPLETED_V1)
    assert info.value.reason is Reason.DOMAIN_INVALID


def test_max_body_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        EventCodecV1(max_body_bytes=0)
