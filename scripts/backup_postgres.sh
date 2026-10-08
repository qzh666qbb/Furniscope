#!/bin/sh
set -eu

: "${POSTGRES_BACKUP_URL:?set POSTGRES_BACKUP_URL to a libpq PostgreSQL URL}"
BACKUP_DIRECTORY=${BACKUP_DIRECTORY:-var/backups/postgres}
BACKUP_RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-14}

case "$BACKUP_DIRECTORY" in
  ""|/|.) echo "unsafe BACKUP_DIRECTORY: $BACKUP_DIRECTORY" >&2; exit 2 ;;
esac
case "$BACKUP_RETENTION_DAYS" in
  *[!0-9]*|"") echo "BACKUP_RETENTION_DAYS must be an integer" >&2; exit 2 ;;
esac

mkdir -p "$BACKUP_DIRECTORY"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$BACKUP_DIRECTORY/furniscope-$timestamp.dump"
partial="$target.partial"
manifest="$target.manifest.json"
manifest_partial="$manifest.partial"

trap 'rm -f "$partial" "$manifest_partial"' EXIT HUP INT TERM
pg_dump --dbname="$POSTGRES_BACKUP_URL" --format=custom --compress=9 \
  --no-owner --no-privileges --file="$partial"
pg_restore --list "$partial" >/dev/null
mv "$partial" "$target"
(
  cd "$BACKUP_DIRECTORY"
  sha256sum "$(basename "$target")" >"$(basename "$target").sha256"
)
dump_sha256=$(cut -d ' ' -f 1 "$target.sha256")
dump_bytes=$(wc -c <"$target" | tr -d ' ')
dump_name=$(basename "$target")
psql "$POSTGRES_BACKUP_URL" -X -v ON_ERROR_STOP=1 -Atq \
  --set=dump_sha256="$dump_sha256" \
  --set=dump_bytes="$dump_bytes" \
  --set=dump_name="$dump_name" <<'SQL' >"$manifest_partial"
SET search_path TO furniscope,public;
SELECT jsonb_pretty(jsonb_build_object(
  'protocol','furniscope-backup-manifest-v1',
  'created_at',to_char(clock_timestamp() AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'source_database',current_database(),
  'server_version',current_setting('server_version'),
  'dump_file',:'dump_name',
  'dump_bytes',:'dump_bytes'::bigint,
  'dump_sha256',:'dump_sha256',
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
));
SQL
mv "$manifest_partial" "$manifest"
(
  cd "$BACKUP_DIRECTORY"
  sha256sum "$(basename "$manifest")" >"$(basename "$manifest").sha256"
)
find "$BACKUP_DIRECTORY" -type f \( \
  -name 'furniscope-*.dump' -o \
  -name 'furniscope-*.dump.sha256' -o \
  -name 'furniscope-*.dump.manifest.json' -o \
  -name 'furniscope-*.dump.manifest.json.sha256' \
  \) \
  -mtime "+$BACKUP_RETENTION_DAYS" -delete
echo "$target"
