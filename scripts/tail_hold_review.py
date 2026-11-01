
"""合尾稳定化转正后复盘（只读）：逐框架统计沿用率/换码率/尾项命中率与 95%CI。"""
from pathlib import Path
import json
import math
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
OUT = DATA / 'tail_hold_review.json'
REPORT_DIR = Path('/root/ops') / 'tail-hold-review'
TZ = timezone(timedelta(hours=8))
TAIL = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
        'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _tv(nums, key):
    return sum(nums[index] for index in TAIL[key]) % 10


def wilson(k, n, z=1.96):
    if not n:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def _actual_map_framework():
    mapping = {}
    for entry in (_read(DATA / 'r1_framework_hit_stats.json', {}) or {}).get('verified', {}).values():
        pub = entry.get('publication') or {}
        actual = entry.get('actual') or {}
        ref, nums = pub.get('ref_period'), actual.get('numbers')
        if ref and isinstance(nums, list) and len(nums) >= 4:
            mapping[str(ref)] = [int(x) for x in nums[:4]]
    return mapping


def _actual_map_r1():
    mapping = {}
    for entry in (_read(DATA / 'r1_stats.json', {}) or {}).get('verified_evaluations') or []:
        target, nums = entry.get('target_period'), entry.get('actual')
        if target and isinstance(nums, list) and len(nums) >= 4:
            mapping[str(target)] = [int(x) for x in nums[:4]]
    return mapping


def _actual_map_six():
    mapping = {}
    for entry in (_read(DATA / 'selected_six_hit_stats.json', {}) or {}).get('verified', {}).values():
        pub = entry.get('publication') or {}
        actual = entry.get('actual') or {}
        ref, nums = pub.get('ref_period'), actual.get('numbers')
        if ref and isinstance(nums, list) and len(nums) >= 4:
            mapping[str(ref)] = [int(x) for x in nums[:4]]
    return mapping


def _framework_entries():
    seals = (_read(DATA / 'r1_framework_seals.json', {}) or {}).get('prediction_seals', {})
    actuals = _actual_map_framework()
    rows = []
    for ref in sorted(seals):
        seal = seals[ref]
        hold = (seal.get('model_metadata') or {}).get('tail_hold3')
        if not isinstance(hold, dict):
            continue
        nums = actuals.get(ref)
        if nums is None:
            continue
        tails = {'qbs': int(seal['response']['he_q3_kill']) % 10,
                 'bsg': int(seal['response']['he_h3_kill']) % 10}
        hits = [_tv(nums, key) != tails[key] for key in tails]
        rows.append((str(ref), hold.get('reused_previous') is True, hits))
    return rows


def _killer_entries():
    seals = (_read(DATA / 'r1_stats.json', {}) or {}).get('prediction_seals', {})
    actuals = _actual_map_r1()
    rows = []
    for ref in sorted(seals):
        seal = seals[ref]
        hold = (seal.get('model_metadata') or {}).get('tail_hold3')
        if not isinstance(hold, dict):
            continue
        nums = actuals.get(str(seal.get('target_period')))
        if nums is None:
            continue
        tails = {key: int(seal[key]) % 10 for key in ('he_all', 'he_q3', 'he_h3')}
        hits = [_tv(nums, 'all') != tails['he_all'],
                _tv(nums, 'qbs') != tails['he_q3'],
                _tv(nums, 'bsg') != tails['he_h3']]
        rows.append((str(ref), hold.get('reused_previous') is True, hits))
    return rows


def _six_entries():
    seals = (_read(DATA / 'selected_six_seals.json', {}) or {}).get('prediction_seals', {})
    actuals = _actual_map_six()
    rows = []
    for ref in sorted(seals):
        seal = seals[ref]
        policy = (seal.get('model_metadata') or {}).get('tail_policy')
        if not isinstance(policy, dict):
            continue
        nums = actuals.get(ref)
        if nums is None:
            continue
        tails = seal['response']['selected_tails']
        if len(tails) != 1:
            continue
        key, kill = next(iter(tails.items()))
        rows.append((str(ref), policy.get('reused_previous') is True, [_tv(nums, key) != int(kill) % 10]))
    return rows


