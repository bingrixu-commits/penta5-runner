# -*- coding: utf-8 -*-
"""三数共现避开 1/2/3/4 组 · 业务日核验统计（只读派生，不改任何模型）。

口径（写死，避免误读）：
- 四组兄弟项：threeDigitAvoid「避开1组」、threeDigitHit「避开2组」、
  threeDigitAvoid2「避开3组」、threeDigitAvoid3「避开4组（参考）」；
  第 4 组自 2026-11-13 起由后端按同一候选池生成，只做面板展示与单组统计，
  不进入 kill1/kill3 的攻击语与整组命中（那两个框架仍按前 3 组、口径不变）。
- 单项命中 = 该组 3 个数字【未全部】出现在 4 球开奖号中（避开成功），
  即 evaluation.independentExtras.<key>.success == True；单项随机基线
  = 1 - P(3 个指定数字在 4 球全部出现) = 97.96%（10^4 精确枚举）；
- 三组合并命中（历史口径）= 同一期前 3 组【全部】避开成功（合取，缺任一组不计入）；
- 四组合并命中（现行口径）= 同一期 4 组【全部】避开成功（合取，第 4 组缺失不计入）；
- 合并随机基线 = 每期按该期各组号码对 10^4 开奖组合精确枚举
  (1 - P(至少一组的数字全部出现))，再按期平均；
- 业务日 = 北京时间 05:00 切日（PENTA5_DAY_RESET_HOUR=5）；
- 过滤条件与后端 projectDailyDisplayCounters / filterDailyDisplayPredictions
  一致：evaluated/truthEligible/voided、immediate 期过渡、开奖时刻不晚于 now、
  目标期分钟与开奖分钟一致、generatedAt 先于开奖、前 4 球数字合法、
  按 targetPeriod 去重；业务日归属按「开奖时刻 - 5h」分组。

产物：
- data/three_avoid2_daily.json：本业务日 + 上一业务日的 4 组单组统计
  （groups_current/groups_previous）、三组合并（current/previous，兼容旧调用方）、
  四组合并（merged4_current/merged4_previous）+ 最新已核验 / 下一期待核验四组号码；
- data/three_avoid2_history.json：按业务日累计（单调取 max(total)，跨预测
  滚动窗口保留已封盘日），按日存 groups / merged / merged4 / single。
"""
import json
import math
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path('/root/penta5-runner')
STATE_PATH = ROOT / 'data' / 'state.json'
OUT_PATH = ROOT / 'data' / 'three_avoid2_daily.json'
HISTORY_PATH = ROOT / 'data' / 'three_avoid2_history.json'

GROUP_KEYS = ('threeDigitAvoid', 'threeDigitHit', 'threeDigitAvoid2', 'threeDigitAvoid3')
HISTORICAL_KEYS = GROUP_KEYS[:3]
GROUP_LABELS = {
    'threeDigitAvoid': '避开1组',
    'threeDigitHit': '避开2组',
    'threeDigitAvoid2': '避开3组',
    'threeDigitAvoid3': '避开4组',
}
SINGLE_KEY = 'threeDigitAvoid2'
SINGLE_BASELINE = 97.96  # 10^4 精确枚举：1 - P(3 个指定数字在 4 球全部出现)
DAY_RESET_HOUR = 5
PERIODS_PER_DAY = 288
DRAW_SECOND = 12
MIN_SAMPLE = 200

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai，与服务器本地时区一致
_ENUM_CACHE = {}


def _parse_any(text):
    """按后端 parseLocalDateTimeValue 语义解析为 +08 aware datetime。

    'YYYY-MM-DD HH:MM:SS'（无时区）按 Asia/Shanghai 本地解析；
    带 Z/±hh:mm 的先按本地解析再换算偏移。
    """
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text:
        return None
    plain = re.match(
        r'^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$', text)
    if plain:
        try:
            return datetime(int(plain.group(1)), int(plain.group(2)), int(plain.group(3)),
                            int(plain.group(4)), int(plain.group(5)), int(plain.group(6) or 0),
                            tzinfo=TZ)
        except ValueError:
            return None
    zoned = re.match(
        r'^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)'
        r'(Z|([+-])(\d{2}):?(\d{2}))$', text)
    if zoned:
        local = _parse_any(zoned.group(1)[:19])
        if local is None:
            return None
        if zoned.group(2) == 'Z':
            return (local - timedelta(hours=8)).astimezone(TZ)
        sign = -1 if zoned.group(3) == '-' else 1
        offset = timedelta(hours=int(zoned.group(4)), minutes=int(zoned.group(5)))
        return (local - sign * offset).astimezone(TZ)
    return None


