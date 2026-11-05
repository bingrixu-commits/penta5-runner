#!/usr/bin/env python3
"""只读展示派生：累计曲线回撤 + 项级命中统计（不改任何预测/结算/封存语义）。

数据源（全部只读）：
  data/kill3_tail/settlements.json   kill3 逐期结算（8 项：2 计入位置 + 合尾 + 2 反向形态 + 3 组三数共现）
  data/kill1_tail/settlements.json   kill1 逐期结算（3 项：2 位置 + 合尾）
  data/r1_framework_hit_stats.json   八项框架逐期 outcomes（8 项）
  data/framework_hitrate_cumulative.json（调用方传入 rows，复用其 rule / n / expected）

输出（仅返回字典，不落盘）：
  {key: {'drawdown': {'n', 'rate', 'peak_rate', 'peak_n', 'current_dd_pp', 'maxdd_pp', 'maxdd_at_n',
                      'random': {'sims', 'median_pp', 'p90_pp', 'p_ge_observed'}},
         'item_stats': {'items', 'periods', 'item_hits', 'item_total', 'item_rate', 'avg_items', 'hist'}}}

口径：
- 累计曲线 r_k = 前 k 期整组命中数 / k（按 target_period 升序）；自峰值回撤 = (历史峰值 - 当前值) * 100。
- 随机参照：以该框架累计精确基线 expected 作为独立伯努利 p，固定种子模拟同 n 期累计曲线，
  burn-in 25 期后取最大回撤分布（中位 / p90 / P(最大回撤 >= 观测自峰值回撤)）。
  含义：同期纯随机序列通常也会出现多大的回撤，用来区分「断崖」与「随机波动」。
- 项级命中：每期命中项数（kill3 8 项、kill1 3 项、framework 8 项）的累计均值与项级命中率。
- 只统计 verified 且计入（counted）的记录；缺项的期只跳过项级统计，整组回撤不受影响。
- 任何异常返回 {}，调用方按缺失降级；numpy 缺失时不输出（视图自动隐藏回撤/项级）。
"""
import json
from pathlib import Path

try:
    import numpy as np
except Exception:  # pragma: no cover - 展示派生降级
    np = None

ROOT = Path('/root/penta5-runner')
MIN_PERIODS = 30
BURN_IN = 25
SEED = 20261107


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _period_key(item):
    return str(item[0] or '').zfill(11)


def _kill3_items(row):
    """kill3 当期命中项数 (hits, total)；任何一项缺 hit 布尔值返回 None。"""
    try:
        counted = row.get('counted') or []
        positions = row.get('positions') or {}
        tail = row.get('tail') or {}
        patterns = row.get('patterns') or {}
        avoid = row.get('three_avoid') or []
        flags = []
        for key in counted:
            flag = (positions.get(key) or {}).get('hit')
            if not isinstance(flag, bool):
                return None
            flags.append(flag)
        flag = tail.get('hit')
        if not isinstance(flag, bool):
            return None
        flags.append(flag)
        for field in ('reverse_parity', 'reverse_size'):
            flag = (patterns.get(field) or {}).get('hit')
            if not isinstance(flag, bool):
                return None
            flags.append(flag)
        if len(avoid) != 3:
            return None
        for entry in avoid:
            if not isinstance(entry, dict) or not isinstance(entry.get('hit'), bool):
                return None
            flags.append(entry['hit'])
        return sum(1 for flag in flags if flag), len(flags)
    except Exception:
        return None


def _kill1_items(row):
    """kill1 当期命中项数 (hits, total)；2 位置 + 1 合尾 = 3 项。"""
    try:
        counted = row.get('counted') or []
        positions = row.get('positions') or {}
        tail = row.get('tail') or {}
        flags = []
        for key in counted:
            flag = (positions.get(key) or {}).get('hit')
            if not isinstance(flag, bool):
                return None
            flags.append(flag)
        flag = tail.get('hit')
        if not isinstance(flag, bool):
            return None
        flags.append(flag)
        return sum(1 for flag in flags if flag), len(flags)
    except Exception:
        return None


