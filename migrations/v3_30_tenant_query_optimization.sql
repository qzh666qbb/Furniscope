\set ON_ERROR_STOP on
BEGIN;
SET LOCAL search_path TO furniscope,public;

-- pgvector is optional. The canonical JSONB embedding remains available when
-- the extension package or CREATE EXTENSION privilege is unavailable.
DO $$
BEGIN
  IF to_regtype('vector') IS NULL
     AND EXISTS(SELECT FROM pg_available_extensions WHERE name='vector') THEN
    BEGIN
      CREATE EXTENSION IF NOT EXISTS vector;
    EXCEPTION WHEN insufficient_privilege THEN
      RAISE NOTICE 'pgvector is available but the migration role cannot install it';
    END;
  END IF;
END $$;

-- This derived index is partitioned independently from the FK-heavy canonical
-- knowledge tables. It can be rebuilt from knowledge_chunks without an online
-- table swap and gives exact tenant pruning before ANN candidate retrieval.
CREATE TABLE IF NOT EXISTS knowledge_chunk_embeddings (
  tenant_id BIGINT NOT NULL,
  chunk_id BIGINT NOT NULL,
  embedding_model VARCHAR(128) NOT NULL,
  embedding_dimensions INTEGER NOT NULL,
  embedding JSONB NOT NULL,
  embedding_checksum CHAR(64) NOT NULL,
  indexed_at TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(tenant_id,chunk_id),
  FOREIGN KEY(tenant_id) REFERENCES tenants(id) ON DELETE CASCADE,
  FOREIGN KEY(chunk_id,tenant_id)
    REFERENCES knowledge_chunks(id,tenant_id) ON DELETE CASCADE,
  CONSTRAINT chk_knowledge_embedding_dimensions CHECK(
    embedding_dimensions>0
    AND jsonb_typeof(embedding)='array'
    AND jsonb_array_length(embedding)=embedding_dimensions
  ),
  CONSTRAINT chk_knowledge_embedding_checksum CHECK(
    embedding_checksum ~ '^[0-9a-f]{64}$'
  )
) PARTITION BY HASH(tenant_id);

DO $$
DECLARE partition_no integer;
DECLARE partition_name text;
BEGIN
  FOR partition_no IN 0..15 LOOP
    partition_name := format(
      'knowledge_chunk_embeddings_p%s',
      lpad(partition_no::text,2,'0')
    );
    EXECUTE format(
      'CREATE TABLE IF NOT EXISTS %I PARTITION OF knowledge_chunk_embeddings
       FOR VALUES WITH(MODULUS 16,REMAINDER %s)',
      partition_name,partition_no
    );
  END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS idx_knowledge_embeddings_model
  ON knowledge_chunk_embeddings(tenant_id,embedding_model,chunk_id);

DO $$
BEGIN
  IF to_regtype('vector') IS NOT NULL THEN
    EXECUTE
      'ALTER TABLE knowledge_chunk_embeddings
       ADD COLUMN IF NOT EXISTS embedding_vector vector(1024)';
    BEGIN
      EXECUTE
        'CREATE INDEX IF NOT EXISTS idx_knowledge_embeddings_hnsw
         ON knowledge_chunk_embeddings
         USING hnsw(embedding_vector vector_cosine_ops)
         WITH(m=16,ef_construction=96)';
    EXCEPTION WHEN feature_not_supported OR undefined_object THEN
      RAISE NOTICE 'pgvector is installed without partitioned HNSW support; using exact vector scan';
    END;
  END IF;
END $$;

INSERT INTO knowledge_chunk_embeddings(
  tenant_id,chunk_id,embedding_model,embedding_dimensions,
  embedding,embedding_checksum,indexed_at
)
SELECT c.tenant_id,c.id,v.embedding_model,v.embedding_dimensions,
       c.embedding,encode(digest(c.embedding::text,'sha256'),'hex'),CURRENT_TIMESTAMP
  FROM knowledge_chunks c
  JOIN knowledge_document_versions v
    ON v.id=c.document_version_id AND v.tenant_id=c.tenant_id
 WHERE c.embedding IS NOT NULL
   AND v.embedding_model IS NOT NULL
   AND v.embedding_dimensions IS NOT NULL
   AND jsonb_array_length(c.embedding)=v.embedding_dimensions
