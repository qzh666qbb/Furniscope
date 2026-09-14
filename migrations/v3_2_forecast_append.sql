-- Tenant-admin weekly forecast data append and retraining jobs.
BEGIN;
SET LOCAL search_path TO furniscope,public;

ALTER TABLE forecast_training_runs
  ADD COLUMN IF NOT EXISTS created_by BIGINT REFERENCES users(id),
  ADD COLUMN IF NOT EXISTS update_kind VARCHAR(16) NOT NULL DEFAULT 'append',
  ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(128),
  ADD COLUMN IF NOT EXISTS input_hash CHAR(64),
  ADD COLUMN IF NOT EXISTS error_code VARCHAR(64),
  ADD COLUMN IF NOT EXISTS error_message TEXT;

DO $$ BEGIN
  ALTER TABLE forecast_training_runs ADD CONSTRAINT chk_forecast_training_update_kind
    CHECK (update_kind IN ('append'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uk_forecast_training_idempotency
  ON forecast_training_runs(tenant_id,idempotency_key)
  WHERE idempotency_key IS NOT NULL;

COMMIT;
