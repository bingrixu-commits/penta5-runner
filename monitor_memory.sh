#!/usr/bin/env bash
# 每 10 分钟记录 penta5 相关 Node 进程 RSS，写入 memory.log
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
LOG="${ROOT}/memory.log"
INTERVAL_SEC="${MONITOR_INTERVAL_SEC:-600}"
TREND_WINDOW_SEC="${MONITOR_TREND_WINDOW_SEC:-10800}"
RUNNER_RSS_WARN_MB="${PENTA5_RUNNER_RSS_WARN_MB:-900}"
RUNNER_RSS_GROWTH_WARN_MB="${PENTA5_RUNNER_RSS_GROWTH_WARN_MB:-180}"
RUNNER_RSS_SUSTAINED_SAMPLES="${PENTA5_RUNNER_RSS_SUSTAINED_SAMPLES:-3}"

# 只盯业务常驻进程（脚本路径片段 → 标签）
declare -A TAG_BY_PATTERN=(
  ["penta5_backend_runner.js"]="penta5-runner"
  ["api/server.js"]="penta5-rds-api"
  ["remote_state_proxy.py"]="penta5-source-proxy"
  ["penta5_supervisor.py"]="penta5-supervisor"
  ["source_proxy.js"]="penta5-source-proxy-js"
)

log_line() {
  # 统一追加一行，便于事后 grep / awk
  printf '%s\n' "$*" >>"$LOG"
}

