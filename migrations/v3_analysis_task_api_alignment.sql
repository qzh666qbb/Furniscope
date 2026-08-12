-- Align V2.1-upgraded analysis_tasks defaults with the V3 from-zero DDL.
-- Additive/default-only migration; no business data is deleted.
BEGIN;
SET LOCAL search_path TO furniscope,public;

UPDATE analysis_tasks SET analysis_config='{}'::jsonb WHERE analysis_config IS NULL;
ALTER TABLE analysis_tasks ALTER COLUMN analysis_config SET DEFAULT '{}'::jsonb;
ALTER TABLE analysis_tasks ALTER COLUMN analysis_config SET NOT NULL;
ALTER TABLE analysis_tasks ALTER COLUMN external_stage SET DEFAULT 'understanding_product';
ALTER TABLE analysis_tasks ALTER COLUMN internal_stage SET DEFAULT 'task_initializing';

-- V2.1 result tables were task-scoped but did not physically carry tenant_id.
-- Backfill the V3 isolation key before making it mandatory.
ALTER TABLE competitor_matches ADD COLUMN IF NOT EXISTS tenant_id BIGINT;
UPDATE competitor_matches c SET tenant_id=t.tenant_id
  FROM analysis_tasks t WHERE t.id=c.analysis_job_id AND c.tenant_id IS NULL;
ALTER TABLE competitor_matches ALTER COLUMN tenant_id SET NOT NULL;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_competitor_tenant_v3') THEN
    ALTER TABLE competitor_matches ADD CONSTRAINT fk_competitor_tenant_v3
      FOREIGN KEY(tenant_id) REFERENCES tenants(id);
  END IF;
END $$;

ALTER TABLE insight_clusters ADD COLUMN IF NOT EXISTS tenant_id BIGINT;
UPDATE insight_clusters c SET tenant_id=t.tenant_id
  FROM analysis_tasks t WHERE t.id=c.analysis_job_id AND c.tenant_id IS NULL;
ALTER TABLE insight_clusters ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE insight_clusters ADD COLUMN IF NOT EXISTS cluster_code VARCHAR(64);
UPDATE insight_clusters SET cluster_code=COALESCE(cluster_code,'CL-'||cluster_no::text);
ALTER TABLE insight_clusters ALTER COLUMN cluster_code SET NOT NULL;
ALTER TABLE insight_clusters ADD COLUMN IF NOT EXISTS name VARCHAR(200);
UPDATE insight_clusters SET name=COALESCE(name,cluster_name);
ALTER TABLE insight_clusters ALTER COLUMN name SET NOT NULL;
ALTER TABLE insight_clusters ADD COLUMN IF NOT EXISTS summary TEXT;
UPDATE insight_clusters SET summary=COALESCE(summary,'');
ALTER TABLE insight_clusters ALTER COLUMN summary SET NOT NULL;
ALTER TABLE insight_clusters ADD COLUMN IF NOT EXISTS sentiment_distribution JSONB;
UPDATE insight_clusters SET sentiment_distribution=COALESCE(
  sentiment_distribution,jsonb_build_object(COALESCE(sentiment,'mixed'),1));
ALTER TABLE insight_clusters ALTER COLUMN sentiment_distribution SET NOT NULL;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_cluster_tenant_v3') THEN
    ALTER TABLE insight_clusters ADD CONSTRAINT fk_cluster_tenant_v3
      FOREIGN KEY(tenant_id) REFERENCES tenants(id);
  END IF;
END $$;

ALTER TABLE market_opportunities ADD COLUMN IF NOT EXISTS tenant_id BIGINT;
UPDATE market_opportunities o SET tenant_id=t.tenant_id
  FROM analysis_tasks t WHERE t.id=o.analysis_job_id AND o.tenant_id IS NULL;
ALTER TABLE market_opportunities ALTER COLUMN tenant_id SET NOT NULL;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='fk_opportunity_tenant_v3') THEN
    ALTER TABLE market_opportunities ADD CONSTRAINT fk_opportunity_tenant_v3
      FOREIGN KEY(tenant_id) REFERENCES tenants(id);
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_match_tenant_task_v3
  ON competitor_matches(tenant_id,analysis_job_id);
CREATE INDEX IF NOT EXISTS idx_cluster_tenant_task_v3
  ON insight_clusters(tenant_id,analysis_job_id);
CREATE INDEX IF NOT EXISTS idx_opportunity_tenant_task_v3
  ON market_opportunities(tenant_id,analysis_job_id);

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM analysis_tasks
     WHERE analysis_config IS NULL OR external_stage IS NULL OR internal_stage IS NULL
  ) THEN
    RAISE EXCEPTION 'analysis_tasks V3 API alignment validation failed';
  END IF;
END;
$$;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM competitor_matches c JOIN analysis_tasks t ON t.id=c.analysis_job_id
     WHERE c.tenant_id<>t.tenant_id
  ) OR EXISTS (
    SELECT 1 FROM insight_clusters c JOIN analysis_tasks t ON t.id=c.analysis_job_id
     WHERE c.tenant_id<>t.tenant_id
  ) OR EXISTS (
    SELECT 1 FROM market_opportunities o JOIN analysis_tasks t ON t.id=o.analysis_job_id
     WHERE o.tenant_id<>t.tenant_id
  ) THEN
    RAISE EXCEPTION 'result table tenant backfill validation failed';
  END IF;
END;
$$;

COMMIT;
