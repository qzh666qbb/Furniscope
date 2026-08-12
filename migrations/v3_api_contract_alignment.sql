\set ON_ERROR_STOP on
BEGIN;
SET search_path TO furniscope, public;

-- Align profiles with Data Dictionary V3 while preserving every existing row.
ALTER TABLE product_profile_versions DROP CONSTRAINT IF EXISTS chk_product_profile_status;
ALTER TABLE product_profile_versions DROP CONSTRAINT IF EXISTS chk_profile_status;
UPDATE product_profile_versions SET status='parsed' WHERE status='needs_confirmation';
ALTER TABLE product_profile_versions ADD CONSTRAINT chk_profile_status
  CHECK (status IN ('draft','parsed','confirmed','superseded'));

-- Convert the legacy wide attribute table to the V3 profile-version projection.
ALTER TABLE product_attributes ADD COLUMN IF NOT EXISTS value JSONB;
UPDATE product_attributes a
   SET profile_version_id=pv.id,
       value=COALESCE(a.attribute_value,
                      CASE WHEN a.raw_value IS NULL THEN 'null'::jsonb ELSE to_jsonb(a.raw_value) END),
       source_type=CASE WHEN a.source_type IN ('confirmed_structured','user_input','document','image','inferred')
                        THEN a.source_type WHEN a.source_type='manual' THEN 'user_input' ELSE 'inferred' END,
       confirmation_status=CASE
         WHEN a.confirmation_status IN ('unconfirmed','confirmed','conflicted','unknown') THEN a.confirmation_status
         WHEN a.confirmation_status IN ('reviewed','accepted') THEN 'confirmed'
         WHEN a.confirmation_status IN ('conflict','rejected') THEN 'conflicted'
         ELSE 'unconfirmed' END
  FROM product_profile_versions pv
 WHERE pv.product_id=a.product_id AND pv.version_no=a.profile_version
   AND a.profile_version_id IS NULL;
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM product_attributes WHERE profile_version_id IS NULL OR value IS NULL) THEN
    RAISE EXCEPTION 'product attribute migration left NULL V3 fields';
  END IF;
END $$;
ALTER TABLE product_attributes ALTER COLUMN profile_version_id SET NOT NULL;
ALTER TABLE product_attributes ALTER COLUMN value SET NOT NULL;
ALTER TABLE product_attributes ALTER COLUMN confirmation_status SET DEFAULT 'unconfirmed';
ALTER TABLE product_attributes DROP CONSTRAINT IF EXISTS uk_product_attr_version_code;
ALTER TABLE product_attributes DROP CONSTRAINT IF EXISTS chk_product_attr_confidence;
ALTER TABLE product_attributes ADD CONSTRAINT uk_product_attribute UNIQUE(profile_version_id,attribute_code);
ALTER TABLE product_attributes ADD CONSTRAINT chk_product_attr_source
  CHECK (source_type IN ('confirmed_structured','user_input','document','image','inferred'));
ALTER TABLE product_attributes ADD CONSTRAINT chk_product_attr_conf
  CHECK (confidence BETWEEN 0 AND 1);
ALTER TABLE product_attributes ADD CONSTRAINT chk_product_attr_confirmation
  CHECK (confirmation_status IN ('unconfirmed','confirmed','conflicted','unknown'));
ALTER TABLE product_attributes DROP COLUMN product_id,DROP COLUMN profile_version,
  DROP COLUMN attribute_name,DROP COLUMN value_type,DROP COLUMN attribute_value,
  DROP COLUMN raw_value,DROP COLUMN is_sensitive;

