#!/usr/bin/env bash
# Version-control the root crontab: the repo copy ops/crontab.txt is the
# reviewed baseline; this tool only dumps/diffs/installs, never edits jobs.
set -euo pipefail
ROOT_DIR="/root/penta5-runner"
SNAPSHOT="$ROOT_DIR/ops/crontab.txt"
case "${1:-diff}" in
  dump)
    crontab -l >"$SNAPSHOT"
    echo "dumped $(wc -l <"$SNAPSHOT") lines to $SNAPSHOT"
    ;;
  install)
    backup="/tmp/crontab.backup.$(date +%Y%m%d_%H%M%S)"
    crontab -l >"$backup"
    crontab "$SNAPSHOT"
    echo "installed $SNAPSHOT; previous crontab saved to $backup"
    ;;
  diff)
    if diff -u "$SNAPSHOT" <(crontab -l) >/tmp/crontab.diff.$$ 2>&1; then
      echo "crontab matches $SNAPSHOT"
      rm -f /tmp/crontab.diff.$$
    else
      echo "crontab drift vs $SNAPSHOT:"; cat /tmp/crontab.diff.$$; rm -f /tmp/crontab.diff.$$
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 dump|install|diff" >&2; exit 1
    ;;
esac
