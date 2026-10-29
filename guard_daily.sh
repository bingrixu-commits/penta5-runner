#!/usr/bin/env bash
# run_stats.py is a one-shot refresh. Bound each guard run and retain its logs.
set -euo pipefail
umask 077

WORK_DIR="/root/penta5-runner"
STATS_SCRIPT="/root/run_stats.py"
STATS_LOG="/root/stats.log"

mkdir -p -- "$WORK_DIR/data"
exec 9>"$WORK_DIR/data/daily-guard.lock"
flock -n 9 || exit 0

# The refresh driver also serializes its own ledger and panel updates.
# Append rather than truncating earlier audit output every 30 minutes.
timeout --signal=TERM --kill-after=5s 45s python3 "$STATS_SCRIPT" >>"$STATS_LOG" 2>&1
