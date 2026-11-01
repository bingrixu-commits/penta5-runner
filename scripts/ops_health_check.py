#!/usr/bin/env python3
"""Read-only-ish operational health probe for the penta5-runner pipeline.

Collects a fixed set of probes into one JSON report plus a human table:

  latest_draw        newest draw period and age of its opentime (warn > 8 min)
  job:framework      data/ai_job_status.json status + last_success_ref lag (>=2 fail)
  job:killer         same for the killer job
  kill3_stability    24h rule_version switches / missing ref slots / publish latency p50
  kill3_freeze       freeze stamp: rule_version + monitored source hashes unchanged, period progress
  stat50_seal        max prediction_seals key in data/stat50_compare_seals.json
  he3_refine         reference_period lag + status
  selected_six       reference_period lag + status
  panel_html         panel.html mtime age (warn > 10 min, fail > 30 min)
  evidence_backup    /root/evidence-archives/latest-verified.json age + archive file
  manifest_drift     evidence_backup.newest_source_manifest() still matches live sources
  disk_root          free space on / (warn < 15%, fail < 8%)
  inode_root         free inodes on /
  memory_available   /proc/meminfo MemAvailable (warn < 15% of total)
  pm2_apps           every pm2 app online
  pm2_restarts       restart-time deltas since the previous state file
  cuopt              last CUOPT_HEALTH line ok:false -> warn
  data_tmp           stale data/tmp* leftovers (age > 10 min; warn > 2 / > 100 MiB)
  crontab_drift      live crontab vs reviewed ops/crontab.txt snapshot
  evidence_size      evidence archive + runtime observability directory sizes
  deepseek_balance   DeepSeek key total_balance (>=8 CNY ok, 3-8 warn, <3 fail)
  provider_402       recent provider HTTP 402 in the AI job logs (<30 min -> warn)
  model_usage        derived token/cache/latency/402 summary (stale > 30 min -> warn)
  coverage           滚动发布覆盖率 coverage_24h（分母=窗口内该框架已上线期数；<95% warn；
                     <80% 且缺口延续到窗口尾部（最近 24 期仍漏 >=3 期）才 fail，已恢复的历史缺口只 warn）

Writes (atomic) data/ops_health.json, data/ops_health_state.json and appends
data/ops_alerts.log only for *transitions* (a check newly entering warn/fail,
escalating, or recovering).  Exit code is 0 for every health outcome - this is
a probe, not a gate - and only 1 for an unexpected internal error.

Standard library only, read-only with respect to pipeline state: it never runs
project scripts, never writes system scheduler state or git metadata, and only calls the DeepSeek balance endpoint.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Asia/Shanghai')
OK, WARN, FAIL = 'ok', 'warn', 'fail'
RANK = {OK: 0, WARN: 1, FAIL: 2}
DEFAULT_ROOT = '/root/penta5-runner'
EVIDENCE_DIR = Path('/root/evidence-archives')
BACKUP_BASE = Path('/root/backups')
PERIOD_RE = re.compile(r'^(\d{8})(\d{3})$')
DRAW_WARN_SECONDS = 8 * 60
PANEL_WARN_SECONDS = 10 * 60
PANEL_FAIL_SECONDS = 30 * 60
EVIDENCE_WARN_SECONDS = 26 * 3600
EVIDENCE_FAIL_SECONDS = 50 * 3600
DISK_WARN_PCT = 15.0
DISK_FAIL_PCT = 8.0
MEMORY_WARN_PCT = 15.0
TMP_WARN_FILES = 2
TMP_STALE_SECONDS = 600
TMP_WARN_BYTES = 100 * 1024 * 1024
PM2_TIMEOUT = 20
HEALTH_JSON = 'ops_health.json'
CRONTAB_SNAPSHOT = 'ops/crontab.txt'
STATE_JSON = 'ops_health_state.json'
ALERTS_LOG = 'ops_alerts.log'
OK_STATES = frozenset(('success', 'current', 'published', 'preserved', 'waiting',
                       'requesting', 'running', 'pending', 'queued', 'retrying',
                       'already-running'))
MANUAL_STATES = frozenset(('paused',))
FAILED_STATES = frozenset(('failed', 'error', 'errored', 'timeout', 'killed',
                           'rejected'))
PAUSE_MARKER = 'data/ai_api_paused.json'
REFERENCE_STATUS_FILES = (
    ('he3_refine', 'he3_refine_status.json'),
    ('selected_six', 'selected_six_status.json'),
)

DEEPSEEK_ENV_KEYS = ('PENTA5_FRAMEWORK_API_KEY', 'DEEPSEEK_API_KEY', 'PENTA5_KILLER_API_KEY')
DEEPSEEK_BALANCE_URL = 'https://api.deepseek.com/user/balance'
DEEPSEEK_BALANCE_TIMEOUT = 10
BALANCE_OK_CNY = 8.0
BALANCE_WARN_CNY = 3.0
PROVIDER_402_LOGS = (
    'ai_job_framework.log',
    'ai_job_killer.log',
    'selected_six_cron.log',
    'he3_refine_cron.log',
    'kill3_tail_cron.log',
    'kill1_tail_cron.log',
    'kill9_tail_cron.log',)
PROVIDER_402_PATTERN = re.compile(r'"status"\s*:\s*402(?!\d)|HTTP\s+402(?!\d)')
PROVIDER_402_AT_RE = re.compile(r'"at"\s*:\s*"([^"]+)"')
PROVIDER_402_WINDOW_SECONDS = 30 * 60
PROVIDER_402_TAIL_LINES = 4000
PROVIDER_402_TAIL_BYTES = 8 * 1024 * 1024
HITRATE_CUMULATIVE_JSON = 'framework_hitrate_cumulative.json'
HITRATE_CUMULATIVE_WARN_SECONDS = 30 * 60
HITRATE_CUMULATIVE_FAIL_SECONDS = 90 * 60
HITRATE_CUMULATIVE_MIN_ROWS = 6
COVERAGE_WARN = 0.95
COVERAGE_FAIL = 0.80
COVERAGE_MIN_ONLINE = 100
COVERAGE_RECENT_PERIODS = 24
COVERAGE_RECENT_FAIL_MISSES = 3
KILL3_STABILITY_WINDOW_SECONDS = 24 * 3600
KILL3_STABILITY_LATENCY_SAMPLE = 20
KILL3_VERSION_WARN_CHANGES = 2
KILL3_MISSING_WARN_SLOTS = 1
KILL3_MISSING_FAIL_SLOTS = 2
KILL3_LATENCY_WARN_SECONDS = 180
KILL3_LATENCY_FAIL_SECONDS = 300
KILL3_FREEZE_JSON = 'ops/kill3-freeze.json'
KILL3_FREEZE_HOURS = 72
KILL3_FREEZE_PERIODS = 100


def _one_line(text):
    return ' '.join(str(text).split())


def _max_status(*statuses):
    best = OK
    for status in statuses:
        if RANK.get(status, 0) > RANK.get(best, 0):
            best = status
    return best


def _entry(name, status, detail, value=None):
    return {'name': name, 'status': status, 'detail': _one_line(detail), 'value': value}


def _human_bytes(count):
    number = float(count)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(number) < 1024 or unit == 'TiB':
            return ('%.0f%s' % (number, unit)) if unit == 'B' else ('%.1f%s' % (number, unit))
        number /= 1024.0
    return '%.1fTiB' % number


def _age_text(seconds):
    if seconds is None:
        return 'unknown'
    seconds = max(0.0, float(seconds))
    if seconds < 120:
        return '%.0fs' % seconds
    if seconds < 7200:
        return '%.1fm' % (seconds / 60.0)
    if seconds < 172800:
        return '%.1fh' % (seconds / 3600.0)
    return '%.1fd' % (seconds / 86400.0)


def _parse_time(value):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace(' ', 'T'))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TZ)
    return parsed.astimezone(TZ)


def _period_slots(ref):
    if not isinstance(ref, str):
        return None
    match = PERIOD_RE.match(ref.strip())
    if not match:
        return None
    digits = match.group(1)
    try:
        day = date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None
    return day.toordinal() * 288 + int(match.group(2))


def _lag_value(latest_ref, ref):
    newest = _period_slots(latest_ref)
    tested = _period_slots(ref)
    if newest is None or tested is None:
        return None
    return tested - newest


def _lag_status(lag):
    if lag is None:
        return FAIL
    if lag >= 2:
        return FAIL
    if lag <= -2:
        return WARN
    return OK


def _lag_text(lag):
    return 'unknown' if lag is None else str(lag)


def _load_json(path):
    try:
        return json.loads(Path(path).read_bytes()), None
    except FileNotFoundError:
        return None, 'missing file %s' % path
    except (OSError, ValueError) as exc:
        return None, 'unreadable %s: %s: %s' % (path, type(exc).__name__, exc)


def _tail_last_line(path, prefix=None, max_bytes=512 * 1024):
    try:
        with open(path, 'rb') as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - max_bytes))
            data = stream.read().decode('utf-8', 'replace').splitlines()
    except OSError as exc:
        return None, '%s: %s' % (type(exc).__name__, exc)
    for line in reversed(data):
        text = line.strip()
        if text and (prefix is None or text.startswith(prefix)):
            return text, None
    return None, 'no %s line' % (prefix.strip() if prefix else 'non-empty')


def _tree_size(path):
    total, files = 0, 0
    for parent, dirnames, filenames in os.walk(path):
        for filename in filenames:
            try:
                info = os.stat(os.path.join(parent, filename))
            except OSError:
                continue
            total += info.st_size
            files += 1
    return total, files


def _fsync_dir(path):
    try:
        handle = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(handle)
    except OSError:
        pass
    finally:
        os.close(handle)


def _atomic_write_json(path, payload, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=str(path.parent))
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=1)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _append_jsonl(path, payload):
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + '\n'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        os.write(handle, line.encode('utf-8'))
    finally:
        os.close(handle)


def _latest_draw(root):
    data = root / 'data'
    notes = []
    payload, error = _load_json(data / 'state.json')
    if isinstance(payload, dict):
        record = payload.get('latestRecord')
        if isinstance(record, dict):
            ref = record.get('period')
            opened = _parse_time(record.get('opentime'))
            if _period_slots(ref) is not None and opened is not None:
                return {'ref': ref, 'opentime': str(record.get('opentime')),
                        'opened': opened, 'source': 'data/state.json'}, notes
        notes.append('state.json has no usable latestRecord')
    else:
        notes.append(error or 'state.json unusable')
    payload, error = _load_json(data / 'stats_draw_history.json')
    if isinstance(payload, dict):
        records = payload.get('records')
        if isinstance(records, list) and records and isinstance(records[-1], dict):
            record = records[-1]
            ref = record.get('period')
            opened = _parse_time(record.get('opentime'))
            if _period_slots(ref) is not None and opened is not None:
                return {'ref': ref, 'opentime': str(record.get('opentime')),
                        'opened': opened,
                        'source': 'data/stats_draw_history.json'}, notes
        notes.append('stats_draw_history.json has no usable last record')
    else:
        notes.append(error or 'stats_draw_history.json unusable')
    payload, error = _load_json(data / 'runtime_observability' / 'summary.json')
    if isinstance(payload, dict) and isinstance(payload.get('source'), dict):
        source = payload['source']
        ref = source.get('latest_reference') or source.get('expected_reference')
        opened = _parse_time(source.get('latest_open_time'))
        if _period_slots(ref) is not None and opened is not None:
            return {'ref': ref, 'opentime': str(source.get('latest_open_time')),
                    'opened': opened,
                    'source': 'data/runtime_observability/summary.json'}, notes
    notes.append(error or 'runtime summary has no usable source.reference')
    return None, notes


def check_latest_draw(ctx):
    draw, notes = _latest_draw(ctx['root'])
    if draw is None:
        return _entry('latest_draw', FAIL,
                      'no usable latest draw (%s)' % '; '.join(notes))
    ctx['latest'] = draw
    age = (ctx['now'] - draw['opened']).total_seconds()
    status = WARN if age > DRAW_WARN_SECONDS else OK
    return _entry('latest_draw', status,
                  'period=%s opentime=%s age=%s source=%s'
                  % (draw['ref'], draw['opentime'], _age_text(age), draw['source']),
                  {'period': draw['ref'], 'age_seconds': round(age, 1)})


def check_ai_jobs(ctx):
    latest = ctx.get('latest')
    payload, error = _load_json(ctx['root'] / 'data' / 'ai_job_status.json')
    jobs = payload.get('jobs') if isinstance(payload, dict) else None
    entries = []
    for job in ('framework', 'killer'):
        name = 'job:' + job
        record = jobs.get(job) if isinstance(jobs, dict) else None
        if not isinstance(record, dict):
            entries.append(_entry(name, FAIL,
                                  'no %s entry in data/ai_job_status.json (%s)'
                                  % (job, error or 'invalid file')))
            continue
        job_status = str(record.get('status') or 'unknown')
        success_ref = record.get('last_success_ref')
        if latest is None:
            entries.append(_entry(name, FAIL,
                                  'cannot evaluate lag: latest draw unknown; status=%s last_success_ref=%s'
                                  % (job_status, success_ref)))
            continue
        lag = _lag_value(latest['ref'], success_ref)
        status = _lag_status(lag)
        if job_status in FAILED_STATES:
            status = _max_status(status, FAIL)
        elif job_status in MANUAL_STATES:
            status = _max_status(status, WARN)
        elif job_status not in OK_STATES:
            status = _max_status(status, WARN)
        detail = ('status=%s lag=%s last_success_ref=%s finished_at=%s'
                  % (job_status, _lag_text(lag), success_ref, record.get('finished_at')))
        if record.get('error'):
            detail += ' error=%s' % record.get('error')
        entries.append(_entry(name, status, detail,
                              {'status': job_status, 'lag': lag,
                               'last_success_ref': success_ref}))
    return entries


def check_kill3(ctx):
    """AI 三位置+一合尾：最新已开奖期应有开奖前封存；超时未封存/未发布即告警。"""
    latest = ctx.get('latest')
    if not isinstance(latest, dict) or not latest.get('ref'):
        return _entry('kill3_tail', WARN, 'latest draw unknown')
    path = ctx['root'] / 'data' / 'kill3_tail' / 'seals.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return _entry('kill3_tail', FAIL, 'missing/unreadable seals.json: %s' % exc)
    records = payload.get('records') if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return _entry('kill3_tail', FAIL, 'seals.json has no records list')
    seal = next((item for item in reversed(records) if isinstance(item, dict)), None)
    ref = str(latest.get('ref') or '')
    opened = _parse_time(latest.get('opentime'))
    age = None if opened is None else ctx['now'].timestamp() - opened.timestamp()
    seal_ref = str(seal.get('ref_period') or '') if isinstance(seal, dict) else ''
    if seal_ref == ref:
        state = str(seal.get('publication_state') or 'unknown')
        published = seal.get('published_at')
        if state == 'published' and published:
            return _entry('kill3_tail', OK,
                          'ref=%s published_at=%s' % (ref, published), {'ref': ref})
        return _entry('kill3_tail', WARN,
                      'ref=%s state=%s (not published)' % (ref, state), {'ref': ref})
    # 当前期尚未封存：开奖后 7 分钟内算正常窗口，超时仍未封存视为任务掉链。
    if isinstance(age, (int, float)) and age <= 420:
        return _entry('kill3_tail', OK,
                      'waiting: latest=%s seal_ref=%s age=%ss'
                      % (ref, seal_ref or '-', round(age)), {'ref': ref})
    age_text = _age_text(age) if isinstance(age, (int, float)) else 'unknown'
    return _entry('kill3_tail', FAIL,
                  'missing seal for latest=%s seal_ref=%s age=%s'
                  % (ref, seal_ref or '-', age_text), {'ref': ref})


def check_kill1(ctx):
    """AI 单位置+一合尾+一形态：最新已开奖期应有开奖前封存；超时未封存/未发布即告警。"""
    latest = ctx.get('latest')
    if not isinstance(latest, dict) or not latest.get('ref'):
        return _entry('kill1_tail', WARN, 'latest draw unknown')
    path = ctx['root'] / 'data' / 'kill1_tail' / 'seals.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        # 上线首期：开奖后 7 分钟宽限内允许 seals.json 尚未落盘（与 check_kill3 的等待窗口一致）；
        # 超过宽限仍无/坏 seals.json 视为任务掉链。
        opened = _parse_time(latest.get('opentime'))
        age = None if opened is None else ctx['now'].timestamp() - opened.timestamp()
        if isinstance(age, (int, float)) and age <= 420:
            return _entry('kill1_tail', WARN,
                          'waiting first seal: latest=%s age=%ss (%s)'
                          % (str(latest.get('ref') or ''), round(age), exc))
        return _entry('kill1_tail', FAIL, 'missing/unreadable seals.json: %s' % exc)
    records = payload.get('records') if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return _entry('kill1_tail', FAIL, 'seals.json has no records list')
    seal = next((item for item in reversed(records) if isinstance(item, dict)), None)
    ref = str(latest.get('ref') or '')
    opened = _parse_time(latest.get('opentime'))
    age = None if opened is None else ctx['now'].timestamp() - opened.timestamp()
    seal_ref = str(seal.get('ref_period') or '') if isinstance(seal, dict) else ''
    if seal_ref == ref:
        state = str(seal.get('publication_state') or 'unknown')
        published = seal.get('published_at')
        if state == 'published' and published:
            return _entry('kill1_tail', OK,
                          'ref=%s published_at=%s' % (ref, published), {'ref': ref})
        return _entry('kill1_tail', WARN,
                      'ref=%s state=%s (not published)' % (ref, state), {'ref': ref})
    # 当前期尚未封存：开奖后 7 分钟内算正常窗口，超时仍未封存视为任务掉链。
    if isinstance(age, (int, float)) and age <= 420:
        return _entry('kill1_tail', OK,
                      'waiting: latest=%s seal_ref=%s age=%ss'
                      % (ref, seal_ref or '-', round(age)), {'ref': ref})
    age_text = _age_text(age) if isinstance(age, (int, float)) else 'unknown'
    return _entry('kill1_tail', FAIL,
                  'missing seal for latest=%s seal_ref=%s age=%s'
                  % (ref, seal_ref or '-', age_text), {'ref': ref})


def check_kill9(ctx):
    """AI 四形态排除+二数合+三数合：最新已开奖期应有开奖前封存；超时未封存/未发布即告警。"""
    latest = ctx.get('latest')
    if not isinstance(latest, dict) or not latest.get('ref'):
        return _entry('kill9_tail', WARN, 'latest draw unknown')
    path = ctx['root'] / 'data' / 'kill9_tail' / 'seals.json'
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        # 上线首期：开奖后 7 分钟宽限内允许 seals.json 尚未落盘（与 check_kill3 的等待窗口一致）；
        # 超过宽限仍无/坏 seals.json 视为任务掉链。
        opened = _parse_time(latest.get('opentime'))
        age = None if opened is None else ctx['now'].timestamp() - opened.timestamp()
        if isinstance(age, (int, float)) and age <= 420:
            return _entry('kill9_tail', WARN,
                          'waiting first seal: latest=%s age=%ss (%s)'
                          % (str(latest.get('ref') or ''), round(age), exc))
        return _entry('kill9_tail', FAIL, 'missing/unreadable seals.json: %s' % exc)
    records = payload.get('records') if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return _entry('kill9_tail', FAIL, 'seals.json has no records list')
    seal = next((item for item in reversed(records) if isinstance(item, dict)), None)
    ref = str(latest.get('ref') or '')
    opened = _parse_time(latest.get('opentime'))
    age = None if opened is None else ctx['now'].timestamp() - opened.timestamp()
    seal_ref = str(seal.get('ref_period') or '') if isinstance(seal, dict) else ''
    if seal_ref == ref:
        state = str(seal.get('publication_state') or 'unknown')
        published = seal.get('published_at')
        if state == 'published' and published:
            return _entry('kill9_tail', OK,
                          'ref=%s published_at=%s' % (ref, published), {'ref': ref})
        return _entry('kill9_tail', WARN,
                      'ref=%s state=%s (not published)' % (ref, state), {'ref': ref})
    # 当前期尚未封存：开奖后 7 分钟内算正常窗口，超时仍未封存视为任务掉链。
    if isinstance(age, (int, float)) and age <= 420:
        return _entry('kill9_tail', OK,
                      'waiting: latest=%s seal_ref=%s age=%ss'
                      % (ref, seal_ref or '-', round(age)), {'ref': ref})
    age_text = _age_text(age) if isinstance(age, (int, float)) else 'unknown'
    return _entry('kill9_tail', FAIL,
                  'missing seal for latest=%s seal_ref=%s age=%s'
                  % (ref, seal_ref or '-', age_text), {'ref': ref})

def check_kill3_stability(ctx):
    """AI 三位置杀码稳定性：24h 规则版本切换 / 缺期槽数 / 最近 20 期发布延迟中位数。"""
    path = ctx['root'] / 'data' / 'kill3_tail' / 'seals.json'
    payload, error = _load_json(path)
    records = payload.get('records') if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        return _entry('kill3_stability', FAIL,
                      'missing/unreadable seals.json records: %s'
                      % (error or 'no records list'),
                      {'error': error or 'no records list'})
    now = ctx['now']
    cutoff = now - timedelta(seconds=KILL3_STABILITY_WINDOW_SECONDS)
    published_all, published_24h, sealed_24h = [], [], []
    for record in records:
        if not isinstance(record, dict):
            continue
        ref = record.get('ref_period')
        slots = _period_slots(ref)
        if slots is None:
            continue
        version = str(record.get('rule_version') or 'unknown')
        published_at = _parse_time(record.get('published_at'))
        sealed_at = published_at or _parse_time(record.get('generated_at'))
        if sealed_at is not None and cutoff <= sealed_at <= now:
            sealed_24h.append((ref, slots, sealed_at))
        if str(record.get('publication_state') or '') != 'published' or published_at is None:
            continue
        opened = _parse_time(record.get('reference_open_time'))
        latency_ms = None if opened is None else (published_at - opened).total_seconds() * 1000.0
        published_all.append((ref, slots, version, published_at, latency_ms))
        if cutoff <= published_at <= now:
            published_24h.append((ref, slots, version, published_at))
    published_all.sort(key=lambda item: (item[1], item[3]))
    published_24h.sort(key=lambda item: (item[1], item[3]))

    version_changes, previous_version = 0, None
    for _ref, _slots, version, _published_at in published_24h:
        if previous_version is not None and version != previous_version:
            version_changes += 1
        previous_version = version
    version_status = WARN if version_changes >= KILL3_VERSION_WARN_CHANGES else OK

    day_slots = {}
    for ref, _slots, _sealed_at in sealed_24h:
        match = PERIOD_RE.match(ref)
        if match:
            day_slots.setdefault(match.group(1), set()).add(int(match.group(2)))
    missing_slots = 0
    for day in sorted(day_slots):
        ordered = sorted(day_slots[day])
        missing_slots += sum(next_slot - slot - 1
                             for slot, next_slot in zip(ordered, ordered[1:])
                             if next_slot - slot > 1)
    # 历史缺期（如 AI 总闸关闭时段）只作 WARN；最近 6 个应封存槽位（不含当前槽）缺期才升级 FAIL。
    today_key = now.strftime('%Y%m%d')
    current_slot = ((now.hour * 60 + now.minute) // 5)
    have_today = day_slots.get(today_key, set())
    recent_missing = [slot for slot in range(max(1, current_slot - 6), current_slot)
                      if slot not in have_today]
    if len(recent_missing) >= 2:
        missing_status = FAIL
    elif missing_slots > 0 or recent_missing:
        missing_status = WARN
    else:
        missing_status = OK

    latencies_ms = [item[4] for item in
                    published_all[-KILL3_STABILITY_LATENCY_SAMPLE:]
                    if item[4] is not None]
    p50_latency = None
    if latencies_ms:
        ordered = sorted(latencies_ms)
        middle = len(ordered) // 2
        p50_latency = (ordered[middle] if len(ordered) % 2
                       else (ordered[middle - 1] + ordered[middle]) / 2.0) / 1000.0
    if p50_latency is None:
        latency_status = WARN
    elif p50_latency > KILL3_LATENCY_FAIL_SECONDS:
        latency_status = FAIL
    elif p50_latency > KILL3_LATENCY_WARN_SECONDS:
        latency_status = WARN
    else:
        latency_status = OK

    status = _max_status(version_status, missing_status, latency_status)
    if not sealed_24h:
        status = _max_status(status, WARN)
    current_version = published_all[-1][2] if published_all else '-'
    latency_text = 'n/a' if p50_latency is None else '%.0fs' % p50_latency
    detail = ('24h: version_changes=%d missing_slots=%d recent_missing=%d '
              'p50_latency=%s rule_version=%s'
              % (version_changes, missing_slots, len(recent_missing),
                 latency_text, current_version))
    return _entry('kill3_stability', status, detail,
                  {'window_hours': KILL3_STABILITY_WINDOW_SECONDS // 3600,
                   'version_changes_24h': version_changes,
                   'missing_slots_24h': missing_slots,
                   'recent_missing_6slots': len(recent_missing),
                   'p50_latency_seconds':
                       None if p50_latency is None else round(p50_latency, 1),
                   'published_24h': len(published_24h),
                   'sealed_24h': len(sealed_24h),
                   'latency_samples': len(latencies_ms),
                   'current_rule_version': current_version})


def _file_sha256(path):
    digest = hashlib.sha256()
    try:
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(65536), b''):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def check_kill3_freeze(ctx):
    """口径冻结守卫：冻结期内 RULE_VERSION 或受控源码变化即 WARN（须走变更门禁）。"""
    root = ctx['root']
    payload, error = _load_json(root / KILL3_FREEZE_JSON)
    if not isinstance(payload, dict):
        return _entry('kill3_freeze', WARN,
                      'freeze stamp missing/unreadable: %s' % (error or 'not a dict'))
    frozen_version = str(payload.get('rule_version') or '')
    frozen_at = _parse_time(payload.get('frozen_at'))
    freeze_ref = str(payload.get('freeze_ref_period') or '')
    try:
        min_periods = int(payload.get('min_periods') or KILL3_FREEZE_PERIODS)
        max_hours = float(payload.get('freeze_hours') or KILL3_FREEZE_HOURS)
    except (TypeError, ValueError):
        min_periods, max_hours = KILL3_FREEZE_PERIODS, KILL3_FREEZE_HOURS
    source = root / 'lib' / 'kill3_tail.py'
    try:
        text = source.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        return _entry('kill3_freeze', FAIL, 'cannot read kill3_tail.py: %s' % exc)
    match = re.search(r"^[ \t]*RULE_VERSION\s*=\s*'([^']+)'", text, re.M)
    current_version = match.group(1) if match else None
    problems = []
    if current_version != frozen_version:
        problems.append('口径变更 frozen=%s current=%s'
                        % (frozen_version or '-', current_version or '-'))
    changed = []
    for rel, expected in (payload.get('files') or {}).items():
        if expected and _file_sha256(root / rel) != expected:
            changed.append(str(rel))
    if changed:
        problems.append('冻结期源码变更: %s' % ', '.join(changed[:3]))
    periods = 0
    try:
        seals_payload, _ = _load_json(root / 'data' / 'kill3_tail' / 'seals.json')
        records = (seals_payload or {}).get('records') if isinstance(seals_payload, dict) else None
        for record in records or []:
            if not isinstance(record, dict):
                continue
            ref = str(record.get('ref_period') or '')
            if (record.get('publication_state') == 'published'
                    and ref and ref >= freeze_ref):
                periods += 1
    except Exception:
        periods = 0
    hours = None
    if frozen_at is not None:
        hours = (ctx['now'] - frozen_at).total_seconds() / 3600.0
    expired = bool((hours is not None and hours >= max_hours) or periods >= min_periods)
    status = WARN if problems else OK
    parts = ['freeze ' + (frozen_version or '-'),
             '进度 %d/%d 期' % (periods, min_periods)]
    if hours is not None:
        parts.append('已冻结 %.1fh/%.0fh' % (hours, max_hours))
    parts.append('冻结期已满（可评审）' if expired else '冻结中')
    if problems:
        parts.append('；'.join(problems))
    return _entry('kill3_freeze', status, ' · '.join(parts),
                  {'rule_version': frozen_version,
                   'current_rule_version': current_version,
                   'periods': periods, 'min_periods': min_periods,
                   'hours': None if hours is None else round(hours, 2),
                   'freeze_hours': max_hours, 'expired': expired,
                   'changed_files': changed})


def check_stat50(ctx):
    latest = ctx.get('latest')
    payload, error = _load_json(ctx['root'] / 'data' / 'stat50_compare_seals.json')
    seals = payload.get('prediction_seals') if isinstance(payload, dict) else None
    if not isinstance(seals, dict) or not seals:
        return _entry('stat50_seal', FAIL,
                      'no prediction_seals in data/stat50_compare_seals.json (%s)'
                      % (error or 'invalid file'))
    refs = [key for key in seals if _period_slots(key) is not None]
    if not refs:
        return _entry('stat50_seal', FAIL, 'prediction_seals has no period-shaped keys')
    last_seal = max(refs, key=_period_slots)
    if latest is None:
        return _entry('stat50_seal', FAIL,
                      'cannot evaluate lag: latest draw unknown; last_seal=%s' % last_seal)
    lag = _lag_value(latest['ref'], last_seal)
    return _entry('stat50_seal', _lag_status(lag),
                  'last_seal=%s lag=%s (latest draw %s)'
                  % (last_seal, _lag_text(lag), latest['ref']),
                  {'last_seal': last_seal, 'lag': lag})


def _paused_jobs(root):
    payload, _ = _load_json(Path(root) / PAUSE_MARKER)
    jobs = payload.get('jobs') if isinstance(payload, dict) else None
    return set(jobs) if isinstance(jobs, list) else set()


def check_reference_statuses(ctx):
    latest = ctx.get('latest')
    entries = []
    paused = _paused_jobs(ctx['root'])
    for name, filename in REFERENCE_STATUS_FILES:
        payload, error = _load_json(ctx['root'] / 'data' / filename)
        if name in paused:
            state = payload.get('status') if isinstance(payload, dict) else 'missing'
            ref = payload.get('reference_period') if isinstance(payload, dict) else None
            entries.append(_entry(name, OK,
                                  'paused (AI API disabled by %s); last status=%s reference=%s'
                                  % (PAUSE_MARKER, state, ref),
                                  {'paused': True, 'status': state, 'reference_period': ref}))
            continue
        if not isinstance(payload, dict):
            entries.append(_entry(name, FAIL,
                                  'missing/unreadable data/%s (%s)'
                                  % (filename, error or 'invalid file')))
            continue
        state = str(payload.get('status') or 'unknown')
        ref = payload.get('reference_period')
        if latest is None:
            entries.append(_entry(name, FAIL,
                                  'cannot evaluate lag: latest draw unknown; status=%s reference_period=%s'
                                  % (state, ref)))
            continue
        lag = _lag_value(latest['ref'], ref)
        status = _lag_status(lag)
        if state in FAILED_STATES:
            status = _max_status(status, FAIL)
        elif state in MANUAL_STATES:
            status = _max_status(status, WARN)
        elif state not in OK_STATES:
            status = _max_status(status, WARN)
        entries.append(_entry(name, status,
                              'status=%s reference_period=%s lag=%s updated_at=%s'
                              % (state, ref, _lag_text(lag), payload.get('updated_at')),
                              {'status': state, 'reference_period': ref, 'lag': lag}))
    return entries


def check_panel(ctx):
    path = ctx['root'] / 'panel.html'
    try:
        info = path.stat()
    except OSError as exc:
        return _entry('panel_html', FAIL, 'missing/unreadable panel.html: %s' % exc)
    age = ctx['now'].timestamp() - info.st_mtime
    if age > PANEL_FAIL_SECONDS:
        status = FAIL
    elif age > PANEL_WARN_SECONDS:
        status = WARN
    else:
        status = OK
    return _entry('panel_html', status,
                  'mtime_age=%s size=%s mtime=%s'
                  % (_age_text(age), _human_bytes(info.st_size),
                     datetime.fromtimestamp(info.st_mtime, TZ).isoformat(timespec='seconds')),
                  {'age_seconds': round(age, 1), 'bytes': info.st_size})


def check_evidence(ctx):
    receipt = EVIDENCE_DIR / 'latest-verified.json'
    payload, error = _load_json(receipt)
    if not isinstance(payload, dict):
        return _entry('evidence_backup', FAIL,
                      'receipt missing/unreadable: %s (%s)' % (receipt, error))
    status = OK
    problems = []
    value = {'verified': payload.get('verified')}
    if payload.get('verified') is not True:
        status = FAIL
        problems.append('verified=%s' % payload.get('verified'))
    verified_at = _parse_time(payload.get('verified_at'))
    if verified_at is None:
        status = FAIL
        problems.append('verified_at missing/unparseable')
    else:
        age = (ctx['now'] - verified_at).total_seconds()
        value['age_seconds'] = round(age, 1)
        if age > EVIDENCE_FAIL_SECONDS:
            status = FAIL
            problems.append('age %s > 50h' % _age_text(age))
        elif age > EVIDENCE_WARN_SECONDS:
            status = _max_status(status, WARN)
            problems.append('age %s > 26h' % _age_text(age))
    archive_name = payload.get('archive_name')
    archive_text = 'archive=missing'
    if isinstance(archive_name, str) and archive_name and '/' not in archive_name:
        archive_path = EVIDENCE_DIR / archive_name
        try:
            info = archive_path.stat()
        except OSError as exc:
            status = FAIL
            problems.append('archive %s unreadable: %s' % (archive_name, exc))
        else:
            archive_text = 'archive=%s size=%s' % (archive_name, _human_bytes(info.st_size))
            value['archive_bytes'] = info.st_size
            advertised = payload.get('size_bytes')
            if isinstance(advertised, int) and advertised != info.st_size:
                status = _max_status(status, WARN)
                problems.append('size mismatch advertised=%d actual=%d'
                                % (advertised, info.st_size))
    else:
        status = FAIL
        problems.append('archive_name invalid')
    detail = 'verified_at=%s %s' % (payload.get('verified_at'), archive_text)
    if problems:
        detail += '; ' + '; '.join(problems)
    return _entry('evidence_backup', status, detail, value)


def check_manifest(ctx):
    root = ctx['root']
    sys.path.insert(0, str(root / 'lib'))
    sys.path.insert(0, str(root))
    try:
        import evidence_backup
    except Exception as exc:
        return _entry('manifest_drift', WARN,
                      'cannot import evidence_backup: %s: %s'
                      % (type(exc).__name__, exc))
    backup_base = ctx.get('backup_base') or BACKUP_BASE
    try:
        selected = evidence_backup.newest_source_manifest(root, backup_base)
    except Exception as exc:
        return _entry('manifest_drift', FAIL,
                      'no baseline manifest matches live sources: %s: %s '
                      '(refresh: scripts/refresh_source_manifest.py --if-drift)'
                      % (type(exc).__name__, exc))
    return _entry('manifest_drift', OK, 'live sources match %s' % selected,
                  {'manifest': str(selected)})


def _free_status(percent):
    if percent < DISK_FAIL_PCT:
        return FAIL
    if percent < DISK_WARN_PCT:
        return WARN
    return OK


def check_disk(ctx):
    try:
        stats = os.statvfs('/')
    except (OSError, AttributeError) as exc:
        return [_entry('disk_root', FAIL, 'statvfs(/) failed: %s' % exc),
                _entry('inode_root', FAIL, 'statvfs(/) failed: %s' % exc)]
    total = stats.f_blocks * stats.f_frsize
    available = stats.f_bavail * stats.f_frsize
    used_pct = (100.0 * available / total) if total else 0.0
    entries = [_entry('disk_root', _free_status(used_pct),
                      'free %s = %.1f%% of %s (used %.1f%%)'
                      % (_human_bytes(available), used_pct, _human_bytes(total),
                         100.0 - used_pct),
                      {'free_percent': round(used_pct, 1),
                       'free_bytes': available, 'total_bytes': total})]
    if stats.f_files:
        inode_pct = 100.0 * stats.f_favail / stats.f_files
        entries.append(_entry('inode_root', _free_status(inode_pct),
                              'free %.1f%% (%d/%d inodes)'
                              % (inode_pct, stats.f_favail, stats.f_files),
                              {'free_percent': round(inode_pct, 1)}))
    else:
        entries.append(_entry('inode_root', OK, 'inode statistics unavailable on /'))
    return entries


def check_memory(ctx):
    try:
        text = Path('/proc/meminfo').read_text(encoding='utf-8')
    except OSError as exc:
        return _entry('memory_available', FAIL, 'cannot read /proc/meminfo: %s' % exc)
    values = {}
    for line in text.splitlines():
        if ':' not in line:
            continue
        key, _, rest = line.partition(':')
        parts = rest.split()
        if parts and parts[0].isdigit():
            values[key.strip()] = int(parts[0]) * 1024
    total = values.get('MemTotal')
    available = values.get('MemAvailable')
    if not total or available is None:
        return _entry('memory_available', FAIL,
                      'MemTotal/MemAvailable missing from /proc/meminfo')
    percent = 100.0 * available / total
    status = WARN if percent < MEMORY_WARN_PCT else OK
    return _entry('memory_available', status,
                  'available %s = %.1f%% of %s'
                  % (_human_bytes(available), percent, _human_bytes(total)),
                  {'available_percent': round(percent, 1)})


def _pm2_apps():
    try:
        completed = subprocess.run(['pm2', 'jlist'], capture_output=True,
                                   text=True, timeout=PM2_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, '%s: %s' % (type(exc).__name__, exc)
    text = (completed.stdout or '').strip()
    if not text:
        return None, 'empty pm2 jlist output (rc=%s)' % completed.returncode
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return None, 'invalid pm2 jlist JSON: %s' % exc
    if not isinstance(payload, list):
        return None, 'unexpected pm2 jlist shape'
    apps = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        env = item.get('pm2_env') if isinstance(item.get('pm2_env'), dict) else {}
        name = item.get('name') or env.get('name') or '?'
        try:
            restarts = int(env.get('restart_time') or 0)
        except (TypeError, ValueError):
            restarts = 0
        apps.append({'name': str(name),
                     'status': str(env.get('status') or 'unknown'),
                     'restarts': restarts})
    return apps, None


def check_pm2(ctx):
    apps, error = _pm2_apps()
    if apps is None:
        return [_entry('pm2_apps', WARN, 'pm2 unavailable: %s' % error),
                _entry('pm2_restarts', WARN, 'pm2 unavailable: %s' % error)]
    if not apps:
        return [_entry('pm2_apps', FAIL, 'pm2 jlist returned no applications'),
                _entry('pm2_restarts', WARN, 'no restart counters available')]
    hard = [app for app in apps
            if app['status'] in ('errored', 'stopped', 'error')]
    soft = [app for app in apps
            if app['status'] != 'online' and app not in hard]
    if hard:
        status = FAIL
    elif soft:
        status = WARN
    else:
        status = OK
    detail = '%d apps, %d online' % (len(apps),
                                     sum(1 for app in apps if app['status'] == 'online'))
    if hard:
        detail += '; not running: ' + ', '.join(
            '%s(%s)' % (app['name'], app['status']) for app in hard)
    if soft:
        detail += '; transient: ' + ', '.join(
            '%s(%s)' % (app['name'], app['status']) for app in soft)
    entries = [_entry('pm2_apps', status, detail,
                      {'apps': [{'name': app['name'], 'status': app['status']}
                                for app in apps]})]
    previous = ctx.get('state', {}).get('pm2_restarts')
    if not isinstance(previous, dict) or not previous:
        entries.append(_entry('pm2_restarts', OK,
                              'baseline established for %d apps; restart deltas start next run'
                              % len(apps)))
    else:
        deltas = []
        resets = []
        for app in apps:
            old = previous.get(app['name'])
            if not isinstance(old, int):
                continue
            if app['restarts'] > old:
                deltas.append((app['name'], app['restarts'] - old))
            elif app['restarts'] < old:
                resets.append(app['name'])
        if deltas:
            restart_status = WARN
            restart_detail = 'restarted since last run: ' + ', '.join(
                '%s(+%d)' % item for item in deltas)
        else:
            restart_status = OK
            restart_detail = 'no restart delta across %d apps' % len(apps)
        if resets:
            restart_detail += '; counter reset: ' + ', '.join(resets)
        entries.append(_entry('pm2_restarts', restart_status, restart_detail,
                              {'deltas': {name: delta for name, delta in deltas},
                               'resets': resets}))
    ctx['pm2_restarts'] = {app['name']: app['restarts'] for app in apps}
    return entries


def check_cuopt(ctx):
    log_path = ctx['root'] / 'data' / 'cuopt_health_cron.log'
    line, error = _tail_last_line(log_path, prefix='CUOPT_HEALTH ')
    if line is None:
        return _entry('cuopt', WARN,
                      'no CUOPT_HEALTH evidence in data/cuopt_health_cron.log (%s)'
                      % (error or 'missing'))
    try:
        payload = json.loads(line.split(' ', 1)[1])
    except (IndexError, ValueError):
        return _entry('cuopt', WARN, 'last CUOPT_HEALTH line is not valid JSON')
    healthy = payload.get('ok') is True
    # 2026-10-30: cuopt 服务器确认不存在, URL 已置空; 这不是故障而是已停用的
    # 外部依赖(auto-top6 立即走 JS 穷举回退), 保持 OK 以免每 10 分钟误报。
    if payload.get('error') == 'cuopt-url-unset':
        return _entry('cuopt', OK, 'disabled: cuopt endpoint unset (JS fallback active)',
                      {'disabled': True, 'checked_at': payload.get('checked_at')})
    status = OK if healthy else WARN
    detail = ('ok=%s checked_at=%s error=%s local_python_fallback=%s'
              % (payload.get('ok'), payload.get('checked_at'), payload.get('error'),
                 payload.get('local_python_fallback')))
    return _entry('cuopt', status, detail,
                  {'ok': payload.get('ok'), 'checked_at': payload.get('checked_at')})


def check_data_tmp(ctx):
    data = ctx['root'] / 'data'
    try:
        candidates = list(data.iterdir())
    except OSError as exc:
        return _entry('data_tmp', FAIL, 'cannot list data/: %s' % exc)
    count, total, names, young = 0, 0, [], 0
    now = time.time()
    for item in candidates:
        if not item.name.startswith('tmp'):
            continue
        try:
            age = now - item.stat().st_mtime
        except OSError:
            continue
        # A refresh writes 100-300MB tmp files for a few seconds and renames
        # them; only count leftovers older than 10 minutes as debris.
        if age < TMP_STALE_SECONDS:
            young += 1
            continue
        count += 1
        names.append(item.name)
        try:
            if item.is_symlink():
                continue
            if item.is_file():
                total += item.stat().st_size
            elif item.is_dir():
                size, _ = _tree_size(item)
                total += size
        except OSError:
            continue
    status = WARN if count > TMP_WARN_FILES or total > TMP_WARN_BYTES else OK
    detail = 'stale_entries=%d bytes=%s names=%s (in-flight=%d)' % (
        count, _human_bytes(total), ','.join(sorted(names)[:5]) or 'none', young)
    return _entry('data_tmp', status, detail, {'count': count, 'bytes': total, 'in_flight': young})


def check_evidence_size(ctx):
    sizes = {}
    total = 0
    missing = []
    for label, path in (('evidence-archives', EVIDENCE_DIR),
                        ('runtime_observability',
                         ctx['root'] / 'data' / 'runtime_observability')):
        if not path.is_dir():
            missing.append(label)
            continue
        size, files = _tree_size(path)
        sizes[label] = {'bytes': size, 'files': files}
        total += size
    if not sizes:
        return _entry('evidence_size', FAIL,
                      'no evidence directories readable (%s)' % ','.join(missing))
    detail = '; '.join('%s=%s/%d files'
                       % (label, _human_bytes(sizes[label]['bytes']),
                          sizes[label]['files']) for label in sizes)
    if missing:
        detail += '; missing: ' + ','.join(missing)
    return _entry('evidence_size', OK, detail, {'bytes': total, 'dirs': sizes})


def check_crontab(ctx):
    """Live root crontab vs the reviewed repo snapshot (ops/crontab.txt)."""
    snapshot = ctx['root'] / CRONTAB_SNAPSHOT
    if not snapshot.is_file():
        return _entry('crontab_drift', WARN, 'snapshot missing: %s' % CRONTAB_SNAPSHOT)
    try:
        expected = [line for line in snapshot.read_text(encoding='utf-8').splitlines()
                    if line.strip() and not line.lstrip().startswith('#')]
    except OSError as exc:
        return _entry('crontab_drift', WARN, 'cannot read snapshot: %s' % exc)
    try:
        result = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return _entry('crontab_drift', WARN, 'cannot read live crontab: %s' % exc)
    if result.returncode != 0:
        return _entry('crontab_drift', WARN, 'crontab -l exited %s' % result.returncode)
    live = [line for line in result.stdout.splitlines()
            if line.strip() and not line.lstrip().startswith('#')]
    if live == expected:
        return _entry('crontab_drift', OK,
                      'live crontab matches %s (%d jobs)' % (CRONTAB_SNAPSHOT, len(live)),
                      {'jobs': len(live)})
    missing = sorted(set(expected) - set(live))
    extra = sorted(set(live) - set(expected))
    detail = 'drift vs %s: missing=%d extra=%d' % (CRONTAB_SNAPSHOT, len(missing), len(extra))
    if missing:
        detail += '; missing_sample=' + ' | '.join(line.split(' ', 4)[-1][:90] for line in missing[:2])
    if extra:
        detail += '; extra_sample=' + ' | '.join(line.split(' ', 4)[-1][:90] for line in extra[:2])
    return _entry('crontab_drift', WARN, detail,
                  {'missing': len(missing), 'extra': len(extra)})


def _dotenv_secret(path, keys):
    """Return (name, value) for the first present .env key; never expose value elsewhere."""
    found = {}
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as stream:
            for raw in stream:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                name, _, value = line.partition('=')
                name = name.strip()
                if name.startswith('export '):
                    name = name[len('export '):].strip()
                if name not in keys:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                    value = value[1:-1].strip()
                if value:
                    found[name] = value
    except OSError as exc:
        return None, 'cannot read .env: %s' % type(exc).__name__
    for name in keys:
        if name in found:
            return name, found[name]
    return None, 'no API key in .env (%s)' % '/'.join(keys)


def check_deepseek_balance(ctx):
    """DeepSeek 余额: >=8 CNY ok, 3-8 CNY warn, <3 CNY fail; 查询/解析失败 warn。"""
    source, secret = _dotenv_secret(ctx['root'] / '.env', DEEPSEEK_ENV_KEYS)
    if source is None:
        return _entry('deepseek_balance', WARN, 'balance skipped: %s' % secret)
    request = urllib.request.Request(
        DEEPSEEK_BALANCE_URL,
        headers={'Authorization': 'Bearer ' + secret, 'Accept': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=DEEPSEEK_BALANCE_TIMEOUT) as response:
            payload = json.loads(response.read().decode('utf-8', 'replace'))
    except urllib.error.HTTPError as exc:
        return _entry('deepseek_balance', WARN,
                      'balance query failed: HTTPError %s' % exc.code)
    except (OSError, ValueError) as exc:
        return _entry('deepseek_balance', WARN,
                      'balance query failed: %s' % type(exc).__name__)
    try:
        first = payload['balance_infos'][0]
        balance = float(first['total_balance'])
        currency = str(first.get('currency') or 'CNY')
        available = payload.get('is_available')
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return _entry('deepseek_balance', WARN,
                      'balance parse failed: %s' % type(exc).__name__)
    if balance >= BALANCE_OK_CNY:
        status = OK
    elif balance >= BALANCE_WARN_CNY:
        status = WARN
    else:
        status = FAIL
    detail = ('total_balance=%.2f %s is_available=%s source=%s (ok>=%.0f warn>=%.0f)'
              % (balance, currency, available, source, BALANCE_OK_CNY, BALANCE_WARN_CNY))
    return _entry('deepseek_balance', status, detail,
                  {'balance': balance, 'currency': currency,
                   'is_available': available, 'source_key': source})


def _read_log_tail(path, max_lines=PROVIDER_402_TAIL_LINES,
                   max_bytes=PROVIDER_402_TAIL_BYTES):
    with open(path, 'rb') as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(max(0, size - max_bytes))
        data = stream.read()
    lines = data.decode('utf-8', 'replace').splitlines()
    if size > len(data) and lines:
        lines = lines[1:]
    return lines[-max_lines:]


def _provider_402_time(line):
    match = PROVIDER_402_AT_RE.search(line)
    if match is None:
        return None
    text = match.group(1).strip()
    if text.endswith(('Z', 'z')):
        text = text[:-1] + '+00:00'
    return _parse_time(text)


def check_provider_402(ctx):
    """近 30 分钟 AI 任务日志出现 provider HTTP 402 -> warn; 读不到按 unknown/ok。"""
    data = ctx['root'] / 'data'
    now = ctx['now']
    events = []
    skipped = []
    untimed_skipped = 0
    for filename in PROVIDER_402_LOGS:
        path = data / filename
        try:
            lines = _read_log_tail(path)
        except OSError as exc:
            skipped.append('%s:%s' % (filename, type(exc).__name__))
            continue
        timed = []
        untimed = []
        for index, line in enumerate(lines):
            if not PROVIDER_402_PATTERN.search(line):
                continue
            moment = _provider_402_time(line)
            if moment is None:
                untimed.append(index)
            else:
                timed.append((index, moment))
        moments = [moment for _, moment in timed]
        # 无时间戳 402 行(同一 402 的报错/traceback)只就近挂到同文件前 50 行内
        # 带 at 的 AI_HTTP_PHASE 402 事件; 不再用文件 mtime 兜底——活跃日志的
        # mtime 一直刷新, 会把旧 402 文本行算成"近期".
        for index in untimed:
            attached = None
            for timed_index, moment in timed:
                if index - 50 <= timed_index < index:
                    attached = moment
            if attached is None:
                untimed_skipped += 1
                continue
            moments.append(attached)
        for moment in moments:
            events.append({'file': 'data/' + filename, 'time': moment})
    recent = [event for event in events
              if (now - event['time']).total_seconds() <= PROVIDER_402_WINDOW_SECONDS]
    untimed_note = ('; untimed_skipped=%d' % untimed_skipped) if untimed_skipped else ''
    if recent:
        latest = max(recent, key=lambda item: item['time'])
        detail = ('402 in last 30m: count=%d latest_file=%s latest_at=%s%s'
                  % (len(recent), latest['file'],
                     latest['time'].isoformat(timespec='seconds'), untimed_note))
        if skipped:
            detail += '; skipped=' + ','.join(skipped[:3])
        return _entry('provider_402', WARN, detail,
                      {'recent': len(recent), 'events': len(events),
                       'untimed_skipped': untimed_skipped,
                       'latest_file': latest['file'],
                       'latest_at': latest['time'].isoformat(timespec='seconds'),
                       'skipped': len(skipped)})
    detail = '近30分钟无402'
    if events:
        latest = max(events, key=lambda item: item['time'])
        detail += (' (older_402=%d last=%s)'
                   % (len(events), latest['time'].isoformat(timespec='seconds')))
    detail += untimed_note
    if skipped:
        detail += '; skipped=' + ','.join(skipped[:3])
    return _entry('provider_402', OK, detail,
                  {'recent': 0, 'events': len(events),
                   'untimed_skipped': untimed_skipped, 'skipped': len(skipped)})


MODEL_USAGE_STALE_SECONDS = 30 * 60


def check_model_usage(ctx):
    """Derived model usage summary (data/model_usage/summary.json); read-only."""
    path = ctx['root'] / 'data' / 'model_usage' / 'summary.json'
    payload, error = _load_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get('today'), dict):
        return _entry('model_usage', WARN,
                      'model usage summary unusable (%s)' % (error or 'invalid shape'),
                      {'state': 'missing'})
    now = ctx['now']
    updated = _parse_time(payload.get('updated_at'))
    age = (now - updated).total_seconds() if updated is not None else None
    if age is None or age > MODEL_USAGE_STALE_SECONDS:
        return _entry('model_usage', WARN,
                      'model usage collector stale: %s' % _age_text(age),
                      {'updated_at': payload.get('updated_at')})
    today = payload['today']
    tokens = today.get('tokens') if isinstance(today.get('tokens'), dict) else {}
    errors = today.get('errors') if isinstance(today.get('errors'), dict) else {}
    latency = today.get('latency') if isinstance(today.get('latency'), dict) else {}
    balance = payload.get('balance') if isinstance(payload.get('balance'), dict) else {}
    health = payload.get('health') if isinstance(payload.get('health'), dict) else {}
    reasons = health.get('reasons') if isinstance(health.get('reasons'), list) else []
    status = {'ok': OK, 'warn': WARN, 'fail': FAIL}.get(str(health.get('status')), OK)
    cache_rate = tokens.get('cache_rate')
    p95 = latency.get('p95')
    balance_value = balance.get('balance')
    detail = ('calls=%s ok=%s fail=%s 402=%s retries=%s cache=%s p95=%s balance=%s age=%s'
              % (today.get('attempts'), today.get('responses'), today.get('error_total'),
                 errors.get('payment', 0), today.get('retries'),
                 '%.1f%%' % (cache_rate * 100) if isinstance(cache_rate, (int, float)) else '-',
                 '%.1fs' % p95 if isinstance(p95, (int, float)) else '-',
                 '%.2f' % balance_value if isinstance(balance_value, (int, float)) else '-',
                 _age_text(age)))
    if reasons:
        detail += '; ' + '; '.join(str(item) for item in reasons[:3])
    return _entry('model_usage', status, detail,
                  {'attempts': today.get('attempts'), 'responses': today.get('responses'),
                   'errors': today.get('error_total'),
                   'payment': errors.get('payment', 0),
                   'tokens': tokens, 'latency': latency,
                   'balance': balance, 'updated_at': payload.get('updated_at'),
                   'health': health})


def check_hitrate_cumulative(ctx):
    """累计命中率报告新鲜度: cron 每 15 分钟生成; >30 分钟 warn, >90 分钟 fail, 缺失 fail。"""
    path = ctx['root'] / 'data' / HITRATE_CUMULATIVE_JSON
    document, error = _load_json(path)
    if document is None:
        return _entry('hitrate_cumulative', FAIL, '累计命中率报告不可用: %s' % error)
    rows = document.get('rows') if isinstance(document, dict) else None
    generated = _parse_time(document.get('generated_at')) if isinstance(document, dict) else None
    if generated is None:
        return _entry('hitrate_cumulative', WARN, '累计命中率报告缺少 generated_at')
    age = max(0.0, (ctx['now'] - generated).total_seconds())
    if age <= HITRATE_CUMULATIVE_WARN_SECONDS:
        status = OK
    elif age <= HITRATE_CUMULATIVE_FAIL_SECONDS:
        status = WARN
    else:
        status = FAIL
    rows_count = len(rows) if isinstance(rows, list) else 0
    detail = ('generated_at=%s age=%s rows=%d as_of=%s'
              % (generated.isoformat(timespec='seconds'), _age_text(age), rows_count,
                 (document.get('as_of_period') or '-')))
    if rows_count < HITRATE_CUMULATIVE_MIN_ROWS:
        status = _max_status(status, WARN)
        detail += ' (rows<%d)' % HITRATE_CUMULATIVE_MIN_ROWS
    return _entry('hitrate_cumulative', status, detail,
                  {'age_seconds': int(age), 'rows': rows_count,
                   'as_of_period': document.get('as_of_period')})


def check_coverage(ctx):
    """滚动发布覆盖率（现行水平），读 framework_hitrate_cumulative.json 的 coverage_24h。

    分母 = 窗口内该框架「已上线」期数（framework_hitrate_cumulative.py 从首条 verified/seal 的
    target_period 起算、窗口起点取 max(上线期, window_start)），因此框架「引入前」与 24h 窗口外的
    历史缺口不会被算成现行掉链。
    24h 窗口内仍含历史缺口（如 2026-10-31 01091..01128 段余额停跑/总闸关闭）但已恢复时只报 WARN；
    只有缺口延续到窗口尾部（最近 COVERAGE_RECENT_PERIODS 期内仍漏 >= COVERAGE_RECENT_FAIL_MISSES 期）
    才判 FAIL——既不会对已恢复的历史缺口长期 FAIL，也能在真掉链时从 WARN 升级到 FAIL。
    """
    path = ctx['root'] / 'data' / HITRATE_CUMULATIVE_JSON
    document, error = _load_json(path)
    if not isinstance(document, dict):
        return _entry('coverage', WARN,
                      '累计命中率报告不可用，无法评估滚动覆盖率: %s' % (error or 'invalid shape'))
    rows = document.get('rows')
    if not isinstance(rows, list) or not rows:
        return _entry('coverage', WARN, '累计命中率报告没有 rows，无法评估滚动覆盖率')
    window = document.get('rolling_window')
    window = window if isinstance(window, dict) else {}
    status = OK
    details, values = [], {}
    applicable = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = str(row.get('key') or '?')
        coverage = row.get('coverage_24h')
        if not isinstance(coverage, (int, float)) or isinstance(coverage, bool):
            continue
        rolling = row.get('rolling')
        windows = rolling.get('windows') if isinstance(rolling, dict) else None
        window_24h = windows.get('24h') if isinstance(windows, dict) else None
        online = window_24h.get('online') if isinstance(window_24h, dict) else None
        if not isinstance(online, int) or isinstance(online, bool) or online < COVERAGE_MIN_ONLINE:
            continue
        applicable += 1
        last_missed = window_24h.get('last_missed_period') if isinstance(window_24h, dict) else None
        recent = window_24h.get('recent') if isinstance(window_24h, dict) else None
        recent = recent if isinstance(recent, dict) else {}
        recent_missed = recent.get('missed')
        if not isinstance(recent_missed, int) or isinstance(recent_missed, bool):
            recent_missed = None
        if coverage < COVERAGE_FAIL:
            if recent_missed is None:
                row_status = WARN
                note = '缺近%d期明细，按历史缺口处理(最后漏期 %s)' % (COVERAGE_RECENT_PERIODS,
                                                                    last_missed or '-')
            elif recent_missed < COVERAGE_RECENT_FAIL_MISSES:
                row_status = WARN
                note = '历史缺口已恢复(近%d期漏%d期，最后漏期 %s)' % (
                    COVERAGE_RECENT_PERIODS, recent_missed, last_missed or '-')
            else:
                row_status = FAIL
                note = '缺口延续到窗口尾部(近%d期仍漏%d期，最后漏期 %s)' % (
                    COVERAGE_RECENT_PERIODS, recent_missed, last_missed or '-')
        elif coverage < COVERAGE_WARN:
            row_status = WARN
            note = '低于 95%%(最后漏期 %s)' % (last_missed or '-')
        else:
            row_status = OK
            note = ''
        status = _max_status(status, row_status)
        values[key] = {'coverage_24h': coverage, 'online': online,
                       'last_missed_period': last_missed,
                       'recent_missed': recent_missed, 'status': row_status}
        details.append('%s=%.2f%%(%d期%s)' % (key, coverage * 100, online,
                                              '，' + note if note else ''))
    if not applicable:
        return _entry('coverage', OK,
                      '窗口内已上线期数>=%d 的框架为 0，暂不判定滚动覆盖率' % COVERAGE_MIN_ONLINE,
                      {'applicable': 0, 'window_end': window.get('end_period')})
    detail = '24h覆盖率(分母=窗口内已上线期数，window_end=%s): %s' % (
        window.get('end_period') or '-', '; '.join(details))
    return _entry('coverage', status, detail,
                  {'applicable': applicable, 'window_end': window.get('end_period'),
                   'rows': values})


AI_CONTROL_JSON = 'ai_api_control.json'
AI_GUARD_JSON = 'ai_balance_guard.json'
AI_GUARD_STALE_SECONDS = 10 * 60


def check_ai_control(ctx):
    """AI 总闸与余额守护心跳: 关闭或心跳停更 -> warn；用于发现漏期前的静默停止。"""
    control, error = _load_json(ctx['root'] / 'data' / AI_CONTROL_JSON)
    guard, guard_error = _load_json(ctx['root'] / 'data' / AI_GUARD_JSON)
    enabled = control.get('enabled') if isinstance(control, dict) else None
    auto_paused = guard.get('auto_paused') if isinstance(guard, dict) else None
    balance = guard.get('last_balance') if isinstance(guard, dict) else None
    checked = _parse_time(guard.get('last_checked_at')) if isinstance(guard, dict) else None
    age = (ctx['now'] - checked).total_seconds() if checked is not None else None
    if enabled is False:
        detail = 'AI 总闸已关闭 generation=%s' % (control.get('generation'),)
        if auto_paused:
            detail += '；余额守护自动暂停 balance=%s' % (balance,)
        return _entry('ai_control', WARN, detail,
                      {'enabled': False, 'auto_paused': bool(auto_paused), 'balance': balance})
    if enabled is not True:
        return _entry('ai_control', WARN, 'AI 总闸状态不可读: %s' % (error or 'missing-enabled'))
    if age is None or age > AI_GUARD_STALE_SECONDS:
        return _entry('ai_control', WARN, '余额守护心跳停更: %s' % _age_text(age),
                      {'age_seconds': None if age is None else int(age)})
    return _entry('ai_control', OK,
                  'AI 总闸开启 generation=%s；余额守护 age=%s balance=%s'
                  % (control.get('generation'), _age_text(age), balance),
                  {'enabled': True, 'age_seconds': int(age), 'balance': balance})


CHECK_FUNCS = (
    check_latest_draw,
    check_ai_jobs,
    check_kill3,
    check_kill1,
    check_kill9,
    check_kill3_stability,
    check_kill3_freeze,
    check_stat50,
    check_reference_statuses,
    check_panel,
    check_evidence,
    check_manifest,
    check_disk,
    check_memory,
    check_pm2,
    check_cuopt,
    check_data_tmp,
    check_crontab,
    check_evidence_size,
    check_deepseek_balance,
    check_provider_402,
    check_hitrate_cumulative,
    check_coverage,
    check_ai_control,
    check_model_usage,
)
FALLBACK_NAMES = {
    'check_latest_draw': ('latest_draw',),
    'check_ai_jobs': ('job:framework', 'job:killer'),
    'check_kill3': ('kill3_tail',),
    'check_kill1': ('kill1_tail',),
    'check_kill9': ('kill9_tail',),
    'check_kill3_stability': ('kill3_stability',),
    'check_kill3_freeze': ('kill3_freeze',),
    'check_stat50': ('stat50_seal',),
    'check_reference_statuses': tuple(name for name, _ in REFERENCE_STATUS_FILES),
    'check_panel': ('panel_html',),
    'check_evidence': ('evidence_backup',),
    'check_manifest': ('manifest_drift',),
    'check_disk': ('disk_root', 'inode_root'),
    'check_memory': ('memory_available',),
    'check_pm2': ('pm2_apps', 'pm2_restarts'),
    'check_cuopt': ('cuopt',),
    'check_data_tmp': ('data_tmp',),
    'check_crontab': ('crontab_drift',),
    'check_evidence_size': ('evidence_size',),
    'check_deepseek_balance': ('deepseek_balance',),
    'check_provider_402': ('provider_402',),
    'check_hitrate_cumulative': ('hitrate_cumulative',),
    'check_coverage': ('coverage',),
    'check_ai_control': ('ai_control',),
    'check_model_usage': ('model_usage',),
}


def _transitions(previous_checks, checks, now_iso):
    transitions = []
    for entry in checks:
        previous = previous_checks.get(entry['name']) if isinstance(previous_checks, dict) else None
        previous_status = previous.get('status') if isinstance(previous, dict) else None
        previous_rank = RANK.get(previous_status, 0)
        new_rank = RANK.get(entry['status'], 0)
        if new_rank == previous_rank:
            continue
        if new_rank > previous_rank:
            kind = 'raise'
        elif new_rank == 0:
            kind = 'recovered'
        else:
            kind = 'ease'
        transitions.append({'at': now_iso, 'check': entry['name'],
                            'from': previous_status or OK, 'to': entry['status'],
                            'kind': kind, 'detail': entry['detail']})
    return transitions


def _print_table(report, transitions):
    checks = report['checks']
    width = max([len(entry['name']) for entry in checks] + [5])
    print('penta5 ops health: overall=%s checked_at=%s'
          % (report['overall'].upper(), report['checked_at']))
    print('%-4s  %-*s  %s' % ('ST', width, 'CHECK', 'DETAIL'))
    for entry in checks:
        print('%-4s  %-*s  %s'
              % (entry['status'].upper(), width, entry['name'], entry['detail']))
    if transitions:
        print('alerts appended: %d (%s)'
              % (len(transitions),
                 ', '.join('%s:%s->%s' % (item['check'], item['from'], item['to'])
                           for item in transitions)))
    else:
        print('alerts appended: 0 (no new transitions)')


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Read-only operational health probe for the penta5-runner pipeline.')
    parser.add_argument('--root', default=DEFAULT_ROOT,
                        help='project root (default %s)' % DEFAULT_ROOT)
    parser.add_argument('--json', dest='as_json', action='store_true',
                        help='print the report as JSON instead of a table')
    parser.add_argument('--quiet', action='store_true',
                        help='suppress the human table; files are still written')
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    now = datetime.now(TZ)
    checked_at = now.isoformat(timespec='milliseconds')
    data = root / 'data'
    if not data.is_dir():
        print('ops_health_check: project data directory missing: %s' % data,
              file=sys.stderr)
        return 1
    state, _ = _load_json(data / STATE_JSON)
    if not isinstance(state, dict):
        state = {}
    ctx = {'root': root, 'now': now, 'latest': None, 'state': state,
           'backup_base': BACKUP_BASE}
    checks = []
    for function in CHECK_FUNCS:
        try:
            result = function(ctx)
        except Exception as exc:
            result = [_entry(name, FAIL,
                             'probe crashed: %s: %s'
                             % (type(exc).__name__, exc))
                      for name in FALLBACK_NAMES[function.__name__]]
        if isinstance(result, dict):
            result = [result]
        for entry in result:
            if entry.get('status') not in RANK:
                entry['status'] = WARN
        checks.extend(result)
    overall = OK
    for entry in checks:
        overall = _max_status(overall, entry['status'])
    previous_checks = state.get('checks') if isinstance(state.get('checks'), dict) else {}
    transitions = _transitions(previous_checks, checks, checked_at)
    report = {'version': 1, 'checked_at': checked_at, 'overall': overall,
              'checks': checks, 'alerts': transitions}
    try:
        _atomic_write_json(data / HEALTH_JSON, report)
        for transition in transitions:
            _append_jsonl(data / ALERTS_LOG, transition)
        _atomic_write_json(data / STATE_JSON, {
            'version': 1, 'checked_at': checked_at, 'overall': overall,
            'checks': {entry['name']: {'status': entry['status'],
                                       'detail': entry['detail']}
                       for entry in checks},
            'pm2_restarts': ctx.get('pm2_restarts') or {},
        })
    except OSError as exc:
        print('ops_health_check: cannot persist results: %s' % exc, file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False), flush=True)
    elif not args.quiet:
        _print_table(report, transitions)
    return 0


if __name__ == '__main__':
    sys.exit(main())