def _settlement_series(root, folder, rule, item_fn):
    settlements = _read(root / 'data' / folder / 'settlements.json') or {}
    seals = _read(root / 'data' / folder / 'seals.json') or {}
    seal_rule = {str(row.get('ref_period')): row.get('rule_version')
                 for row in (seals.get('records') or []) if isinstance(row, dict)}
    series = []
    for row in settlements.get('records') or []:
        if not isinstance(row, dict) or row.get('verified') is not True:
            continue
        if 'counted' not in row:
            continue
        if (row.get('rule_version') != rule
                and seal_rule.get(str(row.get('ref_period'))) != rule):
            continue
        hit = row.get('group_hit')
        if not isinstance(hit, bool):
            continue
        series.append((row.get('target_period'), hit, item_fn(row)))
    series.sort(key=_period_key)
    return series


def _framework_series(root, _rule):
    document = _read(root / 'data' / 'r1_framework_hit_stats.json') or {}
    verified = list((document.get('verified') or {}).values())
    series = []
    for row in verified:
        if not isinstance(row, dict):
            continue
        outcomes = row.get('outcomes')
        if not isinstance(outcomes, dict) or len(outcomes) < 8:
            continue
        flags = list(outcomes.values())
        if not all(isinstance(flag, bool) for flag in flags):
            continue
        if len(flags) != 8:
            continue
        series.append((row.get('target_period'), all(flags),
                       (sum(1 for flag in flags if flag), 8)))
    series.sort(key=_period_key)
    return series


def _drawdown(series):
    n = len(series)
    if n < MIN_PERIODS:
        return None
    running = 0
    peak = None
    peak_n = 0
    max_dd = 0.0
    max_dd_at = None
    for index, (_period, hit, _items) in enumerate(series, 1):
        running += 1 if hit else 0
        rate = running / index
        # burn-in：前 BURN_IN 期不设峰值、不计回撤，避免“首期命中=100% 峰值”的假断崖
        if index <= BURN_IN:
            continue
        if peak is None or rate > peak + 1e-12:
            peak = rate
            peak_n = index
        delta = (peak - rate) * 100.0
        if delta > max_dd:
            max_dd = delta
            max_dd_at = index
    current = running / n
    return {
        'n': n,
        'hits': running,
        'rate': round(current, 6),
        'peak_rate': round(peak, 6),
        'peak_n': peak_n,
        'current_dd_pp': round((peak - current) * 100.0, 2),
        'maxdd_pp': round(max_dd, 2),
        'maxdd_at_n': max_dd_at,
    }


def _random_reference(n, p, observed_dd_pp):
    if np is None or not isinstance(n, int) or n < MIN_PERIODS or not p:
        return None
    if not 0.0 < p < 1.0:
        return None
    sims = 1200 if n <= 1200 else (800 if n <= 2500 else 600)
    rng = np.random.default_rng(SEED)
    draws = (rng.random((sims, n)) < p)
    cumulative = np.cumsum(draws, axis=1, dtype=np.float64)
    steps = np.arange(1, n + 1, dtype=np.float64)
    rate = cumulative / steps
    # 与观测口径一致：峰值只在 burn-in 之后取，避免前 25 期的极端值主导参照分布
    start = max(BURN_IN - 1, 0)
    rate_after_burn = rate[:, start:]
    peak_after_burn = np.maximum.accumulate(rate_after_burn, axis=1)
    max_drawdown_pp = (peak_after_burn - rate_after_burn).max(axis=1) * 100.0
    return {
        'sims': int(sims),
        'median_pp': round(float(np.median(max_drawdown_pp)), 2),
        'p90_pp': round(float(np.percentile(max_drawdown_pp, 90)), 2),
        'p_ge_observed': round(float((max_drawdown_pp >= observed_dd_pp).mean()), 4),
    }


def _item_stats(series):
    rows = [items for _period, _hit, items in series if items]
    if not rows:
        return None
    totals = {total for _hits, total in rows}
    items = totals.pop() if len(totals) == 1 else None
    item_hits = sum(hits for hits, _total in rows)
    item_total = sum(total for _hits, total in rows)
    periods = len(rows)
    histogram = {}
    for hits, total in rows:
        if items is not None and total == items:
            histogram[str(hits)] = histogram.get(str(hits), 0) + 1
    return {
        'items': items,
        'periods': periods,
        'item_hits': item_hits,
        'item_total': item_total,
        'item_rate': round(item_hits / item_total, 6) if item_total else None,
        'avg_items': round(item_hits / periods, 4) if periods else None,
        'hist': histogram,
    }


def _metrics(series, expected):
    drawdown = _drawdown(series)
    if not drawdown:
        return {}
    expected = _num(expected)
    if expected is not None:
        drawdown['random'] = _random_reference(drawdown['n'], expected,
                                               drawdown['current_dd_pp'])
    result = {'drawdown': drawdown}
    item_stats = _item_stats(series)
    if item_stats:
        result['item_stats'] = item_stats
    return result


