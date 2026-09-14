\set ON_ERROR_STOP on
BEGIN;
SET search_path TO furniscope, public;

ALTER TABLE reviews ADD COLUMN IF NOT EXISTS reviewer_location VARCHAR(100);
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS sentiment VARCHAR(16);

COMMIT;
