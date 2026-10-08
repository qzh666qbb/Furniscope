\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS data_class VARCHAR(16) NOT NULL DEFAULT 'business';

ALTER TABLE tenants DROP CONSTRAINT IF EXISTS chk_tenant_data_class;
ALTER TABLE tenants ADD CONSTRAINT chk_tenant_data_class
  CHECK(data_class IN ('business','test','demo'));

CREATE INDEX IF NOT EXISTS idx_tenants_admin_business
  ON tenants(status,updated_at DESC,id)
  WHERE data_class='business';

COMMENT ON COLUMN tenants.data_class IS
  'Explicit administrative visibility class. Platform business lists include business only.';

INSERT INTO schema_migrations(version)
VALUES('v3_33_tenant_data_class')
ON CONFLICT DO NOTHING;

COMMIT;
