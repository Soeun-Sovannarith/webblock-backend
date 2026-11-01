"""The event contract documents, embedded so that the codecs read nothing from disk at run time.

Each constant is the text of one file under ``src/contracts/events``; a unit test keeps every
constant equal to its file. Regenerate a constant by copying the file, never by editing it here.
"""

# src/contracts/events/command-job.v1.schema.json
COMMAND_JOB_SCHEMA = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://webblock.invalid/provisioning/events/command-job.v1.schema.json",
  "title": "Provisioning command job v1 (draft)",
  "description": "Notification that an accepted command of any of the seven kinds is ready for the release worker. Carries identifiers only; the database row is the authority and the worker must compare these identifiers with it. command_id and fencing_generation, optional in the shared envelope, are required here. release_id is present exactly when the kind names a release: a suspend_hosting, resume_hosting or sync_domains job names no release, and every other job names one.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id", "command_id", "fencing_generation", "kind"],
  "properties": {
    "schema_version": { "const": 1 },
    "message_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": { "enum": ["00000000-0000-0000-0000-000000000000"] } },
    "message_type": { "const": "provisioning.command_job" },
    "occurred_at": { "type": "string", "format": "date-time", "maxLength": 40 },
    "correlation_id": { "type": "string", "minLength": 1, "maxLength": 128 },
    "project_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": { "enum": ["00000000-0000-0000-0000-000000000000"] } },
    "release_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": { "enum": ["00000000-0000-0000-0000-000000000000"] } },
    "command_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": { "enum": ["00000000-0000-0000-0000-000000000000"] } },
    "fencing_generation": { "type": "integer", "format": "int64", "minimum": 1, "maximum": 9007199254740991 },
    "kind": { "type": "string", "enum": ["deploy", "promote", "rollback", "suspend_hosting", "resume_hosting", "redeploy", "sync_domains"] }
  },
  "if": { "required": ["kind"], "properties": { "kind": { "enum": ["suspend_hosting", "resume_hosting", "sync_domains"] } } },
  "then": { "not": { "required": ["release_id"] } },
  "else": { "required": ["release_id"] }
}
"""  # noqa: E501 - contract text kept as the file has it

# The v1 event schemas (decision 0022 E4): the text of the files under src/contracts/shared/events,
# copied by `make contracts-sync`; never edit a constant here.

# src/contracts/shared/events/envelope.v1.schema.json
ENVELOPE_V1_SCHEMA = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://webblock.invalid/contracts/events/envelope.v1.schema.json",
  "title": "Event envelope v1",
  "description": "ACCEPTED (Notion \"Web Block architecture\" 4.1). The fields every event body carries, in snake_case. Each event schema repeats them, so one file validates one message. The AMQP type property equals message_type and the AMQP message_id equals the body's message_id.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id"],
  "properties": {
    "schema_version": { "const": 1 },
    "message_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "message_type": { "type": "string", "minLength": 5, "maxLength": 100, "pattern": "^[a-z][a-z_]*\\.[a-z][a-z_]*\\.v[1-9][0-9]*$" },
    "occurred_at": { "type": "string", "format": "date-time", "maxLength": 40 },
    "correlation_id": { "type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]{1,128}$" },
    "project_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} }
  }
}
"""  # noqa: E501 - contract text kept as the file has it

# src/contracts/shared/events/project/source_published.v1.schema.json
SOURCE_PUBLISHED_V1_SCHEMA = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://webblock.invalid/contracts/events/project/source_published.v1.schema.json",
  "title": "Project: source published v1",
  "description": "DRAFT, proposed by provisioning, waits for project. Owner: project. The accepted source of one generation request of a project is the given commit of the given repository. Provisioning confirms the repository with Project and creates one release per project and generation request. subdomain is optional: the site's DNS label when Project already knows it.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id", "generation_request_id", "repository_id", "git_commit_sha"],
  "properties": {
    "schema_version": { "const": 1 },
    "message_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "message_type": { "const": "project.source_published.v1" },
    "occurred_at": { "type": "string", "format": "date-time", "maxLength": 40 },
    "correlation_id": { "type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]{1,128}$" },
    "project_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "generation_request_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "repository_id": { "type": "integer", "format": "int64", "minimum": 1, "maximum": 9007199254740991 },
    "git_commit_sha": { "type": "string", "minLength": 40, "maxLength": 64, "pattern": "^([0-9a-f]{40}|[0-9a-f]{64})$" },
    "subdomain": { "type": "string", "minLength": 1, "maxLength": 63, "pattern": "^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$" }
  }
}
"""  # noqa: E501 - contract text kept as the file has it

# src/contracts/shared/events/project/project_deleted.v1.schema.json
PROJECT_DELETED_V1_SCHEMA = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://webblock.invalid/contracts/events/project/project_deleted.v1.schema.json",
  "title": "Project: project deleted v1",
  "description": "DRAFT, proposed by provisioning, waits for project. Owner: project. The project is deleted. Provisioning takes its site offline with the reason project_deleted and keeps its data.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id"],
  "properties": {
    "schema_version": { "const": 1 },
    "message_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "message_type": { "const": "project.project_deleted.v1" },
    "occurred_at": { "type": "string", "format": "date-time", "maxLength": 40 },
    "correlation_id": { "type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]{1,128}$" },
    "project_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} }
  }
}
"""  # noqa: E501 - contract text kept as the file has it

