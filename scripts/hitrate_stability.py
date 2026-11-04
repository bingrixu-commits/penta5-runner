#!/usr/bin/env python3
"""命中率稳定性 / 断崖护栏（只读派生，cron 驱动）。

数据源
  data/framework_hitrate_cumulative.json  滚动窗口 100/200/300/500 期指标 + 24h/7d 覆盖率
  data/hitrate_guard.json                 缺期分类（switch_off / unexplained）
状态
  data/hitrate_stability_state.json       每个框架现行 rule_version 的首次观测时间
输出
  data/hitrate_stability.json             面板卡 + ops_health 的单一事实源

判定（保守，只为早发现「断崖」与「口径清零」，不做预测语义判断）
  coverage-cliff : 24h 覆盖率 < 80% 且窗口尾部仍漏 >= 3 期               -> alert
  fast-drop      : 滚动 100 期 z <= -2.58 且滚动 300 期 z <= -1.0        -> alert
  slow-drift     : 滚动 100 期 z <= -2.0                                 -> watch
  version-churn  : 观测到口径切换且新口径存活 < 24h 或 < 30 期           -> alert
                   存活 < 72h 或 < 100 期                                -> watch

单日样本只有约 22 期（1 sigma 约 +-10pp），因此本报告只把滚动窗口与累计显著性
当作结论口径，单日百分比仅作展示。

任何异常都以 exit 0 结束；只写 data/ 下的派生文件。
"""
import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from stats_history import beijing_now

CUMULATIVE = 'data/framework_hitrate_cumulative.json'
GUARD = 'data/hitrate_guard.json'
STATE = 'data/hitrate_stability_state.json'
OUTPUT = 'data/hitrate_stability.json'

MIN_SAMPLE = 200
FAST_DROP_Z100 = -2.58
FAST_DROP_Z300 = -1.0
SLOW_DRIFT_Z100 = -2.0
COVERAGE_CLIFF = 0.80
COVERAGE_TAIL_MISSES = 3
CHURN_ALERT_HOURS = 24.0
CHURN_ALERT_PERIODS = 30
CHURN_WATCH_HOURS = 72.0
CHURN_WATCH_PERIODS = 100
STALE_MINUTES = 45

RANK = {'ok': 0, 'watch': 1, 'alert': 2}


def _read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', dir=str(path.parent), delete=False, encoding='utf-8')
    temporary = handle.name
    try:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _pct(value, digits=1):
    return '%.*f%%' % (digits, value * 100.0) if value is not None else '-'


def _window(drift_windows, key):
    windows = drift_windows.get(str(key)) if isinstance(drift_windows, dict) else None
    if not isinstance(windows, dict):
        return None
    result = {
        'n': windows.get('n'),
        'hits': windows.get('hits'),
        'rate': _num(windows.get('rate')),
        'expected': _num(windows.get('expected')),
        'z': _num(windows.get('z')),
        'p': _num(windows.get('p_two')),
        'ci95': windows.get('ci95'),
        'full': bool(windows.get('full')),
    }
    if result['rate'] is None and isinstance(result['n'], int) and result['n'] > 0 \
            and isinstance(result['hits'], int):
        result['rate'] = result['hits'] / float(result['n'])
    return result



def _coverage_note(coverage_24h, recent):
    """24h 窗口覆盖率含窗口内历史缺口，note 用来说明缺口是否仍在发生。"""
    if not isinstance(recent, dict):
        return ''
    verified, online, missed = recent.get('verified'), recent.get('online'), recent.get('missed')
    if not isinstance(online, int) or online <= 0 or not isinstance(verified, int):
        return ''
    if isinstance(missed, int) and missed > 0:
        return '近 %d 期仍有 %d 期未核验' % (online, missed)
    if coverage_24h is not None and coverage_24h < COVERAGE_CLIFF:
        return '24h 窗口含历史缺口，近 %d 期已全部核验（缺口不再发生）' % online
    return ''

