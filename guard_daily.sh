#!/usr/bin/env bash
# Fallback refresh only: the long-running pm2 sync loop (sync_state_to_panel.py)
# already runs /root/run_stats.py on every source change / every minute. This
# guard only needs to cover the case where that loop is dead, so it skips when
# the panel is fresh or another refresh already holds the shared stats lock.
# 2026-10-30: a full run now takes 85-250s (selected_six/he3 stages read
# 300MB+ ledgers); the old unconditional 45s bound killed every run it started.
set -euo pipefail
umask 077

WORK_DIR="/root/penta5-runner"
STATS_SCRIPT="/root/run_stats.py"
STATS_LOG="/root/stats.log"
PANEL="$WORK_DIR/panel.html"
FRESH_SECONDS="${PENTA5_GUARD_FRESH_SECONDS:-900}"
REFRESH_TIMEOUT=300

mkdir -p -- "$WORK_DIR/data"
exec 9>"$WORK_DIR/data/daily-guard.lock"
flock -n 9 || exit 0

if [ -f "$PANEL" ]; then
  panel_age=$(( $(date +%s) - $(stat -c %Y "$PANEL") ))
  if [ "$panel_age" -lt "$FRESH_SECONDS" ]; then
    exit 0
  fi
fi

# Do not queue behind the minute-driven refresh; its own run will refresh.
exec 8>"$WORK_DIR/data/stats-refresh.lock"
if ! flock -n 8; then
  exit 0
fi
flock -u 8
exec 8>&-

timeout --signal=TERM --kill-after=10s "${REFRESH_TIMEOUT}s" python3 "$STATS_SCRIPT" >>"$STATS_LOG" 2>&1
