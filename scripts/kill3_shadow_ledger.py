#!/usr/bin/env python3
"""kill3 纸面跟投账本（只读影子，不产生任何真实下注）。

用途
----
把 kill3 每期封存的「攻击语语义」还原成它真正覆盖的四位码集，并按平台
四定赔率做纸面结算，用来回答一个不依赖面板口径的问题：
如果每期把攻击语覆盖的码各买 1 单位，历史上到底赚不赚？

口径（与 lib/kill3_tail.py 的攻击语、settlements.json 的 group_hit 已逐期对齐）
------------------------------------------------------------------------------
对每期封存，枚举全部 10^4 个四位码（千/百/十/个），剔除：
  1) 被计入位置（positions 中存在、且不是 display_only）等于该位置杀码；
  2) 合尾：tail.key 指定位置子集的和值个位 == tail.kill；
  3) 三数共现（three_avoid 各组的 3 位数字未全部出现在 4 球中即不算剔除）；
  4) 单双形态恰好等于 reverse_parity；
  5) 大小形态恰好等于 reverse_size。
剩余码集 = 攻击语实际覆盖的码集，N = 码数；开奖四球码 ∈ 剩余码集即命中
（与封存/结算里「整组 8 项全中」等价，2026-11-06 全量 1204 期复算 0 mismatch）。

纸面盈亏：每期每个码买 1 单位 -> 投入 N；命中返 9900 单位（四定赔率 9900 含本金，
抽水约 1%）；未中返 0。净 = 9900*命中 - N。另附 1→2→3→4 阶梯倍投压力测试
（连败封顶 4 倍，命中归 1），以及按每码 0.1 元折算的金额。

产物：data/kill3_shadow_ledger.json（面板只读块 lib/kill3_shadow_view.py 读取）；
增量：按 target_period 复用已算行，只有新结算期才重新枚举 10^4（首轮全量约 25s，
之后每期约 20ms）。--rebuild 强制全量重算。

只读边界：只读 data/kill3_tail/{seals,settlements}.json 与 data/kill3_v16_guard.json，
只写 data/kill3_shadow_ledger.json；不调用 AI、不改封存/结算、不下注。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get('PENTA5_ROOT') or '/root/penta5-runner')
LEDGER_VERSION = 1
SEMANTICS_VERSION = 1
SEALS_REL = Path('data') / 'kill3_tail' / 'seals.json'
SETTLEMENTS_REL = Path('data') / 'kill3_tail' / 'settlements.json'
GUARD_REL = Path('data') / 'kill3_v16_guard.json'
OUT_REL = Path('data') / 'kill3_shadow_ledger.json'
POSITIONS = ('q', 'b', 's', 'g')
POS_INDEX = {key: index for index, key in enumerate(POSITIONS)}
TAIL_SETS = {
    'tail_all': (0, 1, 2, 3),
    'tail_qbs': (0, 1, 2),
    'tail_qbg': (0, 1, 3),
    'tail_qsg': (0, 2, 3),
    'tail_bsg': (1, 2, 3),
}
PAYOUT = 9900          # 四定赔率（含本金，平台抽水约 1%）
UNIT_CNY = 0.1         # 每码每单位折算人民币
LADDER = (1, 2, 3, 4)  # 阶梯倍投：连败逐级加码，封顶 4，命中归 1
RULE_RE = re.compile(r"^RULE_VERSION\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)


def _iso(moment=None):
    return (moment or datetime.now().astimezone()).isoformat(timespec='seconds')


def _read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=str(path.parent),
                                         delete=False, suffix='.tmp')
    try:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=1)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.chmod(handle.name, 0o644)
    os.replace(handle.name, str(path))


def read_records(path):
    document = _read_json(path)
    if isinstance(document, dict):
        return list(document.get('records') or [])
    if isinstance(document, list):
        return list(document)
    return []


def current_rule_version(root):
    try:
        text = (Path(root) / 'lib' / 'kill3_tail.py').read_text(encoding='utf-8')
    except OSError:
        return None
    match = RULE_RE.search(text)
    return match.group(1) if match else None


def _as_digits(value):
    text = str(value or '')
    return [int(character) for character in text if character.isdigit()]


def exclude_conditions(seal):
    """把封存记录翻译成 10^4 枚举所需的排除条件（缺失字段按不约束处理）。"""
    display_only = seal.get('display_only')
    killed = {}
    for key, digit in (seal.get('positions') or {}).items():
        if key not in POS_INDEX:
            continue
        if display_only and key == display_only:
            continue
        digits = _as_digits(digit)
        if digits:
            killed[POS_INDEX[key]] = digits[0]
    tail = seal.get('tail') or {}
    tail_positions = TAIL_SETS.get(str(tail.get('key')))
    tail_kill = None
    if tail_positions is not None and tail.get('kill') is not None:
        digits = _as_digits(tail.get('kill'))
        if digits:
            tail_kill = digits[0]
    groups = []
    for item in (seal.get('three_avoid') or []):
        digits = _as_digits((item or {}).get('value'))
        if len(digits) == 3:
            groups.append(tuple(digits))
    parity = seal.get('reverse_parity')
    size = seal.get('reverse_size')
    return {
        'killed': killed,
        'tail_positions': tail_positions,
        'tail_kill': tail_kill,
        'groups': groups,
        'parity': str(parity) if parity else None,
        'size': str(size) if size else None,
    }


def count_attack_codes(conditions):
    """枚举 10^4 个四位码，返回 (剩余码数 N, 剩余码集判定函数)。"""
    killed = conditions['killed']
    tail_positions = conditions['tail_positions']
    tail_kill = conditions['tail_kill']
    groups = conditions['groups']
    parity = conditions['parity']
    size = conditions['size']
    allowed = [[True] * 10 for _ in range(4)]
    for position, digit in killed.items():
        allowed[position][digit] = False
    total = 0
    for q in range(10):
        if not allowed[0][q]:
            continue
        for b in range(10):
            if not allowed[1][b]:
                continue
            for s in range(10):
                if not allowed[2][s]:
                    continue
                for g in range(10):
                    if not allowed[3][g]:
                        continue
                    digits = (q, b, s, g)
                    if tail_positions is not None and tail_kill is not None:
                        if sum(digits[i] for i in tail_positions) % 10 == tail_kill:
                            continue
                    if groups and any(all(digit in digits for digit in group)
                                      for group in groups):
                        continue
                    if parity is not None:
                        if ''.join('单' if digit % 2 else '双' for digit in digits) == parity:
                            continue
                    if size is not None:
                        if ''.join('大' if digit >= 5 else '小' for digit in digits) == size:
                            continue
                    total += 1
    return total


def is_hit(conditions, actual):
    """开奖四球码是否落在攻击语覆盖的剩余码集内。"""
    killed = conditions['killed']
    for position, digit in killed.items():
        if actual[position] == digit:
            return False
    tail_positions = conditions['tail_positions']
    tail_kill = conditions['tail_kill']
    if tail_positions is not None and tail_kill is not None:
        if sum(actual[i] for i in tail_positions) % 10 == tail_kill:
            return False
    groups = conditions['groups']
    if groups and any(all(digit in actual for digit in group) for group in groups):
        return False
    parity = conditions['parity']
    if parity is not None:
        if ''.join('单' if digit % 2 else '双' for digit in actual) == parity:
            return False
    size = conditions['size']
    if size is not None:
        if ''.join('大' if digit >= 5 else '小' for digit in actual) == size:
            return False
    return True


def _max_drawdown(values):
    peak = 0.0
    drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, peak - value)
    return drawdown


def _max_loss_streak(hits):
    longest = current = 0
    for hit in hits:
        if hit:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def build_row(period, seal, settlement, conditions, codes):
    actual = _as_digits(settlement.get('actual'))
    hit = is_hit(conditions, actual)
    stake = codes
    ret = PAYOUT if hit else 0
    return {
        'target_period': str(period),
        'ref_period': str(seal.get('ref_period') or settlement.get('ref_period') or ''),
        'rule_version': str(seal.get('rule_version') or ''),
        'published_at': str(seal.get('published_at') or settlement.get('published_at') or ''),
        'actual': ''.join(str(digit) for digit in actual),
        'n_codes': codes,
        'hit': 1 if hit else 0,
        'stake': stake,
        'ret': ret,
        'pnl': ret - stake,
    }


def decorate(rows):
    """按时间顺序补累计盈亏、回撤、连败与阶梯倍投字段。"""
    cum = 0
    cum_ladder = 0
    multiplier = 1
    for row in rows:
        cum += row['pnl']
        row['cum_pnl'] = cum
        ladder_stake = row['n_codes'] * multiplier
        ladder_ret = PAYOUT * multiplier if row['hit'] else 0
        ladder_pnl = ladder_ret - ladder_stake
        cum_ladder += ladder_pnl
        row['ladder_mult'] = multiplier
        row['ladder_stake'] = ladder_stake
        row['ladder_ret'] = ladder_ret
        row['ladder_pnl'] = ladder_pnl
        row['ladder_cum_pnl'] = cum_ladder
        multiplier = 1 if row['hit'] else min(multiplier + 1, LADDER[-1])
    return rows


def summarize(rows):
    if not rows:
        return {'n': 0, 'hits': 0, 'rate': None, 'coverage_avg': None,
                'cum_stake': 0, 'cum_return': 0, 'pnl': 0, 'roi': None,
                'max_drawdown': 0, 'max_loss_streak': 0,
                'ladder_pnl': 0, 'ladder_max_drawdown': 0,
                'ladder_max_loss_streak': 0}
    hits = sum(row['hit'] for row in rows)
    stake = sum(row['stake'] for row in rows)
    returned = sum(row['ret'] for row in rows)
    pnl = returned - stake
    ladder_pnl = sum(row['ladder_pnl'] for row in rows)
    return {
        'n': len(rows),
        'hits': hits,
        'rate': hits / len(rows),
        'coverage_avg': sum(row['n_codes'] for row in rows) / len(rows) / 10000.0,
        'cum_stake': stake,
        'cum_return': returned,
        'pnl': pnl,
        'roi': (pnl / stake) if stake else None,
        'max_drawdown': _max_drawdown([row['cum_pnl'] for row in rows]),
        'max_loss_streak': _max_loss_streak([row['hit'] for row in rows]),
        'ladder_pnl': ladder_pnl,
        'ladder_max_drawdown': _max_drawdown([row['ladder_cum_pnl'] for row in rows]),
        'ladder_max_loss_streak': _max_loss_streak([row['hit'] for row in rows]),
    }


def load_cache(root):
    try:
        document = _read_json(Path(root) / OUT_REL)
    except (OSError, ValueError):
        return {}
    if not isinstance(document, dict):
        return {}
    if document.get('ledger_version') != LEDGER_VERSION:
        return {}
    if document.get('semantics_version') != SEMANTICS_VERSION:
        return {}
    return {str(row.get('target_period')): row
            for row in (document.get('rows') or []) if row.get('target_period')}


def run(root, rebuild=False, print_summary=False):
    root = Path(root)
    started = datetime.now().astimezone()
    seals = read_records(root / SEALS_REL)
    settlements = read_records(root / SETTLEMENTS_REL)
    seal_by_target = {str(seal.get('target_period')): seal for seal in seals}
    cache = {} if rebuild else load_cache(root)
    rows = []
    skipped = {}
    mismatches = []
    computed = 0
    for settlement in settlements:
        actual = _as_digits(settlement.get('actual'))
        if len(actual) != 4 or settlement.get('group_hit') is None:
            continue
        period = str(settlement.get('target_period'))
        seal = seal_by_target.get(period)
        if seal is None:
            skipped['missing-seal'] = skipped.get('missing-seal', 0) + 1
            continue
        version = str(seal.get('rule_version') or '')
        cached = cache.get(period)
        if cached is not None and cached.get('rule_version') == version:
            row = cached
        else:
            try:
                conditions = exclude_conditions(seal)
                codes = count_attack_codes(conditions)
            except Exception as error:  # noqa: BLE001 - 单期异常不阻断整本账
                skipped['compute-error'] = skipped.get('compute-error', 0) + 1
                skipped.setdefault('compute-error-detail', []).append(
                    '%s: %s: %s' % (period, type(error).__name__, error))
                continue
            row = build_row(period, seal, settlement, conditions, codes)
            computed += 1
        expected = bool(settlement.get('group_hit'))
        if bool(row['hit']) != expected:
            mismatches.append({'target_period': period,
                               'predicted': bool(row['hit']), 'group_hit': expected})
        rows.append(row)
    rows = decorate(rows)
    rows.sort(key=lambda row: row['target_period'])

    rule_version = current_rule_version(root)
    current_rows = [row for row in rows if row['rule_version'] == rule_version]
    if not current_rows:
        latest = rows[-1]['rule_version'] if rows else None
        current_rows = [row for row in rows if row['rule_version'] == latest]
        rule_version = rule_version or latest

    guard = {}
    try:
        guard = _read_json(root / GUARD_REL)
    except (OSError, ValueError):
        guard = {}
    expected_baseline = guard.get('expected') if isinstance(guard, dict) else None
    if guard.get('expected_rule_version') != rule_version:
        expected_baseline = None

    by_version = []
    for version in sorted({row['rule_version'] for row in rows}):
        subset = [row for row in rows if row['rule_version'] == version]
        summary = summarize(subset)
        summary['rule_version'] = version
        summary['first_period'] = subset[0]['target_period']
        summary['last_period'] = subset[-1]['target_period']
        summary['breakeven_rate'] = (summary['coverage_avg'] / (PAYOUT / 10000.0)
                                     if summary['coverage_avg'] is not None else None)
        by_version.append(summary)

    current = summarize(current_rows)
    current['rule_version'] = rule_version
    current['first_period'] = current_rows[0]['target_period'] if current_rows else None
    current['last_period'] = current_rows[-1]['target_period'] if current_rows else None
    current['coverage_baseline'] = expected_baseline
    current['breakeven_rate'] = (current['coverage_avg'] / (PAYOUT / 10000.0)
                                 if current['coverage_avg'] is not None else None)
    overall = summarize(rows)

    payload = {
        'version': 1,
        'ledger_version': LEDGER_VERSION,
        'semantics_version': SEMANTICS_VERSION,
        'root': str(root),
        'generated_at': _iso(),
        'generated_seconds': (datetime.now().astimezone() - started).total_seconds(),
        'rule_version': rule_version,
        'payout': PAYOUT,
        'unit_cny': UNIT_CNY,
        'ladder': list(LADDER),
        'current': current,
        'overall': overall,
        'by_version': by_version,
        'sanity': {'checked': len(rows), 'mismatches': len(mismatches),
                   'examples': mismatches[:5]},
        'skipped': skipped,
        'cache': {'reused': len(rows) - computed, 'computed': computed},
        'rows': rows,
    }
    _write_json_atomic(root / OUT_REL, payload)
    if print_summary or True:
        print('kill3-shadow-ledger: generated=%s rule=%s n=%d hits=%d rate=%s '
              'pnl=%d roi=%s ladder_pnl=%d cached=%d computed=%d mismatches=%d'
              % (payload['generated_at'],
                 (rule_version or '-').split('-v')[-1] if rule_version else '-',
                 current['n'], current['hits'],
                 ('%.4f' % current['rate']) if current['rate'] is not None else '-',
                 current['pnl'],
                 ('%.4f' % current['roi']) if current['roi'] is not None else '-',
                 current['ladder_pnl'], len(rows) - computed, computed,
                 len(mismatches)), flush=True)
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description='kill3 纸面跟投账本（只读）')
    parser.add_argument('--root', default=None)
    parser.add_argument('--rebuild', action='store_true', help='忽略增量缓存，全量重算')
    parser.add_argument('--print', dest='print_summary', action='store_true',
                        help='打印摘要（默认也打印一行 cron 摘要）')
    args = parser.parse_args(argv)
    root = Path(args.root or DEFAULT_ROOT)
    payload = run(root, rebuild=args.rebuild)
    if args.print_summary:
        current = payload['current']
        overall = payload['overall']
        print('rule: %s' % payload['rule_version'])
        print('current: n=%d hits=%d rate=%s coverage=%s pnl=%d roi=%s'
              % (current['n'], current['hits'], current['rate'],
                 current['coverage_avg'], current['pnl'], current['roi']))
        print('overall: n=%d hits=%d rate=%s pnl=%d roi=%s'
              % (overall['n'], overall['hits'], overall['rate'],
                 overall['pnl'], overall['roi']))
        for summary in payload['by_version']:
            print('  %-90s n=%4d hits=%4d rate=%.4f pnl=%9d roi=%s'
                  % (summary['rule_version'], summary['n'], summary['hits'],
                     summary['rate'], summary['pnl'],
                     ('%.4f' % summary['roi']) if summary['roi'] is not None else '-'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
