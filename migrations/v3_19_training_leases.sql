\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;
ALTER TABLE forecast_training_runs ADD COLUMN IF NOT EXISTS execution_token UUID;
ALTER TABLE forecast_training_runs ADD COLUMN IF NOT EXISTS execution_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE forecast_training_runs ADD COLUMN IF NOT EXISTS heartbeat_at TIMESTAMPTZ;
ALTER TABLE forecast_training_runs ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_training_expired_lease
  ON forecast_training_runs(lease_expires_at) WHERE status='running';
INSERT INTO schema_migrations(version) VALUES('v3_19_training_leases') ON CONFLICT DO NOTHING;
COMMIT;
