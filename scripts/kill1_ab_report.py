"""kill1 pro-vs-flash 影子 A/B 报告（预注册判据见 kill1-ab-experiment-plan.md）。

口径与线上封存一致（lib/kill1_tail.py v4）：
- 位置命中 = 该位置实际开奖号 != 杀码（1 个位置算 1 项，计入整组）；
- 合尾命中 = 对应位置和值个位 != 排除码；
- 整组命中 = 1 个位置 + 1 个合尾同时命中（2 项合取）。
评估只为「同一 reference 两臂都成功且 prompt 相同」的配对样本，用
data/kill1_tail/settlements.json 里的 actual 四位开奖号独立复算，不依赖面板统计。
"""
from pathlib import Path
import argparse
import json
import math
import os
import sys

ROOT = Path('/root/penta5-runner')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'lib') not in sys.path:
    sys.path.insert(0, str(ROOT / 'lib'))

import kill1_tail  # noqa: E402

POSITION_INDEX = dict(kill1_tail.POSITION_INDEX)
TAIL_POSITIONS = {
    'tail_all': ('q', 'b', 's', 'g'),
    'tail_qbs': ('q', 'b', 's'),
    'tail_qbg': ('q', 'b', 'g'),
    'tail_qsg': ('q', 's', 'g'),
    'tail_bsg': ('b', 's', 'g'),
}
SAFETY_SAMPLE = 600
FINAL_SAMPLE_2000 = 2000
Z95 = 1.959963984540054


def _env(name, default=''):
    value = os.getenv(name)
    return default if value is None else value.strip()


def ledger_path():
    override = _env('PENTA5_KILL1_AB_LEDGER')
    return Path(override) if override else kill1_tail.DATA_DIR / 'ab_raw.jsonl'


def settlements_path():
    override = _env('PENTA5_KILL1_AB_SETTLEMENTS')
    return Path(override) if override else kill1_tail.SETTLEMENTS_PATH


def seals_path():
    override = _env('PENTA5_KILL1_AB_SEALS')
    return Path(override) if override else kill1_tail.SEALS_PATH


def load_json(path, default):
    try:
        with Path(path).open('r', encoding='utf-8') as handle:
            return json.load(handle)
    except (FileNotFoundError, ValueError):
        return default


def load_ledger(path=None):
    path = Path(path) if path is not None else ledger_path()
    records = []
    try:
        with path.open('r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    records.append(item)
    except FileNotFoundError:
        pass
    return records


def load_actuals(path=None):
    data = load_json(path if path is not None else settlements_path(), {})
    records = data.get('records') if isinstance(data, dict) else data
    actuals = {}
    for item in records or []:
        if not isinstance(item, dict):
            continue
        target = str(item.get('target_period') or '')
        actual = item.get('actual')
        if len(target) != 11 or not isinstance(actual, list) or len(actual) != 4:
            continue
        try:
            digits = [int(value) for value in actual]
        except (TypeError, ValueError):
            continue
        if any(digit < 0 or digit > 9 for digit in digits):
            continue
        actuals[target] = {
            'digits': digits,
            'settlement': item,
        }
    return actuals


def wilson(hits, n, z=Z95):
    if n <= 0:
        return (None, None)
    p = hits / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(b, c):
    """两臂配对：b=仅臂1命中，c=仅臂2命中；返回双侧精确二项 p。"""
    if b < 0 or c < 0:
        raise ValueError('negative discordant count')
    total = b + c
    if total == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(total, i) for i in range(k + 1)) / float(2 ** total)
    return min(1.0, 2.0 * tail)


def paired_delta_ci(b, c, n, z=Z95):
    """配对比例差 (arm2-arm1) 的 Wald 近似 95%CI。"""
    if n <= 0:
        return (None, None)
    delta = (c - b) / float(n)
    variance = (b + c) - ((b - c) ** 2) / float(n)
    se = math.sqrt(max(0.0, variance)) / float(n)
    return (delta - z * se, delta + z * se)


def _digits_map(actual_digits):
    return {key: int(actual_digits[index]) for key, index in POSITION_INDEX.items()}


def evaluate_prediction(prediction, digits_by_key):
    """返回 {positions: {key: bool}, tail: bool|None, group: bool|None}。"""
    positions = (prediction or {}).get('positions') or {}
    counted = (prediction or {}).get('counted') or list(positions)
    tail = (prediction or {}).get('tail') or {}
    position_hits = {}
    for key in counted:
        if key not in digits_by_key or key not in positions:
            return None
        try:
            kill = int(positions[key])
        except (TypeError, ValueError):
            return None
        position_hits[key] = digits_by_key[key] != kill
    key = tail.get('key')
    positions_of_tail = TAIL_POSITIONS.get(key)
    if positions_of_tail is None:
        return None
    try:
        tail_kill = int(tail.get('kill'))
    except (TypeError, ValueError):
        return None
    total = sum(digits_by_key[item] for item in positions_of_tail) % 10
    tail_hit = total != tail_kill
    group_hit = all(position_hits.values()) and tail_hit if position_hits else None
    return {'positions': position_hits, 'tail': tail_hit, 'group': group_hit}