-- Align parse-job field names and externally documented states.
ALTER TABLE product_parse_jobs DROP CONSTRAINT IF EXISTS chk_parse_jobs_status;
ALTER TABLE product_parse_jobs DROP CONSTRAINT IF EXISTS chk_parse_jobs_stage;
ALTER TABLE product_parse_jobs DROP CONSTRAINT IF EXISTS chk_parse_jobs_counts;
UPDATE product_parse_jobs SET status=CASE status WHEN 'processing' THEN 'running'
  WHEN 'partial' THEN 'partial_succeeded' WHEN 'cancelled' THEN 'failed' ELSE status END;
UPDATE product_parse_jobs SET current_stage='queued' WHERE status='queued';
ALTER TABLE product_parse_jobs RENAME COLUMN succeeded_count TO succeeded_file_count;
ALTER TABLE product_parse_jobs RENAME COLUMN failed_count TO failed_file_count;
ALTER TABLE product_parse_jobs RENAME COLUMN requested_by TO created_by;
ALTER TABLE product_parse_jobs DROP COLUMN profile_version,DROP COLUMN extracted_attribute_count,DROP COLUMN conflict_count;
ALTER TABLE product_parse_jobs ALTER COLUMN current_stage SET DEFAULT 'queued';
ALTER TABLE product_parse_jobs ALTER COLUMN parse_job_id TYPE UUID USING parse_job_id::uuid;
ALTER TABLE product_parse_jobs ALTER COLUMN parse_job_id SET DEFAULT gen_random_uuid();
ALTER TABLE product_parse_jobs ADD CONSTRAINT chk_parse_status
  CHECK (status IN ('queued','running','partial_succeeded','succeeded','failed'));
ALTER TABLE product_parse_jobs ADD CONSTRAINT chk_parse_progress CHECK(progress_percent BETWEEN 0 AND 100);
ALTER TABLE product_parse_jobs ADD CONSTRAINT chk_parse_counts CHECK(
  file_count>0 AND succeeded_file_count>=0 AND failed_file_count>=0
  AND succeeded_file_count+failed_file_count<=file_count);

-- File-level result receives the tenant boundary required by every repository query.
ALTER TABLE product_parse_job_files ADD COLUMN IF NOT EXISTS tenant_id BIGINT;
UPDATE product_parse_job_files f SET tenant_id=j.tenant_id FROM product_parse_jobs j WHERE j.id=f.parse_job_id;
ALTER TABLE product_parse_job_files ALTER COLUMN tenant_id SET NOT NULL;
ALTER TABLE product_parse_job_files ADD CONSTRAINT fk_parse_file_tenant FOREIGN KEY(tenant_id) REFERENCES tenants(id);
ALTER TABLE product_parse_job_files DROP COLUMN current_stage,DROP COLUMN extracted_attribute_count;
ALTER TABLE product_parse_job_files ADD CONSTRAINT chk_parse_file_output
  CHECK(output_ref IS NULL OR jsonb_typeof(output_ref)='object');

ALTER TABLE file_assets ADD COLUMN IF NOT EXISTS created_by BIGINT REFERENCES users(id);
ALTER TABLE market_datasets ADD COLUMN IF NOT EXISTS import_asset_id BIGINT REFERENCES file_assets(id);
ALTER TABLE market_datasets ALTER COLUMN quality_report SET NOT NULL;
ALTER TABLE market_datasets ALTER COLUMN limitations SET NOT NULL;

COMMENT ON TABLE product_parse_jobs IS 'V3 persisted asynchronous product-document parsing job.';
COMMENT ON TABLE product_attributes IS 'V3 normalized product attributes keyed by immutable profile version.';

-- Transactional close conditions.
DO $$ BEGIN
  IF EXISTS(SELECT 1 FROM product_profile_versions WHERE status NOT IN ('draft','parsed','confirmed','superseded')) THEN
    RAISE EXCEPTION 'invalid profile status after alignment';
  END IF;
  IF EXISTS(SELECT 1 FROM product_attributes WHERE profile_version_id IS NULL OR value IS NULL) THEN
    RAISE EXCEPTION 'invalid product attribute after alignment';
  END IF;
END $$;
COMMIT;
