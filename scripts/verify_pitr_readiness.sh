#!/bin/sh
set -eu

: "${POSTGRES_VERIFY_URL:?set POSTGRES_VERIFY_URL to a read-only database URL}"
PITR_DIRECTORY=${PITR_DIRECTORY:-var/backups/pitr}

settings=$(psql "$POSTGRES_VERIFY_URL" -X -Atq -F '|' <<'SQL'
SELECT current_setting('archive_mode'),
       current_setting('wal_level'),
       current_setting('archive_command')<>'(disabled)',
       current_setting('archive_timeout')::int<=900;
SQL
)
IFS='|' read -r archive_mode wal_level archive_command_set archive_timeout_ok <<EOF
$settings
EOF
[ "$archive_mode" = on ] || { echo "archive_mode is not on" >&2; exit 1; }
case "$wal_level" in replica|logical) ;; *) echo "wal_level is not replica/logical" >&2; exit 1 ;; esac
[ "$archive_command_set" = t ] || { echo "archive_command is disabled" >&2; exit 1; }
[ "$archive_timeout_ok" = t ] || { echo "archive_timeout exceeds 900 seconds" >&2; exit 1; }

latest=$(find "$PITR_DIRECTORY" -mindepth 1 -maxdepth 1 -type d \
  -name 'base-*' ! -name '*.partial' -print | sort | tail -n 1)
[ -n "$latest" ] || { echo "no completed base backup found" >&2; exit 1; }
pg_verifybackup "$latest"
echo "PITR readiness verified: $latest"
