\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;

ALTER TABLE opportunity_outcome_events
  ADD COLUMN IF NOT EXISTS operational_metrics JSONB;

ALTER TABLE opportunity_outcome_events
  DROP CONSTRAINT IF EXISTS chk_outcome_operational_metrics;
ALTER TABLE opportunity_outcome_events
  ADD CONSTRAINT chk_outcome_operational_metrics CHECK(
    operational_metrics IS NULL OR (
      status<>'planned'
      AND jsonb_typeof(operational_metrics)='object'
      AND jsonb_typeof(operational_metrics->'sample_units')='number'
      AND jsonb_typeof(operational_metrics->'production_units')='number'
      AND jsonb_typeof(operational_metrics->'sold_units')='number'
      AND jsonb_typeof(operational_metrics->'returned_units')='number'
      AND (operational_metrics->>'sample_units')::numeric>=0
      AND (operational_metrics->>'production_units')::numeric>=0
      AND (operational_metrics->>'sold_units')::numeric>=0
      AND (operational_metrics->>'returned_units')::numeric>=0
      AND (operational_metrics->>'sample_units')::numeric
            =trunc((operational_metrics->>'sample_units')::numeric)
      AND (operational_metrics->>'production_units')::numeric
            =trunc((operational_metrics->>'production_units')::numeric)
      AND (operational_metrics->>'sold_units')::numeric
            =trunc((operational_metrics->>'sold_units')::numeric)
      AND (operational_metrics->>'returned_units')::numeric
            =trunc((operational_metrics->>'returned_units')::numeric)
      AND (operational_metrics->>'sold_units')::numeric
            <=(operational_metrics->>'production_units')::numeric
      AND (operational_metrics->>'returned_units')::numeric
            <=(operational_metrics->>'sold_units')::numeric
    )
  );

COMMENT ON COLUMN opportunity_outcome_events.operational_metrics IS
  'Enterprise-reported sample, production, sold and returned units. Derived return rate is observational, not causal.';

INSERT INTO schema_migrations(version)
VALUES('v3_37_operational_outcomes')
ON CONFLICT DO NOTHING;

COMMIT;