ON CONFLICT(tenant_id,chunk_id) DO UPDATE SET
  embedding_model=EXCLUDED.embedding_model,
  embedding_dimensions=EXCLUDED.embedding_dimensions,
  embedding=EXCLUDED.embedding,
  embedding_checksum=EXCLUDED.embedding_checksum,
  indexed_at=EXCLUDED.indexed_at;

DO $$
BEGIN
  IF to_regtype('vector') IS NOT NULL THEN
    EXECUTE
      'UPDATE knowledge_chunk_embeddings
          SET embedding_vector=(embedding::text)::vector(1024)
        WHERE embedding_dimensions=1024
          AND embedding_vector IS NULL
          AND NOT EXISTS(
            SELECT FROM jsonb_array_elements(embedding) item
             WHERE jsonb_typeof(item)<>''number''
          )';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION sync_knowledge_chunk_embedding()
RETURNS trigger LANGUAGE plpgsql
SET search_path TO furniscope,public AS $$
DECLARE model_name text;
DECLARE dimensions integer;
BEGIN
  IF NEW.embedding IS NULL THEN
    DELETE FROM knowledge_chunk_embeddings
     WHERE tenant_id=NEW.tenant_id AND chunk_id=NEW.id;
    RETURN NEW;
  END IF;
  SELECT embedding_model,embedding_dimensions
    INTO model_name,dimensions
    FROM knowledge_document_versions
   WHERE id=NEW.document_version_id AND tenant_id=NEW.tenant_id;
  IF model_name IS NULL OR dimensions IS NULL
     OR jsonb_array_length(NEW.embedding)<>dimensions THEN
    RAISE EXCEPTION 'knowledge chunk embedding metadata is missing or inconsistent'
      USING ERRCODE='23514';
  END IF;
  INSERT INTO knowledge_chunk_embeddings(
    tenant_id,chunk_id,embedding_model,embedding_dimensions,
    embedding,embedding_checksum,indexed_at
  ) VALUES(
    NEW.tenant_id,NEW.id,model_name,dimensions,NEW.embedding,
    encode(digest(NEW.embedding::text,'sha256'),'hex'),CURRENT_TIMESTAMP
  )
  ON CONFLICT(tenant_id,chunk_id) DO UPDATE SET
    embedding_model=EXCLUDED.embedding_model,
    embedding_dimensions=EXCLUDED.embedding_dimensions,
    embedding=EXCLUDED.embedding,
    embedding_checksum=EXCLUDED.embedding_checksum,
    indexed_at=EXCLUDED.indexed_at;
  IF dimensions=1024
     AND to_regtype('vector') IS NOT NULL
     AND EXISTS(
       SELECT FROM information_schema.columns
        WHERE table_schema='furniscope'
          AND table_name='knowledge_chunk_embeddings'
          AND column_name='embedding_vector'
     ) THEN
    EXECUTE
      'UPDATE knowledge_chunk_embeddings
          SET embedding_vector=($1::text)::vector(1024)
        WHERE tenant_id=$2 AND chunk_id=$3'
      USING NEW.embedding::text,NEW.tenant_id,NEW.id;
  END IF;
  RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS trg_sync_knowledge_chunk_embedding ON knowledge_chunks;
CREATE TRIGGER trg_sync_knowledge_chunk_embedding
AFTER INSERT OR UPDATE OF embedding,document_version_id ON knowledge_chunks
FOR EACH ROW EXECUTE FUNCTION sync_knowledge_chunk_embedding();

