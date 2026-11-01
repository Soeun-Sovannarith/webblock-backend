"""The command-job codec (decision 0005 section 6).

Incoming jobs are checked in order (size, content type, AMQP type, one JSON value, the schema of
src/contracts/events/command-job.v1.schema.json, the domain) and every refusal is a
``MessageRejected`` that carries a reason code, the body size and its hash, never the body. The
wire keeps the contract's ``fencing_generation``; inside the package it is the command number
(decision 0012, revision 19 names).
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from provisioning.core.application import InvalidCorrelationId, WorkRef
from provisioning.core.domain import CommandKind, InvalidCommandNumber, InvalidId
from provisioning.presentation.messaging import (
    COMMAND_JOB_MESSAGE_TYPE,
    COMMAND_JOB_SCHEMA,
    CONTENT_TYPE_JSON,
    CommandJobCodec,
    CommandJobPublisher,
    EncodedMessage,
    MessageRejected,
    Reason,
    new_command_job,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
EVENTS = REPO_ROOT / "src" / "contracts" / "events"
VALID = EVENTS / "examples" / "valid"
INVALID = EVENTS / "examples" / "invalid"

PROJECT = UUID("6b1f3a52-0d7e-4b7c-8a21-5c3d9e0f1a02")
RELEASE = UUID("9d2e4b63-1e8f-4c8d-9b32-6d4e0f1a2b03")
COMMAND = UUID("c4a5d674-2f90-4d9e-8c43-7e5f1a2b3c04")
MESSAGE = UUID("3f0c2a52-6a7b-4c8e-9d11-0a1b2c3d4e01")
AT = datetime(2026, 10, 8, 9, 30, 0, 123456, tzinfo=UTC)

LIMIT = 4096


def codec() -> CommandJobCodec:
    return CommandJobCodec(max_body_bytes=LIMIT)


def decode(
    body: bytes,
    *,
    content_type: str | None = CONTENT_TYPE_JSON,
    message_type: str | None = COMMAND_JOB_MESSAGE_TYPE,
) -> WorkRef:
    return codec().decode_command_job(
        body=body, content_type=content_type, message_type=message_type
    )


def rejected(
    body: bytes,
    *,
    content_type: str | None = CONTENT_TYPE_JSON,
    message_type: str | None = COMMAND_JOB_MESSAGE_TYPE,
) -> MessageRejected:
    with pytest.raises(MessageRejected) as caught:
        decode(body, content_type=content_type, message_type=message_type)
    return caught.value


def example(path: Path) -> bytes:
    return path.read_bytes()


def ref(kind: CommandKind, number: int = 4) -> WorkRef:
    return WorkRef(
        command_id=COMMAND,
        project_id=PROJECT,
        kind=kind,
        command_number=number,
        release_id=None if kind.names_no_release() else RELEASE,
    )


def test_the_embedded_schema_is_the_contract_file() -> None:
    on_disk = json.loads((EVENTS / "command-job.v1.schema.json").read_text())
    assert json.loads(COMMAND_JOB_SCHEMA) == on_disk


COMMAND_JOB_EXAMPLES = sorted(VALID.glob("command-job-*.json"))


def test_every_kind_has_a_valid_example() -> None:
    assert [p.stem for p in COMMAND_JOB_EXAMPLES] == [
        "command-job-deploy",
        "command-job-promote",
        "command-job-redeploy",
        "command-job-resume-hosting",
        "command-job-rollback",
        "command-job-suspend-hosting",
    ]


@pytest.mark.parametrize("path", COMMAND_JOB_EXAMPLES, ids=lambda p: p.stem)
def test_every_valid_command_job_example_decodes(path: Path) -> None:
    doc = json.loads(path.read_text())
    got = decode(example(path))
    assert got.command_id == UUID(doc["command_id"])
    assert got.project_id == UUID(doc["project_id"])
    assert got.kind == CommandKind(doc["kind"])
    assert got.command_number == doc["fencing_generation"]
    assert got.release_id == (UUID(doc["release_id"]) if "release_id" in doc else None)


INVALID_COMMAND_JOBS = sorted(INVALID.glob("command-job-*.json"))


@pytest.mark.parametrize("path", INVALID_COMMAND_JOBS, ids=lambda p: p.stem)
def test_every_invalid_command_job_example_is_refused_by_the_schema(path: Path) -> None:
    err = rejected(example(path))
    assert err.reason is Reason.SCHEMA_INVALID
    assert err.schema_locations, "a schema refusal names the failed keywords"


def test_the_invalid_examples_are_all_exercised() -> None:
    assert len(INVALID_COMMAND_JOBS) == 14


def test_the_job_kinds_follow_notion() -> None:
    """Notion "5 · Provisioning" lists redeploy before sync_domains; a redeploy job names the live
    release, so it carries a release_id (decision 0021)."""
    schema = json.loads(COMMAND_JOB_SCHEMA)
    assert schema["properties"]["kind"]["enum"] == [
        "deploy",
        "promote",
        "rollback",
        "suspend_hosting",
        "resume_hosting",
        "redeploy",
        "sync_domains",
    ]
    assert "redeploy" not in schema["if"]["properties"]["kind"]["enum"]


def test_envelope_examples_are_other_messages() -> None:
    # Their AMQP type is set to the job's, so only the body's message_type tells them apart.
    for path in sorted(VALID.glob("envelope-*.json")):
        err = rejected(example(path))
        assert err.reason is Reason.SCHEMA_INVALID, path.stem


def test_checks_run_in_order() -> None:
    good = example(VALID / "command-job-deploy.json")
    assert rejected(b"x" * (LIMIT + 1)).reason is Reason.TOO_LARGE
    assert rejected(good, content_type="text/plain").reason is Reason.CONTENT_TYPE
    assert rejected(good, content_type=None).reason is Reason.CONTENT_TYPE
    assert rejected(good, message_type="other").reason is Reason.MESSAGE_TYPE
    assert rejected(good, message_type=None).reason is Reason.MESSAGE_TYPE
    assert rejected(b"{not json").reason is Reason.NOT_JSON
    assert rejected(good + b" {}").reason is Reason.NOT_JSON
    assert rejected(b"[]").reason is Reason.SCHEMA_INVALID


def test_the_body_limit_is_inclusive() -> None:
    good = example(VALID / "command-job-deploy.json")
    stripped = good.rstrip()
    padded = stripped + b" " * (LIMIT - len(stripped))
    assert len(padded) == LIMIT
    assert decode(padded).kind is CommandKind.DEPLOY
    assert rejected(padded + b" ").reason is Reason.TOO_LARGE


def test_a_body_valid_for_the_schema_but_not_the_domain_is_refused() -> None:
    doc = json.loads(example(VALID / "command-job-deploy.json"))
    doc["fencing_generation"] = 2**53 - 1
    assert decode(json.dumps(doc).encode()).command_number == 2**53 - 1
    doc["correlation_id"] = "c" * 128
    assert decode(json.dumps(doc).encode()).kind is CommandKind.DEPLOY
    # occurred_at in the schema's format but no valid instant: Feb 30.
    doc["occurred_at"] = "2026-02-30T00:00:00Z"
    assert rejected(json.dumps(doc).encode()).reason is Reason.SCHEMA_INVALID


@pytest.mark.parametrize("field", ["message_id", "occurred_at", "correlation_id", "kind"])
def test_tr24_a_field_that_is_no_string_is_domain_invalid_without_the_schema(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """TR-24: the decoder checks each text field's type itself, so a number where the contract
    has a string is ``DOMAIN_INVALID`` even if the schema check let it through."""
    monkeypatch.setattr(
        "provisioning.presentation.messaging.codec.check_schema", lambda *_args: None
    )
    doc = json.loads(example(VALID / "command-job-deploy.json"))
    doc[field] = 123
    assert rejected(json.dumps(doc).encode()).reason is Reason.DOMAIN_INVALID


def test_a_rejection_never_holds_the_body() -> None:
    marker = "c0rrelation-marker-value"
    doc = json.loads(example(VALID / "command-job-deploy.json"))
    doc["correlation_id"] = marker
    doc["kind"] = "bogus"
    body = json.dumps(doc).encode()
    err = rejected(body)
    text = f"{err} {err!r} {err.args}"
    assert marker not in text and "bogus" not in text
    assert err.size == len(body)
    assert err.body_sha256 == hashlib.sha256(body).hexdigest()
    assert err.__cause__ is None and err.__suppress_context__
    for body in (b'{"x": "' + marker.encode() + b'"', marker.encode()):
        err = rejected(body)
        assert marker not in f"{err} {err!r} {err.args}"
        assert err.__cause__ is None and err.__suppress_context__


def test_reasons_are_stable_codes() -> None:
    assert [r.value for r in Reason] == [
        "too_large",
        "content_type",
        "unknown_message_type",
        "not_json",
        "schema_invalid",
        "envelope_mismatch",
        "domain_invalid",
    ]


def test_new_codec_refuses_a_non_positive_limit() -> None:
    for limit in (0, -1):
        with pytest.raises(ValueError, match="positive"):
            CommandJobCodec(max_body_bytes=limit)


@pytest.mark.parametrize("kind", list(CommandKind))
def test_encode_round_trips_every_kind(kind: CommandKind) -> None:
    job = new_command_job(
        message_id=MESSAGE, ref=ref(kind), correlation_id="corr-1", occurred_at=AT
    )
    message = codec().encode_command_job(job)
    assert message == EncodedMessage(
        body=message.body,
        content_type=CONTENT_TYPE_JSON,
        message_type=COMMAND_JOB_MESSAGE_TYPE,
        message_id=str(MESSAGE),
        correlation_id="corr-1",
        timestamp=AT,
    )
    doc = json.loads(message.body)
    assert doc["fencing_generation"] == 4
    assert doc["occurred_at"] == "2026-10-08T09:30:00.123456Z"
    assert "command_number" not in doc
    assert ("release_id" in doc) is not kind.names_no_release()
    assert decode(message.body) == ref(kind)


def test_new_command_job_refuses_invalid_input() -> None:
    with pytest.raises(InvalidId):
        new_command_job(
            message_id=UUID(int=0), ref=ref(CommandKind.DEPLOY), correlation_id="c", occurred_at=AT
        )
    with pytest.raises(InvalidCommandNumber):
        new_command_job(
            message_id=MESSAGE, ref=ref(CommandKind.DEPLOY, 0), correlation_id="c", occurred_at=AT
        )
    for corr in ("", "c" * 129):
        with pytest.raises(InvalidCorrelationId):
            new_command_job(
                message_id=MESSAGE,
                ref=ref(CommandKind.DEPLOY),
                correlation_id=corr,
                occurred_at=AT,
            )
    with pytest.raises(ValueError, match="timezone"):
        new_command_job(
            message_id=MESSAGE,
            ref=ref(CommandKind.DEPLOY),
            correlation_id="c",
            occurred_at=AT.replace(tzinfo=None),
        )


def test_encode_refuses_a_body_over_the_limit() -> None:
    small = CommandJobCodec(max_body_bytes=64)
    job = new_command_job(
        message_id=MESSAGE, ref=ref(CommandKind.DEPLOY), correlation_id="c", occurred_at=AT
    )
    with pytest.raises(ValueError, match="limit"):
        small.encode_command_job(job)


class RecordingRawPublisher:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail = fail

    async def publish(
        self,
        *,
        exchange: str,
        routing_key: str,
        body: bytes,
        content_type: str,
        message_type: str,
        message_id: str,
        correlation_id: str,
        timestamp: datetime,
    ) -> None:
        self.calls.append(
            {
                "exchange": exchange,
                "routing_key": routing_key,
                "body": body,
                "content_type": content_type,
                "message_type": message_type,
                "message_id": message_id,
                "correlation_id": correlation_id,
                "timestamp": timestamp,
            }
        )
        if self.fail is not None:
            raise self.fail


@pytest.mark.asyncio
async def test_the_publisher_encodes_then_publishes_to_its_exchange() -> None:
    raw = RecordingRawPublisher()
    publisher = CommandJobPublisher(
        codec=codec(), raw=raw, exchange="provisioning.jobs", routing_key="command_job.v1"
    )
    await publisher.publish_command_job(
        message_id=MESSAGE, ref=ref(CommandKind.PROMOTE), correlation_id="corr-9", occurred_at=AT
    )
    [call] = raw.calls
    assert call["exchange"] == "provisioning.jobs"
    assert call["routing_key"] == "command_job.v1"
    assert call["message_type"] == COMMAND_JOB_MESSAGE_TYPE
    assert call["message_id"] == str(MESSAGE)
    assert call["correlation_id"] == "corr-9"
    body = call["body"]
    assert isinstance(body, bytes)
    assert decode(body) == ref(CommandKind.PROMOTE)


@pytest.mark.asyncio
async def test_the_publisher_never_publishes_an_invalid_job_and_passes_failures_on() -> None:
    raw = RecordingRawPublisher()
    publisher = CommandJobPublisher(codec=codec(), raw=raw, exchange="e", routing_key="k")
    with pytest.raises(InvalidCorrelationId):
        await publisher.publish_command_job(
            message_id=MESSAGE, ref=ref(CommandKind.DEPLOY), correlation_id="", occurred_at=AT
        )
    assert raw.calls == []
    failing = CommandJobPublisher(
        codec=codec(),
        raw=RecordingRawPublisher(fail=ConnectionError("nacked")),
        exchange="e",
        routing_key="k",
    )
    with pytest.raises(ConnectionError):
        await failing.publish_command_job(
            message_id=MESSAGE, ref=ref(CommandKind.DEPLOY), correlation_id="c", occurred_at=AT
        )


@pytest.mark.asyncio
async def test_one_codec_is_safe_for_concurrent_use() -> None:
    shared = codec()
    bodies = [example(p) for p in COMMAND_JOB_EXAMPLES] * 20

    async def one(body: bytes) -> WorkRef:
        await asyncio.sleep(0)
        return shared.decode_command_job(
            body=body, content_type=CONTENT_TYPE_JSON, message_type=COMMAND_JOB_MESSAGE_TYPE
        )

    got = await asyncio.gather(*(one(b) for b in bodies))
    assert [r.kind for r in got] == [decode(b).kind for b in bodies]
