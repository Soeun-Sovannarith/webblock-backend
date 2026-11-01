"""RabbitMQ events exchanged with the Project Service.

The platform's message format (Project's .claude/docs/contracts/service-communication.md, the same envelope as
Provisioning's events/envelope.v1.schema.json): snake_case bodies, carrying identifiers, not content. Every body has
the envelope fields plus the event's own. The schemas are Project's `events/project/*` and
`events/ai_codegen/generation_completed.v1.schema.json` in web-block-contracts.
"""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field

CORRELATION_ID = r"^[A-Za-z0-9._:-]{1,128}$"
SHA256_HEX = r"^[0-9a-f]{64}$"


class EventEnvelope(BaseModel):
    """The fields every event carries. Inbound events ignore unknown fields (a v1 schema may gain optional ones)."""
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal[1] = 1
    message_id: UUID
    message_type: str
    occurred_at: datetime
    correlation_id: str = Field(pattern=CORRELATION_ID)
    project_id: UUID


class GenerationRequestedEvent(EventEnvelope):
    """Inbound from Project: project.generation_requested.v1. Payment admitted the request and it is generating.
    The input itself is read from Project's codegen-input endpoint and checked against captured_input_hash."""
    message_type: Literal["project.generation_requested.v1"] = "project.generation_requested.v1"
    generation_request_id: UUID
    captured_input_hash: str = Field(pattern=SHA256_HEX)


class GenerationCancelledEvent(EventEnvelope):
    """Inbound from Project: project.generation_cancelled.v1. The request passed its hard deadline; stop its run."""
    message_type: Literal["project.generation_cancelled.v1"] = "project.generation_cancelled.v1"
    generation_request_id: UUID


class GenerationCompletedEvent(EventEnvelope):
    """Outbound to Project: ai_codegen.generation_completed.v1, once per request when its final run ends. Project
    then reads the run (GET /internal/v1/generation-runs/{id}), which is the authority, and downloads its artifacts."""
    model_config = ConfigDict(extra="forbid")

    message_type: Literal["ai_codegen.generation_completed.v1"] = "ai_codegen.generation_completed.v1"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    generation_request_id: UUID
    generation_run_id: UUID
    outcome: Literal["succeeded", "failed"]
