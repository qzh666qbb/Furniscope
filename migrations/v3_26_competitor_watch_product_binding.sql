\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;

-- Move product binding out of runtime DDL so fresh installs and upgrades expose
-- the same competitor-watch contract before the API process starts.
ALTER TABLE competitor_watch_targets
  ADD COLUMN IF NOT EXISTS product_id BIGINT,
  ADD COLUMN IF NOT EXISTS match_score NUMERIC(6,4),
  ADD COLUMN IF NOT EXISTS compare_selected BOOLEAN NOT NULL DEFAULT TRUE;

DO $$ BEGIN
  ALTER TABLE competitor_watch_targets
    ADD CONSTRAINT fk_competitor_watch_product
    FOREIGN KEY(product_id,tenant_id) REFERENCES products(id,tenant_id);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE competitor_watch_targets
    ADD CONSTRAINT chk_competitor_watch_match_score
    CHECK(match_score IS NULL OR match_score BETWEEN 0 AND 1);
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE INDEX IF NOT EXISTS idx_competitor_watch_product
  ON competitor_watch_targets(tenant_id,product_id,status,updated_at DESC)
  WHERE product_id IS NOT NULL;

INSERT INTO schema_migrations(version)
VALUES('v3_26_competitor_watch_product_binding')
ON CONFLICT DO NOTHING;
COMMIT;