def _shifted(now):
    """businessDayKey 等价：now - 5h 的日历日期（+08）。"""
    return (now - timedelta(hours=DAY_RESET_HOUR)).strftime('%Y-%m-%d')


def _biz_start(now):
    shifted = now - timedelta(hours=DAY_RESET_HOUR)
    return datetime(shifted.year, shifted.month, shifted.day,
                    DAY_RESET_HOUR, 0, 0, tzinfo=TZ)


def _period_ok(period):
    if not isinstance(period, str):
        return None
    period = period.replace('period:', '', 1).strip()
    if not re.match(r'^\d{11}$', period):
        return None
    seq = int(period[8:])
    try:
        day = datetime(int(period[:4]), int(period[4:6]), int(period[6:8]))
    except ValueError:
        return None
    if not 1 <= seq <= PERIODS_PER_DAY:
        return None
    return day, seq


def _period_slot(period):
    parsed = _period_ok(period)
    if not parsed:
        return None
    day, seq = parsed
    minutes = seq * 5
    hour = minutes // 60
    minute = minutes % 60
    if hour >= 24:  # 与 JS Date 构造一致：seq=288 滚到次日 00:00
        day = day + timedelta(days=hour // 24)
        hour = hour % 24
    return datetime(day.year, day.month, day.day, hour, minute, DRAW_SECOND, tzinfo=TZ)


def _expected_next(base):
    parsed = _period_ok(base)
    if not parsed:
        return None
    day, seq = parsed
    if seq < PERIODS_PER_DAY:
        return day.strftime('%Y%m%d') + str(seq + 1).zfill(3)
    return (day + timedelta(days=1)).strftime('%Y%m%d') + '001'


def _collect(now):
    """按后端 filterDailyDisplayPredictions 同口径收集「已校验」预测。"""
    drops = {
        'no_eval': 0, 'not_truth': 0, 'time_outside': 0, 'not_immediate': 0,
        'minute_mismatch': 0, 'predates_fail': 0, 'bad_numbers': 0, 'dup': 0,
    }
    try:
        doc = json.loads(STATE_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return [], drops, None
    predictions = doc.get('predictions')
    if not isinstance(predictions, list):
        return [], drops, doc
    rows = []
    for pred in predictions:
        if not isinstance(pred, dict):
            drops['no_eval'] += 1
            continue
        if pred.get('evaluated') is not True or pred.get('counterSkipped') is True:
            drops['no_eval'] += 1
            continue
        ev = pred.get('evaluation')
        if not isinstance(ev, dict) or ev.get('truthEligible') is not True or ev.get('voided') is True:
            drops['not_truth'] += 1
            continue
        opened = _parse_any(ev.get('targetOpenTime'))
        if opened is None or opened > now:
            drops['time_outside'] += 1
            continue
        base = pred.get('basePeriod') or pred.get('baseUid')
        target = ev.get('targetPeriod') or ev.get('targetUid')
        expected = _expected_next(base)
        actual_target = target.replace('period:', '', 1).strip() if isinstance(target, str) else ''
        if expected is None or expected != actual_target:
            drops['not_immediate'] += 1
            continue
        slot = _period_slot(actual_target)
        if slot is None or (slot - timedelta(seconds=slot.second, microseconds=slot.microsecond)
                            != opened - timedelta(seconds=opened.second, microseconds=opened.microsecond)):
            drops['minute_mismatch'] += 1
            continue
        generated = _parse_any(pred.get('generatedAt'))
        if generated is None or generated > opened:
            drops['predates_fail'] += 1
            continue
        numbers = ev.get('actualNumbers')
        if (not isinstance(numbers, list) or len(numbers) < 4
                or not all(isinstance(item, str) and re.match(r'^\d$', item) for item in numbers[:4])):
            drops['bad_numbers'] += 1
            continue
        rows.append((opened, pred))
    rows.sort(key=lambda item: item[0], reverse=True)
    seen = set()
    result = []
    for opened, pred in rows:
        ev = pred.get('evaluation') or {}
        key = (ev.get('targetPeriod') or ev.get('targetUid') or '')
        key = key.replace('period:', '', 1).strip()
        if not key or key in seen:
            drops['dup'] += 1
            continue
        seen.add(key)
        result.append(pred)
    return result, drops, doc


def _group_digits(value):
    if not isinstance(value, str) or not value:
        return None
    digits = [int(char) for char in value if char.isdigit()]
    if len(digits) != len(value) or not digits:
        return None
    if any(digit > 9 for digit in digits):
        return None
    return frozenset(digits)


def _cooccur_prob(group_sets):
    """P(至少一组数字在 4 球开奖组合中全部出现)，10^4 开奖组合精确枚举。"""
    key = tuple(sorted(group_sets, key=lambda item: (len(item), sorted(item))))
    cached = _ENUM_CACHE.get(key)
    if cached is not None:
        return cached
    bad = 0
    for code in range(10000):
        digits = {code // 1000, (code // 100) % 10, (code // 10) % 10, code % 10}
        if any(group <= digits for group in group_sets):
            bad += 1
    prob = bad / 10000.0
    _ENUM_CACHE[key] = prob
    return prob


def _count_single(rows, day, key=SINGLE_KEY):
    """单项核验计数（避开成功=命中），供单组统计/参照列使用。"""
    total = 0
    hit = 0
    first = None
    last = None
    last_opened = None
    for pred in rows:
        ev = pred.get('evaluation') or {}
        opened = _parse_any(ev.get('targetOpenTime'))
        if _shifted(opened) != day:
            continue
        extras = ev.get('independentExtras')
        entry = (extras or {}).get(key) or {}
        success = entry.get('success') if isinstance(entry, dict) else None
        if not isinstance(success, bool):
            continue  # 与后端 add() 一致：非布尔不计数
        total += 1
        if success:
            hit += 1
        period = (ev.get('targetPeriod') or ev.get('targetUid') or '')
        period = period.replace('period:', '', 1).strip()
        if first is None or period < first:
            first = period
        if last is None or period > last:
            last = period
            last_opened = opened
        elif last == period:
            last_opened = opened
    rate = round(hit * 100.0 / total, 4) if total else None
    return {
        'key': key,
        'label': GROUP_LABELS.get(key, key),
        'total': total,
        'hit': hit,
        'rate': rate,
        'baseline': SINGLE_BASELINE,
        'delta': round(rate - SINGLE_BASELINE, 4) if rate is not None else None,
        'first_period': first,
        'last_period': last,
        'last_open_time': last_opened.strftime('%Y-%m-%d %H:%M:%S') if last_opened else None,
        'sample_low': total < MIN_SAMPLE,
    }


def _count_merged(rows, day, keys=HISTORICAL_KEYS):
    """合并核验计数（同一期 keys 全部避开成功才记命中）+ 逐期精确枚举基线。"""
    total = 0
    hit = 0
    expected = 0.0
    first = None
    last = None
    last_opened = None
    for pred in rows:
        ev = pred.get('evaluation') or {}
        opened = _parse_any(ev.get('targetOpenTime'))
        if _shifted(opened) != day:
            continue
        extras = ev.get('independentExtras') or {}
        successes = {}
        group_sets = []
        for key in keys:
            entry = extras.get(key) or {}
            success = entry.get('success') if isinstance(entry, dict) else None
            digits = _group_digits(entry.get('value')) if isinstance(entry, dict) else None
            if not isinstance(success, bool) or digits is None:
                successes = None
                break
            successes[key] = success
            group_sets.append(digits)
        if successes is None:
            continue  # 任一组缺号码/缺核验 → 本期不计入合并
        total += 1
        if all(successes.values()):
            hit += 1
        expected += 1.0 - _cooccur_prob(tuple(group_sets))
        period = (ev.get('targetPeriod') or ev.get('targetUid') or '')
        period = period.replace('period:', '', 1).strip()
        if first is None or period < first:
            first = period
        if last is None or period > last:
            last = period
            last_opened = opened
        elif last == period:
            last_opened = opened
    rate = round(hit * 100.0 / total, 4) if total else None
    baseline = round(expected * 100.0 / total, 4) if total else None
    return {
        'total': total,
        'hit': hit,
        'rate': rate,
        'baseline': baseline,
        'delta': round(rate - baseline, 4) if (rate is not None and baseline is not None) else None,
        'first_period': first,
        'last_period': last,
        'last_open_time': last_opened.strftime('%Y-%m-%d %H:%M:%S') if last_opened else None,
        'sample_low': total < MIN_SAMPLE,
    }


def _count_groups(rows, day):
    return {key: _count_single(rows, day, key) for key in GROUP_KEYS}


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with open(handle, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=1)
            stream.flush()
            import os as _os
            _os.fsync(stream.fileno())
        _os.replace(temporary, path)
    finally:
        try:
            import os as _os
            _os.unlink(temporary)
        except OSError:
            pass


def _load_history():
    try:
        return json.loads(HISTORY_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _monotonic(old, new):
    """同一业务日只允许样本量增长，不回退（跨预测滚动窗口）。"""
    out = dict(old or {})
    if int((new or {}).get('total') or 0) >= int(out.get('total') or 0):
        out.update(new or {})
    return out


def _upsert_history(history, day, groups, merged3, merged4, current_day, now):
    entry = history.get(day)
    if not isinstance(entry, dict):
        entry = {}
    groups_out = dict(entry.get('groups') or {})
    for key in GROUP_KEYS:
        groups_out[key] = _monotonic(groups_out.get(key), groups.get(key) or {})
    merged_out = _monotonic(entry.get('merged'), merged3)
    merged4_out = _monotonic(entry.get('merged4'), merged4)
    single_out = _monotonic(entry.get('single'), groups.get(SINGLE_KEY) or {})
    history[day] = {
        'expected_periods': PERIODS_PER_DAY,
        'groups': groups_out,
        'merged': merged_out,
        'merged4': merged4_out,
        'single': single_out,
        'sealed': bool(day < current_day),
        'updated_at': now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
    }
    return history


def _groups_of(extras, with_success):
    groups = {}
    for key in GROUP_KEYS:
        entry = extras.get(key) or {}
        item = {'key': key, 'label': GROUP_LABELS[key], 'value': entry.get('value')}
        if with_success:
            item['success'] = entry.get('success')
        groups[key] = item
    return groups


def _merged_compat(groups, keys):
    """合并结论；缺任一组/非布尔返回 False，另给出是否齐全。"""
    values_ready = all(isinstance((groups.get(key) or {}).get('success'), bool) for key in keys)
    if not values_ready:
        return False, False
    return all(groups[key]['success'] is True for key in keys), True


def compute(now=None, write=True):
    now = now if isinstance(now, datetime) else datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    rows, drops, doc = _collect(now)
    shifted = now - timedelta(hours=DAY_RESET_HOUR)
    current_day = shifted.strftime('%Y-%m-%d')
    previous_day = (shifted - timedelta(days=1)).strftime('%Y-%m-%d')

    groups_current = _count_groups(rows, current_day)
    groups_previous_rows = _count_groups(rows, previous_day)
    merged3_current = _count_merged(rows, current_day, HISTORICAL_KEYS)
    merged3_previous_rows = _count_merged(rows, previous_day, HISTORICAL_KEYS)
    merged4_current = _count_merged(rows, current_day, GROUP_KEYS)
    merged4_previous_rows = _count_merged(rows, previous_day, GROUP_KEYS)

    history = _load_history()
    history = _upsert_history(history, current_day, groups_current, merged3_current,
                              merged4_current, current_day, now)
    history = _upsert_history(history, previous_day, groups_previous_rows,
                              merged3_previous_rows, merged4_previous_rows, current_day, now)
    previous_entry = history.get(previous_day) or {}
    groups_previous = previous_entry.get('groups') or groups_previous_rows
    previous_merged = previous_entry.get('merged') or merged3_previous_rows
    previous_merged4 = previous_entry.get('merged4') or merged4_previous_rows

    current = merged3_current
    as_of_period = (current.get('last_period')
                    or merged4_current.get('last_period')
                    or previous_merged.get('last_period'))
    as_of_time = (current.get('last_open_time')
                  or merged4_current.get('last_open_time')
                  or previous_merged.get('last_open_time'))

    latest_groups = None
    pending_groups = None
    for pred in rows:
        ev = pred.get('evaluation') or {}
        extras = ev.get('independentExtras') or {}
        groups = _groups_of(extras, with_success=True)
        if any(isinstance(g.get('success'), bool) for g in groups.values()):
            merged3, _ = _merged_compat(groups, HISTORICAL_KEYS)
            merged4, merged4_available = _merged_compat(groups, GROUP_KEYS)
            latest_groups = {
                'period': (ev.get('targetPeriod') or ev.get('targetUid') or '').replace('period:', '', 1).strip(),
                'groups': groups,
                'actual': ev.get('actualNumbers'),
                'merged_success': merged3,
                'merged4_success': merged4,
                'merged4_available': merged4_available,
            }
            break
    current_prediction = (doc or {}).get('currentPrediction')
    if isinstance(current_prediction, dict) and current_prediction.get('evaluated') is not True:
        extras = current_prediction.get('extraKills') or {}
        groups = _groups_of(extras, with_success=False)
        if any(g.get('value') for g in groups.values()):
            pending_groups = {
                'period': (current_prediction.get('targetPeriod') or current_prediction.get('targetUid') or '').replace('period:', '', 1).strip(),
                'groups': groups,
                'merged4_ready': all(bool(g.get('value')) for g in groups.values()),
            }
    if pending_groups and latest_groups and pending_groups['period'] <= latest_groups['period']:
        pending_groups = None  # 磁盘快照滞后：该期已核验，不显示重复待核验

    report = {
        'version': 'three-avoid2-daily-v3',
        'group_keys': list(GROUP_KEYS),
        'group_labels': dict(GROUP_LABELS),
        'historical_keys': list(HISTORICAL_KEYS),
        'single_key': SINGLE_KEY,
        'baseline_single': SINGLE_BASELINE,
        'baseline_source': ('单项基线 = 1 - P(3 个指定数字在 4 球全部出现)；'
                            '合并基线 = 每期按该期各组号码 10^4 开奖组合精确枚举'
                            '(1 - P(至少一组的数字全部出现)) 再按期平均'),
        'hit_definition': ('单项：该组 3 个数字未在 4 球开奖号中全部出现（避开成功）；'
                           '三组合并：同一期前 3 组全部避开成功（历史口径）；'
                           '四组合并：同一期 4 组全部避开成功（第 4 组缺失不计入）'),
        'generated_at': now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'state_updated_at': (doc or {}).get('updatedAt'),
        'business_day': current_day,
        'as_of_period': as_of_period,
        'as_of_time': as_of_time,
        'min_sample': MIN_SAMPLE,
        'current': current,
        'previous': {
            'business_day': previous_day,
            'total': previous_merged.get('total', 0),
            'hit': previous_merged.get('hit', 0),
            'rate': previous_merged.get('rate'),
            'baseline': previous_merged.get('baseline'),
            'delta': previous_merged.get('delta'),
            'first_period': previous_merged.get('first_period'),
            'last_period': previous_merged.get('last_period'),
            'last_open_time': previous_merged.get('last_open_time'),
            'sealed': previous_entry.get('sealed', False),
            'sample_low': int(previous_merged.get('total') or 0) < MIN_SAMPLE,
        },
        'merged4_current': merged4_current,
        'merged4_previous': {
            'business_day': previous_day,
            'total': previous_merged4.get('total', 0),
            'hit': previous_merged4.get('hit', 0),
            'rate': previous_merged4.get('rate'),
            'baseline': previous_merged4.get('baseline'),
            'delta': previous_merged4.get('delta'),
            'first_period': previous_merged4.get('first_period'),
            'last_period': previous_merged4.get('last_period'),
            'last_open_time': previous_merged4.get('last_open_time'),
            'sealed': previous_entry.get('sealed', False),
            'sample_low': int(previous_merged4.get('total') or 0) < MIN_SAMPLE,
        },
        'groups_current': groups_current,
        'groups_previous': groups_previous,
        'single_current': groups_current.get(SINGLE_KEY) or _count_single(rows, current_day),
        'single_previous': groups_previous.get(SINGLE_KEY) or _count_single(rows, previous_day),
        'latest_groups': latest_groups,
        'pending_groups': pending_groups,
        'diagnostics': {
            'collected': len(rows),
            'drops': drops,
            'history_days': sorted(history.keys()),
            'enum_cache': len(_ENUM_CACHE),
        },
    }
    if write:
        _write_json(HISTORY_PATH, history)
        _write_json(OUT_PATH, report)
    return report, history


def _fmt(row, day_label):
    if not row.get('total'):
        return '  %-24s 暂无已核验记录' % day_label
    rate = ('%.2f%%' % row['rate']) if row.get('rate') is not None else '-'
    delta = row.get('delta')
    delta_text = ('%+.2fpp' % delta) if delta is not None else '-'
    baseline = row.get('baseline')
    baseline_text = ('%.2f%%' % baseline) if isinstance(baseline, (int, float)) else '-'
    note = '样本不足(n<%d)' % MIN_SAMPLE if row.get('sample_low') else '已核验'
    return '  %-24s %s  命中 %d/%d  基线 %s  与基线 %s  %s  期号 %s~%s' % (
        day_label, rate, row.get('hit', 0), row.get('total', 0), baseline_text, delta_text, note,
        row.get('first_period'), row.get('last_period'))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    now = None
    dry = False
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == '--now' and index + 1 < len(argv):
            now = _parse_any(argv[index + 1])
            if now is None:
                print('invalid --now value: %s' % argv[index + 1], file=sys.stderr)
                return 2
            index += 2
            continue
        if arg == '--dry':
            dry = True
            index += 1
            continue
        index += 1
    report, history = compute(now=now, write=not dry)
    print('== 三数共现避开 1/2/3/4 组 · 业务日核验 ==')
    print('单组：4 组各自结算；三组合并=前 3 组全避开（历史口径）；四组合并=4 组全避开')
    print('模拟时刻: %s' % (now.strftime('%Y-%m-%d %H:%M:%S') if now else '实时'))
    for key in GROUP_KEYS:
        row = (report['groups_current'] or {}).get(key) or {}
        print(_fmt(row, '本业务日 ' + report['business_day'] + ' ' + GROUP_LABELS[key]))
    for key in GROUP_KEYS:
        row = (report['groups_previous'] or {}).get(key) or {}
        print(_fmt(row, '上一业务日 ' + report['previous']['business_day'] + ' ' + GROUP_LABELS[key]))
    print(_fmt(report['current'], '三组合并 · 本业务日 ' + report['business_day']))
    print(_fmt(report['previous'], '三组合并 · 上一业务日 ' + report['previous']['business_day']))
    print(_fmt(report['merged4_current'], '四组合并 · 本业务日 ' + report['business_day']))
    print(_fmt(report['merged4_previous'], '四组合并 · 上一业务日 ' + report['previous']['business_day']))
    if report.get('pending_groups'):
        pg = report['pending_groups']
        print('  下一期 %s 待核验: %s' % (pg['period'], ' '.join(
            '%s=%s' % (g['label'], g['value'] or '-') for g in pg['groups'].values())))
    if report.get('latest_groups'):
        lg = report['latest_groups']
        print('  最新一期 %s 已核验: %s 开奖=%s 三组合并=%s 四组合并=%s' % (
            lg['period'], ' '.join('%s=%s(%s)' % (g['label'], g['value'] or '-',
            '✓' if g.get('success') else ('—' if lg.get('merged4_available') is False and g['key'] not in HISTORICAL_KEYS else '✗'))
            for g in lg['groups'].values()),
            '·'.join(str(x) for x in (lg.get('actual') or [])[:4]),
            '全避开✓' if lg.get('merged_success') else '未全避开✗',
            ('全避开✓' if lg.get('merged4_success') else '未全避开✗')
            if lg.get('merged4_available') else '第4组未发布'))
    print('as_of_period=%s as_of_time=%s' % (report['as_of_period'], report['as_of_time']))
    print('collected=%d drops=%s enum_cache=%d' % (report['diagnostics']['collected'],
          json.dumps(report['diagnostics']['drops'], sort_keys=True),
          report['diagnostics']['enum_cache']))
    if not dry:
        print('written: %s' % OUT_PATH)
        print('history days: %s' % ','.join(report['diagnostics']['history_days']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
