\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;

CREATE TABLE IF NOT EXISTS data_metric_catalog (
  metric_code VARCHAR(64) PRIMARY KEY,
  display_name VARCHAR(120) NOT NULL,
  description VARCHAR(500) NOT NULL,
  source_kind VARCHAR(16) NOT NULL,
  value_type VARCHAR(16) NOT NULL,
  unit VARCHAR(32),
  expression_key VARCHAR(64) NOT NULL UNIQUE,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT chk_data_metric_source CHECK(source_kind IN ('sales','inventory')),
  CONSTRAINT chk_data_metric_value_type CHECK(
    value_type IN ('decimal','integer','currency')
  )
);

INSERT INTO data_metric_catalog(
  metric_code,display_name,description,source_kind,value_type,unit,expression_key
) VALUES
  ('sales_units','销量','筛选范围内确认口径的销量合计','sales','decimal','件','sum_sales_units'),
  ('average_daily_sales','日均销量','销量合计除以有记录的自然日数','sales','decimal','件/日','average_daily_sales'),
  ('sales_revenue','销售额','仅汇总具有明确成交单价的销量行，不进行汇率换算','sales','currency',NULL,'sum_known_revenue'),
  ('average_selling_price','加权成交价','仅按具有明确成交单价的销量加权计算','sales','currency',NULL,'weighted_known_price'),
  ('active_days','销售天数','筛选范围内有销量记录的自然日数','sales','integer','天','count_sales_days'),
  ('sku_count','SKU 数','筛选范围内不同 SKU 数量','sales','integer','个','count_sales_skus'),
  ('site_count','站点数','筛选范围内不同站点数量','sales','integer','个','count_sales_sites'),
  ('inventory_units','期末库存','筛选范围内最新日期的库存快照','inventory','decimal','件','latest_inventory_units'),
  ('average_inventory','平均库存','筛选范围内库存快照的算术平均值','inventory','decimal','件','average_inventory_units'),
  ('stockout_days','缺货天数','库存快照等于零的不同自然日数','inventory','integer','天','count_stockout_days')
ON CONFLICT(metric_code) DO UPDATE SET
  display_name=EXCLUDED.display_name,
  description=EXCLUDED.description,
  source_kind=EXCLUDED.source_kind,
  value_type=EXCLUDED.value_type,
  unit=EXCLUDED.unit,
  expression_key=EXCLUDED.expression_key,
  is_active=TRUE,
  updated_at=CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS data_fact_projections (
  tenant_id BIGINT NOT NULL,
  data_version_id BIGINT NOT NULL,
  source_kind VARCHAR(16) NOT NULL,
  canonical_sha256 CHAR(64) NOT NULL,
  projection_sha256 CHAR(64) NOT NULL,
  sales_row_count INTEGER NOT NULL DEFAULT 0,
  inventory_row_count INTEGER NOT NULL DEFAULT 0,
  projected_at TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(tenant_id,data_version_id),
  FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
  FOREIGN KEY(data_version_id,tenant_id)
    REFERENCES forecast_data_versions(id,tenant_id) ON DELETE CASCADE,
  CONSTRAINT chk_fact_projection_kind CHECK(source_kind IN ('sales','inventory')),
  CONSTRAINT chk_fact_projection_hashes CHECK(
    canonical_sha256 ~ '^[0-9a-f]{64}$'
    AND projection_sha256 ~ '^[0-9a-f]{64}$'
  ),
  CONSTRAINT chk_fact_projection_counts CHECK(
    sales_row_count>=0 AND inventory_row_count>=0
    AND sales_row_count+inventory_row_count>0
  )
);

