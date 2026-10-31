# -*- coding: utf-8 -*-
"""三数共现避开3组 · 业务日核验统计（只读派生，不改任何模型）。

口径（与 /accuracy「今日过滤与参考项」同源，写死避免误读）：
- 项：threeDigitAvoid2「三数共现避开3组」——模型每期给出 1 组 3 个数字并要求避开；
- 命中 = 该组 3 个数字【未全部】出现在 4 球开奖号中（避开成功），
  即 evaluation.independentExtras.threeDigitAvoid2.success == True；
- 随机基线 = 1 - P(3 个指定数字在 4 球全部出现) = 97.96%（10^4 精确枚举，
  与 accuracy.html EXACT_BASELINES['threeDigitAvoid2'] 同值）；
- 业务日 = 北京时间 05:00 切日（PENTA5_DAY_RESET_HOUR=5）；
- 只统计「已校验」期，过滤条件与后端 projectDailyDisplayCounters /
  filterDailyDisplayPredictions 一致：evaluated/truthEligible/voided、
  immediate 期过渡、开奖时刻落在业务日窗口、目标期分钟与开奖分钟一致、
  generatedAt 先于开奖、前 4 球数字合法、按 targetPeriod 去重。

产物：
- data/three_avoid2_daily.json：本业务日 + 上一业务日核验表（面板区块数据源）；
- data/three_avoid2_history.json：按业务日累计（单调取 max(total)，跨预测
  滚动窗口保留已封盘日）。
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

KEY = 'threeDigitAvoid2'
LABEL = '三数共现避开3组'
BASELINE = 97.96  # 10^4 精确枚举：1 - P(3 个指定数字在 4 球全部出现)
DAY_RESET_HOUR = 5
PERIODS_PER_DAY = 288
DRAW_SECOND = 12
MIN_SAMPLE = 200

TZ = timezone(timedelta(hours=8))  # Asia/Shanghai，与服务器本地时区一致


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
    start = _biz_start(now)
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


def _count(rows, day):
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
        success = (extras or {}).get(KEY) or {}
        success = success.get('success') if isinstance(success, dict) else None
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
        'total': total,
        'hit': hit,
        'rate': rate,
        'delta': round(rate - BASELINE, 4) if rate is not None else None,
        'first_period': first,
        'last_period': last,
        'last_open_time': last_opened.strftime('%Y-%m-%d %H:%M:%S') if last_opened else None,
        'sample_low': total < MIN_SAMPLE,
    }


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


def _upsert_history(history, day, counts, current_day, now):
    entry = history.get(day)
    if not isinstance(entry, dict):
        entry = {}
    previous_total = int(entry.get('total') or 0)
    merged = dict(entry)
    merged['expected_periods'] = PERIODS_PER_DAY
    if counts['total'] >= previous_total:
        merged.update({
            'total': counts['total'],
            'hit': counts['hit'],
            'rate': counts['rate'],
            'delta': counts['delta'],
            'first_period': counts['first_period'],
            'last_period': counts['last_period'],
            'last_open_time': counts['last_open_time'],
        })
    merged['sealed'] = bool(day < current_day)
    merged['updated_at'] = now.strftime('%Y-%m-%dT%H:%M:%S+08:00')
    history[day] = merged
    return history


def compute(now=None, write=True):
    now = now if isinstance(now, datetime) else datetime.now(TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ)
    rows, drops, doc = _collect(now)
    shifted = now - timedelta(hours=DAY_RESET_HOUR)
    current_day = shifted.strftime('%Y-%m-%d')
    previous_day = (shifted - timedelta(days=1)).strftime('%Y-%m-%d')
    history = _load_history()
    for day in (current_day, previous_day):
        history = _upsert_history(history, day, _count(rows, day), current_day, now)
    current = _count(rows, current_day)
    previous = history.get(previous_day) or {}
    as_of_period = current.get('last_period') or previous.get('last_period')
    as_of_time = current.get('last_open_time') or previous.get('last_open_time')
    latest_value = None
    pending_value = None
    for pred in rows:
        ev = pred.get('evaluation') or {}
        extras = (ev.get('independentExtras') or {}).get(KEY) or {}
        if isinstance(extras.get('success'), bool):
            latest_value = {
                'period': (ev.get('targetPeriod') or ev.get('targetUid') or '').replace('period:', '', 1).strip(),
                'value': extras.get('value'),
                'success': extras.get('success'),
                'actual': ev.get('actualNumbers'),
            }
            break
    current_prediction = (doc or {}).get('currentPrediction')
    if isinstance(current_prediction, dict) and current_prediction.get('evaluated') is not True:
        extra = (current_prediction.get('extraKills') or {}).get(KEY)
        if isinstance(extra, dict) and extra.get('value'):
            pending_value = {
                'period': (current_prediction.get('targetPeriod') or current_prediction.get('targetUid') or '').replace('period:', '', 1).strip(),
                'value': extra['value'],
            }
    if pending_value and latest_value and pending_value['period'] <= latest_value['period']:
        pending_value = None  # 磁盘快照滞后：该期已核验，不显示重复待核验
    report = {
        'version': 'three-avoid2-daily-v1',
        'key': KEY,
        'label': LABEL,
        'baseline': BASELINE,
        'baseline_source': ('10^4 精确枚举：1 - P(3 个指定数字在 4 球全部出现)；'
                            '与 /accuracy EXACT_BASELINES.threeDigitAvoid2 同值'),
        'hit_definition': '该组 3 个数字未在 4 球开奖号中全部出现（避开成功）',
        'generated_at': now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'state_updated_at': (doc or {}).get('updatedAt'),
        'business_day': current_day,
        'as_of_period': as_of_period,
        'as_of_time': as_of_time,
        'min_sample': MIN_SAMPLE,
        'latest_value': latest_value,
        'pending_value': pending_value,
        'current': current,
        'previous': {
            'business_day': previous_day,
            'total': previous.get('total', 0),
            'hit': previous.get('hit', 0),
            'rate': previous.get('rate'),
            'delta': previous.get('delta'),
            'first_period': previous.get('first_period'),
            'last_period': previous.get('last_period'),
            'last_open_time': previous.get('last_open_time'),
            'sealed': previous.get('sealed', False),
            'sample_low': int(previous.get('total') or 0) < MIN_SAMPLE,
        },
        'diagnostics': {
            'collected': len(rows),
            'drops': drops,
            'history_days': sorted(history.keys()),
        },
    }
    if write:
        _write_json(HISTORY_PATH, history)
        _write_json(OUT_PATH, report)
    return report, history


def _fmt(row, day_label):
    if not row.get('total'):
        return '  %-14s 暂无已核验记录' % day_label
    rate = ('%.2f%%' % row['rate']) if row.get('rate') is not None else '-'
    delta = row.get('delta')
    delta_text = ('%+.2fpp' % delta) if delta is not None else '-'
    note = '样本不足(n<%d)' % MIN_SAMPLE if row.get('sample_low') else '已核验'
    return '  %-14s %s  命中 %d/%d  与基线 %s  %s  期号 %s~%s' % (
        day_label, rate, row.get('hit', 0), row.get('total', 0), delta_text, note,
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
    print('== 三数共现避开3组 · 业务日核验 ==')
    print('基线 %.2f%%（10^4 精确枚举）· 命中口径：3 数字未在 4 球全部出现（避开成功）' % BASELINE)
    print('模拟时刻: %s' % (now.strftime('%Y-%m-%d %H:%M:%S') if now else '实时'))
    print(_fmt(report['current'], '本业务日 ' + report['business_day']))
    print(_fmt(report['previous'], '上一业务日 ' + report['previous']['business_day']))
    print('as_of_period=%s as_of_time=%s' % (report['as_of_period'], report['as_of_time']))
    print('collected=%d drops=%s' % (report['diagnostics']['collected'],
                                     json.dumps(report['diagnostics']['drops'], sort_keys=True)))
    if not dry:
        print('written: %s' % OUT_PATH)
        print('history days: %s' % ','.join(report['diagnostics']['history_days']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
