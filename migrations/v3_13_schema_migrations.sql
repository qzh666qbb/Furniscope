\set ON_ERROR_STOP on
BEGIN;
SET search_path TO furniscope, public;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version TEXT PRIMARY KEY,
  applied_at TIMESTAMPTZ(3) NOT NULL DEFAULT now(),
  checksum TEXT
);

INSERT INTO schema_migrations (version) VALUES
  ('v2_1_to_v3'),
  ('v3_analysis_task_api_alignment'),
  ('v3_api_contract_alignment'),
  ('v3_runtime_alignment'),
  ('v3_evidence_trigger_schema_fix'),
  ('v3_forecast_integration'),
  ('v3_1_tenant_forecast'),
  ('v3_2_forecast_append'),
  ('v3_3_enterprise_fit_v2'),
  ('v3_3_market_dataset_preview'),
  ('v3_4_competitor_tracking'),
  ('v3_5_admin_control_center'),
  ('v3_6_registration_applications'),
  ('v3_7_authorized_market_signals'),
  ('v3_8_alert_notifications'),
  ('v3_9_active_web_collection'),
  ('v3_10_analysis_workspaces'),
  ('v3_11_forecast_sku_aliases'),
  ('v3_11_watch_compare_selected'),
  ('v3_12_workspace_message_kind_context'),
  ('v3_13_schema_migrations')
ON CONFLICT (version) DO NOTHING;

COMMIT;
