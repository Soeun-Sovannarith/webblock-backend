-- Transactional outbox: the tables of Namastack Outbox 1.7.3 (namastack-outbox-jdbc, schema/postgres/outbox-tables.sql),
-- created here instead of at startup (namastack.outbox.jdbc.schema-initialization.enabled=false) and placed in
-- project_messaging (namastack.outbox.jdbc.schema-name=project_messaging). Columns, types and indexes are the library's; a
-- library upgrade that changes them needs a new migration. Spike S1: docs/spikes/S1-outbox.md.

-- One row per scheduled payload and handler: an integration event for the Rabbit handler, or a local job.
CREATE TABLE project_messaging.outbox_record (
  id             varchar(255)  NOT NULL,
  status         varchar(20)   NOT NULL,
  record_key     varchar(255)  NOT NULL,
  record_type    varchar(255)  NOT NULL,
  payload        text          NOT NULL,
  context        text,
  created_at     timestamptz   NOT NULL,
  completed_at   timestamptz,
  failure_count  integer       NOT NULL,
  failure_reason varchar(1000),
  next_retry_at  timestamptz   NOT NULL,
  partition_no   integer       NOT NULL,
  handler_id     varchar(1000) NOT NULL,
  CONSTRAINT outbox_record_pkey PRIMARY KEY (id)
);

-- The running application instances (heartbeats); used to share the 256 partitions between instances.
CREATE TABLE project_messaging.outbox_instance (
  instance_id    varchar(255) NOT NULL,
  hostname       varchar(255) NOT NULL,
  port           integer      NOT NULL,
  status         varchar(50)  NOT NULL,
  started_at     timestamptz  NOT NULL,
  last_heartbeat timestamptz  NOT NULL,
  created_at     timestamptz  NOT NULL,
  updated_at     timestamptz  NOT NULL,
  CONSTRAINT outbox_instance_pkey PRIMARY KEY (instance_id)
);

-- Which instance owns which partition (optimistic version); a record's partition is a hash of its key.
CREATE TABLE project_messaging.outbox_partition (
  partition_number integer     NOT NULL,
  instance_id      varchar(255),
  version          bigint      NOT NULL DEFAULT 0,
  updated_at       timestamptz NOT NULL,
  CONSTRAINT outbox_partition_pkey PRIMARY KEY (partition_number)
);

CREATE INDEX idx_outbox_record_record_key_created ON project_messaging.outbox_record (record_key, created_at);
CREATE INDEX idx_outbox_record_partition_status_retry ON project_messaging.outbox_record (partition_no, status, next_retry_at);
CREATE INDEX idx_outbox_record_status_retry ON project_messaging.outbox_record (status, next_retry_at);
CREATE INDEX idx_outbox_record_status ON project_messaging.outbox_record (status);
CREATE INDEX idx_outbox_record_record_key_completed_created
  ON project_messaging.outbox_record (record_key, completed_at, created_at);
CREATE INDEX idx_outbox_instance_status_heartbeat ON project_messaging.outbox_instance (status, last_heartbeat);
CREATE INDEX idx_outbox_instance_last_heartbeat ON project_messaging.outbox_instance (last_heartbeat);
CREATE INDEX idx_outbox_instance_status ON project_messaging.outbox_instance (status);
CREATE INDEX idx_outbox_partition_instance_id ON project_messaging.outbox_partition (instance_id);

-- project_app gets only what the library executes (JdbcOutbox*Repository, 1.7.3):
-- records are inserted and updated, never deleted (namastack.outbox.processing.delete-completed-records=false);
-- partitions are inserted and updated; instances are also deleted (stale instances, and its own row at shutdown).
GRANT SELECT, INSERT, UPDATE ON project_messaging.outbox_record, project_messaging.outbox_partition TO project_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON project_messaging.outbox_instance TO project_app;