def _kill3_entries():
    actuals = {}
    for settlement in (_read(DATA / 'kill3_tail/settlements.json', {}) or {}).get('records') or []:
        nums = settlement.get('actual')
        target = settlement.get('target_period')
        if target and isinstance(nums, list) and len(nums) >= 4:
            actuals[str(target)] = [int(x) for x in nums[:4]]
    seals = {}
    for seal in (_read(DATA / 'kill3_tail/seals.json', {}) or {}).get('records') or []:
        if isinstance(seal, dict) and seal.get('tail_policy'):
            seals[str(seal.get('target_period'))] = seal
    rows = []
    for target in sorted(seals):
        seal = seals[target]
        nums = actuals.get(target)
        tail = seal.get('tail') or {}
        key = str(tail.get('key') or '').replace('tail_', '')
        if nums is None or key not in TAIL:
            continue
        hit = [_tv(nums, key) != int(tail.get('kill')) % 10]
        rows.append((target, (seal.get('tail_policy') or {}).get('reused_previous') is True, hit))
    return rows


def _kill1_entries():
    '''kill1_tail（AI 单位置 + 一合尾 + 一形态）的尾项稳定化复盘行。'''
    actuals = {}
    for settlement in (_read(DATA / 'kill1_tail/settlements.json', {}) or {}).get('records') or []:
        nums = settlement.get('actual')
        target = settlement.get('target_period')
        if target and isinstance(nums, list) and len(nums) >= 4:
            actuals[str(target)] = [int(x) for x in nums[:4]]
    seals = {}
    for seal in (_read(DATA / 'kill1_tail/seals.json', {}) or {}).get('records') or []:
        if isinstance(seal, dict) and seal.get('tail_policy'):
            seals[str(seal.get('target_period'))] = seal
    rows = []
    for target in sorted(seals):
        seal = seals[target]
        nums = actuals.get(target)
        tail = seal.get('tail') or {}
        key = str(tail.get('key') or '').replace('tail_', '')
        if nums is None or key not in TAIL:
            continue
        hit = [_tv(nums, key) != int(tail.get('kill')) % 10]
        rows.append((target, (seal.get('tail_policy') or {}).get('reused_previous') is True, hit))
    return rows


def _kill2p_entries():
    '''kill2p_tail（AI 二位置 + 一合尾 + 两反向形态）的尾项稳定化复盘行。'''
    actuals = {}
    for settlement in (_read(DATA / 'kill2p_tail/settlements.json', {}) or {}).get('records') or []:
        nums = settlement.get('actual')
        target = settlement.get('target_period')
        if target and isinstance(nums, list) and len(nums) >= 4:
            actuals[str(target)] = [int(x) for x in nums[:4]]
    seals = {}
    for seal in (_read(DATA / 'kill2p_tail/seals.json', {}) or {}).get('records') or []:
        if isinstance(seal, dict) and seal.get('tail_policy'):
            seals[str(seal.get('target_period'))] = seal
    rows = []
    for target in sorted(seals):
        seal = seals[target]
        nums = actuals.get(target)
        tail = seal.get('tail') or {}
        key = str(tail.get('key') or '').replace('tail_', '')
        if nums is None or key not in TAIL:
            continue
        hit = [_tv(nums, key) != int(tail.get('kill')) % 10]
        rows.append((target, (seal.get('tail_policy') or {}).get('reused_previous') is True, hit))
    return rows


def _kill2_entries():
    '''kill2_tail（AI 二位置 + 一合尾 + 二数合/三数合）的尾项稳定化复盘行。'''
    actuals = {}
    for settlement in (_read(DATA / 'kill2_tail/settlements.json', {}) or {}).get('records') or []:
        nums = settlement.get('actual')
        target = settlement.get('target_period')
        if target and isinstance(nums, list) and len(nums) >= 4:
            actuals[str(target)] = [int(x) for x in nums[:4]]
    seals = {}
    for seal in (_read(DATA / 'kill2_tail/seals.json', {}) or {}).get('records') or []:
        if isinstance(seal, dict) and seal.get('tail_policy'):
            seals[str(seal.get('target_period'))] = seal
    rows = []
    for target in sorted(seals):
        seal = seals[target]
        nums = actuals.get(target)
        tail = seal.get('tail') or {}
        key = str(tail.get('key') or '').replace('tail_', '')
        if nums is None or key not in TAIL:
            continue
        hit = [_tv(nums, key) != int(tail.get('kill')) % 10]
        rows.append((target, (seal.get('tail_policy') or {}).get('reused_previous') is True, hit))
    return rows