def _judge(coverage_24h, recent, w100, w300, n, churn):
    reasons = []
    level = 'ok'

    def raise_level(value):
        nonlocal level
        if RANK[value] > RANK[level]:
            level = value

    missed = recent.get('missed') if isinstance(recent, dict) else None
    if coverage_24h is not None and coverage_24h < COVERAGE_CLIFF \
            and isinstance(missed, int) and missed >= COVERAGE_TAIL_MISSES:
        reasons.append('覆盖断崖: 24h 覆盖率 %s，窗口尾部仍漏 %d 期' % (_pct(coverage_24h), missed))
        raise_level('alert')

    if w100 and w100.get('full') and isinstance(w100.get('n'), int) and w100['n'] >= 100:
        z100, z300 = w100.get('z'), (w300 or {}).get('z')
        if z100 is not None and z100 <= FAST_DROP_Z100 \
                and (z300 is None or z300 <= FAST_DROP_Z300):
            reasons.append('短期陡降: 滚动100期 z=%.2f(<=%.2f) 且 300期 z=%s'
                           % (z100, FAST_DROP_Z100, '%.2f' % z300 if z300 is not None else '-'))
            raise_level('alert')
        elif z100 is not None and z100 <= SLOW_DRIFT_Z100:
            reasons.append('短期走弱: 滚动100期 z=%.2f' % z100)
            raise_level('watch')

    if isinstance(churn, dict) and churn.get('observed_change'):
        hours = churn.get('age_hours')
        periods = churn.get('rule_n')
        hours = hours if isinstance(hours, (int, float)) else None
        periods = periods if isinstance(periods, int) else None
        if (hours is not None and hours < CHURN_ALERT_HOURS) or \
                (periods is not None and periods < CHURN_ALERT_PERIODS):
            reasons.append('口径清零风险: 现行口径仅存活 %s / %s 期'
                           % ('%.1fh' % hours if hours is not None else '-',
                              periods if periods is not None else '-'))
            raise_level('alert')
        elif (hours is not None and hours < CHURN_WATCH_HOURS) or \
                (periods is not None and periods < CHURN_WATCH_PERIODS):
            reasons.append('口径存活偏短: %s / %s 期'
                           % ('%.1fh' % hours if hours is not None else '-',
                              periods if periods is not None else '-'))
            raise_level('watch')

    if isinstance(n, int) and n < MIN_SAMPLE and not reasons:
        reasons.append('累计样本 %d < %d，判定仅供参考' % (n, MIN_SAMPLE))
    return level, reasons


def _verdict(level, usable):
    if level == 'alert':
        return '断崖/清零护栏触发'
    if level == 'watch':
        return '观察(样本或波动偏大)'
    if not usable:
        return '样本不足'
    return '正常波动'