def _rate(hits, n):
    return (hits / float(n)) if n else None


def _fmt_rate(hits, n, digits=2):
    if not n:
        return '-'
    return '%.*f%% (%d/%d)' % (digits, 100.0 * hits / n, hits, n)


def build_pairs(ledger, actuals):
    flash = {}
    pro = {}
    for record in ledger:
        reference = str(record.get('reference') or '')
        arm = record.get('arm')
        if len(reference) != 11:
            continue
        if arm == 'flash' and record.get('status') == 'ok':
            flash.setdefault(reference, record)
        elif arm == 'pro' and record.get('status') == 'ok':
            pro.setdefault(reference, record)
    pairs = []
    pending = 0
    prompt_mismatch = 0
    for reference in sorted(set(flash) & set(pro)):
        flash_record = flash[reference]
        pro_record = pro[reference]
        target = str(flash_record.get('target') or '')
        if not target:
            continue
        if flash_record.get('prompt_sha256') != pro_record.get('prompt_sha256'):
            prompt_mismatch += 1
            continue
        settled = actuals.get(target)
        if settled is None:
            pending += 1
            continue
        digits = _digits_map(settled['digits'])
        flash_eval = evaluate_prediction(flash_record.get('prediction'), digits)
        pro_eval = evaluate_prediction(pro_record.get('prediction'), digits)
        if flash_eval is None or pro_eval is None:
            continue
        pairs.append({
            'reference': reference,
            'target': target,
            'flash': flash_record,
            'pro': pro_record,
            'flash_eval': flash_eval,
            'pro_eval': pro_eval,
            'settlement': settled['settlement'],
        })
    return pairs, pending, prompt_mismatch


def arm_metric(pairs, arm, metric):
    hits = 0
    n = 0
    for pair in pairs:
        value = pair['%s_eval' % arm].get(metric)
        if value is None:
            continue
        n += 1
        hits += 1 if value else 0
    return hits, n


def paired_counts(pairs, metric):
    """返回 (both, flash_only, pro_only, neither)：配对四格。"""
    both = flash_only = pro_only = neither = 0
    for pair in pairs:
        flash_hit = pair['flash_eval'].get(metric)
        pro_hit = pair['pro_eval'].get(metric)
        if flash_hit is None or pro_hit is None:
            continue
        if flash_hit and pro_hit:
            both += 1
        elif flash_hit:
            flash_only += 1
        elif pro_hit:
            pro_only += 1
        else:
            neither += 1
    return both, flash_only, pro_only, neither


def item_breakdown(pairs, item_metric):
    rows = {}
    for pair in pairs:
        for arm in ('flash', 'pro'):
            target = pair['%s_eval' % arm].get(item_metric)
            if target is None:
                continue
            for key, hit in target.items():
                row = rows.setdefault(key, {'flash': [0, 0], 'pro': [0, 0]})
                row[arm][0] += 1 if hit else 0
                row[arm][1] += 1
    return rows


def tail_breakdown(pairs):
    rows = {}
    for pair in pairs:
        for arm in ('flash', 'pro'):
            prediction = pair[arm].get('prediction') or {}
            key = (prediction.get('tail') or {}).get('key')
            hit = pair['%s_eval' % arm].get('tail')
            if not key or hit is None:
                continue
            row = rows.setdefault(key, {'flash': [0, 0], 'pro': [0, 0]})
            row[arm][0] += 1 if hit else 0
            row[arm][1] += 1
    return rows


def sealed_metrics(pairs):
    rows = []
    for pair in pairs:
        settlement = pair.get('settlement') or {}
        if settlement.get('rule_version') != kill1_tail.RULE_VERSION:
            continue
        actual_hit = settlement.get('group_hit')
        if actual_hit is None:
            continue
        rows.append({
            'reference': pair['reference'],
            'target': pair['target'],
            'sealed_group': bool(actual_hit),
            'flash_group': pair['flash_eval'].get('group'),
            'pro_group': pair['pro_eval'].get('group'),
        })
    return rows


