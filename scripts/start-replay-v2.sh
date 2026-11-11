#!/bin/bash
# kill15 v2 回放：与 v1 完全相同的 12 期（ref 20261112049..060），确保可比。
set -u
cd /root/penta5-runner || exit 1
mkdir -p data/kill15_tail/archive

rm -f data/kill15_tail/replay_v2.json data/kill15_tail/replay_v2.log

nohup flock -n /var/lock/penta5-kill15-replay-v2.lock \
  python3 -u scripts/replay_kill15.py \
  --from 20261112049 --to 20261112061 --sleep 1 \
  --out data/kill15_tail/replay_v2.json --tag v2 \
  --compare data/kill15_tail/archive/replay_v1_049_060.json \
  >> data/kill15_tail/replay_v2.log 2>&1 &

echo "launched pid=$!"
sleep 15
grep -vE "Warning|warnings.warn" data/kill15_tail/replay_v2.log 2>/dev/null | tail -6
