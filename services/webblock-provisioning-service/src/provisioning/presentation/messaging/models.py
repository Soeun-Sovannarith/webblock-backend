"""The typed v1 events (decision 0022 E11).

One frozen pydantic model per event, with the types, ranges and patterns of its JSON schema in
``src/contracts/shared/events``. The schemas stay the shared contract; these models are
Provisioning's typed view of them, and ``test_models.py`` fails when the two disagree on an example.
Read a body with ``model_validate_json`` so that strict JSON mode applies. A ``ValidationError``
carries the offending input: use ``error_locations``, which names fields only.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
)

_MAX_INT64_SAFE = 2**53 - 1
_CANONICAL_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DATE_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})")
_NIL = UUID(int=0)
_DNS_LABEL = r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$"


def _uuid(value: object) -> UUID:
    """A canonical lowercase UUID string (or a UUID object), never the nil UUID. The messages hold
    no part of the input."""
    if isinstance(value, UUID):
        parsed = value
    elif isinstance(value, str) and _CANONICAL_UUID.fullmatch(value):
        parsed = UUID(value)
    else:
        raise ValueError("not a canonical lowercase UUID")
    if parsed == _NIL:
        raise ValueError("the nil UUID is refused")
    return parsed


def _date_time(value: object) -> object:
    """RFC 3339 text of at most 40 characters becomes an aware datetime. pydantic's own parser is
    laxer, so the shape is checked here first (and strict mode takes no text after a validator)."""
    if not isinstance(value, str):
        return value
    if len(value) > 40 or _DATE_TIME.fullmatch(value) is None:
        raise ValueError("not an RFC 3339 date-time")
    return datetime.fromisoformat(value)


Id = Annotated[UUID, BeforeValidator(_uuid)]
Instant = Annotated[AwareDatetime, BeforeValidator(_date_time)]
CorrelationId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._:-]{1,128}$")]
DnsLabel = Annotated[str, StringConstraints(min_length=1, max_length=63, pattern=_DNS_LABEL)]
EventKey = Annotated[str, StringConstraints(min_length=1, max_length=255)]
PositiveInt64 = Annotated[int, Field(strict=True, ge=1, le=_MAX_INT64_SAFE)]


class EnvelopeV1(BaseModel):
    """The fields every v1 event carries (envelope.v1.schema.json)."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    # A strict int: Literal[1] also takes true and 1.0.
    schema_version: Annotated[int, Field(strict=True, ge=1, le=1)]
    message_id: Id
    message_type: Annotated[str, StringConstraints(min_length=5, max_length=100)]
    occurred_at: Instant
    correlation_id: CorrelationId
    project_id: Id


class SourcePublishedV1(EnvelopeV1):
    message_type: Literal["project.source_published.v1"]
    generation_request_id: Id
    repository_id: PositiveInt64
    git_commit_sha: Annotated[
        str,
        StringConstraints(min_length=40, max_length=64, pattern=r"^([0-9a-f]{40}|[0-9a-f]{64})$"),
    ]
    subdomain: DnsLabel | None = None


class ProjectDeletedV1(EnvelopeV1):
    message_type: Literal["project.project_deleted.v1"]


class OwnerStatusChangedV1(EnvelopeV1):
    message_type: Literal["project.owner_status_changed.v1"]
    status: Literal["active", "suspended"]
    status_version: PositiveInt64
    subdomain: DnsLabel


class HostingEntitlementEndedV1(EnvelopeV1):
    message_type: Literal["payment.hosting_entitlement_ended.v1"]
    event_key: EventKey
    reason: Literal["trial_ended", "grace_expired", "plan_canceled", "over_website_limit"]


class HostingEntitlementRestoredV1(EnvelopeV1):
    message_type: Literal["payment.hosting_entitlement_restored.v1"]
    event_key: EventKey


class PublishCompletedV1(EnvelopeV1):
    message_type: Literal["provisioning.publish_completed.v1"]
    event_key: Annotated[
        str,
        StringConstraints(
            min_length=54,
            max_length=54,
            pattern=r"^publish-completed-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        ),
    ]
    release_id: Id


EVENT_MODELS: dict[str, type[EnvelopeV1]] = {
    "project.source_published.v1": SourcePublishedV1,
    "project.project_deleted.v1": ProjectDeletedV1,
    "project.owner_status_changed.v1": OwnerStatusChangedV1,
    "payment.hosting_entitlement_ended.v1": HostingEntitlementEndedV1,
    "payment.hosting_entitlement_restored.v1": HostingEntitlementRestoredV1,
    "provisioning.publish_completed.v1": PublishCompletedV1,
}


def error_locations(error: ValidationError) -> tuple[str, ...]:
    """The field paths of ``error`` as ``/a/b`` (sorted, unique); never an input or a message."""
    return tuple(sorted({"/" + "/".join(str(part) for part in e["loc"]) for e in error.errors()}))
