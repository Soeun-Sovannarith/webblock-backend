-- project_messaging holds the inbox (this table) and the outbox (V4). Flyway creates the schema (spring.flyway.schemas)
-- as project_migrator; project_ops keeps only Flyway's history table.
REVOKE ALL ON SCHEMA project_messaging FROM PUBLIC;

-- Inbox: one row per consumed RabbitMQ message, written in the transaction of the message's effect.
CREATE TABLE project_messaging.processed_messages (
  message_id   uuid         NOT NULL,
  message_type varchar(100) NOT NULL,
  outcome      varchar(50)  NOT NULL,
  processed_at timestamptz  NOT NULL,
  CONSTRAINT processed_messages_pkey PRIMARY KEY (message_id),
  CONSTRAINT processed_messages_message_type_check CHECK (message_type ~ '^[a-z_]+\.[a-z_]+\.v[0-9]+$'),
  CONSTRAINT processed_messages_outcome_check CHECK (outcome ~ '^[a-z_]+$')
);