def usage_totals(pairs):
    totals = {'flash': {'prompt': 0, 'completion': 0, 'reasoning': 0, 'elapsed': []},
              'pro': {'prompt': 0, 'completion': 0, 'reasoning': 0, 'elapsed': []}}
    for pair in pairs:
        for arm in ('flash', 'pro'):
            meta = pair[arm].get('meta') or {}
            usage = meta.get('usage') or {}
            prompt = usage.get('prompt_tokens')
            completion = usage.get('completion_tokens')
            reasoning = usage.get('reasoning_tokens')
            if isinstance(prompt, int):
                totals[arm]['prompt'] += prompt
            if isinstance(completion, int):
                totals[arm]['completion'] += completion
            if isinstance(reasoning, int):
                totals[arm]['reasoning'] += reasoning
            elapsed = meta.get('elapsed_seconds')
            if isinstance(elapsed, (int, float)):
                totals[arm]['elapsed'].append(float(elapsed))
    return totals


def _percentile(values, ratio):
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(ratio * (len(ordered) - 1)))))
    return ordered[index]


def verdict(n, delta_pp, p_value):
    if n < SAFETY_SAMPLE:
        return 'continue：样本 %d < %d，只看安全性（失败率/成本），不下结论' % (n, SAFETY_SAMPLE)
    worse = delta_pp is not None and p_value is not None and delta_pp <= -3.0 and p_value < 0.01
    better = delta_pp is not None and p_value is not None and delta_pp >= 5.0 and p_value < 0.001
    if worse:
        return 'STOP：pro 显著更差（Δ≤-3pp 且 p<.01，触发预注册提前停规则）'
    if better:
        return 'STOP：pro 显著更好（Δ≥+5pp 且 p<.001，触发预注册提前停规则）'
    if n < FINAL_SAMPLE_2000:
        return 'continue：未触发提前停规则；n=%d < 2000，继续采集' % n
    if p_value is not None and p_value < 0.05 and delta_pp is not None and abs(delta_pp) >= 3.0:
        return 'FINAL：n≥2000 且 |Δ|≥3pp、p<.05，可下结论'
    return 'FINAL：n≥2000 但未检出 ≥3pp 显著差异，判 pro 无实质增益（回退/维持 flash）'


def build_report():
    ledger = load_ledger()
    actuals = load_actuals()
    pairs, pending, prompt_mismatch = build_pairs(ledger, actuals)
    flash_fail = sum(1 for r in ledger if r.get('arm') == 'flash'
                     and r.get('status') != 'ok')
    pro_fail_records = [r for r in ledger if r.get('arm') == 'pro'
                        and r.get('status') == 'fail']
    flash_ok = sum(1 for r in ledger if r.get('arm') == 'flash' and r.get('status') == 'ok')
    pro_ok = sum(1 for r in ledger if r.get('arm') == 'pro' and r.get('status') == 'ok')
    n = len(pairs)
    result = {
        'ledger': str(ledger_path()),
        'settlements': str(settlements_path()),
        'counts': {
            'flash_ok': flash_ok,
            'pro_ok': pro_ok,
            'flash_fail': flash_fail,
            'pro_fail': len(pro_fail_records),
            'pairs': n,
            'pending_settlement': pending,
            'prompt_mismatch': prompt_mismatch,
        },
        'metrics': {},
        'items': {},
        'tails': {},
        'sealed': None,
        'usage': usage_totals(pairs),
        'verdict': None,
    }
    for metric in ('group', 'tail'):
        flash_hits, flash_n = arm_metric(pairs, 'flash', metric)
        pro_hits, pro_n = arm_metric(pairs, 'pro', metric)
        both, flash_only, pro_only, neither = paired_counts(pairs, metric)
        usable = both + flash_only + pro_only + neither
        p = mcnemar_exact(flash_only, pro_only) if usable else None
        delta_pp = (100.0 * (pro_hits - flash_hits) / usable) if usable else None
        ci = paired_delta_ci(flash_only, pro_only, usable) if usable else (None, None)
        result['metrics'][metric] = {
            'flash': {'hits': flash_hits, 'n': flash_n,
                      'rate': _rate(flash_hits, flash_n),
                      'wilson': wilson(flash_hits, flash_n)},
            'pro': {'hits': pro_hits, 'n': pro_n,
                    'rate': _rate(pro_hits, pro_n),
                    'wilson': wilson(pro_hits, pro_n)},
            'discordant': {'both': both, 'flash_only': flash_only,
                           'pro_only': pro_only, 'neither': neither},
            'delta_pp': delta_pp,
            'delta_ci_pp': (None, None) if ci[0] is None
                           else (100.0 * ci[0], 100.0 * ci[1]),
            'mcnemar_p': p,
        }
    result['items'] = {key: {'flash': tuple(row['flash']), 'pro': tuple(row['pro'])}
                       for key, row in item_breakdown(pairs, 'positions').items()}
    result['tails'] = {key: {'flash': tuple(row['flash']), 'pro': tuple(row['pro'])}
                       for key, row in tail_breakdown(pairs).items()}
    sealed_rows = sealed_metrics(pairs)
    if sealed_rows:
        sealed_hits = sum(1 for row in sealed_rows if row['sealed_group'])
        flash_hits = sum(1 for row in sealed_rows if row['flash_group'])
        pro_hits = sum(1 for row in sealed_rows if row['pro_group'])
        result['sealed'] = {
            'n': len(sealed_rows),
            'sealed': [sealed_hits, len(sealed_rows)],
            'flash': [flash_hits, len(sealed_rows)],
            'pro': [pro_hits, len(sealed_rows)],
        }
    group_metric = result['metrics']['group']
    result['verdict'] = verdict(n, group_metric['delta_pp'], group_metric['mcnemar_p'])
    pro_errors = {}
    for record in pro_fail_records:
        error = record.get('error') or {}
        label = str(error.get('type') or 'unknown')
        pro_errors[label] = pro_errors.get(label, 0) + 1
    result['counts']['pro_error_types'] = pro_errors
    return result


