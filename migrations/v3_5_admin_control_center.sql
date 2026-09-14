\set ON_ERROR_STOP on
BEGIN;
SET search_path TO furniscope, public;

-- Product-level capabilities enabled for an enterprise tenant. This is deliberately
-- a small entitlement list rather than a dynamic RBAC matrix.
ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS entitlements JSONB NOT NULL DEFAULT '["sales_forecast"]'::jsonb;

DO $$ BEGIN
  ALTER TABLE tenants ADD CONSTRAINT chk_tenant_entitlements_array
    CHECK (jsonb_typeof(entitlements) = 'array');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMIT;
