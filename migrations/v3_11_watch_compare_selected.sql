\set ON_ERROR_STOP on
BEGIN;
SET search_path TO furniscope, public;

ALTER TABLE competitor_watch_targets
  ADD COLUMN IF NOT EXISTS compare_selected BOOLEAN NOT NULL DEFAULT TRUE;

COMMIT;
