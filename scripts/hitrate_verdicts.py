#!/usr/bin/env python3
"""全史命中率判定（只读派生）：六项框架逐项累计命中率 vs 精确随机基线，自动判定持续亏损。

口径（写死）
- 只用 data/selected_six_hit_stats.json 的 verified 条目（开奖后已结算核验）；
  每条含 publication.response / publication.revision / outcomes / target_period。
- 基线复用 scripts/hitrate_attribution.py 的 six_baseline：对该期封存参数在 10^4 开奖空间精确枚举。
- 判定规则（预登记）：n >= 200 才判；family-wise alpha = 0.05 / 可判项数（Bonferroni）；
  p < alpha 且 delta_pp <= -3.0 判 confirmed-deficit，否则 not-confirmed；n < 200 记 pending。
- 观察项（不等于确认亏损）：近 300 期 z <= -2.58（双侧 p<0.01）且全史 delta_pp < 0 记 watch。

产物 data/hitrate_verdicts.json：lib/hitrate_attribution_view.py 渲染面板一行，
scripts/ops_health_check.py 读它告警；cron 每小时跑本脚本（scripts/hitrate_verdicts.py）。
"""
import argparse
import json
import math
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'scripts'))

SIX_HIT_STATS = 'data/selected_six_hit_stats.json'
OUTPUT = 'data/hitrate_verdicts.json'
TZ = ZoneInfo('Asia/Shanghai')

MIN_N = 200
CONFIRM_ALPHA = 0.05
CONFIRM_DELTA_PP = -3.0
WATCH_WINDOW = 300
WATCH_Z = -2.58
WINDOWS = (100, 200, 300, 400, 800)

SIX_ORDER = ('position_q', 'position_b', 'position_s', 'position_g',
             'tail_all', 'tail_qbs', 'tail_qbg', 'tail_qsg', 'tail_bsg',
             'he2_keep', 'he3_keep', 'reverse_parity', 'reverse_size')
EXTRA_LABELS = {'reverse_parity': '单双反向排除', 'reverse_size': '大小反向排除'}


def _label(item, position_labels, tail_labels, labels):
    if item.startswith('position_'):
        return position_labels.get(item[len('position_'):], item) + '杀码'
    if item.startswith('tail_'):
        return tail_labels.get(item[len('tail_'):], item) + '杀'
    return labels.get(item) or EXTRA_LABELS.get(item, item)


def _p_two(z):
    return None if z is None else math.erfc(abs(z) / math.sqrt(2.0))


def _stats(rows):
    """rows: [(period, revision, hit, baseline)] 按 period 升序。"""
    n = len(rows)
    if not n:
        return None
    hits = sum(1 for row in rows if row[2])
    probs = [row[3] for row in rows]
    expected = sum(probs)
    var = sum(p * (1.0 - p) for p in probs)
    z = (hits - expected) / math.sqrt(var) if var > 0 else None
    return {'n': n, 'hits': hits, 'rate': round(hits / float(n), 4),
            'baseline': round(expected / n, 4),
            'delta_pp': round((hits - expected) / n * 100.0, 2),
            'z': None if z is None else round(z, 2), 'p': _p_two(z)}


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


