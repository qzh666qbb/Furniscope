\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;
ALTER TABLE forecast_data_versions ADD COLUMN IF NOT EXISTS auxiliary_sources JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE forecast_data_versions ADD COLUMN IF NOT EXISTS template_snapshot JSONB;
CREATE TABLE IF NOT EXISTS forecast_import_templates (
  id BIGSERIAL PRIMARY KEY,
  template_uuid UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
  tenant_id BIGINT NOT NULL REFERENCES tenants(id),
  created_by BIGINT NOT NULL,
  name VARCHAR(80) NOT NULL,
  revision INTEGER NOT NULL CHECK(revision>0),
  source_version_id BIGINT NOT NULL,
  rules JSONB NOT NULL,
  columns_info JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(id,tenant_id),
  UNIQUE(tenant_id,name,revision),
  FOREIGN KEY(created_by,tenant_id) REFERENCES users(id,tenant_id),
  FOREIGN KEY(source_version_id,tenant_id) REFERENCES forecast_data_versions(id,tenant_id)
);
ALTER TABLE forecast_import_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope ON forecast_import_templates;
CREATE POLICY tenant_scope ON forecast_import_templates TO furniscope_tenant
 USING(tenant_id=furniscope.current_tenant_id())
 WITH CHECK(tenant_id=furniscope.current_tenant_id());
GRANT SELECT,INSERT ON forecast_import_templates TO furniscope_tenant;
GRANT USAGE,SELECT ON SEQUENCE forecast_import_templates_id_seq TO furniscope_tenant;
CREATE OR REPLACE FUNCTION guard_import_template() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Import template revisions are immutable';
END $$;
DROP TRIGGER IF EXISTS trg_import_template ON forecast_import_templates;
CREATE TRIGGER trg_import_template BEFORE UPDATE OR DELETE ON forecast_import_templates
 FOR EACH ROW EXECUTE FUNCTION guard_import_template();
INSERT INTO schema_migrations(version) VALUES('v3_21_import_templates') ON CONFLICT DO NOTHING;
COMMIT;