CREATE TABLE IF NOT EXISTS sales_facts_daily (
  tenant_id BIGINT NOT NULL,
  data_version_id BIGINT NOT NULL,
  fact_date DATE NOT NULL,
  sku VARCHAR(128) NOT NULL,
  site VARCHAR(16) NOT NULL,
  sales_units NUMERIC(24,6) NOT NULL,
  sales_status VARCHAR(24) NOT NULL,
  unit_price NUMERIC(24,6),
  discount NUMERIC(8,7),
  currency CHAR(3),
  source_record_sha256 CHAR(64) NOT NULL,
  PRIMARY KEY(tenant_id,data_version_id,fact_date,sku,site),
  FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
  FOREIGN KEY(data_version_id,tenant_id)
    REFERENCES forecast_data_versions(id,tenant_id) ON DELETE CASCADE,
  CONSTRAINT chk_sales_fact_status CHECK(
    sales_status IN ('active','out_of_stock','discontinued','unknown')
  ),
  CONSTRAINT chk_sales_fact_values CHECK(
    unit_price IS NULL OR unit_price>=0
  ),
  CONSTRAINT chk_sales_fact_discount CHECK(
    discount IS NULL OR discount BETWEEN 0 AND 1
  ),
  CONSTRAINT chk_sales_fact_currency CHECK(
    (unit_price IS NULL OR currency IS NOT NULL)
    AND (currency IS NULL OR currency ~ '^[A-Z]{3}$')
  ),
  CONSTRAINT chk_sales_fact_hash CHECK(
    source_record_sha256 ~ '^[0-9a-f]{64}$'
  )
);
CREATE INDEX IF NOT EXISTS idx_sales_facts_tenant_date
  ON sales_facts_daily(tenant_id,fact_date,sku,site);
CREATE INDEX IF NOT EXISTS idx_sales_facts_tenant_sku_date
  ON sales_facts_daily(tenant_id,sku,fact_date,site);

CREATE TABLE IF NOT EXISTS inventory_facts_daily (
  tenant_id BIGINT NOT NULL,
  data_version_id BIGINT NOT NULL,
  fact_date DATE NOT NULL,
  sku VARCHAR(128) NOT NULL,
  site VARCHAR(16) NOT NULL,
  inventory_units NUMERIC(24,6) NOT NULL,
  source_record_sha256 CHAR(64) NOT NULL,
  PRIMARY KEY(tenant_id,data_version_id,fact_date,sku,site),
  FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
  FOREIGN KEY(data_version_id,tenant_id)
    REFERENCES forecast_data_versions(id,tenant_id) ON DELETE CASCADE,
  CONSTRAINT chk_inventory_fact_value CHECK(inventory_units>=0),
  CONSTRAINT chk_inventory_fact_hash CHECK(
    source_record_sha256 ~ '^[0-9a-f]{64}$'
  )
);
CREATE INDEX IF NOT EXISTS idx_inventory_facts_tenant_date
  ON inventory_facts_daily(tenant_id,fact_date,sku,site);
CREATE INDEX IF NOT EXISTS idx_inventory_facts_tenant_sku_date
  ON inventory_facts_daily(tenant_id,sku,fact_date,site);

