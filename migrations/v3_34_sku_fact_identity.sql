\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;

CREATE OR REPLACE FUNCTION rename_tenant_sku_facts(
  p_tenant_id BIGINT,
  p_old_sku TEXT,
  p_new_sku TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path TO furniscope,pg_temp
AS $$
DECLARE
  sales_updated INTEGER;
  inventory_updated INTEGER;
BEGIN
  IF furniscope.current_tenant_id() IS DISTINCT FROM p_tenant_id THEN
    RAISE EXCEPTION 'Tenant identity does not match SKU rename scope'
      USING ERRCODE='42501';
  END IF;
  IF NULLIF(btrim(p_old_sku),'') IS NULL OR NULLIF(btrim(p_new_sku),'') IS NULL THEN
    RAISE EXCEPTION 'SKU rename requires non-empty old and new values'
      USING ERRCODE='22023';
  END IF;
  IF NOT EXISTS(
    SELECT 1
      FROM products
     WHERE tenant_id=p_tenant_id
       AND deleted_at IS NULL
       AND upper(btrim(sku))=upper(btrim(p_new_sku))
  ) THEN
    RAISE EXCEPTION 'Target SKU is not an active tenant product'
      USING ERRCODE='23503';
  END IF;

  UPDATE sales_facts_daily
     SET sku=p_new_sku
   WHERE tenant_id=p_tenant_id
     AND upper(btrim(sku))=upper(btrim(p_old_sku));
  GET DIAGNOSTICS sales_updated = ROW_COUNT;

  UPDATE inventory_facts_daily
     SET sku=p_new_sku
   WHERE tenant_id=p_tenant_id
     AND upper(btrim(sku))=upper(btrim(p_old_sku));
  GET DIAGNOSTICS inventory_updated = ROW_COUNT;

  RETURN jsonb_build_object(
    'sales_updated',sales_updated,
    'inventory_updated',inventory_updated
  );
END
$$;

ALTER FUNCTION rename_tenant_sku_facts(BIGINT,TEXT,TEXT)
  OWNER TO furniscope_platform_admin;
REVOKE ALL ON FUNCTION rename_tenant_sku_facts(BIGINT,TEXT,TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION rename_tenant_sku_facts(BIGINT,TEXT,TEXT)
  TO furniscope_tenant,furniscope_platform_admin;

COMMENT ON FUNCTION rename_tenant_sku_facts(BIGINT,TEXT,TEXT) IS
  'Controlled product-identity rename for append-only sales and inventory facts.';

INSERT INTO schema_migrations(version)
VALUES('v3_34_sku_fact_identity')
ON CONFLICT DO NOTHING;

COMMIT;
