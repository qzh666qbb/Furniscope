#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  echo "usage: RECOVERY_TARGET_TIME=... WAL_ARCHIVE_DIRECTORY=... CONFIRM_PITR_RESTORE=prepare $0 BASE_BACKUP TARGET_DATA_DIRECTORY" >&2
  exit 2
fi
: "${RECOVERY_TARGET_TIME:?set the UTC recovery target, for example 2026-10-08T01:30:00Z}"
: "${WAL_ARCHIVE_DIRECTORY:?set the mounted WAL archive directory}"
: "${CONFIRM_PITR_RESTORE:?set CONFIRM_PITR_RESTORE=prepare}"
[ "$CONFIRM_PITR_RESTORE" = prepare ] || {
  echo "CONFIRM_PITR_RESTORE must equal prepare" >&2
  exit 2
}

base_backup=$1
target=$2
[ -d "$base_backup" ] || { echo "base backup not found: $base_backup" >&2; exit 2; }
case "$target" in ""|/|.) echo "unsafe target directory: $target" >&2; exit 2 ;; esac
printf '%s' "$RECOVERY_TARGET_TIME" | grep -Eq \
  '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?Z$' || {
  echo "RECOVERY_TARGET_TIME must be an RFC3339 UTC timestamp" >&2
  exit 2
}
printf '%s' "$WAL_ARCHIVE_DIRECTORY" | grep -Eq '^/[A-Za-z0-9_./-]+$' || {
  echo "WAL_ARCHIVE_DIRECTORY must be an absolute path without spaces" >&2
  exit 2
}

pg_verifybackup "$base_backup"
mkdir -p "$target"
[ -z "$(find "$target" -mindepth 1 -maxdepth 1 -print -quit)" ] || {
  echo "target data directory must be empty" >&2
  exit 2
}
cp -a "$base_backup/." "$target/"
rm -f "$target/postmaster.pid" "$target/standby.signal"
cat >>"$target/postgresql.auto.conf" <<EOF
restore_command = 'cp $WAL_ARCHIVE_DIRECTORY/%f %p'
recovery_target_time = '$RECOVERY_TARGET_TIME'
recovery_target_action = 'promote'
EOF
touch "$target/recovery.signal"
chmod 0700 "$target"
echo "$target"
