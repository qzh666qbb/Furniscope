\set ON_ERROR_STOP on
BEGIN;
SET search_path TO furniscope, public;

-- Product-center SKU <-> training-history SKU. Extra engine SKUs are dropped
-- when refreshing tenant_sku_catalog.
CREATE TABLE IF NOT EXISTS forecast_sku_aliases (
  product_sku VARCHAR(128) PRIMARY KEY,
  source_sku VARCHAR(128) NOT NULL UNIQUE
);
COMMENT ON TABLE forecast_sku_aliases IS
  'Maps enterprise product SKUs onto Sales Forecast V4 training SKUs. Unmapped engine SKUs are not kept in tenant_sku_catalog.';

INSERT INTO forecast_sku_aliases (product_sku, source_sku) VALUES
  ('HF-A0393','FBA1LED908'),
  ('HF-A0049-1','KW908-EU'),
  ('HF-A0058','KW1363'),
  ('HF-A0460','OS2302'),
  ('HF-A0595','YU1402'),
  ('HF-A0628','KW1365'),
  ('HF-A0657','KW791GN'),
  ('HF-A0691-1','KU871'),
  ('HF-A0699-1','KW1367'),
  ('HF-A0708','KW2301'),
  ('HF-A0758-1','OS1362'),
  ('HF-A0772','KU1127'),
  ('HF-A0775','KW791DN'),
  ('HF-A0107','OS731'),
  ('HF-A0108','EZ295'),
  ('HF-A0205','KU1516'),
  ('HF-A0215','OS804'),
  ('HF-A0303','LED908C'),
  ('HF-A0307','EZ295C'),
  ('HF-A0489','KU293'),
  ('HF-A0599','LED1803'),
  ('HF-A0610','OS1364'),
  ('HF-A0655','LED1126N'),
  ('HF-A0675','KW1240D'),
  ('HF-A0704','KW1383'),
  ('HF-A0719-1','EZ908-Y'),
  ('HF-A0720','CEZLED871'),
  ('HF-A0725','KU871C-M'),
  ('HF-A0756','YU1372'),
  ('HF-A0743','KU296B'),
  ('HF-A0753','LED1800'),
  ('HF-A0763','LED295'),
  ('HF-A0765','EZ1701'),
  ('HF-A0451','KW1371'),
  ('HF-A0632','LED295C'),
  ('HF-A0732M','OS1382'),
  ('HF-A0216H','KW1302'),
  ('HF-A0590','KW2300'),
  ('HF-A0689','KY1327'),
  ('HF-A0083-5','OS732'),
  ('HF-A0083-1','OS1370'),
  ('HF-A0573-1','KW1389'),
  ('HF-A0186','KW908-EUC'),
  ('HF-A0570','KU1519'),
  ('HF-B0428','KW792GN'),
  ('HF-B0136','YU1402B'),
  ('HF-B0639','YU298B'),
  ('HF-B0516','EZ1500C'),
  ('HF-B0609','LED1401'),
  ('HF-B0503-1','LED1802'),
  ('HF-B0023','YU1402D-M'),
  ('HF-A0722','EU908-EU'),
  ('HF-A600','EZ1800'),
  ('HF-A703','EZ296B'),
  ('HF-A0709','KU274'),
  ('HF-A0744','KW1241B'),
  ('HF-A0769','LED1513'),
  ('HF-B0641','OS1213'),
  ('HF-B0218','LED298'),
  ('HF-B0437-1','YU1511C'),
  ('HF-B0607','YU1515'),
  ('HF-B0579-2','KU744B'),
  ('HF-B0636','KY1603B'),
  ('HF-B0637','YU1511'),
  ('HF-B0653','YU1512'),
  ('HF-B0665','EU1362B'),
  ('HF-A0325','EZ203'),
  ('HF-A0422','KU298B'),
  ('HF-A0356','LED1328'),
  ('HF-A0425','LED1402'),
  ('HF-A0578','LED277'),
  ('HF-B0143A','YU1510'),
  ('HF-B0142','KW1367D'),
  ('HF-A0321A','KY1330'),
  ('HF-A0345','LED1117'),
  ('HF-A0396-1','LED1801')
ON CONFLICT (product_sku) DO UPDATE SET source_sku=EXCLUDED.source_sku;

INSERT INTO tenant_sku_catalog
  (tenant_id, sku, site, category_code, lifecycle_status, label_status,
   history_weeks, model_eligible, source_id, attributes)
SELECT c.tenant_id, a.product_sku, c.site, COALESCE(p.category_code, c.category_code),
       c.lifecycle_status, c.label_status, c.history_weeks, true, c.source_id,
       COALESCE(c.attributes, '{}'::jsonb) || jsonb_build_object('source_sku', a.source_sku)
  FROM tenant_sku_catalog c
  JOIN forecast_sku_aliases a ON upper(trim(c.sku))=upper(trim(a.source_sku))
  JOIN products p ON p.tenant_id=c.tenant_id AND p.deleted_at IS NULL
   AND upper(trim(p.sku))=upper(trim(a.product_sku))
ON CONFLICT (tenant_id, sku, site) DO UPDATE SET
  history_weeks=EXCLUDED.history_weeks,
  model_eligible=true,
  category_code=COALESCE(EXCLUDED.category_code, tenant_sku_catalog.category_code),
  attributes=EXCLUDED.attributes,
  updated_at=now();

DELETE FROM tenant_sku_catalog c
 WHERE NOT EXISTS (
   SELECT 1 FROM products p
    WHERE p.tenant_id=c.tenant_id AND p.deleted_at IS NULL
      AND upper(trim(p.sku))=upper(trim(c.sku))
 );

COMMIT;
