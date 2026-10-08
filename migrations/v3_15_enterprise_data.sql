\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;

CREATE TABLE IF NOT EXISTS forecast_data_versions (
  id BIGSERIAL PRIMARY KEY,
  version_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  created_by BIGINT NOT NULL REFERENCES users(id),
  filename TEXT NOT NULL,
  raw_storage_key TEXT NOT NULL,
  raw_sha256 CHAR(64) NOT NULL,
  rules JSONB NOT NULL DEFAULT '{}'::jsonb,
  quality JSONB NOT NULL DEFAULT '{}'::jsonb,
  columns_info JSONB NOT NULL DEFAULT '[]'::jsonb,
  preview_sha256 CHAR(64),
  canonical_storage_key TEXT,
  canonical_sha256 CHAR(64),
  status VARCHAR(16) NOT NULL DEFAULT 'uploaded'
    CHECK(status IN ('uploaded','previewed','confirmed')),
  parent_version_id BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  confirmed_at TIMESTAMPTZ,
  UNIQUE(id,tenant_id),
  FOREIGN KEY(parent_version_id,tenant_id) REFERENCES forecast_data_versions(id,tenant_id),
  CHECK(status<>'confirmed' OR (canonical_storage_key IS NOT NULL
        AND canonical_sha256 IS NOT NULL AND confirmed_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_forecast_data_tenant ON forecast_data_versions(tenant_id,created_at DESC);
CREATE OR REPLACE FUNCTION guard_confirmed_forecast_data() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status='confirmed' AND NEW IS DISTINCT FROM OLD THEN
    RAISE EXCEPTION 'Confirmed forecast data versions are immutable';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_confirmed_forecast_data ON forecast_data_versions;
CREATE TRIGGER trg_confirmed_forecast_data BEFORE UPDATE ON forecast_data_versions
  FOR EACH ROW EXECUTE FUNCTION guard_confirmed_forecast_data();
ALTER TABLE forecast_training_runs ADD COLUMN IF NOT EXISTS data_version_id BIGINT;
ALTER TABLE forecast_training_runs ADD COLUMN IF NOT EXISTS parent_data_version_id BIGINT;
ALTER TABLE forecast_training_runs ADD COLUMN IF NOT EXISTS baseline_deployment_id BIGINT;
ALTER TABLE forecast_training_runs ADD COLUMN IF NOT EXISTS code_sha256 CHAR(64);

-- Earlier migrations used both unnamed and named CHECKs. Replace only checks
-- governing this column; preserve all other historical constraints.
DO $$
DECLARE c RECORD;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
    WHERE conrelid='forecast_training_runs'::regclass AND contype='c'
      AND (pg_get_constraintdef(oid) LIKE '%update_kind%'
           OR pg_get_constraintdef(oid) LIKE '%status%')
  LOOP EXECUTE format('ALTER TABLE forecast_training_runs DROP CONSTRAINT %I',c.conname); END LOOP;
END $$;
ALTER TABLE forecast_training_runs ADD CONSTRAINT chk_training_mode
  CHECK(update_kind IN ('initial','append','rebuild'));
ALTER TABLE forecast_training_runs ADD CONSTRAINT chk_training_status
  CHECK(status IN ('queued','running','succeeded','failed','cancelled','rejected'));
DO $$ BEGIN
  ALTER TABLE forecast_training_runs ADD CONSTRAINT fk_training_data_tenant
    FOREIGN KEY(data_version_id,tenant_id) REFERENCES forecast_data_versions(id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE forecast_training_runs ADD CONSTRAINT fk_training_parent_tenant
    FOREIGN KEY(parent_data_version_id,tenant_id) REFERENCES forecast_data_versions(id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
CREATE UNIQUE INDEX IF NOT EXISTS uk_training_active_tenant ON forecast_training_runs(tenant_id)
  WHERE status IN ('queued','running');

CREATE TABLE IF NOT EXISTS tenant_forecast_sku_aliases (
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  source_context TEXT NOT NULL,
  product_sku VARCHAR(128) NOT NULL,
  source_sku VARCHAR(128) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(tenant_id,source_context,product_sku),
  UNIQUE(tenant_id,source_context,source_sku)
);
-- Only migrate an already materialized mapping with an explicit source_sku and
-- an active deployment; SKU-name coincidence alone is not ownership evidence.
INSERT INTO tenant_forecast_sku_aliases(tenant_id,source_context,product_sku,source_sku)
SELECT DISTINCT c.tenant_id,m.state_uri,c.sku,c.attributes->>'source_sku'
  FROM tenant_sku_catalog c
  JOIN forecast_model_deployments d ON d.tenant_id=c.tenant_id AND d.status='active'
  JOIN forecast_models m ON m.id=d.model_id
 WHERE NULLIF(c.attributes->>'source_sku','') IS NOT NULL
   AND c.sku<>c.attributes->>'source_sku'
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),checksum TEXT
);
INSERT INTO schema_migrations(version) VALUES('v3_15_enterprise_data') ON CONFLICT DO NOTHING;
COMMIT;
