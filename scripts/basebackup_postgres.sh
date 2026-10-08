#!/bin/sh
set -eu

: "${POSTGRES_REPLICATION_URL:?set POSTGRES_REPLICATION_URL to the replication login URL}"
PITR_DIRECTORY=${PITR_DIRECTORY:-var/backups/pitr}
PITR_RETENTION_DAYS=${PITR_RETENTION_DAYS:-7}

case "$PITR_DIRECTORY" in
  ""|/|.) echo "unsafe PITR_DIRECTORY: $PITR_DIRECTORY" >&2; exit 2 ;;
esac
case "$PITR_RETENTION_DAYS" in
  *[!0-9]*|"") echo "PITR_RETENTION_DAYS must be an integer" >&2; exit 2 ;;
esac

mkdir -p "$PITR_DIRECTORY"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$PITR_DIRECTORY/base-$timestamp"
partial="$target.partial"
trap 'rm -rf "$partial"' EXIT HUP INT TERM

pg_basebackup \
  --dbname="$POSTGRES_REPLICATION_URL" \
  --pgdata="$partial" \
  --format=plain \
  --wal-method=stream \
  --checkpoint=fast \
  --manifest-checksums=SHA256 \
  --no-password
pg_verifybackup "$partial"
chmod -R go-rwx "$partial"
mv "$partial" "$target"

find "$PITR_DIRECTORY" -mindepth 1 -maxdepth 1 -type d \
  -name 'base-*' -mtime "+$PITR_RETENTION_DAYS" -exec rm -rf {} +
echo "$target"
