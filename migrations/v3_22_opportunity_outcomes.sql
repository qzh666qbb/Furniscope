\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;

-- No backfill: today's database cannot prove the historical feature state.
ALTER TABLE market_opportunities ADD COLUMN IF NOT EXISTS decision_snapshot JSONB;
ALTER TABLE opportunity_feedback_events ALTER COLUMN created_at SET DEFAULT clock_timestamp();
CREATE OR REPLACE FUNCTION capture_opportunity_decision() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE task_snapshot jsonb;
BEGIN
  IF TG_OP='DELETE' THEN
    IF OLD.decision_snapshot IS NOT NULL THEN
      RAISE EXCEPTION 'Opportunity decision snapshot is immutable' USING ERRCODE='23514';
    END IF;
    RETURN OLD;
  ELSIF TG_OP='INSERT' THEN
    SELECT jsonb_build_object(
      'task_uuid',t.task_uuid,'created_at',t.created_at,'product_id',t.product_id,
      'product_profile_version_id',t.product_profile_version_id,'dataset_id',t.dataset_id,
      'enterprise_profile_snapshot',t.enterprise_profile_snapshot)
      INTO task_snapshot FROM furniscope.analysis_tasks t
      WHERE t.id=NEW.analysis_job_id AND t.tenant_id=NEW.tenant_id;
    NEW.decision_snapshot := jsonb_build_object(
      'format_version','opportunity-features-v2','captured_at',clock_timestamp(),
      'task',task_snapshot,
      'opportunity',to_jsonb(NEW)-ARRAY['decision_snapshot','created_at','updated_at']);
  ELSIF NEW.decision_snapshot IS DISTINCT FROM OLD.decision_snapshot
     OR (OLD.decision_snapshot IS NOT NULL AND
       (to_jsonb(NEW)-ARRAY['updated_at','status','calculated_at']) IS DISTINCT FROM
       (to_jsonb(OLD)-ARRAY['updated_at','status','calculated_at'])) THEN
    RAISE EXCEPTION 'Opportunity decision snapshot is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_opportunity_decision ON market_opportunities;
CREATE TRIGGER trg_opportunity_decision BEFORE INSERT OR UPDATE OR DELETE ON market_opportunities
 FOR EACH ROW EXECUTE FUNCTION capture_opportunity_decision();
DO $$ BEGIN
  ALTER TABLE opportunity_feedback_events ADD CONSTRAINT uk_feedback_opportunity_tenant
    UNIQUE(id,opportunity_id,analysis_job_id,tenant_id);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS opportunity_outcome_events (
  id BIGSERIAL PRIMARY KEY,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  opportunity_id BIGINT NOT NULL,
  analysis_job_id BIGINT NOT NULL,
  accepted_feedback_id BIGINT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision>0),
  status VARCHAR(24) NOT NULL CHECK(status IN ('planned','in_progress','completed','abandoned')),
  implementation_start DATE,
  implementation_end DATE,
  observation_start DATE,
  observation_end DATE,
  result_label VARCHAR(24) CHECK(result_label IN ('achieved','not_achieved','inconclusive')),
  evidence TEXT NOT NULL CHECK(length(btrim(evidence)) BETWEEN 1 AND 4000),
  financials JSONB,
  data_version_id BIGINT,
  sales_snapshot JSONB,
  created_by BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(opportunity_id,revision),
  FOREIGN KEY(opportunity_id,analysis_job_id,tenant_id) REFERENCES market_opportunities(id,analysis_job_id,tenant_id),
  FOREIGN KEY(accepted_feedback_id,opportunity_id,analysis_job_id,tenant_id)
    REFERENCES opportunity_feedback_events(id,opportunity_id,analysis_job_id,tenant_id),
  FOREIGN KEY(data_version_id,tenant_id) REFERENCES forecast_data_versions(id,tenant_id),
  FOREIGN KEY(created_by,tenant_id) REFERENCES users(id,tenant_id),
  CHECK(implementation_end IS NULL OR (implementation_start IS NOT NULL AND implementation_end>=implementation_start)),
  CHECK((observation_start IS NULL AND observation_end IS NULL) OR
    (observation_start IS NOT NULL AND observation_end IS NOT NULL
     AND implementation_start IS NOT NULL AND observation_end>=observation_start
     AND observation_start>=implementation_start)),
  CHECK(status NOT IN ('in_progress','completed') OR implementation_start IS NOT NULL),
  CHECK(status<>'completed' OR (implementation_end IS NOT NULL AND observation_end IS NOT NULL)),
  CHECK(result_label IS NULL OR status='completed'),
  CHECK(financials IS NULL OR (observation_end IS NOT NULL AND jsonb_typeof(financials)='object')),
  CHECK((data_version_id IS NULL AND sales_snapshot IS NULL) OR
        (data_version_id IS NOT NULL AND sales_snapshot IS NOT NULL AND observation_end IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_outcomes_tenant ON opportunity_outcome_events(tenant_id,id);
ALTER TABLE opportunity_outcome_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope ON opportunity_outcome_events;
CREATE POLICY tenant_scope ON opportunity_outcome_events TO furniscope_tenant
 USING(tenant_id=furniscope.current_tenant_id())
 WITH CHECK(tenant_id=furniscope.current_tenant_id());
GRANT SELECT,INSERT ON opportunity_outcome_events TO furniscope_tenant;
GRANT USAGE,SELECT ON SEQUENCE opportunity_outcome_events_id_seq TO furniscope_tenant;
DROP TRIGGER IF EXISTS trg_decision_history ON opportunity_outcome_events;
CREATE TRIGGER trg_decision_history BEFORE UPDATE OR DELETE ON opportunity_outcome_events
 FOR EACH ROW EXECUTE FUNCTION guard_decision_history();

-- Even a missed application check must not attach an outcome to a rejection.
CREATE OR REPLACE FUNCTION guard_outcome_acceptance() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS(SELECT FROM furniscope.opportunity_feedback_events f WHERE f.id=NEW.accepted_feedback_id
      AND f.tenant_id=NEW.tenant_id AND f.opportunity_id=NEW.opportunity_id AND f.status='accepted') THEN
    RAISE EXCEPTION 'Outcome requires an accepted feedback event' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_outcome_acceptance ON opportunity_outcome_events;
CREATE TRIGGER trg_outcome_acceptance BEFORE INSERT ON opportunity_outcome_events
 FOR EACH ROW EXECUTE FUNCTION guard_outcome_acceptance();
INSERT INTO schema_migrations(version) VALUES('v3_22_opportunity_outcomes') ON CONFLICT DO NOTHING;
COMMIT;
