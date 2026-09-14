\set ON_ERROR_STOP on
BEGIN;
SET search_path TO furniscope, public;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ(3) NOT NULL DEFAULT now(),
  checksum TEXT
);

INSERT INTO schema_migrations (version) VALUES
  ('v3_14_schema_bootstrap_only')
ON CONFLICT (version) DO NOTHING;

COMMIT;
