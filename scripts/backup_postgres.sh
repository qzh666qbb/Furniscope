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

trap 'rm -f "$partial"' EXIT HUP INT TERM
pg_dump --dbname="$POSTGRES_BACKUP_URL" --format=custom --compress=9 \
  --no-owner --no-privileges --file="$partial"
pg_restore --list "$partial" >/dev/null
mv "$partial" "$target"
(
  cd "$BACKUP_DIRECTORY"
  sha256sum "$(basename "$target")" >"$(basename "$target").sha256"
)
find "$BACKUP_DIRECTORY" -type f \( -name 'furniscope-*.dump' -o -name 'furniscope-*.dump.sha256' \) \
  -mtime "+$BACKUP_RETENTION_DAYS" -delete
echo "$target"