def main(argv=None):
    parser = argparse.ArgumentParser(description='penta5 hit-rate stability / cliff guard')
    parser.add_argument('--root', default=str(ROOT))
    args = parser.parse_args(argv)
    root = Path(args.root)
    now = beijing_now()
    now_iso = now.isoformat()

    cumulative = _read_json(root / CUMULATIVE, None)
    if not isinstance(cumulative, dict) or not isinstance(cumulative.get('rows'), list):
        print('hitrate_stability: cumulative report unavailable, skip')
        return 0
    rows_in = [row for row in cumulative['rows'] if isinstance(row, dict) and row.get('key')]
    drift = cumulative.get('drift') if isinstance(cumulative.get('drift'), dict) else {}
    drift_by_key = {}
    for item in drift.get('rows') or []:
        if isinstance(item, dict) and item.get('key'):
            drift_by_key[str(item['key'])] = item.get('windows') or {}

    guard = _read_json(root / GUARD, {}) or {}
    guard_missing = ((guard.get('missing') or {}).get('models') or {}) if isinstance(guard, dict) else {}
    key_alias = {'framework': 'framework', 'he3': 'he3_refine', 'six': 'selected_six'}

    state = _read_json(root / STATE, {}) or {}
    state_rows = state.get('rows') if isinstance(state.get('rows'), dict) else {}
    next_state = {'version': 2, 'updated_at': now_iso, 'rows': {}}

    out_rows = []
    alerts, watch = [], []
    for row in rows_in:
        key = str(row['key'])
        rule = str(row.get('rule') or '')
        n = row.get('n') if isinstance(row.get('n'), int) else None
        coverage_24h = _num(row.get('coverage_24h'))
        coverage_7d = _num(row.get('coverage_7d'))
        rolling = row.get('rolling') if isinstance(row.get('rolling'), dict) else {}
        windows_24h = ((rolling.get('windows') or {}).get('24h') or {}) if isinstance(rolling.get('windows'), dict) else {}
        recent = windows_24h.get('recent') if isinstance(windows_24h.get('recent'), dict) else {}

        previous = state_rows.get(key) if isinstance(state_rows.get(key), dict) else None
        churn = {'observed_change': False, 'age_hours': None, 'rule_n': None, 'switch_at': None}
        if previous is None or not previous.get('rule'):
            entry = {'rule': rule, 'since': now_iso, 'n_at_switch': n, 'switch_at': None,
                     'observed_change': False}
        elif str(previous.get('rule')) != rule:
            entry = {'rule': rule, 'since': now_iso, 'n_at_switch': n, 'switch_at': now_iso,
                     'observed_change': True}
        else:
            entry = {'rule': rule, 'since': previous.get('since') or now_iso,
                     'n_at_switch': previous.get('n_at_switch'),
                     'switch_at': previous.get('switch_at'),
                     'observed_change': bool(previous.get('observed_change'))}
        next_state['rows'][key] = entry
        switch_at = entry.get('switch_at')
        if switch_at:
            try:
                moment = datetime.fromisoformat(str(switch_at))
                churn['observed_change'] = True
                churn['age_hours'] = (now - moment).total_seconds() / 3600.0
                churn['switch_at'] = switch_at
                base = entry.get('n_at_switch')
                if isinstance(base, int) and isinstance(n, int):
                    churn['rule_n'] = max(0, n - base)
                if churn['age_hours'] > CHURN_WATCH_HOURS and \
                        (churn['rule_n'] is None or churn['rule_n'] >= CHURN_WATCH_PERIODS):
                    churn['observed_change'] = False
            except (TypeError, ValueError):
                pass

        w100 = _window(drift_by_key.get(key), 100)
        w300 = _window(drift_by_key.get(key), 300)
        w500 = _window(drift_by_key.get(key), 500)
        level, reasons = _judge(coverage_24h, recent, w100, w300, n, churn)
        usable = isinstance(n, int) and n >= MIN_SAMPLE

        guard_row = guard_missing.get(key_alias.get(key, key)) if isinstance(guard_missing, dict) else None
        switch_off = guard_row.get('switch_off_count') if isinstance(guard_row, dict) else None

        out_rows.append({
            'key': key,
            'label': row.get('label'),
            'items': row.get('items'),
            'rule': rule,
            'n': n,
            'hits': row.get('hits'),
            'rate': _num(row.get('rate')),
            'expected': _num(row.get('expected')),
            'delta': _num(row.get('delta')),
            'p': _num(row.get('p')),
            'ci95': row.get('ci95'),
            'cum_verdict': row.get('verdict'),
            'coverage_24h': coverage_24h,
            'coverage_7d': coverage_7d,
            'recent24': {'verified': recent.get('verified'), 'online': recent.get('online'),
                         'missed': recent.get('missed'), 'hits': recent.get('hits'),
                         'rate': _num(recent.get('group_rate'))},
            'coverage_recent24': (recent['verified'] / float(recent['online'])
                                  if isinstance(recent.get('online'), int) and recent['online'] > 0
                                  and isinstance(recent.get('verified'), int) else None),
            'coverage_note': _coverage_note(coverage_24h, recent),
            'w100': w100,
            'w300': w300,
            'w500': w500,
            'switch_off_count': switch_off,
            'churn': churn,
            'stat_usable': usable,
            'cliff': {'level': level, 'reasons': reasons},
            'verdict': _verdict(level, usable),
        })
        if level == 'alert':
            alerts.append({'key': key, 'label': row.get('label'), 'reasons': reasons})
        elif level == 'watch':
            watch.append({'key': key, 'label': row.get('label'), 'reasons': reasons})

    status = 'fail' if alerts else ('warn' if watch else 'ok')
    report = {
        'version': 1,
        'revision': 'hitrate-stability-v1',
        'generated_at': now_iso,
        'as_of_period': cumulative.get('as_of_period'),
        'basis': '滚动窗口 100/200/300/500 期 + 累计显著性；单日 n 约 22 期(1 sigma 约 +-10pp)，'
                 '日百分比只作展示，不作为结论口径',
        'windows_note': '滚动窗口结束时点 = 上一个已结算期；full=false 表示窗口未填满',
        'coverage_note': 'coverage_24h 是 24h 滑动窗口覆盖率（窗口内历史缺口仍计入）；coverage_recent24 只看窗口内最近 24 期，用于判断缺口是否仍在发生',
        'thresholds': {'min_sample': MIN_SAMPLE, 'fast_drop_z100': FAST_DROP_Z100,
                       'fast_drop_z300': FAST_DROP_Z300, 'slow_drift_z100': SLOW_DRIFT_Z100,
                       'coverage_cliff': COVERAGE_CLIFF,
                       'coverage_tail_misses': COVERAGE_TAIL_MISSES,
                       'churn_alert_hours': CHURN_ALERT_HOURS,
                       'churn_alert_periods': CHURN_ALERT_PERIODS},
        'rows': out_rows,
        'summary': {'status': status, 'alerts': alerts, 'watch': watch,
                    'alert_count': len(alerts), 'watch_count': len(watch)},
    }
    _atomic_json(root / OUTPUT, report)
    _atomic_json(root / STATE, next_state)

    print('hitrate_stability %s status=%s alerts=%d watch=%d as_of=%s'
          % (now_iso, status, len(alerts), len(watch), report.get('as_of_period')))
    for row in out_rows:
        print('  %-14s n=%-5s w100=%-7s w300=%-7s cov24=%-6s %s'
              % (row['key'], row['n'] if row['n'] is not None else '-',
                 _pct(row['w100']['rate']) if row['w100'] else '-',
                 _pct(row['w300']['rate']) if row['w300'] else '-',
                 _pct(row['coverage_24h']),
                 ('ALERT ' if row['cliff']['level'] == 'alert' else
                  'WATCH ' if row['cliff']['level'] == 'watch' else 'ok'))
              + (' | ' + '; '.join(row['cliff']['reasons']) if row['cliff']['reasons'] else ''))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:  # 派生护栏绝不阻断主链
        print('hitrate_stability: unexpected error: %s' % exc)
        sys.exit(0)