def main(argv=None):
    parser = argparse.ArgumentParser(description='penta5 six-item full-history hit-rate verdicts')
    parser.add_argument('--root', default=str(ROOT))
    args = parser.parse_args(argv)
    root = Path(args.root)
    now = datetime.now(TZ)

    from hitrate_attribution import (LABELS, POSITION_LABELS, TAIL_LABELS, six_baseline)
    payload = json.loads((root / SIX_HIT_STATS).read_text(encoding='utf-8'))
    verified = payload.get('verified') if isinstance(payload, dict) else None
    verified = verified if isinstance(verified, dict) else {}

    rows = defaultdict(list)
    for target, entry in verified.items():
        if not isinstance(entry, dict):
            continue
        publication = entry.get('publication') if isinstance(entry.get('publication'), dict) else {}
        outcomes = entry.get('outcomes') if isinstance(entry.get('outcomes'), dict) else {}
        if not outcomes:
            continue
        response = publication.get('response') if isinstance(publication.get('response'), dict) else {}
        seal = {'response': response, 'revision': str(publication.get('revision') or '')}
        period = str(entry.get('target_period') or target)
        for item in SIX_ORDER:
            if item not in outcomes:
                continue
            try:
                baseline = six_baseline(seal, item)
            except Exception:
                baseline = None
            if baseline is None:
                continue
            rows[item].append((period, seal['revision'],
                               1 if outcomes[item] else 0, float(baseline)))

    preliminary = []
    for item in SIX_ORDER:
        data = rows.get(item) or []
        if not data:
            continue
        data.sort(key=lambda row: row[0])
        stats = _stats(data)
        if stats is None:
            continue
        preliminary.append((item, data, stats))
    judged_count = sum(1 for _item, _data, stats in preliminary
                       if stats['n'] >= MIN_N and stats['p'] is not None)
    alpha_each = CONFIRM_ALPHA / float(max(1, judged_count))

    items = []
    periods = []
    for item, data, stats in preliminary:
        if data:
            periods.append(data[-1][0])
        watch = False
        watch_stats = None
        if len(data) >= WATCH_WINDOW:
            candidate = _stats(data[-WATCH_WINDOW:])
            if (candidate and candidate['z'] is not None
                    and candidate['z'] <= WATCH_Z and candidate['delta_pp'] < 0):
                watch = True
                watch_stats = candidate
        if stats['n'] < MIN_N or stats['p'] is None:
            status = 'pending'
        elif stats['p'] < alpha_each and stats['delta_pp'] <= CONFIRM_DELTA_PP:
            status = 'confirmed-deficit'
        else:
            status = 'not-confirmed'
        windows = []
        for window in WINDOWS:
            if len(data) < window:
                continue
            entry = _stats(data[-window:])
            entry['window'] = window
            windows.append(entry)
        groups = defaultdict(list)
        for row in data:
            groups[row[1]].append(row)
        by_revision = []
        for revision, group in sorted(groups.items(), key=lambda pair: -len(pair[1])):
            entry = _stats(group)
            entry['revision'] = revision or '-'
            by_revision.append(entry)
        note = ''
        if watch and watch_stats:
            note = '近 %d 期 %+.2fpp（z=%s, p=%s），全史 %+.2fpp' % (
                WATCH_WINDOW, watch_stats['delta_pp'], watch_stats['z'],
                '-' if watch_stats['p'] is None else round(watch_stats['p'], 4), stats['delta_pp'])
        items.append({
            'key': item, 'label': _label(item, POSITION_LABELS, TAIL_LABELS, LABELS),
            'n': stats['n'], 'hits': stats['hits'], 'rate': stats['rate'],
            'baseline': stats['baseline'], 'delta_pp': stats['delta_pp'],
            'z': stats['z'], 'p': stats['p'], 'status': status, 'watch': watch,
            'note': note, 'first_period': data[0][0], 'last_period': data[-1][0],
            'windows': windows, 'by_revision': by_revision,
        })

    confirmed = [item['label'] for item in items if item['status'] == 'confirmed-deficit']
    watch = [item['label'] for item in items if item['watch']]
    pending = [item['label'] for item in items if item['status'] == 'pending']
    report = {
        'version': 1, 'revision': 'hitrate-verdicts-v1',
        'generated_at': now.isoformat(),
        'source': SIX_HIT_STATS, 'ledger_updated_at': payload.get('updated_at'),
        'verified_entries': len(verified),
        'as_of_period': max(periods) if periods else '',
        'rule': {'min_n': MIN_N, 'alpha': CONFIRM_ALPHA,
                 'bonferroni': 'alpha / judged-items', 'min_delta_pp': CONFIRM_DELTA_PP,
                 'watch': {'window': WATCH_WINDOW, 'z': WATCH_Z}},
        'family_size': judged_count, 'alpha_each': round(alpha_each, 6),
        'summary': {'items': len(items), 'judged': judged_count,
                    'pending': len(pending), 'confirmed': confirmed, 'watch': watch},
        'items': items,
    }
    _atomic_json(root / OUTPUT, report)

    print('hitrate_verdicts %s source=%d items=%d judged=%d alpha=%.6f confirmed=%d watch=%d pending=%d as_of=%s'
          % (now.isoformat(), len(verified), len(items), judged_count, alpha_each,
             len(confirmed), len(watch), len(pending), report['as_of_period']))
    for item in items:
        print('  %-16s n=%-4d rate=%.4f base=%.4f d=%+6.2fpp p=%s status=%-15s%s'
              % (item['label'], item['n'], item['rate'], item['baseline'],
                 item['delta_pp'], '-' if item['p'] is None else '%.4f' % item['p'],
                 item['status'], ' watch' if item['watch'] else ''))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(0)
