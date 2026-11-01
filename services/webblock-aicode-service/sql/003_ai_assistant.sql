-- AI service, design assistant (ERD page "AI"). project_id, user_id and page_id reference other services
-- and have no foreign keys.
CREATE SCHEMA IF NOT EXISTS ai_assistant;

DO $$ BEGIN CREATE TYPE ai_assistant.message_role AS ENUM ('USER','ASSISTANT'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE ai_assistant.change_set_status AS ENUM ('PROPOSED','APPLIED','DISCARDED','REJECTED'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN CREATE TYPE ai_assistant.change_operation AS ENUM ('ADD','UPDATE','REMOVE','MOVE'); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS ai_assistant.conversations (
 conversation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id uuid NOT NULL,
 user_id uuid,
 title varchar(255),
 created_at timestamptz NOT NULL DEFAULT now(),
 last_message_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_conversations_project ON ai_assistant.conversations(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS ai_assistant.messages (
 message_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 conversation_id uuid NOT NULL REFERENCES ai_assistant.conversations(conversation_id) ON DELETE CASCADE,
 message_number integer NOT NULL CHECK(message_number > 0),
 role ai_assistant.message_role NOT NULL,
 content text NOT NULL,
 page_id uuid,
 model_name varchar(255),
 input_tokens integer,
 output_tokens integer,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(conversation_id, message_number)
);

CREATE TABLE IF NOT EXISTS ai_assistant.change_sets (
 change_set_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 conversation_id uuid NOT NULL REFERENCES ai_assistant.conversations(conversation_id) ON DELETE CASCADE,
 message_id uuid NOT NULL UNIQUE REFERENCES ai_assistant.messages(message_id) ON DELETE CASCADE,
 project_id uuid NOT NULL,
 page_id uuid NOT NULL,
 component_catalog_version varchar(100) NOT NULL,
 base_edit_revision bigint NOT NULL,
 status ai_assistant.change_set_status NOT NULL DEFAULT 'PROPOSED',
 rejection_code varchar(100),
 applied_edit_revision bigint,
 created_at timestamptz NOT NULL DEFAULT now(),
 decided_at timestamptz
);

CREATE TABLE IF NOT EXISTS ai_assistant.change_items (
 change_item_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 change_set_id uuid NOT NULL REFERENCES ai_assistant.change_sets(change_set_id) ON DELETE CASCADE,
 item_order integer NOT NULL CHECK(item_order > 0),
 operation ai_assistant.change_operation NOT NULL,
 component_id varchar(255),
 component_type varchar(100),
 position jsonb,
 props_patch jsonb,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(change_set_id, item_order)
);
