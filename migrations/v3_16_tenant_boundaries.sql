\set ON_ERROR_STOP on
\ir v3_4_competitor_tracking.sql
BEGIN;
SET LOCAL search_path TO furniscope,public;

-- The login remains trusted for authentication and platform scheduling. Business
-- transactions SET LOCAL ROLE to this non-owner, non-bypass role after authentication.
DO $$ BEGIN
  IF NOT EXISTS(SELECT FROM pg_roles WHERE rolname='furniscope_tenant') THEN
    CREATE ROLE furniscope_tenant NOLOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
  IF EXISTS(SELECT FROM pg_roles WHERE rolname='furniscope_tenant'
            AND (rolsuper OR rolbypassrls OR rolcanlogin)) THEN
    RAISE EXCEPTION 'furniscope_tenant must be NOLOGIN NOSUPERUSER NOBYPASSRLS';
  END IF;
  EXECUTE format('GRANT furniscope_tenant TO %I',current_user);
END $$;
GRANT USAGE ON SCHEMA furniscope TO furniscope_tenant;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA furniscope TO furniscope_tenant;

CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS bigint
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('furniscope.tenant_id',true),'')::bigint
$$;

-- All tenant entities get the same fail-closed policy, including future tables
-- if this idempotent migration is replayed. The owner/admin path is deliberate.
DO $$
DECLARE t RECORD;
BEGIN
  FOR t IN SELECT c.table_name FROM information_schema.columns c
    JOIN information_schema.tables b USING(table_schema,table_name)
    WHERE c.table_schema='furniscope' AND c.column_name='tenant_id'
      AND b.table_type='BASE TABLE'
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t.table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_scope ON %I',t.table_name);
    EXECUTE format('CREATE POLICY tenant_scope ON %I TO furniscope_tenant
      USING (tenant_id=furniscope.current_tenant_id())
      WITH CHECK (tenant_id=furniscope.current_tenant_id())',t.table_name);
    EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON %I TO furniscope_tenant',t.table_name);
    IF EXISTS(SELECT FROM information_schema.columns
              WHERE table_schema='furniscope' AND table_name=t.table_name AND column_name='id') THEN
      EXECUTE format('CREATE UNIQUE INDEX IF NOT EXISTS %I ON %I(id,tenant_id)',
                     'uk_boundary_'||t.table_name,t.table_name);
    END IF;
  END LOOP;
END $$;

-- Add compound foreign keys alongside existing cascade/set-null behavior.
-- User actor references can legitimately point to a platform administrator;
-- ownership is enforced on business entities, not on administrative attribution.
DO $$
DECLARE f RECORD;
BEGIN
  FOR f IN
    SELECT c.conname,c.conrelid::regclass child,c.confrelid::regclass parent,
           a.attname child_key,b.attname parent_key
      FROM pg_constraint c
      JOIN pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=c.conkey[1]
      JOIN pg_attribute b ON b.attrelid=c.confrelid AND b.attnum=c.confkey[1]
     WHERE c.contype='f' AND array_length(c.conkey,1)=1
       AND c.connamespace='furniscope'::regnamespace AND b.attname='id'
       AND c.confrelid<>'users'::regclass
       AND EXISTS(SELECT FROM pg_attribute WHERE attrelid=c.conrelid AND attname='tenant_id' AND NOT attisdropped)
       AND EXISTS(SELECT FROM pg_attribute WHERE attrelid=c.confrelid AND attname='tenant_id' AND NOT attisdropped)
  LOOP
    IF NOT EXISTS(SELECT FROM pg_constraint WHERE conrelid=f.child
                  AND conname=left('boundary_'||f.conname,63)) THEN
      EXECUTE format('ALTER TABLE %s ADD CONSTRAINT %I FOREIGN KEY(%I,tenant_id)
        REFERENCES %s(id,tenant_id)',f.child,left('boundary_'||f.conname,63),f.child_key,f.parent);
    END IF;
  END LOOP;
END $$;

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope ON tenants;
CREATE POLICY tenant_scope ON tenants TO furniscope_tenant
  USING(id=furniscope.current_tenant_id());
GRANT SELECT ON tenants,prompt_templates,model_route_configs TO furniscope_tenant;
DO $$ BEGIN
  ALTER TABLE forecast_data_versions ADD CONSTRAINT fk_data_creator_tenant
    FOREIGN KEY(created_by,tenant_id) REFERENCES users(id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE forecast_training_runs ADD CONSTRAINT fk_training_creator_tenant
    FOREIGN KEY(created_by,tenant_id) REFERENCES users(id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE forecast_models ADD CONSTRAINT chk_model_owner_scope CHECK(
    (model_scope='shared_base' AND owner_tenant_id IS NULL) OR
    (model_scope='tenant_private' AND owner_tenant_id IS NOT NULL));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
ALTER TABLE forecast_models ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_model_read ON forecast_models;
CREATE POLICY tenant_model_read ON forecast_models FOR SELECT TO furniscope_tenant
  USING(model_scope='shared_base' OR owner_tenant_id=furniscope.current_tenant_id());
DROP POLICY IF EXISTS tenant_model_write ON forecast_models;
CREATE POLICY tenant_model_write ON forecast_models FOR ALL TO furniscope_tenant
  USING(model_scope='tenant_private' AND owner_tenant_id=furniscope.current_tenant_id())
  WITH CHECK(model_scope='tenant_private' AND owner_tenant_id=furniscope.current_tenant_id());
GRANT SELECT,INSERT,UPDATE,DELETE ON forecast_models TO furniscope_tenant;

CREATE OR REPLACE FUNCTION guard_model_owner() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.owner_tenant_id,NEW.model_scope) IS DISTINCT FROM (OLD.owner_tenant_id,OLD.model_scope) THEN
    RAISE EXCEPTION 'Model ownership is immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_model_owner ON forecast_models;
CREATE TRIGGER trg_model_owner BEFORE UPDATE ON forecast_models
  FOR EACH ROW EXECUTE FUNCTION guard_model_owner();

CREATE OR REPLACE FUNCTION guard_tenant_model_reference() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE mid bigint; m RECORD;
BEGIN
  mid := (to_jsonb(NEW)->>TG_ARGV[0])::bigint;
  IF mid IS NULL THEN RETURN NEW; END IF;
  SELECT owner_tenant_id,model_scope INTO m FROM forecast_models WHERE id=mid;
  IF NOT FOUND OR (m.model_scope='tenant_private' AND m.owner_tenant_id<>NEW.tenant_id)
     OR (TG_TABLE_NAME='forecast_training_runs' AND m.model_scope<>'tenant_private') THEN
    RAISE EXCEPTION 'Model does not belong to tenant' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
DO $$
DECLARE t text; col text;
BEGIN
  FOREACH t IN ARRAY ARRAY['forecast_model_deployments','forecast_jobs','forecast_runs','forecast_training_runs'] LOOP
    col := CASE WHEN t='forecast_training_runs' THEN 'artifact_model_id' ELSE 'model_id' END;
    EXECUTE format('DROP TRIGGER IF EXISTS trg_tenant_model ON %I',t);
    EXECUTE format('CREATE TRIGGER trg_tenant_model BEFORE INSERT OR UPDATE ON %I
      FOR EACH ROW EXECUTE FUNCTION guard_tenant_model_reference(%L)',t,col);
    IF t='forecast_training_runs' THEN
      EXECUTE 'SELECT 1 FROM forecast_training_runs r JOIN forecast_models m ON m.id=r.artifact_model_id
        WHERE m.model_scope<>''tenant_private'' OR m.owner_tenant_id<>r.tenant_id LIMIT 1' INTO col;
    ELSE
      EXECUTE format('SELECT 1 FROM %I r JOIN forecast_models m ON m.id=r.model_id
        WHERE m.model_scope=''tenant_private'' AND m.owner_tenant_id<>r.tenant_id LIMIT 1',t) INTO col;
    END IF;
    IF col IS NOT NULL THEN
      RAISE EXCEPTION 'Existing cross-tenant model reference in %. Reconcile before migration.',t;
    END IF;
  END LOOP;
END $$;
DO $$ BEGIN
  ALTER TABLE forecast_model_deployments ADD CONSTRAINT uk_deployment_model_tenant UNIQUE(id,model_id,tenant_id);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE forecast_jobs ADD CONSTRAINT fk_job_deployment_model
    FOREIGN KEY(deployment_id,model_id,tenant_id) REFERENCES forecast_model_deployments(id,model_id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE forecast_runs ADD CONSTRAINT fk_run_deployment_model
    FOREIGN KEY(deployment_id,model_id,tenant_id) REFERENCES forecast_model_deployments(id,model_id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE forecast_runs ADD CONSTRAINT uk_run_job_tenant UNIQUE(id,job_id,tenant_id);
EXCEPTION WHEN duplicate_object OR duplicate_table THEN NULL; END $$;
DO $$ BEGIN
  ALTER TABLE forecast_results ADD CONSTRAINT fk_result_run_job
    FOREIGN KEY(run_id,job_id,tenant_id) REFERENCES forecast_runs(id,job_id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- The junction has no tenant column. Derive access from BOTH parents.
ALTER TABLE cluster_members ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope ON cluster_members;
CREATE POLICY tenant_scope ON cluster_members TO furniscope_tenant
  USING(EXISTS(SELECT FROM insight_clusters c JOIN review_aspects a
      ON a.tenant_id=c.tenant_id AND a.analysis_job_id=c.analysis_job_id
      WHERE c.id=cluster_id AND a.id=review_aspect_id AND c.tenant_id=furniscope.current_tenant_id()))
  WITH CHECK(EXISTS(SELECT FROM insight_clusters c JOIN review_aspects a
      ON a.tenant_id=c.tenant_id AND a.analysis_job_id=c.analysis_job_id
      WHERE c.id=cluster_id AND a.id=review_aspect_id AND c.tenant_id=furniscope.current_tenant_id()));
GRANT SELECT,INSERT,UPDATE,DELETE ON cluster_members TO furniscope_tenant;

INSERT INTO schema_migrations(version) VALUES('v3_16_tenant_boundaries') ON CONFLICT DO NOTHING;
COMMIT;
