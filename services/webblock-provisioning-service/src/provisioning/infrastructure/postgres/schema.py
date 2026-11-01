"""SQLAlchemy Core table definitions that mirror the migrated schema (decision 0014 section 11).

The revision 19 tables after migration ``00011`` (decision 0012 section 3), with exact column
names, types and nullability. These objects describe the database for building statements; they
never create, alter or reflect it: the goose migrations are the only schema source, and
``tests/integration/test_schema_mirror.py`` fails on any drift between this module and the
migrated catalogue. Enum labels come from the domain's enums, so the drift test also checks them.

Owned by task B3; F1 and F2 import from here and ask B3 or the lead for a change. Wave 6 task W6-E
owns it now and added ``usage_outbox`` (migration ``00016``). Migration ``00018`` (decision 0021)
adds ``offline_reason``, ``project_hosting.offline_reasons`` and ``owner_status_version``,
``commands.hosting_reason``, and renames ``github_repository_id`` to ``repository_id``. Migration
``00020`` adds ``project_hosting.subdomain``, nullable until the backfill; ``00021`` makes it NOT
NULL. Migration ``00022`` (decision 0023 G16) rebuilds ``project_hosting``, ``commands`` and
``source_published_events`` in the column order of Notion "5 · Provisioning" and puts
``release_step_name`` in Notion's order; the columns below follow that order.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import (
    CHAR,
    VARCHAR,
    BigInteger,
    Column,
    Integer,
    MetaData,
    Table,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import ARRAY, ENUM, TIMESTAMP

from provisioning.core.application import SourceEventStatus
from provisioning.core.application.usage.ports import UsageEventStatus
from provisioning.core.domain import (
    CommandKind,
    CommandStatus,
    HostingStatus,
    OfflineReason,
    ReleaseStatus,
    ReleaseStepName,
    ReleaseStepStatus,
    RoutingStatus,
)

# implicit_returning is off: an INSERT returns only what a statement asks for, so a role without
# SELECT on a table (the intake role on release_steps) can insert into it, and rowcount is the
# driver's.
metadata: Final = MetaData()

_TS = TIMESTAMP(timezone=True)


COMMAND_KIND: Final = ENUM(
    *[m.value for m in CommandKind], name="command_kind", schema="provisioning", create_type=False
)
COMMAND_STATUS: Final = ENUM(
    *[m.value for m in CommandStatus],
    name="command_status",
    schema="provisioning",
    create_type=False,
)
RELEASE_STATUS: Final = ENUM(
    *[m.value for m in ReleaseStatus],
    name="release_status",
    schema="provisioning",
    create_type=False,
)
RELEASE_STEP_NAME: Final = ENUM(
    *[m.value for m in ReleaseStepName],
    name="release_step_name",
    schema="provisioning",
    create_type=False,
)
RELEASE_STEP_STATUS: Final = ENUM(
    *[m.value for m in ReleaseStepStatus],
    name="release_step_status",
    schema="provisioning",
    create_type=False,
)
ROUTING_STATUS: Final = ENUM(
    *[m.value for m in RoutingStatus],
    name="routing_status",
    schema="provisioning",
    create_type=False,
)
HOSTING_STATUS: Final = ENUM(
    *[m.value for m in HostingStatus],
    name="hosting_status",
    schema="provisioning",
    create_type=False,
)
OFFLINE_REASON: Final = ENUM(
    *[m.value for m in OfflineReason],
    name="offline_reason",
    schema="provisioning",
    create_type=False,
)
SOURCE_EVENT_STATUS: Final = ENUM(
    *[m.value for m in SourceEventStatus],
    name="source_event_status",
    schema="provisioning_ops",
    create_type=False,
)

USAGE_EVENT_STATUS: Final = ENUM(
    *[m.value for m in UsageEventStatus],
    name="usage_event_status",
    schema="provisioning_ops",
    create_type=False,
)

releases: Final = Table(
    "releases",
    metadata,
    Column("release_id", Uuid(), primary_key=True),
    Column("project_id", Uuid(), nullable=False),
    Column("generation_request_id", Uuid(), nullable=False),
    Column("repository_id", BigInteger(), nullable=False),
    Column("git_commit_sha", VARCHAR(64), nullable=False),
    Column("container_image_digest", VARCHAR(71), nullable=True),
    Column("status", RELEASE_STATUS, nullable=False),
    Column("failure_code", VARCHAR(100), nullable=True),
    Column("failure_summary", Text(), nullable=True),
    Column("created_at", _TS, nullable=False),
    Column("updated_at", _TS, nullable=False),
    Column("promoted_at", _TS, nullable=True),
    schema="provisioning",
    implicit_returning=False,
)

project_hosting: Final = Table(
    "project_hosting",
    metadata,
    Column("project_id", Uuid(), primary_key=True),
    Column("live_release_id", Uuid(), nullable=True),
    Column("subdomain", VARCHAR(63), nullable=False),
    Column("last_command_number", BigInteger(), nullable=False),
    Column("routing_status", ROUTING_STATUS, nullable=False),
    Column("hosting_status", HOSTING_STATUS, nullable=False),
    Column("offline_reasons", ARRAY(OFFLINE_REASON), nullable=False),
    Column("owner_status_version", BigInteger(), nullable=False),
    Column("updated_at", _TS, nullable=False),
    schema="provisioning",
    implicit_returning=False,
)

release_steps: Final = Table(
    "release_steps",
    metadata,
    Column("release_step_id", Uuid(), primary_key=True),
    Column("release_id", Uuid(), nullable=False),
    Column("step_name", RELEASE_STEP_NAME, nullable=False),
    Column("attempt", Integer(), nullable=False),
    Column("status", RELEASE_STEP_STATUS, nullable=False),
    Column("started_at", _TS, nullable=True),
    Column("finished_at", _TS, nullable=True),
    Column("deadline_at", _TS, nullable=True),
    Column("failure_code", VARCHAR(100), nullable=True),
    Column("log_ref", VARCHAR(500), nullable=True),
    schema="provisioning",
    implicit_returning=False,
)

commands: Final = Table(
    "commands",
    metadata,
    Column("command_id", Uuid(), primary_key=True),
    Column("project_id", Uuid(), nullable=False),
    Column("release_id", Uuid(), nullable=True),
    Column("kind", COMMAND_KIND, nullable=False),
    Column("hosting_reason", OFFLINE_REASON, nullable=True),
    Column("idempotency_key", VARCHAR(255), nullable=False),
    Column("request_fingerprint", CHAR(64), nullable=False),
    Column("command_number", BigInteger(), nullable=False),
    Column("requested_by_subject", VARCHAR(255), nullable=True),
    Column("status", COMMAND_STATUS, nullable=False),
    Column("failure_code", VARCHAR(100), nullable=True),
    Column("created_at", _TS, nullable=False),
    Column("updated_at", _TS, nullable=False),
    schema="provisioning",
    implicit_returning=False,
)

command_dispatch: Final = Table(
    "command_dispatch",
    metadata,
    Column("command_id", Uuid(), primary_key=True),
    Column("correlation_id", VARCHAR(128), nullable=False),
    Column("deadline_at", _TS, nullable=False),
    Column("next_publish_at", _TS, nullable=False),
    Column("publish_attempts", Integer(), nullable=False),
    Column("last_broker_ack_at", _TS, nullable=True),
    Column("execution_attempts", Integer(), nullable=False),
    Column("claim_number", BigInteger(), nullable=False),
    Column("claimed_by_worker", VARCHAR(100), nullable=True),
    Column("claim_expires_at", _TS, nullable=True),
    Column("retry_after", _TS, nullable=True),
    Column("last_failure_code", VARCHAR(100), nullable=True),
    Column("finished_at", _TS, nullable=True),
    Column("created_at", _TS, nullable=False),
    Column("updated_at", _TS, nullable=False),
    schema="provisioning_ops",
    implicit_returning=False,
)

source_published_events: Final = Table(
    "source_published_events",
    metadata,
    Column("project_id", Uuid(), primary_key=True),
    Column("generation_request_id", Uuid(), primary_key=True),
    Column("repository_id", BigInteger(), nullable=False),
    Column("git_commit_sha", VARCHAR(64), nullable=False),
    Column("status", SOURCE_EVENT_STATUS, nullable=False),
    Column("attempts", Integer(), nullable=False),
    Column("max_attempts", Integer(), nullable=False),
    Column("next_attempt_at", _TS, nullable=False),
    Column("outcome_code", VARCHAR(100), nullable=True),
    Column("release_id", Uuid(), nullable=True),
    Column("mismatched_repeat_count", Integer(), nullable=False),
    Column("last_mismatched_repeat_at", _TS, nullable=True),
    Column("received_at", _TS, nullable=False),
    Column("updated_at", _TS, nullable=False),
    Column("finished_at", _TS, nullable=True),
    Column("last_manual_retry_by", VARCHAR(255), nullable=True),
    Column("last_manual_retry_at", _TS, nullable=True),
    schema="provisioning_ops",
    implicit_returning=False,
)

usage_outbox: Final = Table(
    "usage_outbox",
    metadata,
    Column("release_id", Uuid(), primary_key=True),
    Column("project_id", Uuid(), nullable=False),
    Column("event_key", VARCHAR(255), nullable=False),
    Column("completed_at", _TS, nullable=False),
    Column("status", USAGE_EVENT_STATUS, nullable=False),
    Column("attempts", Integer(), nullable=False),
    Column("next_attempt_at", _TS, nullable=False),
    Column("last_failure_code", VARCHAR(100), nullable=True),
    Column("sent_at", _TS, nullable=True),
    Column("created_at", _TS, nullable=False),
    Column("updated_at", _TS, nullable=False),
    schema="provisioning_ops",
    implicit_returning=False,
)
