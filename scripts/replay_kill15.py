#!/usr/bin/env python3
"""kill15 回放器 v2：用严格无未来信息的方式重跑历史期，并与旧回放对比。

与正式账本完全隔离：
  正式 -> data/kill15_tail/{seals,settlements}.json
  回放 -> data/kill15_tail/replay_v{tag}.json     （mode=replay）

用法：
  python3 scripts/replay_kill15.py --limit 12 --dry-run          # 只看会跑哪几期
  python3 scripts/replay_kill15.py --limit 12                    # 真跑（每期一次模型调用）
  python3 scripts/replay_kill15.py --limit 12 --out data/kill15_tail/replay_v2.json
  python3 scripts/replay_kill15.py --limit 12 --out data/kill15_tail/replay_v2.json \
      --compare data/kill15_tail/archive/replay_v1_049_060.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT / 'lib'))

import kill15_tail  # noqa: E402
from stats_history import load_snapshot  # noqa: E402

DEFAULT_OUT = ROOT / 'data' / 'kill15_tail' / 'replay_v2.json'


def load_replay(path):
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    return doc.get('records') or []


def save_replay(records, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    kill15_tail.atomic_json(path, {'version': 1, 'mode': 'replay', 'records': records})


def _rates(rows):
    total = len(rows)
    group_hits = sum(1 for row in rows if row.get('group_hit'))
    bets = sum(len(row.get('positions') or {}) for row in rows)
    hits = sum(1 for row in rows for item in (row.get('positions') or {}).values()
               if item.get('hit'))
    return {
        'total': total,
        'group': group_hits,
        'group_rate': (100.0 * group_hits / total) if total else 0.0,
        'bets': bets,
        'hits': hits,
        'single_rate': (100.0 * hits / bets) if bets else 0.0,
    }


def print_compare(current_rows, compare_path):
    other_rows = load_replay(compare_path)
    if not other_rows:
        print('\ncompare: %s 无可读记录' % compare_path)
        return
    mine = {str(row.get('ref_period')): row for row in current_rows}
    theirs = {str(row.get('ref_period')): row for row in other_rows}
    print('\n=== 回放对比（旧 vs 新）===')
    print('%-14s %-22s %-22s' % ('ref_period', '旧: ' + Path(compare_path).name, '新: ' + Path(
        str(DEFAULT_OUT)).name))
    for period in sorted(set(mine) | set(theirs)):
        def cell(row):
            if not row:
                return '-'
            return '%s %d/4' % ('HIT' if row.get('group_hit') else 'miss',
                                int(row.get('position_hits') or 0))
        print('%-14s %-22s %-22s' % (period, cell(theirs.get(period)), cell(mine.get(period))))
    old = _rates(other_rows)
    new = _rates(current_rows)
    print('\n旧: 整组 %d/%d = %.1f%% | 单码 %d/%d = %.1f%%'
          % (old['group'], old['total'], old['group_rate'], old['hits'], old['bets'], old['single_rate']))
    print('新: 整组 %d/%d = %.1f%% | 单码 %d/%d = %.1f%%'
          % (new['group'], new['total'], new['group_rate'], new['hits'], new['bets'], new['single_rate']))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--limit', type=int, default=12)
    parser.add_argument('--from', dest='start', default=None)
    parser.add_argument('--to', dest='end', default=None)
    parser.add_argument('--out', default=str(DEFAULT_OUT))
    parser.add_argument('--tag', default='v2')
    parser.add_argument('--compare', default=None)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--sleep', type=float, default=1.0)
    args = parser.parse_args()
    out_path = Path(args.out)

    snapshot = load_snapshot(persist=False)
    records = [r for r in (snapshot.get('records') or []) if isinstance(r, dict)]
    aligned = [(str(r.get('period')), list(r.get('numbers') or [])[:4]) for r in records]
    aligned = [(p, [int(v) for v in digits]) for p, digits in aligned
               if len(digits) == 4 and all(0 <= int(v) <= 9 for v in digits)]
    rows_all = [digits for _p, digits in aligned]

    if args.start and args.end:
        candidates = [(i, p) for i, (p, _d) in enumerate(aligned)
                      if args.start <= p <= args.end][:-1]
    else:
        # last N references whose target already drawn: take the newest N+1 draws, drop the newest as target
        span = min(len(aligned) - 1, args.limit)
        start_index = max(1, len(aligned) - span - 1)
        candidates = [(i, aligned[i][0]) for i in range(start_index, len(aligned) - 1)]

    if not candidates:
        print('nothing to replay')
        return 0

    print('replay plan: %d 期, %s -> %s | prompt=%s | out=%s'
          % (len(candidates), candidates[0][1], candidates[-1][1],
             kill15_tail.PROMPT_VERSION, out_path))
    if args.dry_run:
        for index, period in candidates:
            rows = rows_all[:index + 1]
            ctx = kill15_tail.build_context(rows, settlements=load_replay(out_path))
            print('  %s  a_cap=%d  pools=%s'
                  % (period, ctx['a_cap'],
                     {k: ctx['positions'][k]['pool'] for k in kill15_tail.POSITION_KEYS}))
        return 0

    existing = load_replay(out_path)
    done = {str(r.get('ref_period')) for r in existing}
    added = 0
    for index, period in candidates:
        if period in done:
            print('skip existing replay: %s' % period)
            continue
        rows = rows_all[:index + 1]
        local = load_replay(out_path)          # rolling hit-rates come from this replay only
        ctx = kill15_tail.build_context(rows, settlements=local)
        target = aligned[index + 1][0] if index + 1 < len(aligned) else kill15_tail.next_period(period)
        prompt, audit = kill15_tail.build_prompt(period, target, rows, context=ctx)
        try:
            prediction, meta = kill15_tail.request_prediction_anytime(prompt, period, target, ctx)
        except Exception as error:
            print('%s failed: %s: %s' % (period, type(error).__name__, str(error)[:200]))
            break

        actual = aligned[index + 1][1]
        positions = {}
        hits = []
        allocation = prediction['allocation']
        for key, label, pos_index in kill15_tail.POSITIONS:
            kill = prediction['kills'][key]
            real = actual[pos_index]
            hit = real != kill
            hits.append(hit)
            positions[key] = {
                'key': key, 'label': label, 'kill': kill, 'actual': real, 'hit': hit,
                'group': 'A' if key in allocation['a'] else 'B',
                'b_sub': allocation['b_sub'].get(key),
            }
        record = {
            'version': 1,
            'mode': 'replay',
            'replay_tag': args.tag,
            'rule_version': kill15_tail.RULE_VERSION,
            'prompt_version': kill15_tail.PROMPT_VERSION,
            'ref_period': period,
            'target_period': target,
            'replayed_at': kill15_tail.beijing_now().isoformat(),
            'allocation': allocation,
            'self_check': prediction.get('self_check') or {},
            'positions': positions,
            'position_hits': sum(1 for h in hits if h),
            'group_hit': all(hits),
            'a_cap': ctx['a_cap'],
            'regime': ctx['regime'],
            'performance_before': ctx['performance'],
            'candidate_pools': {k: ctx['positions'][k]['pool'] for k in kill15_tail.POSITION_KEYS},
            'reason': prediction.get('reason'),
            'model': meta.get('model'),
            'expected_group_rate': kill15_tail.EXPECTED_GROUP_RATE,
        }
        existing = load_replay(out_path)
        existing.append(record)
        save_replay(existing, out_path)
        added += 1
        detail = ' '.join('%s%d->%d%s' % (v['label'], v['kill'], v['actual'], 'v' if v['hit'] else 'x')
                          for v in positions.values())
        print('%s -> %s  %s  %s  A=%s B=%s %s'
              % (period, target, 'HIT' if record['group_hit'] else 'miss', detail,
                 '+'.join(allocation['a']), '+'.join(allocation['b']),
                 json.dumps(allocation.get('b_sub'), ensure_ascii=False)))
        time.sleep(args.sleep)

    rows = load_replay(out_path)
    mine = _rates(rows)
    print('\nreplay total: %d 期 (本次新增 %d) | 整组 %d/%d = %.1f%% | 单码 %d/%d = %.1f%%'
          % (mine['total'], added, mine['group'], mine['total'], mine['group_rate'],
             mine['hits'], mine['bets'], mine['single_rate']))
    if args.compare:
        print_compare(rows, args.compare)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print('replay failed: %s: %s' % (type(error).__name__, str(error)[:200]))
        raise SystemExit(1)