-- Tenant-first indexes match the production list, aggregation, and pagination
-- paths. They also provide a bounded migration step before any canonical table
-- is promoted to a dedicated Cell.
CREATE INDEX IF NOT EXISTS idx_listing_tenant_dataset_time
  ON market_listings(tenant_id,dataset_id,captured_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_review_tenant_dataset_time
  ON reviews(tenant_id,dataset_id,reviewed_at DESC NULLS LAST,id DESC)
  WHERE is_valid;
CREATE INDEX IF NOT EXISTS idx_review_tenant_dataset_sentiment
  ON reviews(tenant_id,dataset_id,sentiment,reviewed_at DESC NULLS LAST,id DESC)
  WHERE is_valid;
CREATE INDEX IF NOT EXISTS idx_sentiment_tenant_market_time
  ON sentiment_events(tenant_id,market_country,received_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_sentiment_tenant_market_sentiment_time
  ON sentiment_events(
    tenant_id,market_country,sentiment,received_at DESC,id DESC
  );
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_tenant_document
  ON knowledge_chunks(
    tenant_id,document_id,document_version_id,chunk_index
  );

CREATE TABLE IF NOT EXISTS table_scaling_policies (
  table_name VARCHAR(128) PRIMARY KEY,
  current_layout VARCHAR(24) NOT NULL,
  partition_key VARCHAR(128),
  partition_count INTEGER,
  promote_after_rows BIGINT,
  promotion_target VARCHAR(24) NOT NULL,
  rationale VARCHAR(1000) NOT NULL,
  updated_at TIMESTAMPTZ(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT chk_table_scaling_layout CHECK(
    current_layout IN ('tenant_indexed','hash_partitioned')
  ),
  CONSTRAINT chk_table_scaling_target CHECK(
    promotion_target IN ('hash_partitioned','dedicated_cell')
  ),
  CONSTRAINT chk_table_scaling_partition CHECK(
    (current_layout='hash_partitioned'
      AND partition_key IS NOT NULL AND partition_count>0)
    OR current_layout='tenant_indexed'
  ),
  CONSTRAINT chk_table_scaling_threshold CHECK(
    promote_after_rows IS NULL OR promote_after_rows>0
  )
);

INSERT INTO table_scaling_policies(
  table_name,current_layout,partition_key,partition_count,
  promote_after_rows,promotion_target,rationale
) VALUES
  (
    'knowledge_chunk_embeddings','hash_partitioned','tenant_id',16,
    NULL,'dedicated_cell',
    'Derived ANN index can be rebuilt independently; large tenants move by Cell migration.'
  ),
  (
    'market_listings','tenant_indexed',NULL,NULL,10000000,'dedicated_cell',
    'Canonical table has cross-table foreign keys; avoid an in-place partition table swap.'
  ),
  (
    'reviews','tenant_indexed',NULL,NULL,20000000,'dedicated_cell',
    'Canonical evidence rows retain stable IDs; scale large tenants through Cell placement.'
  ),
  (
    'sentiment_events','tenant_indexed',NULL,NULL,10000000,'dedicated_cell',
    'Append-heavy feed uses tenant/time indexes before Cell promotion.'
  ),
  (
    'audit_logs','tenant_indexed',NULL,NULL,20000000,'dedicated_cell',
    'Append-only hash chain must preserve sequence and immutable evidence during migration.'
  ),
  (
    'forecast_results','tenant_indexed',NULL,NULL,20000000,'dedicated_cell',
    'Tenant/job range scans are indexed; dedicated Cells isolate sustained forecast volume.'
  )
ON CONFLICT(table_name) DO UPDATE SET
  current_layout=EXCLUDED.current_layout,
  partition_key=EXCLUDED.partition_key,
  partition_count=EXCLUDED.partition_count,
  promote_after_rows=EXCLUDED.promote_after_rows,
  promotion_target=EXCLUDED.promotion_target,
  rationale=EXCLUDED.rationale,
  updated_at=CURRENT_TIMESTAMP;

ALTER TABLE knowledge_chunk_embeddings ENABLE ROW LEVEL SECURITY;
ALTER TABLE knowledge_chunk_embeddings FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_scope ON knowledge_chunk_embeddings;
CREATE POLICY tenant_scope ON knowledge_chunk_embeddings TO furniscope_tenant
  USING(tenant_id=furniscope.current_tenant_id())
  WITH CHECK(tenant_id=furniscope.current_tenant_id());
DROP POLICY IF EXISTS platform_admin_scope ON knowledge_chunk_embeddings;
CREATE POLICY platform_admin_scope ON knowledge_chunk_embeddings
  TO furniscope_platform_admin USING(true) WITH CHECK(true);

GRANT SELECT,INSERT,UPDATE,DELETE ON knowledge_chunk_embeddings
  TO furniscope_tenant,furniscope_platform_admin;
GRANT SELECT ON table_scaling_policies
  TO furniscope_platform_admin,furniscope_scheduler,furniscope_monitor;
GRANT INSERT,UPDATE,DELETE ON table_scaling_policies
  TO furniscope_platform_admin;

DROP TRIGGER IF EXISTS trg_tenant_write_fence ON knowledge_chunk_embeddings;
CREATE TRIGGER trg_tenant_write_fence
BEFORE INSERT OR UPDATE OR DELETE ON knowledge_chunk_embeddings
FOR EACH ROW EXECUTE FUNCTION enforce_tenant_write_fence();

INSERT INTO schema_migrations(version)
VALUES('v3_30_tenant_query_optimization')
ON CONFLICT DO NOTHING;
COMMIT;
