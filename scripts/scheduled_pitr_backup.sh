#!/bin/sh
set -eu

PITR_BASEBACKUP_INTERVAL_SECONDS=${PITR_BASEBACKUP_INTERVAL_SECONDS:-86400}
case "$PITR_BASEBACKUP_INTERVAL_SECONDS" in
  *[!0-9]*|"") echo "PITR_BASEBACKUP_INTERVAL_SECONDS must be an integer" >&2; exit 2 ;;
esac

while true; do
  /scripts/basebackup_postgres.sh
  sleep "$PITR_BASEBACKUP_INTERVAL_SECONDS"
done
