BEGIN;

CREATE OR REPLACE FUNCTION furniscope.validate_review_aspect_span()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE source_text TEXT;
BEGIN
  SELECT content_original
    INTO source_text
    FROM furniscope.reviews
   WHERE id=NEW.review_id;
  IF source_text IS NULL OR NEW.evidence_end > char_length(source_text) OR
     substring(source_text FROM NEW.evidence_start+1 FOR NEW.evidence_end-NEW.evidence_start) <> NEW.evidence_quote THEN
    RAISE EXCEPTION 'evidence span/quote does not match protected review original';
  END IF;
  RETURN NEW;
END;
$$;

COMMIT;
