-- A new website's business profile waits here, never in an outbox row (outbox rows are kept after completion), until
-- the catalog registration job has written it to the catalog; the job then deletes the row, so no personal data stays
-- behind. Operational storage beside the inbox and outbox (spec 4.4), not a revision 19 business table.
CREATE TABLE project_messaging.pending_catalog_registrations (
  project_id       uuid        NOT NULL,
  business_profile jsonb       NOT NULL,
  created_at       timestamptz NOT NULL,
  CONSTRAINT pending_catalog_registrations_pkey PRIMARY KEY (project_id),
  CONSTRAINT pending_catalog_registrations_profile_object CHECK (jsonb_typeof(business_profile) = 'object')
);

GRANT SELECT, INSERT, DELETE ON project_messaging.pending_catalog_registrations TO project_app;
