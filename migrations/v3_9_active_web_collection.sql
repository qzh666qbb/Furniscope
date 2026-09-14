\set ON_ERROR_STOP on
BEGIN;
SET search_path TO furniscope, public;

ALTER TABLE authorized_collect_sources DROP CONSTRAINT IF EXISTS chk_collect_source_kind;
ALTER TABLE authorized_collect_sources ADD CONSTRAINT chk_collect_source_kind CHECK (
  source_kind IN ('authorized_market_json','authorized_review_stream','amazon_product_page',
                  'amazon_review_page','web_review_page'));

ALTER TABLE policy_sources
  ADD COLUMN IF NOT EXISTS schedule_minutes INTEGER NOT NULL DEFAULT 1440;
ALTER TABLE policy_sources DROP CONSTRAINT IF EXISTS chk_policy_schedule;
ALTER TABLE policy_sources ADD CONSTRAINT chk_policy_schedule
  CHECK (schedule_minutes BETWEEN 5 AND 10080);

COMMENT ON TABLE authorized_collect_sources IS
  'Tenant-authorized API, push, Amazon and external review web collection sources.';

COMMIT;
