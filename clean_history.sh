#!/usr/bin/env bash
# 05:00 maintenance: archive current snapshots and selected logs.
# Never clear the draw store or globally flush PM2/audit logs.
set -euo pipefail
umask 077

WORK_DIR="/root/penta5-runner"
BACKUP_DIR="$WORK_DIR/data/backups"
ARCHIVE_DIR="$WORK_DIR/data/log-archive"
TODAY="$(TZ=Asia/Shanghai date +%Y%m%d)"
MAX_ARCHIVE_BYTES="${PENTA5_LOG_ARCHIVE_MAX_BYTES:-33554432}"
case "$MAX_ARCHIVE_BYTES" in
  ''|*[!0-9]*) echo "Invalid PENTA5_LOG_ARCHIVE_MAX_BYTES" >&2; exit 1 ;;
esac
mkdir -p -- "$BACKUP_DIR" "$ARCHIVE_DIR"
exec 9>"$WORK_DIR/data/daily-maintenance.lock"
flock -n 9 || exit 0

archive_once() {
  local source="$1" target="$2" limit="$3" temporary=""
  [[ -f "$source" && ! -L "$source" ]] || return 0
  [[ ! -e "$target" ]] || return 0
  local bytes
  bytes="$(stat -c %s -- "$source")"
  if (( bytes > limit )); then
    printf 'WARN archive deferred: %s exceeds %s bytes; original retained.\n' "$source" "$limit" >&2
    return 0
  fi
  temporary="$(mktemp "$target.tmp.XXXXXX")"
  if gzip -c -- "$source" >"$temporary"; then
    chmod 600 -- "$temporary"
    mv -- "$temporary" "$target"
  else
    rm -f -- "$temporary"
    return 1
  fi
}

# Snapshot files are never used as a substitute for a database backup.
archive_once "$WORK_DIR/data/state.json" "$BACKUP_DIR/state_$TODAY.json.gz" "$MAX_ARCHIVE_BYTES"

# Exact allowlist only. Keep active files and existing audit archives intact.
# PM2 rotation and database backup retention require a separate reviewed policy.
for name in auto_r1.log auto_match5.log auto_r1_framework.log memory.log; do
  archive_once "$WORK_DIR/$name" "$ARCHIVE_DIR/${name}_$TODAY.gz" "$MAX_ARCHIVE_BYTES"
done

printf '[%s] Daily snapshot/log archive complete; source data and audit logs retained.\n' "$(TZ=Asia/Shanghai date '+%Y-%m-%d %H:%M:%S')"
