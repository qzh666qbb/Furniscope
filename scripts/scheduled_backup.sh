#!/bin/sh
set -eu

BACKUP_INTERVAL_SECONDS=${BACKUP_INTERVAL_SECONDS:-86400}
case "$BACKUP_INTERVAL_SECONDS" in
  *[!0-9]*|"") echo "BACKUP_INTERVAL_SECONDS must be an integer" >&2; exit 2 ;;
esac
while true; do
  /scripts/backup_postgres.sh
  sleep "$BACKUP_INTERVAL_SECONDS"
done
