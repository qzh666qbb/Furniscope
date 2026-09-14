\set ON_ERROR_STOP on
BEGIN;
SET search_path TO furniscope, public;

-- Runtime repositories use soft deletion for tenant-scoped product and dataset reads.
ALTER TABLE products ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ(3);
ALTER TABLE market_datasets ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ(3);
CREATE INDEX IF NOT EXISTS idx_products_tenant_active
  ON products(tenant_id, updated_at DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_datasets_tenant_active
  ON market_datasets(tenant_id, updated_at DESC) WHERE deleted_at IS NULL;

COMMIT;