CREATE TABLE IF NOT EXISTS data_query_executions (
  query_uuid UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id BIGINT NOT NULL,
  workspace_id BIGINT,
  turn_uuid UUID,
  requested_by BIGINT NOT NULL,
  source_data_version_id BIGINT NOT NULL,
  source_version_uuid UUID NOT NULL,
  source_canonical_sha256 CHAR(64) NOT NULL,
  query_hash CHAR(64) NOT NULL,
  query_plan JSONB NOT NULL,
  resolved_filters JSONB NOT NULL,
  result_data JSONB NOT NULL,
  result_sha256 CHAR(64) NOT NULL,
  result_row_count INTEGER NOT NULL,
  duration_ms INTEGER NOT NULL,
  created_at TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(query_uuid,tenant_id),
  FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
  FOREIGN KEY(workspace_id,tenant_id)
    REFERENCES analysis_workspaces(id,tenant_id) ON DELETE CASCADE,
  FOREIGN KEY(turn_uuid,tenant_id)
    REFERENCES analysis_workspace_turns(turn_uuid,tenant_id) ON DELETE SET NULL (turn_uuid),
  FOREIGN KEY(requested_by,tenant_id)
    REFERENCES users(id,tenant_id),
  FOREIGN KEY(source_data_version_id,tenant_id)
    REFERENCES forecast_data_versions(id,tenant_id),
  CONSTRAINT chk_data_query_hashes CHECK(
    source_canonical_sha256 ~ '^[0-9a-f]{64}$'
    AND query_hash ~ '^[0-9a-f]{64}$'
    AND result_sha256 ~ '^[0-9a-f]{64}$'
  ),
  CONSTRAINT chk_data_query_json CHECK(
    jsonb_typeof(query_plan)='object'
    AND jsonb_typeof(resolved_filters)='object'
    AND jsonb_typeof(result_data)='object'
  ),
  CONSTRAINT chk_data_query_limits CHECK(
    result_row_count>=0 AND result_row_count<=500 AND duration_ms>=0
  )
);
CREATE INDEX IF NOT EXISTS idx_data_query_tenant_time
  ON data_query_executions(tenant_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_data_query_workspace_time
  ON data_query_executions(tenant_id,workspace_id,created_at DESC)
  WHERE workspace_id IS NOT NULL;

DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY[
    'data_fact_projections','sales_facts_daily','inventory_facts_daily',
    'data_query_executions'
  ] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',table_name);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY',table_name);
    EXECUTE format('DROP POLICY IF EXISTS tenant_scope ON %I',table_name);
    EXECUTE format(
      'CREATE POLICY tenant_scope ON %I TO furniscope_tenant
       USING(tenant_id=furniscope.current_tenant_id())
       WITH CHECK(tenant_id=furniscope.current_tenant_id())',
      table_name
    );
    EXECUTE format('DROP POLICY IF EXISTS platform_admin_scope ON %I',table_name);
    EXECUTE format(
      'CREATE POLICY platform_admin_scope ON %I TO furniscope_platform_admin
       USING(true) WITH CHECK(true)',
      table_name
    );
    EXECUTE format('DROP TRIGGER IF EXISTS trg_tenant_write_fence ON %I',table_name);
    EXECUTE format(
      'CREATE TRIGGER trg_tenant_write_fence
       BEFORE INSERT OR UPDATE OR DELETE ON %I
       FOR EACH ROW EXECUTE FUNCTION enforce_tenant_write_fence()',
      table_name
    );
  END LOOP;
END $$;

GRANT SELECT ON data_metric_catalog TO
  furniscope_tenant,furniscope_platform_admin,furniscope_monitor;
GRANT SELECT,INSERT ON
  data_fact_projections,sales_facts_daily,inventory_facts_daily,
  data_query_executions
  TO furniscope_tenant;
GRANT SELECT,INSERT,UPDATE,DELETE ON
  data_fact_projections,sales_facts_daily,inventory_facts_daily,
  data_query_executions
  TO furniscope_platform_admin;
REVOKE UPDATE,DELETE,TRUNCATE ON
  data_fact_projections,sales_facts_daily,inventory_facts_daily,
  data_query_executions
  FROM PUBLIC,furniscope_tenant,furniscope_runtime,furniscope_scheduler;

INSERT INTO table_scaling_policies(
  table_name,current_layout,partition_key,partition_count,
  promote_after_rows,promotion_target,rationale
) VALUES
  (
    'sales_facts_daily','tenant_indexed',NULL,NULL,20000000,'dedicated_cell',
    'Immutable daily facts use tenant/date indexes before sustained volume moves to a dedicated Cell.'
  ),
  (
    'inventory_facts_daily','tenant_indexed',NULL,NULL,20000000,'dedicated_cell',
    'Immutable inventory snapshots retain source-version lineage and scale through Cell placement.'
  ),
  (
    'data_query_executions','tenant_indexed',NULL,NULL,10000000,'dedicated_cell',
    'Auditable query evidence remains tenant-local and moves with the tenant Cell.'
  )
ON CONFLICT(table_name) DO UPDATE SET
  current_layout=EXCLUDED.current_layout,
  partition_key=EXCLUDED.partition_key,
  partition_count=EXCLUDED.partition_count,
  promote_after_rows=EXCLUDED.promote_after_rows,
  promotion_target=EXCLUDED.promotion_target,
  rationale=EXCLUDED.rationale,
  updated_at=CURRENT_TIMESTAMP;

INSERT INTO schema_migrations(version)
VALUES('v3_31_controlled_data_query')
ON CONFLICT DO NOTHING;
COMMIT;