log_runner_memory_trend() {
  # 只与同一 PID 的样本比较。服务重启后的 RSS 基线不同，不能把一次受控
  # 重启错误识别为三小时内存泄漏；本函数只报警，不会自行重启业务进程。
  python3 - "$LOG" "$TREND_WINDOW_SEC" "$RUNNER_RSS_WARN_MB" "$RUNNER_RSS_GROWTH_WARN_MB" "$RUNNER_RSS_SUSTAINED_SAMPLES" <<'PY' >>"$LOG" 2>/dev/null || true
import datetime as dt
import re
import sys
from pathlib import Path

log_path = Path(sys.argv[1])
window_seconds = max(60, int(float(sys.argv[2])))
rss_warn_mb = float(sys.argv[3])
growth_warn_mb = float(sys.argv[4])
sustained_sample_count = max(2, int(float(sys.argv[5])))
header_re = re.compile(r"^===== (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) [+-]\d{4} =====$")
runner_re = re.compile(r"pid=(\d+)\s+rss_kb=\d+\s+rss_mb=([0-9.]+)\s+tag=penta5-runner\b")
current_time = None
samples = []

try:
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
except Exception as exc:
    print(f"WARN memory-trend-read-failed error={type(exc).__name__}")
    raise SystemExit(0)

for line in lines:
    header = header_re.match(line)
    if header:
        try:
            current_time = dt.datetime.strptime(header.group(1), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            current_time = None
        continue
    row = runner_re.search(line)
    if row and current_time is not None:
        samples.append((current_time, row.group(1), float(row.group(2))))

if not samples:
    print("WARN memory-trend-no-runner-sample")
    raise SystemExit(0)

latest_time, latest_pid, latest_rss = samples[-1]
cutoff = latest_time - dt.timedelta(seconds=window_seconds)
same_pid = [(at, rss) for at, pid, rss in samples if pid == latest_pid and at >= cutoff]
if len(same_pid) < 2:
    print(
        "memory-trend tag=penta5-runner pid=%s samples=%s window_sec=%s current_mb=%.1f status=warming-up"
        % (latest_pid, len(same_pid), window_seconds, latest_rss)
    )
    raise SystemExit(0)

baseline_time, baseline_rss = same_pid[0]
peak_rss = max(value for _, value in same_pid)
growth = latest_rss - baseline_rss
span_seconds = max(0, int((latest_time - baseline_time).total_seconds()))
print(
    "memory-trend tag=penta5-runner pid=%s samples=%s span_sec=%s baseline_mb=%.1f current_mb=%.1f peak_mb=%.1f growth_mb=%.1f"
    % (latest_pid, len(same_pid), span_seconds, baseline_rss, latest_rss, peak_rss, growth)
)
if latest_rss >= rss_warn_mb:
    print("WARN memory-rss-threshold tag=penta5-runner pid=%s current_mb=%.1f threshold_mb=%.1f" % (latest_pid, latest_rss, rss_warn_mb))
if growth >= growth_warn_mb:
    recent_values = [value for _, value in same_pid[-sustained_sample_count:]]
    has_sustained_window = len(recent_values) == sustained_sample_count and len(same_pid) > sustained_sample_count
    sustained_floor = min(recent_values) if has_sustained_window else None
    sustained_growth = (sustained_floor - baseline_rss) if sustained_floor is not None else None
    if sustained_growth is not None and sustained_growth >= growth_warn_mb:
        print(
            "WARN memory-growth-threshold tag=penta5-runner pid=%s growth_mb=%.1f sustained_growth_mb=%.1f threshold_mb=%.1f samples=%s"
            % (latest_pid, growth, sustained_growth, growth_warn_mb, sustained_sample_count)
        )
    else:
        print(
            "memory-transient-spike tag=penta5-runner pid=%s growth_mb=%.1f threshold_mb=%.1f sustained_samples_required=%s"
            % (latest_pid, growth, growth_warn_mb, sustained_sample_count)
        )
PY
}

collect_once() {
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S %z')"
  log_line "===== ${ts} ====="

  # 系统内存概览
  if command -v free >/dev/null 2>&1; then
    free -m | awk -v ts="$ts" '
      /^Mem:/ { printf "sys mem_total_mb=%s used_mb=%s free_mb=%s available_mb=%s\n", $2,$3,$4,$7 }
      /^Swap:/ { printf "sys swap_total_mb=%s used_mb=%s free_mb=%s\n", $2,$3,$4 }
    ' >>"$LOG"
  fi

  local found=0
  # 用 ps 扫一遍，按业务脚本路径匹配
  while IFS= read -r line; do
    # pid rss(kb) cmd...
    local pid rss_kb cmd
    pid="$(awk '{print $1}' <<<"$line")"
    rss_kb="$(awk '{print $2}' <<<"$line")"
    cmd="$(awk '{for (i=3;i<=NF;i++) printf "%s%s", $i, (i<NF?" ":""); print ""}' <<<"$line")"
    # 跳过监控自身 / 临时 wrapper
    if [[ "$cmd" == *monitor_memory.sh* || "$cmd" == *extglob* || "$cmd" == *CURSOR_SANDBOX* ]]; then
      continue
    fi
    local match=0
    local key=""
    local pattern
    for pattern in "${!TAG_BY_PATTERN[@]}"; do
      if [[ "$cmd" == *"$pattern"* ]]; then
        match=1
        key="${TAG_BY_PATTERN[$pattern]}"
        break
      fi
    done
    if [[ $match -eq 1 ]]; then
      found=1
      local rss_mb
      rss_mb="$(awk -v k="$rss_kb" 'BEGIN { printf "%.1f", k/1024 }')"
      log_line "pid=${pid} rss_kb=${rss_kb} rss_mb=${rss_mb} tag=${key} cmd=${cmd}"
    fi
  done < <(ps -eo pid=,rss=,args= --no-headers 2>/dev/null || ps -eo pid,rss,args)

  if [[ $found -eq 0 ]]; then
    log_line "WARN no matching node/penta5 process found"
  fi

  # pm2 摘要（若可用）
  if command -v pm2 >/dev/null 2>&1; then
    pm2 jlist 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for p in data if isinstance(data, list) else []:
    name = p.get("name") or ""
    if "penta5" not in name:
        continue
    env = (p.get("pm2_env") or {})
    monit = p.get("monit") or {}
    mem = monit.get("memory")
    mb = round(mem / 1024 / 1024, 1) if isinstance(mem, (int, float)) else None
    print(
        "pm2 name=%s status=%s restarts=%s memory_mb=%s"
        % (name, env.get("status"), env.get("restart_time"), mb)
    )
' >>"$LOG" 2>/dev/null || true
  fi

  log_runner_memory_trend

  log_line ""
}

mkdir -p "$ROOT"
touch "$LOG"
log_line "===== monitor_memory started pid=$$ interval_sec=${INTERVAL_SEC} trend_window_sec=${TREND_WINDOW_SEC} ====="
log_line ""

while true; do
  collect_once || log_line "WARN collect_once failed at $(date '+%Y-%m-%d %H:%M:%S')"
  sleep "$INTERVAL_SEC"
done