def format_text(report):
    counts = report['counts']
    lines = []
    lines.append('kill1 pro-vs-flash 影子 A/B 报告  as_of=%s' % _now_text())
    lines.append('台账：%s' % report['ledger'])
    lines.append('配对样本 n=%d；flash ok=%d pro ok=%d；待开奖=%d；prompt 不一致=%d'
                 % (counts['pairs'], counts['flash_ok'], counts['pro_ok'],
                    counts['pending_settlement'], counts['prompt_mismatch']))
    lines.append('失败：pro=%d %s；flash=%d'
                 % (counts['pro_fail'], counts.get('pro_error_types') or {},
                    counts['flash_fail']))
    lines.append('')
    for metric, label in (('group', '整组（2 位置+1 合尾 3 项全中）'),
                          ('tail', '合尾单项')):
        row = report['metrics'][metric]
        flash = row['flash']
        pro = row['pro']
        ci_lo, ci_hi = row['delta_ci_pp']
        ci_text = '-' if ci_lo is None else '[%+.2f, %+.2f]' % (ci_lo, ci_hi)
        p_text = '-' if row['mcnemar_p'] is None else '%.4g' % row['mcnemar_p']
        delta_text = '-' if row['delta_pp'] is None else '%+.2fpp' % row['delta_pp']
        lines.append('%s：' % label)
        lines.append('  flash %s' % _fmt_rate(flash['hits'], flash['n']))
        lines.append('  pro   %s' % _fmt_rate(pro['hits'], pro['n']))
        lines.append('  Δ(pro-flash)=%s  95%%CI %s  McNemar p=%s  四格 both/flash-only/pro-only/neither=%s'
                     % (delta_text, ci_text, p_text,
                        tuple(row['discordant'][key] for key in
                              ('both', 'flash_only', 'pro_only', 'neither'))))
    lines.append('')
    lines.append('位置项（每项独立计数）：')
    for key in kill1_tail.POSITION_ORDER:
        row = report['items'].get(key)
        if not row:
            continue
        lines.append('  %s flash %s  pro %s'
                     % (kill1_tail.POSITION_LABELS.get(key, key),
                        _fmt_rate(*row['flash']), _fmt_rate(*row['pro'])))
    lines.append('合尾项（按 tail key）：')
    for key in sorted(report['tails']):
        row = report['tails'][key]
        lines.append('  %s flash %s  pro %s' % (key, _fmt_rate(*row['flash']),
                                                _fmt_rate(*row['pro'])))
    lines.append('')
    usage = report['usage']
    for arm in ('flash', 'pro'):
        elapsed = usage[arm]['elapsed']
        lines.append('%s tokens: prompt=%d completion=%d reasoning=%d；elapsed p50=%s p90=%s'
                     % (arm, usage[arm]['prompt'], usage[arm]['completion'],
                        usage[arm]['reasoning'],
                        _fmt_seconds(_percentile(elapsed, 0.5)),
                        _fmt_seconds(_percentile(elapsed, 0.9))))
    sealed = report.get('sealed')
    if sealed:
        lines.append('')
        lines.append('同 target 对照（v4 已封存）：sealed %s；flash raw %s；pro raw %s'
                     % (_fmt_rate(*sealed['sealed']), _fmt_rate(*sealed['flash']),
                        _fmt_rate(*sealed['pro'])))
    lines.append('')
    lines.append('判定：%s' % report['verdict'])
    return '\n'.join(lines)


def _now_text():
    from stats_history import beijing_now
    return beijing_now().strftime('%Y-%m-%d %H:%M:%S')


def _fmt_seconds(value):
    return '-' if value is None else '%.1fs' % value


def main(argv=None):
    parser = argparse.ArgumentParser(description='kill1 pro-vs-flash A/B 报告')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    args = parser.parse_args(argv)
    report = build_report()
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_text(report))
    return 0


if __name__ == '__main__':
    sys.exit(main())