def _kill4_entries():
    '''kill4_tail（AI 三位置 + 一合尾 + 二数合/三数合）的尾项稳定化复盘行。'''
    actuals = {}
    for settlement in (_read(DATA / 'kill4_tail/settlements.json', {}) or {}).get('records') or []:
        nums = settlement.get('actual')
        target = settlement.get('target_period')
        if target and isinstance(nums, list) and len(nums) >= 4:
            actuals[str(target)] = [int(x) for x in nums[:4]]
    seals = {}
    for seal in (_read(DATA / 'kill4_tail/seals.json', {}) or {}).get('records') or []:
        if isinstance(seal, dict) and seal.get('tail_policy'):
            seals[str(seal.get('target_period'))] = seal
    rows = []
    for target in sorted(seals):
        seal = seals[target]
        nums = actuals.get(target)
        tail = seal.get('tail') or {}
        key = str(tail.get('key') or '').replace('tail_', '')
        if nums is None or key not in TAIL:
            continue
        hit = [_tv(nums, key) != int(tail.get('kill')) % 10]
        rows.append((target, (seal.get('tail_policy') or {}).get('reused_previous') is True, hit))
    return rows


FRAMEWORKS = (
    ('AI三位置', _kill3_entries),
    ('AI单位置+一合尾+一形态', _kill1_entries),
    ('AI二位置+一合尾+两反向形态', _kill2p_entries),
    ('AI二位置+一合尾+二数合/三数合', _kill2_entries),
    ('AI三位置+一合尾+二数合/三数合', _kill4_entries),
    ('固定八码', _framework_entries),
    ('七项独立v7', _six_entries),
    ('killer九项', _killer_entries),
)


def build():
    frameworks = {}
    for name, loader in FRAMEWORKS:
        try:
            rows = loader()
        except Exception as error:
            frameworks[name] = {'error_type': type(error).__name__}
            continue
        n = sum(len(row[2]) for row in rows)
        hits = sum(sum(1 for hit in row[2] if hit) for row in rows)
        reused = sum(1 for row in rows if row[1])
        changes = len(rows) - reused
        lo, hi = wilson(hits, n)
        frameworks[name] = {'periods': len(rows), 'items': n, 'hits': hits,
                            'rate': (hits / n) if n else None,
                            'ci95': [lo, hi], 'reused_periods': reused,
                            'reuse_rate': (reused / len(rows)) if rows else None,
                            'change_periods': changes,
                            'first_period': rows[0][0] if rows else None,
                            'last_period': rows[-1][0] if rows else None}
    now = datetime.now(TZ)
    return {'version': 1, 'generated_at': now.isoformat(timespec='seconds'),
            'note': '只读复盘：尾项命中率基线各单项90%；沿用率=本期沿用上期尾码的期数占比。',
            'frameworks': frameworks}


def render(data):
    lines = ['# 合尾稳定化转正后复盘', '',
             '> 生成：%s（只读，不改任何封存/统计）' % data.get('generated_at'), '',
             '| 框架 | 转正后首期 | 最近期 | 期数 | 尾项样本 | 尾项命中率(95%CI) | 沿用期数 | 沿用率 | 换码期数 |',
             '|---|---|---|---|---|---|---|---|---|']
    for name, item in data.get('frameworks', {}).items():
        if item.get('error_type'):
            lines.append('| %s | - | - | - | - | 读取失败(%s) | - | - | - |' % (name, item['error_type']))
            continue
        ci = item.get('ci95') or [None, None]
        rate = ('%.2f%%' % (100 * item['rate'])) if item.get('rate') is not None else '-'
        ci_text = ('[%.1f%%, %.1f%%]' % (100 * ci[0], 100 * ci[1])) if ci[0] is not None else '-'
        reuse = ('%.1f%%' % (100 * item['reuse_rate'])) if item.get('reuse_rate') is not None else '-'
        lines.append('| %s | %s | %s | %d | %d | %s %s | %d | %s | %d |' % (
            name, item.get('first_period') or '-', item.get('last_period') or '-',
            item.get('periods') or 0, item.get('items') or 0, rate, ci_text,
            item.get('reused_periods') or 0, reuse, item.get('change_periods') or 0))
    lines += ['', '注：单尾杀1码的随机基线是90%；沿用率越高代表换码越少，不代表命中率更高。']
    return '\n'.join(lines)


def main():
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(TZ).strftime('%Y%m%d-%H%M%S')
    report = REPORT_DIR / ('review-' + stamp + '.md')
    text = render(data)
    report.write_text(text + '\n', encoding='utf-8')
    print(json.dumps({'status': 'ok', 'generated_at': data['generated_at'],
                      'frameworks': {name: {'periods': item.get('periods'),
                                            'rate': item.get('rate'),
                                            'reuse_rate': item.get('reuse_rate')}
                                     for name, item in data['frameworks'].items()}},
                     ensure_ascii=False, sort_keys=True, separators=(',', ':')))
    print('report: ' + str(report))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