def _settlement_version_rows(root, folder):
    """全部 verified+计入记录（跨版本）：[(target_period, rule, joint_hit, item_hits, item_total)]。"""
    payload = _read(root / 'data' / folder / 'settlements.json') or {}
    seals = _read(root / 'data' / folder / 'seals.json') or {}
    seal_rule = {}
    for record in (seals.get('records') or []):
        if isinstance(record, dict) and record.get('ref_period'):
            seal_rule[str(record.get('ref_period'))] = record.get('rule_version')
    rows = []
    for record in (payload.get('records') or []):
        if not isinstance(record, dict):
            continue
        if not record.get('verified') or 'counted' not in record:
            continue
        rule = record.get('rule_version') or seal_rule.get(str(record.get('ref_period'))) or 'unknown'
        counted = record.get('counted') or []
        positions = record.get('positions') or {}
        tail = record.get('tail') or {}
        item_hits = sum(1 for position in counted
                        if isinstance(positions.get(position), dict)
                        and positions[position].get('hit'))
        if isinstance(tail, dict) and tail.get('hit'):
            item_hits += 1
        rows.append((record.get('target_period'), str(rule),
                     bool(record.get('group_hit')), item_hits, len(counted) + 1))
    return rows


def _version_stats(rows):
    """跨版本分段 + 核心杀项（计入位置 + 合尾）项级累计：切版不清零、整组按各版基线重标定。"""
    segments, by_rule = [], {}
    core_hits = core_total = 0
    for period, rule, joint, item_hits, item_total in rows:
        core_hits += item_hits
        core_total += item_total
        segment = by_rule.get(rule)
        if segment is None:
            segment = {'rule': rule, 'n': 0, 'hits': 0,
                       'first_period': period, 'last_period': period,
                       'core_hits': 0, 'core_total': 0}
            by_rule[rule] = segment
            segments.append(segment)
        segment['n'] += 1
        segment['hits'] += 1 if joint else 0
        segment['last_period'] = period
        segment['core_hits'] += item_hits
        segment['core_total'] += item_total
    for segment in segments:
        segment['rate'] = (round(segment['hits'] / segment['n'], 6)
                           if segment['n'] else None)
        segment['core_rate'] = (round(segment['core_hits'] / segment['core_total'], 6)
                                if segment['core_total'] else None)
    periods = len(rows)
    return {
        'core': {'periods': periods, 'item_hits': core_hits, 'item_total': core_total,
                 'item_rate': round(core_hits / core_total, 6) if core_total else None,
                 'avg_items': round(core_total / periods, 4) if periods else None},
        'segments': segments,
    }


def build_display_metrics(rows, as_of=None, root=None):
    """rows: framework_hitrate_cumulative.json 的 rows；as_of: 报告 as_of_period（截断到同一期）。

    返回 {key: {'drawdown':…, 'item_stats':…}}。
    """
    if not isinstance(rows, list):
        return {}
    root = Path(root) if root else ROOT
    rules = {}
    expected = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get('key'):
            continue
        key = str(row['key'])
        rules[key] = str(row.get('rule') or '')
        expected[key] = row.get('expected')
    loaders = {
        'kill3_tail': lambda rule: _settlement_series(root, 'kill3_tail', rule, _kill3_items),
        'kill1_tail': lambda rule: _settlement_series(root, 'kill1_tail', rule, _kill1_items),
        'framework': lambda rule: _framework_series(root, rule),
    }
    result = {}
    for key, loader in loaders.items():
        rule = rules.get(key)
        if not rule:
            continue
        try:
            series = loader(rule)
            if as_of:
                cutoff = str(as_of).zfill(11)
                series = [item for item in series
                          if str(item[0] or "").zfill(11) <= cutoff]
            metrics = _metrics(series, expected.get(key))
        except Exception:
            metrics = {}
        if metrics and key in ('kill3_tail', 'kill1_tail'):
            try:
                version_rows = _settlement_version_rows(root, key)
                if as_of:
                    version_cutoff = str(as_of).zfill(11)
                    version_rows = [item for item in version_rows
                                    if str(item[0] or '').zfill(11) <= version_cutoff]
                metrics['versions'] = _version_stats(version_rows)
            except Exception:
                pass
        if metrics:
            result[key] = metrics
    return result
