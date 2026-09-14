-- FurniScope V3 additive integration for Sales Forecast V4.
-- Model binaries and feature state remain in trusted object/file storage; the
-- database stores immutable version metadata, requests, runs, and predictions.
BEGIN;
SET LOCAL search_path TO furniscope,public;

CREATE TABLE IF NOT EXISTS forecast_models (
  id BIGSERIAL PRIMARY KEY,
  model_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  model_code VARCHAR(64) NOT NULL,
  owner_tenant_id BIGINT REFERENCES tenants(id),
  model_scope VARCHAR(24) NOT NULL DEFAULT 'shared_base'
    CHECK (model_scope IN ('shared_base','tenant_private')),
  version VARCHAR(64) NOT NULL,
  engine VARCHAR(32) NOT NULL DEFAULT 'xgboost_lightgbm_v4',
  state_uri TEXT NOT NULL,
  state_checksum CHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'active'
    CHECK (status IN ('draft','active','retired','invalid')),
  supported_granularities JSONB NOT NULL DEFAULT '["day","week"]'::jsonb
    CHECK (jsonb_typeof(supported_granularities)='array'),
  training_data_through DATE,
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metrics)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(model_code,version,state_checksum)
);

CREATE TABLE IF NOT EXISTS tenant_data_sources (
  id BIGSERIAL PRIMARY KEY,
  source_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  name VARCHAR(160) NOT NULL,
  source_kind VARCHAR(32) NOT NULL
    CHECK (source_kind IN ('sales_history','inventory','product_catalog','market_data')),
  connection_type VARCHAR(24) NOT NULL
    CHECK (connection_type IN ('upload','api','database','object_storage')),
  secret_ref TEXT,
  authorization_reference TEXT NOT NULL,
  safe_config JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(safe_config)='object'),
  status VARCHAR(16) NOT NULL DEFAULT 'active'
    CHECK (status IN ('pending','active','disabled','invalid')),
  created_by BIGINT NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,name),
  UNIQUE(id,tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_tenant_data_sources_tenant
  ON tenant_data_sources(tenant_id,status,source_kind);

CREATE TABLE IF NOT EXISTS tenant_sku_catalog (
  id BIGSERIAL PRIMARY KEY,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  sku VARCHAR(128) NOT NULL,
  site VARCHAR(16) NOT NULL,
  category_code VARCHAR(64),
  lifecycle_status VARCHAR(24) NOT NULL DEFAULT 'active'
    CHECK (lifecycle_status IN ('active','out_of_stock','discontinued','unknown')),
  label_status VARCHAR(16) NOT NULL DEFAULT 'unknown'
    CHECK (label_status IN ('complete','partial','unknown')),
  history_weeks INTEGER NOT NULL DEFAULT 0 CHECK (history_weeks>=0),
  model_eligible BOOLEAN NOT NULL DEFAULT false,
  source_id BIGINT,
  attributes JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(attributes)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,sku,site),
  FOREIGN KEY(source_id,tenant_id) REFERENCES tenant_data_sources(id,tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_tenant_sku_catalog_active
  ON tenant_sku_catalog(tenant_id,site,sku) WHERE lifecycle_status='active';

CREATE TABLE IF NOT EXISTS forecast_model_deployments (
  id BIGSERIAL PRIMARY KEY,
  deployment_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  model_id BIGINT NOT NULL REFERENCES forecast_models(id),
  scenario_code VARCHAR(64) NOT NULL DEFAULT 'sales_forecast',
  status VARCHAR(16) NOT NULL DEFAULT 'active'
    CHECK (status IN ('active','inactive','rollback')),
  route_policy JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(route_policy)='object'),
  deployed_by BIGINT REFERENCES users(id),
  deployed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  retired_at TIMESTAMPTZ
  ,UNIQUE(id,tenant_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS uk_forecast_deployment_active
  ON forecast_model_deployments(tenant_id,scenario_code) WHERE status='active';
CREATE INDEX IF NOT EXISTS idx_forecast_deployment_model
  ON forecast_model_deployments(model_id,tenant_id);

CREATE TABLE IF NOT EXISTS forecast_training_runs (
  id BIGSERIAL PRIMARY KEY,
  training_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  source_snapshot JSONB NOT NULL CHECK (jsonb_typeof(source_snapshot)='object'),
  algorithm_version VARCHAR(64) NOT NULL,
  feature_version VARCHAR(64) NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','running','succeeded','failed','cancelled')),
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metrics)='object'),
  artifact_model_id BIGINT REFERENCES forecast_models(id),
  created_by BIGINT REFERENCES users(id),
  update_kind VARCHAR(16) NOT NULL DEFAULT 'append' CHECK (update_kind IN ('append')),
  idempotency_key VARCHAR(128), input_hash CHAR(64),
  error_code VARCHAR(64), error_message TEXT,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_forecast_training_tenant
  ON forecast_training_runs(tenant_id,created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uk_forecast_training_idempotency
  ON forecast_training_runs(tenant_id,idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS forecast_jobs (
  id BIGSERIAL PRIMARY KEY,
  job_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  created_by BIGINT NOT NULL REFERENCES users(id),
  product_id BIGINT REFERENCES products(id),
  analysis_task_id BIGINT REFERENCES analysis_tasks(id),
  model_id BIGINT REFERENCES forecast_models(id),
  deployment_id BIGINT,
  job_name VARCHAR(200) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'draft'
    CHECK (status IN ('draft','queued','running','succeeded','failed','cancelled')),
  granularity VARCHAR(8) NOT NULL CHECK (granularity IN ('day','week')),
  horizon INTEGER NOT NULL CHECK (horizon BETWEEN 1 AND 365),
  start_date DATE,
  skus JSONB NOT NULL CHECK (jsonb_typeof(skus)='array' AND jsonb_array_length(skus)>0),
  sites JSONB NOT NULL CHECK (jsonb_typeof(sites)='array' AND jsonb_array_length(sites)>0),
  scenario_config JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(scenario_config)='object'),
  input_hash CHAR(64) NOT NULL,
  idempotency_key VARCHAR(128) NOT NULL,
  progress_percent NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (progress_percent BETWEEN 0 AND 100),
  failure_code VARCHAR(64),
  failure_message TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,idempotency_key),
  CONSTRAINT fk_forecast_job_tenant_deployment FOREIGN KEY(deployment_id,tenant_id)
    REFERENCES forecast_model_deployments(id,tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_forecast_jobs_tenant_time
  ON forecast_jobs(tenant_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_forecast_jobs_analysis
  ON forecast_jobs(tenant_id,analysis_task_id) WHERE analysis_task_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS forecast_runs (
  id BIGSERIAL PRIMARY KEY,
  run_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  job_id BIGINT NOT NULL REFERENCES forecast_jobs(id) ON DELETE CASCADE,
  model_id BIGINT NOT NULL REFERENCES forecast_models(id),
  deployment_id BIGINT,
  input_hash CHAR(64) NOT NULL,
  status VARCHAR(24) NOT NULL DEFAULT 'running'
    CHECK (status IN ('running','succeeded','failed')),
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metrics)='object'),
  error_code VARCHAR(64),
  error_message TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  CONSTRAINT fk_forecast_run_tenant_deployment FOREIGN KEY(deployment_id,tenant_id)
    REFERENCES forecast_model_deployments(id,tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_forecast_runs_job ON forecast_runs(tenant_id,job_id,started_at DESC);

CREATE TABLE IF NOT EXISTS forecast_results (
  id BIGSERIAL PRIMARY KEY,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  job_id BIGINT NOT NULL REFERENCES forecast_jobs(id) ON DELETE CASCADE,
  run_id BIGINT NOT NULL REFERENCES forecast_runs(id) ON DELETE CASCADE,
  sku VARCHAR(128) NOT NULL,
  site VARCHAR(16) NOT NULL,
  bucket_start DATE NOT NULL,
  bucket_end DATE NOT NULL,
  predicted_sales NUMERIC(18,4) NOT NULL CHECK (predicted_sales>=0),
  lower_bound NUMERIC(18,4) CHECK (lower_bound IS NULL OR lower_bound>=0),
  upper_bound NUMERIC(18,4) CHECK (upper_bound IS NULL OR upper_bound>=0),
  reliability CHAR(1) NOT NULL CHECK (reliability IN ('A','B','C','D')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(run_id,sku,site,bucket_start)
);
CREATE INDEX IF NOT EXISTS idx_forecast_results_job
  ON forecast_results(tenant_id,job_id,sku,site,bucket_start);

COMMIT;
