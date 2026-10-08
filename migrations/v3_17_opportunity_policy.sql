\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;
CREATE TABLE IF NOT EXISTS enterprise_opportunity_policies (
  id BIGSERIAL PRIMARY KEY,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  version INTEGER NOT NULL CHECK(version>0),
  config JSONB NOT NULL CHECK(jsonb_typeof(config)='object'),
  created_by BIGINT NOT NULL REFERENCES users(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(tenant_id,version),
  FOREIGN KEY(created_by,tenant_id) REFERENCES users(id,tenant_id)
);
ALTER TABLE market_opportunities ADD COLUMN IF NOT EXISTS market_score NUMERIC(5,2);
ALTER TABLE market_opportunities ADD COLUMN IF NOT EXISTS adjusted_score NUMERIC(5,2);
ALTER TABLE market_opportunities ADD COLUMN IF NOT EXISTS policy_snapshot JSONB;
ALTER TABLE market_opportunities ALTER COLUMN enterprise_fit_score DROP NOT NULL;
ALTER TABLE market_opportunities ALTER COLUMN unmet_need_score DROP NOT NULL;
DO $$ BEGIN
  ALTER TABLE market_opportunities ADD CONSTRAINT uk_opportunity_task_tenant UNIQUE(id,analysis_job_id,tenant_id);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL; END $$;
CREATE TABLE IF NOT EXISTS opportunity_feedback_events (
  id BIGSERIAL PRIMARY KEY,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  opportunity_id BIGINT NOT NULL,
  analysis_job_id BIGINT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision>0),
  status VARCHAR(24) NOT NULL CHECK(status IN ('accepted','rejected','pending_validation')),
  reason TEXT NOT NULL CHECK(length(reason) BETWEEN 1 AND 2000),
  score_snapshot JSONB NOT NULL CHECK(jsonb_typeof(score_snapshot)='object'),
  created_by BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(opportunity_id,revision),
  FOREIGN KEY(opportunity_id,analysis_job_id,tenant_id) REFERENCES market_opportunities(id,analysis_job_id,tenant_id),
  FOREIGN KEY(created_by,tenant_id) REFERENCES users(id,tenant_id)
);
CREATE INDEX IF NOT EXISTS idx_feedback_tenant ON opportunity_feedback_events(tenant_id,id);
CREATE OR REPLACE FUNCTION guard_decision_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Decision history is immutable' USING ERRCODE='23514';
END $$;
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['enterprise_opportunity_policies','opportunity_feedback_events'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_scope ON %I',t);
    EXECUTE format('CREATE POLICY tenant_scope ON %I TO furniscope_tenant
      USING(tenant_id=furniscope.current_tenant_id())
      WITH CHECK(tenant_id=furniscope.current_tenant_id())',t);
    EXECUTE format('GRANT SELECT,INSERT ON %I TO furniscope_tenant',t);
    EXECUTE format('DROP TRIGGER IF EXISTS trg_decision_history ON %I',t);
    EXECUTE format('CREATE TRIGGER trg_decision_history BEFORE UPDATE OR DELETE ON %I
      FOR EACH ROW EXECUTE FUNCTION guard_decision_history()',t);
  END LOOP;
END $$;
GRANT USAGE,SELECT ON SEQUENCE enterprise_opportunity_policies_id_seq,opportunity_feedback_events_id_seq TO furniscope_tenant;
CREATE OR REPLACE FUNCTION guard_task_decision_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.enterprise_profile_snapshot ? 'opportunity_policy'
     AND (NEW.enterprise_profile_snapshot IS DISTINCT FROM OLD.enterprise_profile_snapshot
          OR NEW.enterprise_profile_version IS DISTINCT FROM OLD.enterprise_profile_version) THEN
    RAISE EXCEPTION 'Task decision snapshot is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_task_decision_snapshot ON analysis_tasks;
CREATE TRIGGER trg_task_decision_snapshot BEFORE UPDATE ON analysis_tasks
  FOR EACH ROW EXECUTE FUNCTION guard_task_decision_snapshot();
INSERT INTO schema_migrations(version) VALUES('v3_17_opportunity_policy') ON CONFLICT DO NOTHING;
COMMIT;
