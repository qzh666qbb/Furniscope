-- Existing V3 installations: tenant-aware forecast catalog, sources and deployments.
BEGIN;
SET LOCAL search_path TO furniscope,public;

ALTER TABLE forecast_models ADD COLUMN IF NOT EXISTS owner_tenant_id BIGINT REFERENCES tenants(id);
ALTER TABLE forecast_models ADD COLUMN IF NOT EXISTS model_scope VARCHAR(24) NOT NULL DEFAULT 'shared_base';
DO $$ BEGIN
  ALTER TABLE forecast_models ADD CONSTRAINT chk_forecast_model_scope
    CHECK (model_scope IN ('shared_base','tenant_private'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Definitions match migrations/v3_forecast_integration.sql; copied here so existing
-- databases can upgrade without replaying the baseline migration.
CREATE TABLE IF NOT EXISTS tenant_data_sources (
  id BIGSERIAL PRIMARY KEY, source_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id), name VARCHAR(160) NOT NULL,
  source_kind VARCHAR(32) NOT NULL CHECK (source_kind IN ('sales_history','inventory','product_catalog','market_data')),
  connection_type VARCHAR(24) NOT NULL CHECK (connection_type IN ('upload','api','database','object_storage')),
  secret_ref TEXT, authorization_reference TEXT NOT NULL,
  safe_config JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(safe_config)='object'),
  status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('pending','active','disabled','invalid')),
  created_by BIGINT NOT NULL REFERENCES users(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(tenant_id,name), UNIQUE(id,tenant_id));
CREATE INDEX IF NOT EXISTS idx_tenant_data_sources_tenant ON tenant_data_sources(tenant_id,status,source_kind);
DO $$ BEGIN ALTER TABLE tenant_data_sources ADD CONSTRAINT uk_tenant_data_source_id_tenant
  UNIQUE(id,tenant_id); EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS tenant_sku_catalog (
  id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id), sku VARCHAR(128) NOT NULL,
  site VARCHAR(16) NOT NULL, category_code VARCHAR(64),
  lifecycle_status VARCHAR(24) NOT NULL DEFAULT 'active' CHECK (lifecycle_status IN ('active','out_of_stock','discontinued','unknown')),
  label_status VARCHAR(16) NOT NULL DEFAULT 'unknown' CHECK (label_status IN ('complete','partial','unknown')),
  history_weeks INTEGER NOT NULL DEFAULT 0 CHECK (history_weeks>=0), model_eligible BOOLEAN NOT NULL DEFAULT false,
  source_id BIGINT, attributes JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(attributes)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(tenant_id,sku,site),
  FOREIGN KEY(source_id,tenant_id) REFERENCES tenant_data_sources(id,tenant_id));
CREATE INDEX IF NOT EXISTS idx_tenant_sku_catalog_active ON tenant_sku_catalog(tenant_id,site,sku) WHERE lifecycle_status='active';
DO $$ BEGIN ALTER TABLE tenant_sku_catalog ADD CONSTRAINT fk_tenant_sku_source
  FOREIGN KEY(source_id,tenant_id) REFERENCES tenant_data_sources(id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS forecast_model_deployments (
  id BIGSERIAL PRIMARY KEY, deployment_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id), model_id BIGINT NOT NULL REFERENCES forecast_models(id),
  scenario_code VARCHAR(64) NOT NULL DEFAULT 'sales_forecast',
  status VARCHAR(16) NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive','rollback')),
  route_policy JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(route_policy)='object'),
  deployed_by BIGINT REFERENCES users(id), deployed_at TIMESTAMPTZ NOT NULL DEFAULT now(), retired_at TIMESTAMPTZ,
  UNIQUE(id,tenant_id));
CREATE UNIQUE INDEX IF NOT EXISTS uk_forecast_deployment_active ON forecast_model_deployments(tenant_id,scenario_code) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_forecast_deployment_model ON forecast_model_deployments(model_id,tenant_id);
DO $$ BEGIN ALTER TABLE forecast_model_deployments ADD CONSTRAINT uk_forecast_deployment_id_tenant
  UNIQUE(id,tenant_id); EXCEPTION WHEN duplicate_object THEN NULL; END $$;
ALTER TABLE forecast_jobs ADD COLUMN IF NOT EXISTS deployment_id BIGINT;
ALTER TABLE forecast_runs ADD COLUMN IF NOT EXISTS deployment_id BIGINT;
DO $$ BEGIN ALTER TABLE forecast_jobs ADD CONSTRAINT fk_forecast_job_tenant_deployment
  FOREIGN KEY(deployment_id,tenant_id) REFERENCES forecast_model_deployments(id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN ALTER TABLE forecast_runs ADD CONSTRAINT fk_forecast_run_tenant_deployment
  FOREIGN KEY(deployment_id,tenant_id) REFERENCES forecast_model_deployments(id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS forecast_training_runs (
  id BIGSERIAL PRIMARY KEY, training_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id), source_snapshot JSONB NOT NULL CHECK (jsonb_typeof(source_snapshot)='object'),
  algorithm_version VARCHAR(64) NOT NULL, feature_version VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metrics)='object'), artifact_model_id BIGINT REFERENCES forecast_models(id),
  created_by BIGINT REFERENCES users(id), update_kind VARCHAR(16) NOT NULL DEFAULT 'append' CHECK (update_kind IN ('append')),
  idempotency_key VARCHAR(128), input_hash CHAR(64), error_code VARCHAR(64), error_message TEXT,
  started_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS idx_forecast_training_tenant ON forecast_training_runs(tenant_id,created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uk_forecast_training_idempotency ON forecast_training_runs(tenant_id,idempotency_key) WHERE idempotency_key IS NOT NULL;
COMMIT;
