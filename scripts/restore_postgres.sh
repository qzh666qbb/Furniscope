#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "usage: RESTORE_DATABASE_URL=... CONFIRM_RESTORE=restore $0 BACKUP.dump" >&2
  exit 2
fi
: "${RESTORE_DATABASE_URL:?set RESTORE_DATABASE_URL to the isolated target database}"
: "${CONFIRM_RESTORE:?set CONFIRM_RESTORE=restore after verifying the target}"
[ "$CONFIRM_RESTORE" = restore ] || { echo "CONFIRM_RESTORE must equal restore" >&2; exit 2; }

backup=$1
[ -f "$backup" ] || { echo "backup not found: $backup" >&2; exit 2; }
[ -f "$backup.sha256" ] || { echo "checksum not found: $backup.sha256" >&2; exit 2; }
manifest="$backup.manifest.json"
[ -f "$manifest" ] || { echo "manifest not found: $manifest" >&2; exit 2; }
[ -f "$manifest.sha256" ] || { echo "manifest checksum not found: $manifest.sha256" >&2; exit 2; }
(
  cd "$(dirname "$backup")"
  sha256sum -c "$(basename "$backup").sha256"
  sha256sum -c "$(basename "$manifest").sha256"
)
pg_restore --list "$backup" >/dev/null
manifest_json=$(cat "$manifest")
manifest_protocol=$(psql "$RESTORE_DATABASE_URL" -X -Atq \
  --set=manifest="$manifest_json" <<'SQL'
SELECT :'manifest'::jsonb->>'protocol';
SQL
)
[ "$manifest_protocol" = furniscope-backup-manifest-v1 ] || {
  echo "unsupported backup manifest protocol: $manifest_protocol" >&2
  exit 2
}
manifest_sha=$(psql "$RESTORE_DATABASE_URL" -X -Atq \
  --set=manifest="$manifest_json" <<'SQL'
SELECT :'manifest'::jsonb->>'dump_sha256';
SQL
)
actual_sha=$(cut -d ' ' -f 1 "$backup.sha256")
[ "$manifest_sha" = "$actual_sha" ] || {
  echo "manifest dump checksum does not match sidecar checksum" >&2
  exit 2
}
source_database=$(psql "$RESTORE_DATABASE_URL" -X -Atq \
  --set=manifest="$manifest_json" <<'SQL'
SELECT :'manifest'::jsonb->>'source_database';
SQL
)
target_database=$(psql "$RESTORE_DATABASE_URL" -X -Atq -c "SELECT current_database()")
[ "$source_database" != "$target_database" ] || {
  echo "refusing to restore into a database with the source database name" >&2
  exit 2
}
pg_restore --dbname="$RESTORE_DATABASE_URL" --clean --if-exists --no-owner --no-privileges "$backup"
verified=$(psql "$RESTORE_DATABASE_URL" -X -Atq \
  --set=manifest="$manifest_json" <<'SQL'
SET search_path TO furniscope,public;
WITH actual AS (
  SELECT jsonb_build_object(
    'schema_migrations',COALESCE((
      SELECT jsonb_agg(version ORDER BY version) FROM schema_migrations
    ),'[]'::jsonb),
    'critical_row_counts',jsonb_build_object(
      'tenants',(SELECT count(*) FROM tenants),
      'users',(SELECT count(*) FROM users),
      'products',(SELECT count(*) FROM products),
      'market_datasets',(SELECT count(*) FROM market_datasets),
      'analysis_tasks',(SELECT count(*) FROM analysis_tasks),
      'audit_logs',(SELECT count(*) FROM audit_logs)
    ),
    'audit_chain_heads',COALESCE((
      SELECT jsonb_object_agg(scope_id,head ORDER BY scope_id)
        FROM (
          SELECT COALESCE(tenant_id,0)::text AS scope_id,
                 jsonb_build_object(
                   'chain_sequence',max(chain_sequence),
                   'event_hash',(array_agg(event_hash ORDER BY chain_sequence DESC))[1]
                 ) AS head
            FROM audit_logs GROUP BY COALESCE(tenant_id,0)
        ) chains
    ),'{}'::jsonb)
  ) AS value
)
SELECT (
  value->'schema_migrations'=:'manifest'::jsonb->'schema_migrations'
  AND value->'critical_row_counts'=:'manifest'::jsonb->'critical_row_counts'
  AND value->'audit_chain_heads'=:'manifest'::jsonb->'audit_chain_heads'
)::text FROM actual;
SQL
)
[ "$verified" = true ] || {
  echo "restored migrations, critical row counts, or audit chain heads differ from manifest" >&2
  exit 1
}
echo "restore completed: $backup"
