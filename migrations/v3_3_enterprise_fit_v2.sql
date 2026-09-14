-- Versioned enterprise profile snapshots and explainable opportunity fit confidence.
BEGIN;
SET LOCAL search_path TO furniscope,public;

ALTER TABLE enterprise_profiles
  ADD COLUMN IF NOT EXISTS profile_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE analysis_tasks
  ADD COLUMN IF NOT EXISTS enterprise_profile_version INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS enterprise_profile_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE market_opportunities
  ADD COLUMN IF NOT EXISTS enterprise_fit_confidence NUMERIC(5,4) NOT NULL DEFAULT 0.4;

UPDATE analysis_tasks t
   SET enterprise_profile_version=COALESCE(ep.profile_version,0),
       enterprise_profile_snapshot=jsonb_build_object(
         'profile_version',COALESCE(ep.profile_version,0),
         'captured_at',t.created_at,
         'profile',CASE WHEN ep.id IS NULL THEN NULL ELSE to_jsonb(ep)-'id'-'tenant_id' END,
         'capabilities',COALESCE((
           SELECT jsonb_agg(to_jsonb(mc)-'id'-'tenant_id'-'created_by' ORDER BY mc.capability_type,mc.capability_code)
             FROM manufacturing_capabilities mc WHERE mc.tenant_id=t.tenant_id
         ),'[]'::jsonb)
       )
  FROM (SELECT id AS tenant_id FROM tenants) tenant
  LEFT JOIN enterprise_profiles ep ON ep.tenant_id=tenant.tenant_id
 WHERE tenant.tenant_id=t.tenant_id
   AND t.enterprise_profile_snapshot='{}'::jsonb;

UPDATE analysis_reports r
   SET enterprise_profile_snapshot=t.enterprise_profile_snapshot
  FROM analysis_tasks t
 WHERE t.id=r.analysis_job_id AND t.tenant_id=r.tenant_id
   AND (r.enterprise_profile_snapshot='{}'::jsonb
        OR r.enterprise_profile_snapshot='{"source":"tenant_capabilities"}'::jsonb);

DO $$ BEGIN
  ALTER TABLE analysis_tasks ADD CONSTRAINT chk_task_enterprise_snapshot_v2
    CHECK (jsonb_typeof(enterprise_profile_snapshot)='object' AND enterprise_profile_version>=0);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE enterprise_profiles ADD CONSTRAINT chk_enterprise_profile_version_v2
    CHECK (profile_version>=1);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE market_opportunities ADD CONSTRAINT chk_opportunity_fit_confidence_v2
    CHECK (enterprise_fit_confidence BETWEEN 0 AND 1);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMIT;
