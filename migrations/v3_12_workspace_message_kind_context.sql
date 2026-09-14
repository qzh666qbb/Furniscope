\set ON_ERROR_STOP on
BEGIN;
SET search_path TO furniscope, public;

ALTER TABLE analysis_workspace_messages DROP CONSTRAINT IF EXISTS chk_workspace_message_kind;
ALTER TABLE analysis_workspace_messages ADD CONSTRAINT chk_workspace_message_kind CHECK (
  message_kind IN ('greeting', 'text', 'run_event', 'task_chat', 'error', 'context')
);

COMMIT;
