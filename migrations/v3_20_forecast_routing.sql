\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;
ALTER TABLE forecast_jobs ADD COLUMN IF NOT EXISTS routing_snapshot JSONB;
COMMENT ON COLUMN forecast_jobs.routing_snapshot IS
  'SKU and reference bindings captured atomically with deployment selection at queue time';
CREATE OR REPLACE FUNCTION guard_forecast_routing() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.routing_snapshot IS NOT NULL AND
     (NEW.routing_snapshot,NEW.model_id,NEW.deployment_id) IS DISTINCT FROM
     (OLD.routing_snapshot,OLD.model_id,OLD.deployment_id) THEN
    RAISE EXCEPTION 'Queued forecast routing is immutable';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_forecast_routing ON forecast_jobs;
CREATE TRIGGER trg_forecast_routing BEFORE UPDATE ON forecast_jobs
  FOR EACH ROW EXECUTE FUNCTION guard_forecast_routing();
INSERT INTO schema_migrations(version) VALUES('v3_20_forecast_routing') ON CONFLICT DO NOTHING;
COMMIT;
