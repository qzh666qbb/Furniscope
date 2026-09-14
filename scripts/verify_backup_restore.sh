#!/bin/sh
set -eu

: "${POSTGRES_BACKUP_URL:?set the source database URL}"
: "${RESTORE_TEST_DATABASE_URL:?set an isolated restore-test database URL}"
[ "$POSTGRES_BACKUP_URL" != "$RESTORE_TEST_DATABASE_URL" ] || {
  echo "source and restore-test URLs must differ" >&2
  exit 2
}

verification_dir=$(mktemp -d)
trap 'rm -rf "$verification_dir"' EXIT HUP INT TERM
backup=$(BACKUP_DIRECTORY="$verification_dir" BACKUP_RETENTION_DAYS=1 /scripts/backup_postgres.sh 2>/dev/null || \
         BACKUP_DIRECTORY="$verification_dir" BACKUP_RETENTION_DAYS=1 "$(dirname "$0")/backup_postgres.sh")
RESTORE_DATABASE_URL="$RESTORE_TEST_DATABASE_URL" CONFIRM_RESTORE=restore \
  "$(dirname "$0")/restore_postgres.sh" "$backup" >/dev/null

source_tables=$(psql "$POSTGRES_BACKUP_URL" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='furniscope'")
restored_tables=$(psql "$RESTORE_TEST_DATABASE_URL" -Atc "SELECT count(*) FROM information_schema.tables WHERE table_schema='furniscope'")
[ "$source_tables" = "$restored_tables" ] || {
  echo "restore verification failed: source=$source_tables restored=$restored_tables" >&2
  exit 1
}
echo "restore verification passed: $restored_tables furniscope tables"
