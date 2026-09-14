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
(
  cd "$(dirname "$backup")"
  sha256sum -c "$(basename "$backup").sha256"
)
pg_restore --list "$backup" >/dev/null
pg_restore --dbname="$RESTORE_DATABASE_URL" --clean --if-exists --no-owner --no-privileges "$backup"
echo "restore completed: $backup"