# src/contracts/shared/events/project/owner_status_changed.v1.schema.json
OWNER_STATUS_CHANGED_V1_SCHEMA = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://webblock.invalid/contracts/events/project/owner_status_changed.v1.schema.json",
  "title": "Project: owner status changed v1",
  "description": "DRAFT, proposed by provisioning, waits for project. Owner: project. The owner of the project was suspended or made active again. status_version grows by one with each change, so a consumer ignores an older or equal version. subdomain is the site's DNS label.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id", "status", "status_version", "subdomain"],
  "properties": {
    "schema_version": { "const": 1 },
    "message_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "message_type": { "const": "project.owner_status_changed.v1" },
    "occurred_at": { "type": "string", "format": "date-time", "maxLength": 40 },
    "correlation_id": { "type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]{1,128}$" },
    "project_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "status": { "type": "string", "enum": ["active", "suspended"] },
    "status_version": { "type": "integer", "format": "int64", "minimum": 1, "maximum": 9007199254740991 },
    "subdomain": { "type": "string", "minLength": 1, "maxLength": 63, "pattern": "^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$" }
  }
}
"""  # noqa: E501 - contract text kept as the file has it

# src/contracts/shared/events/payment/hosting_entitlement_ended.v1.schema.json
HOSTING_ENTITLEMENT_ENDED_V1_SCHEMA = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://webblock.invalid/contracts/events/payment/hosting_entitlement_ended.v1.schema.json",
  "title": "Payment: hosting entitlement ended v1",
  "description": "DRAFT, proposed by provisioning, waits for payment. Owner: payment. The project may no longer be hosted. event_key is Payment's unique key for this event; a consumer uses it to drop a repeat. Provisioning rechecks the entitlement before it acts.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id", "event_key", "reason"],
  "properties": {
    "schema_version": { "const": 1 },
    "message_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "message_type": { "const": "payment.hosting_entitlement_ended.v1" },
    "occurred_at": { "type": "string", "format": "date-time", "maxLength": 40 },
    "correlation_id": { "type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]{1,128}$" },
    "project_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "event_key": { "type": "string", "minLength": 1, "maxLength": 255 },
    "reason": { "type": "string", "enum": ["trial_ended", "grace_expired", "plan_canceled", "over_website_limit"] }
  }
}
"""  # noqa: E501 - contract text kept as the file has it

# src/contracts/shared/events/payment/hosting_entitlement_restored.v1.schema.json
HOSTING_ENTITLEMENT_RESTORED_V1_SCHEMA = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://webblock.invalid/contracts/events/payment/hosting_entitlement_restored.v1.schema.json",
  "title": "Payment: hosting entitlement restored v1",
  "description": "DRAFT, proposed by provisioning, waits for payment. Owner: payment. The project may be hosted again. event_key is Payment's unique key for this event; a consumer uses it to drop a repeat. Provisioning rechecks the entitlement before it acts.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id", "event_key"],
  "properties": {
    "schema_version": { "const": 1 },
    "message_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "message_type": { "const": "payment.hosting_entitlement_restored.v1" },
    "occurred_at": { "type": "string", "format": "date-time", "maxLength": 40 },
    "correlation_id": { "type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]{1,128}$" },
    "project_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "event_key": { "type": "string", "minLength": 1, "maxLength": 255 }
  }
}
"""  # noqa: E501 - contract text kept as the file has it

# src/contracts/shared/events/provisioning/publish_completed.v1.schema.json
PUBLISH_COMPLETED_V1_SCHEMA = r"""{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://webblock.invalid/contracts/events/provisioning/publish_completed.v1.schema.json",
  "title": "Provisioning: publish completed v1",
  "description": "OWNED BY provisioning. A release of the project went live. Payment counts it as one publish. event_key is publish-completed-<release_id>, the same for every repeat of this event.",
  "type": "object",
  "additionalProperties": false,
  "required": ["schema_version", "message_id", "message_type", "occurred_at", "correlation_id", "project_id", "event_key", "release_id"],
  "properties": {
    "schema_version": { "const": 1 },
    "message_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "message_type": { "const": "provisioning.publish_completed.v1" },
    "occurred_at": { "type": "string", "format": "date-time", "maxLength": 40 },
    "correlation_id": { "type": "string", "minLength": 1, "maxLength": 128, "pattern": "^[A-Za-z0-9._:-]{1,128}$" },
    "project_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} },
    "event_key": { "type": "string", "minLength": 54, "maxLength": 54, "pattern": "^publish-completed-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$" },
    "release_id": { "type": "string", "format": "uuid", "minLength": 36, "maxLength": 36, "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", "not": {"enum": ["00000000-0000-0000-0000-000000000000"]} }
  }
}
"""  # noqa: E501 - contract text kept as the file has it
