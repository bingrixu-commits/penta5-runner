#!/usr/bin/env python3
"""Read-only-ish operational health probe for the penta5-runner pipeline.

Collects a fixed set of probes into one JSON report plus a human table:

  latest_draw        newest draw period and age of its opentime (warn > 8 min)
  job:framework      data/ai_job_status.json status + last_success_ref lag (>=2 fail)
  job:killer         same for the killer job
  stat50_seal        max prediction_seals key in data/stat50_compare_seals.json
  he3_refine         reference_period lag + status
  cold_framework     reference_period lag + status
  cold_fixed_framework  reference_period lag + status
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

Writes (atomic) data/ops_health.json, data/ops_health_state.json and appends
data/ops_alerts.log only for *transitions* (a check newly entering warn/fail,
escalating, or recovering).  Exit code is 0 for every health outcome - this is
a probe, not a gate - and only 1 for an unexpected internal error.

Standard library only, read-only with respect to pipeline state: it never runs
project scripts, never writes system scheduler state or git metadata, and never calls external services.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import date, datetime
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
    ('cold_framework', 'cold_framework_status.json'),
    ('cold_fixed_framework', 'cold_fixed_framework_status.json'),
    ('selected_six', 'selected_six_status.json'),
)


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


CHECK_FUNCS = (
    check_latest_draw,
    check_ai_jobs,
    check_kill3,
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
)
FALLBACK_NAMES = {
    'check_latest_draw': ('latest_draw',),
    'check_ai_jobs': ('job:framework', 'job:killer'),
    'check_kill3': ('kill3_tail',),
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
