#!/usr/bin/env python3
"""框架整组命中率累计报告：版本锁定 + 精确枚举随机基线 + Wilson 区间 + 发布覆盖率。

口径（写死，避免误读）：
- 整组命中 = 一期要求全部项目同时命中；随机期望 = 现行规则用 10^4 开奖组合精确枚举校准值。
- 只统计已结算/已核验记录；缺预测、待核验、冲突期不进分母。
- 逐框架按「当前规则版本」锁定样本；旧版本单独列出，不混算。
- 覆盖率 = 当前版本已核验期数 / 首期到末期之间的计划期数（累计口径，含历史缺口）；
  计划期命中率 = 命中数 / 计划期数（把漏发期按 0 命中计入，用于暴露 402/停机造成的真实损失）。
- 滚动口径 coverage_24h / coverage_7d / plan_hit_rate_24h / plan_hit_rate_7d（现行水平，看这几个，
  不要拿累计覆盖率当现行覆盖率）：窗口结束于「上一个已结算期」= 最新已开奖期 - 1 期
  （最新一期刚开奖、各框架尚未结算，属在途期，记在 rolling.pending_period 且不计入分母/漏期），
  向前 288 期（24h）/ 2016 期（7d）；
  分母 = 窗口内该框架「已上线」期数（从该框架第一条 verified/seal 的 target_period 起算，
  窗口起点取 max(上线期, window_start)）；分子 = 窗口内 verified 且计入的 target_period 数；
  plan_hit_rate_* = 窗口内命中数 / 分母（漏期按 0 命中）。
  顶层 coverage_note 说明累计覆盖率的缺口来源（余额停跑/引入前），避免把历史缺口误判成现行掉链。
- n < 200 判定「样本不足」，不做优劣结论；p 为双侧二项检验（方差用 p0(1-p0)/n）。

数据源：data/r1_stats.json、r1_framework_hit_stats.json、selected_six_hit_stats.json、
kill3_tail/settlements.json+seals.json、strategy_comparison_stats.json、*_status.json。
只读；产物 data/framework_hitrate_cumulative.json，由 crontab 每 15 分钟刷新。
"""
import glob
import json
import math
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
OUT = DATA / 'framework_hitrate_cumulative.json'
MIN_SAMPLE = 200
BASIS = '现行规则 10^4 开奖组合精确枚举校准（2026-10-31）'
DATA_LAG_NOTE = ('账本每期刷新，as_of 与各账本可能差 ≤1 期（时序竞争：最新一期结算与报告生成并发，'
                 '报告可能落后或超前对应账本 1 期）')
JOINT_FORM_BOUNDARY_NOTE = ('该时刻起 framework/killer 的 2 个反向形态改为联合优化形态（主判断不变）；'
                            '跨分界累计趋势需谨慎解读')

RULE_BASELINE = {
    ('killer', 'conditional-transition-v2'): (9, 0.4162),
    ('framework', 'marginal-frequency-v2'): (8, 0.5559),
    ('selected_six', 'selected-six-independent-v4-minimax-forms'): (8, 0.5160),
    ('selected_six', 'selected-six-independent-v6-panel-context'): (8, 0.5160),
    ('selected_six', 'selected-six-independent-v7-single-tail'): (7, 0.5726),
    ('he3_refine', 'he3-refine-v2-balanced'): (1, 0.9375),
    ('he3_refine', 'he3-refine-v3-panel-context'): (1, 0.9375),
    ('kill3_tail', 'kill3-tail-v4'): (5, 0.6191),
    ('kill3_tail', 'kill3-tail-v5-panel-context'): (5, 0.6191),
    ('kill3_tail', 'kill3-tail-v6-three-positions'): (6, 0.5478),
    ('kill3_tail', 'kill3-tail-v7-two-counted'): (5, 0.6191),
    ('kill3_tail', 'kill3-tail-v8-three-positions-hold3'): (6, 0.5478),
    ('kill3_tail', 'kill3-tail-v9-two-counted-hold3'): (5, 0.6191),
    ('kill3_tail', 'kill3-tail-v10-two-counted-free'): (5, 0.6191),
    ('kill1_tail', 'kill1-tail-v1-one-position-one-form'): (3, 0.7538),
    ('kill2p_tail', 'kill2p-tail-v1-two-positions-one-tail-two-forms'): (5, 0.6203),
    ('kill2p_tail', 'kill2p-tail-v2-panel-context-rich'): (5, 0.6203),
    ('kill2_tail', 'kill2-tail-v1-two-positions-he2he3'): (7, 0.5708),
    ('kill2_tail', 'kill2-tail-v2-two-positions-he2he3-rot3'): (7, 0.5708),
    ('kill4_tail', 'kill4-tail-v1-three-positions-he2he3'): (8, 0.5038),
    ('kill5_tail', 'kill5-tail-v1-three-positions-he2he3'): (8, 0.5038),
    ('kill6_tail', 'kill6-tail-v1-three-positions-he2he3'): (8, 0.5038),
}
LABELS = {
    'killer': '智能深排 killer（9 项）',
    'framework': '固定八码框架（8 项）',
    'selected_six': '七项独立框架 selected_six（7 项）',
    'he3_refine': '合尾 he3 剩余 5 码',
    'kill3_tail': 'AI 三位置杀码',
    'kill1_tail': 'AI 单位置杀码（3 项）',
    'kill2p_tail': 'AI 二位置杀码（5 项）',
    'kill2_tail': 'AI 二位置杀码 + 二数合/三数合（7 项）',
    'kill4_tail': 'AI 三位置杀码 + 二数合/三数合（8 项）',
    'kill5_tail': 'AI 三位置杀码 + 二数合/三数合（8 项·自由尾码）',
    'kill6_tail': 'AI 三位置杀码 + 二数合/三数合（8 项）',
}
WINDOWS = {
    'killer': '自账本首期累计',
    'framework': '自账本首期累计',
    'selected_six': '当前规则版本累计',
    'he3_refine': '当前规则版本累计',
    'kill3_tail': '当前规则版本累计',
    'kill1_tail': '当前规则版本累计',
    'kill2p_tail': '当前规则版本累计',
    'kill2_tail': '当前规则版本累计',
    'kill4_tail': '当前规则版本累计',
    'kill5_tail': '当前规则版本累计',
    'kill6_tail': '当前规则版本累计',
}
ROLLING_WINDOWS = (('24h', 288), ('7d', 2016))
ROLLING_RECENT_PERIODS = 24
ROLLING_DEFINITION = (
    '滚动覆盖率 coverage_24h/coverage_7d = 窗口内 verified 且计入的 target_period 数 / 窗口内该框架已上线期数；'
    'plan_hit_rate_24h/plan_hit_rate_7d = 窗口内命中数 / 窗口内已上线期数（漏期按 0 命中计入）。'
    '窗口结束于上一个已结算期（最新已开奖期 - 1 期；最新一期在途待结算，记 pending_period，'
    '不计入分母/漏期），向前 288 期(24h) / 2016 期(7d)；分母从该框架第一条 verified/seal 的 target_period 起算，'
    '窗口起点取 max(上线期, window_start)，因此「引入前」与窗口外期数不算掉链。')
COVERAGE_NOTE = (
    '累计覆盖率 coverage 是「当前规则版本首期→末期」的累计口径，含历史缺口，不能当现行覆盖率：'
    '2026-10-31 01030..01038 / 01059 / 01085 / 01091..01128 / 01135..01143 等连续段为账户余额停跑、'
    'AI 总闸关闭（并非现行掉链），13:15 恢复后主链未再连续掉期（此后仅个别单期偶发缺口）；'
    '另各框架「引入前」期数（如 kill1_tail 自 20261101014 起）也不属于掉链。'
    '现行水平请看 coverage_24h / coverage_7d / plan_hit_rate_24h / plan_hit_rate_7d。')


def _read(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _wilson(hits, n, z=1.96):
    if n <= 0:
        return [None, None]
    p = hits / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return [round((c - s) / d, 6), round((c + s) / d, 6)]


def _p_two(hits, n, p0):
    if n <= 0 or p0 is None:
        return None
    z = (hits / n - p0) / math.sqrt(p0 * (1 - p0) / n)
    return round(math.erfc(abs(z) / math.sqrt(2)), 6)


def _need_n(p0, delta=0.02):
    za, zb = 1.959964, 0.841621
    p1 = min(0.999, p0 + delta)
    return int(-(-((za * math.sqrt(p0 * (1 - p0)) + zb * math.sqrt(p1 * (1 - p1))) ** 2
                   / (p1 - p0) ** 2) // 1))


def _period_index(period):
    try:
        text = str(period)
        if len(text) < 11:
            return None
        day = date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        index = int(text[8:11])
        if index < 0 or index > 287:
            return None
        return day.toordinal() * 288 + index
    except (TypeError, ValueError):
        return None


def _draw_index(value):
    """官方期号 YYYYMMDD001..288 的连续槽位索引（288 期/日；slot288 = 次日 00:00）。

    滚动窗口按此索引对齐（1..288 全部有效）；_period_index 是旧「计划期数」口径，
    只认 0..287，两者用途不同，不要互相替代。
    """
    try:
        text = str(value)
        if len(text) < 11:
            return None
        day = date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        index = int(text[8:11])
        if index < 1 or index > 288:
            return None
        return day.toordinal() * 288 + index - 1
    except (TypeError, ValueError):
        return None


def _period_from_index(index):
    """_draw_index 的逆映射，用于在报告里写回期号。"""
    if not isinstance(index, int) or isinstance(index, bool):
        return None
    day, slot = divmod(index, 288)
    try:
        moment = date.fromordinal(day)
    except (TypeError, ValueError, OverflowError):
        return None
    return '%04d%02d%02d%03d' % (moment.year, moment.month, moment.day, slot + 1)


def _planned(periods):
    indexes = [_period_index(p) for p in periods]
    indexes = [i for i in indexes if i is not None]
    if not indexes:
        return None
    return max(indexes) - min(indexes) + 1


def _expected(key, rule):
    item = RULE_BASELINE.get((key, rule))
    if item is None:
        return None
    return item[1]


def _items(key, rule):
    item = RULE_BASELINE.get((key, rule))
    return item[0] if item else None


_LATEST_DRAW = []
_WINDOW_END = []


def _latest_draw_period():
    """最新已开奖期（滚动窗口锚点）：data/state.json latestRecord，回退 stats_draw_history.json。"""
    if _LATEST_DRAW:
        return _LATEST_DRAW[0]
    period = None
    document = _read(DATA / 'state.json')
    record = document.get('latestRecord') if isinstance(document, dict) else None
    if isinstance(record, dict) and _draw_index(record.get('period')) is not None:
        period = str(record.get('period'))
    if period is None:
        document = _read(DATA / 'stats_draw_history.json')
        records = document.get('records') if isinstance(document, dict) else None
        if isinstance(records, list) and records and isinstance(records[-1], dict):
            if _draw_index(records[-1].get('period')) is not None:
                period = str(records[-1]['period'])
    _LATEST_DRAW.append(period)
    return period


def _window_end_index():
    if not _WINDOW_END:
        _WINDOW_END.append(_draw_index(_latest_draw_period()))
    return _WINDOW_END[0]


def _settled_end_index():
    """已结算窗口结束索引 = 最新已开奖期 - 1。

    最新一期刚开奖、各框架可能尚未结算（在途期），不能算成漏发：
    窗口结束于上一个已结算期，在途期只记在 rolling.pending_period。
    """
    latest = _window_end_index()
    return None if latest is None else latest - 1


def _reset_rolling_cache():
    _LATEST_DRAW.clear()
    _WINDOW_END.clear()


def _rolling_points(records):
    """[(target, hit)] -> {期号: (index, hit)}；同一期多条记录按「任一条命中即命中」去重。"""
    entries = {}
    for target, hit in records or ():
        index = _draw_index(target)
        if index is None:
            continue
        text = str(target)
        previous = entries.get(text)
        entries[text] = (index, bool(hit) or (previous[1] if previous else False))
    return entries


def _rolling_fields(points, launch_periods, end_index):
    """滚动覆盖率 / 计划命中率（现行水平）；锚点不可用时全部返回 None。"""
    result = {'coverage_24h': None, 'coverage_7d': None,
              'plan_hit_rate_24h': None, 'plan_hit_rate_7d': None,
              'rolling': {'window_end': None, 'pending_period': None, 'launch_period': None,
                          'windows': {},
                          'note': '最新已开奖期不可用，无法计算滚动窗口'}}
    if end_index is None:
        return result
    pending_index = end_index + 1
    entries = points or {}
    launches = []
    for value in launch_periods or ():
        index = _draw_index(value)
        if index is not None:
            launches.append(index)
    launches.extend(index for index, _ in entries.values())
    first_index = min(launches) if launches else None
    result['rolling']['window_end'] = _period_from_index(end_index)
    result['rolling']['pending_period'] = _period_from_index(pending_index)
    result['rolling']['launch_period'] = _period_from_index(first_index)
    result['rolling']['note'] = ('窗口结束于上一个已结算期；最新一期(pending_period)在途待结算，不计入分母/漏期')
    for label, size in ROLLING_WINDOWS:
        start_index = end_index - size + 1
        if first_index is None:
            low, online = start_index, 0
        else:
            low = max(start_index, first_index)
            online = end_index - low + 1 if low <= end_index else 0
        window = [(index, hit) for index, hit in entries.values()
                  if low <= index <= end_index]
        verified = len(window)
        hits = sum(1 for _, hit in window if hit)
        coverage = round(verified / online, 6) if online > 0 else None
        plan_rate = round(hits / online, 6) if online > 0 else None
        present = {index for index, _ in window}
        missed = [index for index in range(low, end_index + 1) if index not in present] if online > 0 else []
        recent_start = max(low, end_index - ROLLING_RECENT_PERIODS + 1)
        recent_online = max(0, end_index - recent_start + 1) if online > 0 else 0
        recent_present = {index for index, _ in window if index >= recent_start}
        recent_missed = ([index for index in range(recent_start, end_index + 1)
                          if index not in recent_present] if recent_online > 0 else [])
        recent_hits = sum(1 for index, hit in window if index >= recent_start and hit)
        result['coverage_' + label] = coverage
        result['plan_hit_rate_' + label] = plan_rate
        result['rolling']['windows'][label] = {
            'window': size, 'start_period': _period_from_index(low),
            'launch_period': _period_from_index(first_index),
            'online': online, 'verified': verified, 'missed': len(missed), 'hits': hits,
            'coverage': coverage, 'plan_hit_rate': plan_rate,
            'last_missed_period': _period_from_index(missed[-1]) if missed else None,
            'recent': {'periods': ROLLING_RECENT_PERIODS, 'online': recent_online,
                       'verified': recent_online - len(recent_missed),
                       'missed': len(recent_missed), 'hits': recent_hits,
                       'coverage': (round((recent_online - len(recent_missed)) / recent_online, 6)
                                    if recent_online > 0 else None),
                       'group_rate': (round(recent_hits / recent_online, 6)
                                      if recent_online > 0 else None),
                       'last_missed_period': (_period_from_index(recent_missed[-1])
                                              if recent_missed else None)},
        }
    return result


def _rolling_window_summary():
    latest_index = _window_end_index()
    end_index = None if latest_index is None else latest_index - 1
    return {'latest_draw_period': _period_from_index(latest_index),
            'end_period': _period_from_index(end_index),
            'pending_period': _period_from_index(latest_index),
            'end_period_source': ('data/state.json#latestRecord；回退 data/stats_draw_history.json#records[-1]'),
            'note': ('最新已开奖期（pending_period）在途待结算，不计入滚动窗口分母/漏期；'
                     '滚动窗口结束于上一个已结算期 end_period。'),
            'windows': [{'label': label, 'size': size,
                         'start_period': (_period_from_index(end_index - size + 1)
                                          if end_index is not None else None)}
                        for label, size in ROLLING_WINDOWS]}


SERIES = {}
DRIFT_WINDOWS = (100, 200, 300, 500)
DRIFT_MIN_SAMPLE = 100
DRIFT_CUSUM_N = 500
JOINT_SHADOW_PATH = DATA / 'joint_form_shadow.json'
_JOINT_EXPECTED_CACHE = {}


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
    handle = tempfile.NamedTemporaryFile('w', dir=str(path.parent), delete=False,
                                         encoding='utf-8', prefix='.' + path.name + '.')
    temporary = handle.name
    try:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(temporary, path)
    return path


def _p_two_expected(hits, p0s):
    """按逐期随机期望混算的二项正态近似检验（分界前后基线不同的记录可合在一起算）。"""
    if not p0s:
        return None
    total = sum(p0s)
    variance = sum(p0 * (1 - p0) for p0 in p0s)
    if variance <= 0:
        return None
    z = (hits - total) / math.sqrt(variance)
    return round(math.erfc(abs(z) / math.sqrt(2)), 6)


def _joint_expected(promoted_moment):
    """killer/framework 逐目标期精确随机期望。

    分界前封存的是 AI 形态 -> expected_ai；分界后封存的是联合优化形态 -> expected_opt。
    两列都来自 data/joint_form_shadow.json（scripts/run_joint_form_shadow.py 每期用 10^4
    开奖组合精确枚举写入），因此「累计行 expected」可按记录逐期加权，而不是用单一旧基线。
    """
    cache_key = promoted_moment.isoformat() if promoted_moment is not None else '(none)'
    cached = _JOINT_EXPECTED_CACHE.get(cache_key)
    if cached is not None:
        return cached
    document = _read(JOINT_SHADOW_PATH) or {}
    models = document.get('models') or {}
    mapping, summary = {}, {}
    for name in ('killer', 'framework'):
        model = models.get(name) or {}
        records = model.get('records') if isinstance(model, dict) else None
        if not isinstance(records, dict):
            records = {}
        per_target = {}
        for key, record in records.items():
            if not isinstance(record, dict):
                continue
            # shadow records 以 ref_period 为键，但评估/期望属于自身的 target_period；
            # 账本按 target_period 关联，必须用 record['target_period'] 映射，不能把 ref 键当 target。
            target = str(record.get('target_period') or key)
            value = None
            if promoted_moment is not None:
                slot = _period_slot(target)
                value = record.get('expected_opt') if (slot is not None and slot > promoted_moment) else record.get('expected_ai')
            else:
                value = record.get('expected_ai')
            if not isinstance(value, (int, float)) or not (0.0 < float(value) < 1.0):
                fallback = record.get('expected_opt')
                value = fallback if (isinstance(fallback, (int, float))
                                     and 0.0 < float(fallback) < 1.0) else None
            if value is not None:
                per_target[target] = float(value)
        mapping[name] = per_target
        shadow = model.get('summary') or {}
        summary[name] = {'before': shadow.get('expected_ai_avg'),
                         'after': shadow.get('expected_opt_avg'),
                         'n': shadow.get('n')}
    _JOINT_EXPECTED_CACHE[cache_key] = (mapping, summary)
    return mapping, summary


def _joint_expected_map(name, promoted_moment):
    if promoted_moment is None:
        return None
    return _joint_expected(promoted_moment)[0].get(name)


def _expected_split(records, p0s, promoted_moment):
    before, after = [], []
    for (target, _, _), p0 in zip(records, p0s):
        if p0 is None:
            continue
        slot = _period_slot(target)
        (after if (slot is not None and slot > promoted_moment) else before).append(p0)
    def _mean(values):
        return round(sum(values) / len(values), 6) if values else None
    return {'before': _mean(before), 'n_before': len(before),
            'after': _mean(after), 'n_after': len(after)}


def _window_row(series, size):
    rows = series[-size:]
    n = len(rows)
    hits = sum(1 for _, joint, _, _ in rows if joint)
    p0s = [p0 for _, _, _, p0 in rows if p0 is not None]
    expected = round(sum(p0s) / len(p0s), 6) if p0s else None
    z = p = None
    if p0s:
        variance = sum(p0 * (1 - p0) for p0 in p0s)
        if variance > 0:
            z = round((hits - sum(p0s)) / math.sqrt(variance), 3)
            p = round(math.erfc(abs(z) / math.sqrt(2)), 6)
    return {'window': size, 'n': n, 'full': n >= size, 'hits': hits,
            'rate': round(hits / n, 6) if n else None,
            'expected': expected, 'z': z, 'p_two': p, 'ci95': _wilson(hits, n)}


def _cusum_stat(series, limit=DRIFT_CUSUM_N):
    rows = series[-limit:]
    c = 0.0
    peak = 0.0
    peak_side = 'none'
    variance = 0.0
    counted = 0
    for _, joint, _, p0 in rows:
        if p0 is None:
            continue
        counted += 1
        variance += p0 * (1 - p0)
        c += (1.0 if joint else 0.0) - p0
        if abs(c) > abs(peak):
            peak = c
            peak_side = 'down' if c < 0 else 'up'
    if variance <= 0:
        return {'n': counted, 'z': None, 'z_peak': None, 'side': peak_side}
    return {'n': counted, 'z': round(c / math.sqrt(variance), 3),
            'z_peak': round(peak / math.sqrt(variance), 3), 'side': peak_side}


def _drift_verdict(windows, cusum):
    """滚动窗口 + CUSUM 只查「命中率向下漂移」；多窗口多重比较下 |z|~2 属正常。"""
    window200 = windows.get(200) or {}
    window300 = windows.get(300) or {}
    z200 = window200.get('z') if window200.get('full') else None
    z300 = window300.get('z') if window300.get('full') else None
    peak = cusum.get('z_peak')
    if z200 is None and z300 is None and peak is None:
        return 'insufficient', '样本不足(滚动窗口无有效样本)'
    if ((z200 is not None and z200 <= -3.0) or (z300 is not None and z300 <= -3.0)
            or (peak is not None and peak <= -4.0)):
        return 'fail', '显著下漂(单窗 z<=-3 或 CUSUM 峰<=-4sigma)'
    if ((z200 is not None and z200 <= -2.0 and z300 is not None and z300 <= -2.0)
            or (peak is not None and peak <= -3.0)):
        return 'warn', '向下漂移预警(近200/300期同向 z<=-2 或 CUSUM 峰<=-3sigma)'
    if z300 is not None and z300 >= 3.0:
        return 'high', '显著高于随机期望(仅参考)'
    return 'ok', '与自身随机期望无法区分'


def _drift_row(row):
    key = row.get('key')
    series = SERIES.get(key) or []
    windows = {size: _window_row(series, size) for size in DRIFT_WINDOWS}
    cusum = _cusum_stat(series)
    if len(series) < DRIFT_MIN_SAMPLE:
        status, verdict = 'insufficient', '样本不足(n<%d)' % DRIFT_MIN_SAMPLE
    else:
        status, verdict = _drift_verdict(windows, cusum)
        if status == 'ok' and len(series) < 200:
            verdict += '(n<200,仅参考)'
    return {'key': key, 'label': row.get('label'), 'rule': row.get('rule'),
            'n': len(series), 'windows': windows, 'cusum': cusum,
            'status': status, 'verdict': verdict}


def _drift_report(rows):
    drift_rows = []
    for row in rows:
        try:
            drift_rows.append(_drift_row(row))
        except Exception as error:  # never break the cumulative report
            drift_rows.append({'key': row.get('key'), 'label': row.get('label'),
                               'status': 'unknown',
                               'verdict': '计算失败: %s: %s' % (type(error).__name__, error)})
    return {'version': 1,
            'generated_at': datetime.now().isoformat(timespec='seconds'),
            'windows': list(DRIFT_WINDOWS), 'min_sample': DRIFT_MIN_SAMPLE,
            'cusum_limit': DRIFT_CUSUM_N,
            'note': ('滚动窗口/CUSUM 只用于发现命中率向下漂移，不作模型优劣结论；'
                     'killer/framework 的随机期望按逐期精确枚举加权（分界前 AI 形态 / 分界后联合优化形态）。'
                     '多窗口多重比较下 |z|~2 属正常，预警阈值取 |z|>=2 且两窗同向、显著取 |z|>=3 或 CUSUM 峰>=4sigma。'),
            'rows': drift_rows}



def _verdict(n, p):
    if p is None:
        return '基线待校准'
    if n < MIN_SAMPLE:
        return '样本不足(n<%d)' % MIN_SAMPLE
    if p < 0.05:
        return '偏离随机基线'
    return '与随机无法区分'


def _period_slot(period):
    try:
        text = str(period)
        day = date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        index = int(text[8:11])
        return datetime(day.year, day.month, day.day) + timedelta(minutes=5 * index)
    except (TypeError, ValueError):
        return None


def _joint_form_boundary():
    """joint_form 自动转正分界时刻；未转正或缺 promoted_at 时返回 (None, None)。"""
    promotion = _read(DATA / 'joint_form_promotion.json') or {}
    if promotion.get('auto_promoted') is not True:
        return None, None
    promote_state = _read(DATA / 'joint_form_auto_promote.json') or {}
    promoted_at = promotion.get('promoted_at') or promote_state.get('promoted_at')
    if not isinstance(promoted_at, str) or not promoted_at:
        return None, None
    try:
        moment = datetime.fromisoformat(promoted_at)
    except (TypeError, ValueError):
        return None, None
    if moment.tzinfo is not None:
        moment = moment.replace(tzinfo=None)
    return promoted_at, moment


def _post_optimized_count(records, promoted_moment):
    """target 槽位晚于转正时刻的已核验期数（分界后形态已改用联合优化形态）。"""
    count = 0
    for target, _, _ in records:
        slot = _period_slot(target)
        if slot is not None and slot > promoted_moment:
            count += 1
    return count


def _with_post_optimized(row, records, promoted_at, promoted_moment):
    count = _post_optimized_count(records, promoted_moment)
    row['post_optimized_n'] = count
    suffix = ('其中 %d 期 target 槽位晚于 joint_form 转正时刻 %s（该时刻起反向形态改用联合优化形态，'
              '主判断不变），跨分界累计趋势需谨慎解读' % (count, promoted_at))
    row['note'] = (row['note'] + '；' + suffix) if row.get('note') else suffix
    return row


def _build(key, rule, records, source, note='', expected_map=None, promoted_moment=None,
           rolling=None, launch=None):
    """records: [(target_period, joint_hit_bool, model_or_None)]。

    rolling: 该框架「全部已核验且计入」的 [(target_period, hit)]（可跨规则版本，只用于滚动覆盖率），
    缺省用 records；launch: 判定「上线期」的期号集合（verified + seal 的 target_period），缺省用 records。
    """
    n = len(records)
    hits = sum(1 for _, joint, _ in records if joint)
    base_expected = _expected(key, rule)
    p0s = []
    for target, _, _ in records:
        value = expected_map.get(str(target)) if expected_map else None
        p0s.append(value if value is not None else base_expected)
    resolved = [value for value in p0s if value is not None]
    expected = round(sum(resolved) / len(resolved), 6) if resolved else None
    rate = round(hits / n, 6) if n else None
    delta = round(rate - expected, 6) if (rate is not None and expected is not None) else None
    p = _p_two_expected(hits, resolved) if resolved else _p_two(hits, n, base_expected)
    SERIES[key] = [(str(target), bool(joint), model, p0)
                   for (target, joint, model), p0 in zip(records, p0s)]
    targets = [target for target, _, _ in records]
    planned = _planned(targets)
    coverage = round(n / planned, 6) if (planned and planned > 0) else None
    planned_hit_rate = round(hits / planned, 6) if (planned and planned > 0) else None
    models = {}
    for _, _, model in records:
        if not model:
            continue
        bucket = models.setdefault(model, [0, 0])
        bucket[0] += 1
        bucket[1] += 1 if _ else 0
    model_split = [{'model': model, 'n': value[0], 'hits': value[1],
                    'rate': round(value[1] / value[0], 6) if value[0] else None}
                   for model, value in sorted(models.items(), key=lambda item: -item[1][0])]
    valid = sorted([str(t) for t in targets if t is not None])
    row = {
        'key': key, 'label': LABELS[key], 'rule': rule,
        'items': _items(key, rule), 'window': WINDOWS[key],
        'n': n, 'hits': hits, 'rate': rate, 'ci95': _wilson(hits, n),
        'expected': expected, 'delta': delta, 'p': p,
        'expected_split': _expected_split(records, p0s, promoted_moment) if (expected_map and promoted_moment is not None) else None,
        'expected_basis': ('per-record-exact' if expected_map else 'rule-constant'),
        'verdict': _verdict(n, p), 'planned': planned, 'coverage': coverage,
        'planned_hit_rate': planned_hit_rate,
        'first_period': valid[0] if valid else None,
        'last_period': valid[-1] if valid else None,
        'need_n_2pp': _need_n(expected) if expected is not None else None,
        'model_split': model_split, 'note': note, 'source': source,
    }
    rolling_records = ([(target, joint) for target, joint, _ in records]
                       if rolling is None else list(rolling))
    launch_periods = ([target for target, _, _ in records]
                      if launch is None else list(launch))
    row.update(_rolling_fields(_rolling_points(rolling_records), launch_periods,
                               _settled_end_index()))
    return row


def _model_of(publication):
    metadata = (publication or {}).get('model_metadata') or {}
    if isinstance(metadata, dict):
        return metadata.get('requested_model')
    return None


def _seal_model_map(path):
    """seal ref_period -> requested_model（framework/killer 的 hit_stats 不带 model_metadata 时兜底）。"""
    document = _read(path) or {}
    seals = document.get('prediction_seals') if isinstance(document, dict) else None
    if not isinstance(seals, dict):
        return {}
    mapping = {}
    for reference, seal in seals.items():
        if not isinstance(seal, dict):
            continue
        model = _model_of(seal)
        if model:
            mapping[str(reference)] = model
    return mapping


def _rule_from_strategy(key):
    stats = _read(DATA / 'strategy_comparison_stats.json') or {}
    model = (stats.get('models') or {}).get(key) or {}
    return model.get('strategy_id')


def cum_killer():
    doc = _read(DATA / 'r1_stats.json') or {}
    evaluations = doc.get('verified_evaluations') or []
    model_by_ref = _seal_model_map(DATA / 'r1_stats.json')
    records = []
    for row in evaluations:
        outcome = row.get('outcome') or {}
        if not isinstance(outcome, dict) or len(outcome) < 9:
            continue
        records.append((row.get('target_period'), all(bool(v) for v in outcome.values()),
                        model_by_ref.get(str(row.get('ref_period')))))
    rule = _rule_from_strategy('killer') or 'conditional-transition-v2'
    promoted_at, promoted_moment = _joint_form_boundary()
    expected_map = _joint_expected_map('killer', promoted_moment)
    launch = [target for target, _, _ in records]
    seals = doc.get('prediction_seals') if isinstance(doc, dict) else None
    for seal in (seals or {}).values():
        if isinstance(seal, dict) and seal.get('target_period'):
            launch.append(seal.get('target_period'))
    row = _build('killer', rule, records, 'data/r1_stats.json',
                 '账本未标注规则版本；按现行规则 conditional-transition-v2 复核全部记录',
                 expected_map=expected_map, promoted_moment=promoted_moment, launch=launch)
    if promoted_moment is not None:
        row = _with_post_optimized(row, records, promoted_at, promoted_moment)
    return row


def cum_framework():
    doc = _read(DATA / 'r1_framework_hit_stats.json') or {}
    verified = list((doc.get('verified') or {}).values())
    model_by_ref = _seal_model_map(DATA / 'r1_framework_seals.json')
    records = []
    for row in verified:
        outcome = row.get('outcomes') or {}
        if not isinstance(outcome, dict) or len(outcome) < 8:
            continue
        model = model_by_ref.get(str(row.get('ref_period'))) or _model_of(row.get('publication'))
        records.append((row.get('target_period'), all(bool(v) for v in outcome.values()), model))
    rule = _rule_from_strategy('framework') or 'marginal-frequency-v2'
    promoted_at, promoted_moment = _joint_form_boundary()
    expected_map = _joint_expected_map('framework', promoted_moment)
    launch = [target for target, _, _ in records]
    seal_doc = _read(DATA / 'r1_framework_seals.json') or {}
    seals = seal_doc.get('prediction_seals') if isinstance(seal_doc, dict) else None
    for seal in (seals or {}).values():
        if isinstance(seal, dict) and seal.get('target_period'):
            launch.append(seal.get('target_period'))
    row = _build('framework', rule, records, 'data/r1_framework_hit_stats.json',
                 '账本未标注规则版本；按现行规则 marginal-frequency-v2 复核全部记录',
                 expected_map=expected_map, promoted_moment=promoted_moment, launch=launch)
    if promoted_moment is not None:
        row = _with_post_optimized(row, records, promoted_at, promoted_moment)
    return row


def cum_selected_six():
    doc = _read(DATA / 'selected_six_hit_stats.json') or {}
    verified = list((doc.get('verified') or {}).values())
    stats = _read(DATA / 'selected_six_stats.json') or {}
    rule = stats.get('rule_revision') or 'selected-six-independent-v4-minimax-forms'
    records, history, rolling = [], {}, []
    for row in verified:
        publication = row.get('publication') or {}
        revision = publication.get('revision') or '(未标注)'
        outcome = row.get('outcomes') or {}
        if not isinstance(outcome, dict) or not outcome:
            continue
        joint = all(bool(v) for v in outcome.values())
        model = _model_of(publication)
        rolling.append((row.get('target_period'), joint))
        if revision == rule:
            records.append((row.get('target_period'), joint, model))
        else:
            bucket = history.setdefault(revision, [0, 0])
            bucket[0] += 1
            bucket[1] += 1 if joint else 0
    row = _build('selected_six', rule, records, 'data/selected_six_hit_stats.json',
                 rolling=rolling, launch=[target for target, _ in rolling])
    row['history'] = [{'revision': revision, 'n': value[0], 'hits': value[1],
                       'rate': round(value[1] / value[0], 6) if value[0] else None}
                      for revision, value in sorted(history.items())]
    return row


def cum_he3():
    doc = _read(DATA / 'he3_refine_hit_stats.json') or {}
    verified = list((doc.get('verified') or {}).values())
    status = _read(DATA / 'he3_refine_status.json') or {}
    rule = status.get('revision') or 'he3-refine-v2-balanced'
    records, history, rolling = [], {}, []
    for row in verified:
        publication = row.get('publication') or {}
        revision = publication.get('revision') or '(未标注)'
        hit = row.get('hit')
        if not isinstance(hit, bool):
            continue
        model = _model_of(publication)
        rolling.append((row.get('target_period'), hit))
        if revision == rule:
            records.append((row.get('target_period'), hit, model))
        else:
            bucket = history.setdefault(revision, [0, 0])
            bucket[0] += 1
            bucket[1] += 1 if hit else 0
    row = _build('he3_refine', rule, records, 'data/he3_refine_hit_stats.json',
                 rolling=rolling, launch=[target for target, _ in rolling])
    row['history'] = [{'revision': revision, 'n': value[0], 'hits': value[1],
                       'rate': round(value[1] / value[0], 6) if value[0] else None}
                      for revision, value in sorted(history.items())]
    return row


def cum_kill3():
    settlements = _read(DATA / 'kill3_tail' / 'settlements.json') or {}
    seals = _read(DATA / 'kill3_tail' / 'seals.json') or {}
    seal_records = seals.get('records') or []
    rule = 'kill3-tail-v4'
    if seal_records and isinstance(seal_records[-1], dict):
        rule = seal_records[-1].get('rule_version') or rule
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in seal_records if isinstance(record, dict)}
    records, rolling, launch = [], [], []
    for record in seal_records:
        if isinstance(record, dict) and record.get('target_period'):
            launch.append(record.get('target_period'))
    for row in settlements.get('records') or []:
        if not isinstance(row, dict):
            continue
        if row.get('target_period'):
            launch.append(row.get('target_period'))
        if not row.get('verified'):
            continue
        if 'counted' not in row:
            continue
        hit = bool(row.get('group_hit'))
        rolling.append((row.get('target_period'), hit))
        if (row.get('rule_version') != rule
                and seal_rule.get(str(row.get('ref_period'))) != rule):
            continue
        records.append((row.get('target_period'), hit, None))
    return _build('kill3_tail', rule, records, 'data/kill3_tail/settlements.json',
                  rolling=rolling, launch=launch)


def cum_kill1():
    settlements = _read(DATA / 'kill1_tail' / 'settlements.json') or {}
    seals = _read(DATA / 'kill1_tail' / 'seals.json') or {}
    seal_records = seals.get('records') or []
    rule = 'kill1-tail-v1-one-position-one-form'
    if seal_records and isinstance(seal_records[-1], dict):
        rule = seal_records[-1].get('rule_version') or rule
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in seal_records if isinstance(record, dict)}
    records, rolling, launch = [], [], []
    for record in seal_records:
        if isinstance(record, dict) and record.get('target_period'):
            launch.append(record.get('target_period'))
    for row in settlements.get('records') or []:
        if not isinstance(row, dict):
            continue
        if row.get('target_period'):
            launch.append(row.get('target_period'))
        if not row.get('verified'):
            continue
        if 'counted' not in row:
            continue
        hit = bool(row.get('group_hit'))
        rolling.append((row.get('target_period'), hit))
        if (row.get('rule_version') != rule
                and seal_rule.get(str(row.get('ref_period'))) != rule):
            continue
        records.append((row.get('target_period'), hit, None))
    return _build('kill1_tail', rule, records, 'data/kill1_tail/settlements.json',
                  rolling=rolling, launch=launch)


def cum_kill2p():
    settlements = _read(DATA / 'kill2p_tail' / 'settlements.json') or {}
    seals = _read(DATA / 'kill2p_tail' / 'seals.json') or {}
    seal_records = seals.get('records') or []
    rule = 'kill2p-tail-v1-two-positions-one-tail-two-forms'
    if seal_records and isinstance(seal_records[-1], dict):
        rule = seal_records[-1].get('rule_version') or rule
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in seal_records if isinstance(record, dict)}
    records, rolling, launch = [], [], []
    for record in seal_records:
        if isinstance(record, dict) and record.get('target_period'):
            launch.append(record.get('target_period'))
    for row in settlements.get('records') or []:
        if not isinstance(row, dict):
            continue
        if row.get('target_period'):
            launch.append(row.get('target_period'))
        if not row.get('verified'):
            continue
        if 'counted' not in row:
            continue
        hit = bool(row.get('group_hit'))
        rolling.append((row.get('target_period'), hit))
        if (row.get('rule_version') != rule
                and seal_rule.get(str(row.get('ref_period'))) != rule):
            continue
        records.append((row.get('target_period'), hit, None))
    return _build('kill2p_tail', rule, records, 'data/kill2p_tail/settlements.json',
                  rolling=rolling, launch=launch)


def cum_kill2():
    settlements = _read(DATA / 'kill2_tail' / 'settlements.json') or {}
    seals = _read(DATA / 'kill2_tail' / 'seals.json') or {}
    seal_records = seals.get('records') or []
    rule = 'kill2-tail-v2-two-positions-he2he3-rot3'
    if seal_records and isinstance(seal_records[-1], dict):
        rule = seal_records[-1].get('rule_version') or rule
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in seal_records if isinstance(record, dict)}
    records, rolling, launch = [], [], []
    for record in seal_records:
        if isinstance(record, dict) and record.get('target_period'):
            launch.append(record.get('target_period'))
    for row in settlements.get('records') or []:
        if not isinstance(row, dict):
            continue
        if row.get('target_period'):
            launch.append(row.get('target_period'))
        if not row.get('verified'):
            continue
        if 'counted' not in row:
            continue
        hit = bool(row.get('group_hit'))
        rolling.append((row.get('target_period'), hit))
        if (row.get('rule_version') != rule
                and seal_rule.get(str(row.get('ref_period'))) != rule):
            continue
        records.append((row.get('target_period'), hit, None))
    return _build('kill2_tail', rule, records, 'data/kill2_tail/settlements.json',
                  rolling=rolling, launch=launch)


def cum_kill4():
    settlements = _read(DATA / 'kill4_tail' / 'settlements.json') or {}
    seals = _read(DATA / 'kill4_tail' / 'seals.json') or {}
    seal_records = seals.get('records') or []
    rule = 'kill4-tail-v1-three-positions-he2he3'
    if seal_records and isinstance(seal_records[-1], dict):
        rule = seal_records[-1].get('rule_version') or rule
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in seal_records if isinstance(record, dict)}
    records, rolling, launch = [], [], []
    for record in seal_records:
        if isinstance(record, dict) and record.get('target_period'):
            launch.append(record.get('target_period'))
    for row in settlements.get('records') or []:
        if not isinstance(row, dict):
            continue
        if row.get('target_period'):
            launch.append(row.get('target_period'))
        if not row.get('verified'):
            continue
        if 'counted' not in row:
            continue
        hit = bool(row.get('group_hit'))
        rolling.append((row.get('target_period'), hit))
        if (row.get('rule_version') != rule
                and seal_rule.get(str(row.get('ref_period'))) != rule):
            continue
        records.append((row.get('target_period'), hit, None))
    return _build('kill4_tail', rule, records, 'data/kill4_tail/settlements.json',
                  rolling=rolling, launch=launch)


def cum_kill6():
    settlements = _read(DATA / 'kill6_tail' / 'settlements.json') or {}
    seals = _read(DATA / 'kill6_tail' / 'seals.json') or {}
    seal_records = seals.get('records') or []
    rule = 'kill6-tail-v1-three-positions-he2he3'
    if seal_records and isinstance(seal_records[-1], dict):
        rule = seal_records[-1].get('rule_version') or rule
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in seal_records if isinstance(record, dict)}
    records, rolling, launch = [], [], []
    for record in seal_records:
        if isinstance(record, dict) and record.get('target_period'):
            launch.append(record.get('target_period'))
    for row in settlements.get('records') or []:
        if not isinstance(row, dict):
            continue
        if row.get('target_period'):
            launch.append(row.get('target_period'))
        if not row.get('verified'):
            continue
        if 'counted' not in row:
            continue
        hit = bool(row.get('group_hit'))
        rolling.append((row.get('target_period'), hit))
        if (row.get('rule_version') != rule
                and seal_rule.get(str(row.get('ref_period'))) != rule):
            continue
        records.append((row.get('target_period'), hit, None))
    return _build('kill6_tail', rule, records, 'data/kill6_tail/settlements.json',
                  rolling=rolling, launch=launch)


def cum_kill5():
    settlements = _read(DATA / 'kill5_tail' / 'settlements.json') or {}
    seals = _read(DATA / 'kill5_tail' / 'seals.json') or {}
    seal_records = seals.get('records') or []
    rule = 'kill5-tail-v1-three-positions-he2he3'
    if seal_records and isinstance(seal_records[-1], dict):
        rule = seal_records[-1].get('rule_version') or rule
    seal_rule = {str(record.get('ref_period')): record.get('rule_version')
                 for record in seal_records if isinstance(record, dict)}
    records, rolling, launch = [], [], []
    for record in seal_records:
        if isinstance(record, dict) and record.get('target_period'):
            launch.append(record.get('target_period'))
    for row in settlements.get('records') or []:
        if not isinstance(row, dict):
            continue
        if row.get('target_period'):
            launch.append(row.get('target_period'))
        if not row.get('verified'):
            continue
        if 'counted' not in row:
            continue
        hit = bool(row.get('group_hit'))
        rolling.append((row.get('target_period'), hit))
        if (row.get('rule_version') != rule
                and seal_rule.get(str(row.get('ref_period'))) != rule):
            continue
        records.append((row.get('target_period'), hit, None))
    return _build('kill5_tail', rule, records, 'data/kill5_tail/settlements.json',
                  rolling=rolling, launch=launch)


def build():
    rows = []
    errors = []
    _reset_rolling_cache()
    promoted_at, promoted_moment = _joint_form_boundary()
    for name, func in (('killer', cum_killer), ('framework', cum_framework),
                       ('selected_six', cum_selected_six), ('he3_refine', cum_he3),
                       ('kill3_tail', cum_kill3),
                       ('kill1_tail', cum_kill1),
                       ('kill2p_tail', cum_kill2p),
                       ('kill2_tail', cum_kill2),
                       ('kill4_tail', cum_kill4),
                       ('kill5_tail', cum_kill5),
                       ('kill6_tail', cum_kill6)):
        try:
            row = func()
            if row and row.get('n'):
                rows.append(row)
        except Exception as error:  # keep other frameworks alive
            errors.append('%s: %s: %s' % (name, type(error).__name__, error))
    for row in rows:
        if row.get('key') == 'selected_six':
            row['note'] = ((row['note'] + '；' + DATA_LAG_NOTE)
                           if row.get('note') else DATA_LAG_NOTE)
    drift = _drift_report(rows)
    joint_baseline = None
    if promoted_moment is not None:
        _, joint_summary = _joint_expected(promoted_moment)
        joint_baseline = {
            'promoted_at': promoted_at,
            'source': 'data/joint_form_shadow.json#summary',
            'basis': '分界前 AI 形态 expected_ai / 分界后联合优化形态 expected_opt，均为逐期 10^4 精确枚举均值',
            'models': joint_summary,
        }
    as_of = None
    for row in rows:
        period = row.get('last_period')
        if period and (as_of is None or str(period) > str(as_of)):
            as_of = period
    now = datetime.now()
    return {
        'version': 1,
        'generated_at': now.isoformat(timespec='seconds'),
        'as_of_period': as_of,
        'basis': BASIS,
        'min_sample': MIN_SAMPLE,
        'definition': '整组命中 = 一期全部项目同时命中；随机期望按现行规则精确枚举；覆盖率=已核验/计划期数',
        'data_lag_note': DATA_LAG_NOTE,
        'rolling_definition': ROLLING_DEFINITION,
        'coverage_note': COVERAGE_NOTE,
        'rolling_window': _rolling_window_summary(),
        'joint_form_boundary': ({
            'promoted_at': promoted_at,
            'affected': ['killer', 'framework'],
            'note': JOINT_FORM_BOUNDARY_NOTE,
        } if promoted_moment is not None else None),
        'errors': errors,
        'drift': drift,
        'joint_form_baseline': joint_baseline,
        'rows': rows,
    }


def main():
    try:
        report = build()
        payload = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(',', ':')) + '\n'
        fd, temporary = tempfile.mkstemp(dir=str(OUT.parent), prefix='.framework_hitrate.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, OUT)
        finally:
            try:
                os.unlink(temporary)
            except OSError:
                pass
        joint_baseline = report.get('joint_form_baseline')
        if joint_baseline:
            _write_json(DATA / 'joint_form_baseline.json', joint_baseline)
        for drift_row in (report.get('drift') or {}).get('rows') or []:
            windows = drift_row.get('windows') or {}
            window200 = windows.get(200) or {}
            window300 = windows.get(300) or {}
            cusum = drift_row.get('cusum') or {}
            print('drift {key} status={status} n={n} z200={z200} z300={z300} cusum_peak={peak}'.format(
                key=drift_row.get('key'), status=drift_row.get('status'),
                n=drift_row.get('n'), z200=window200.get('z'),
                z300=window300.get('z'), peak=cusum.get('z_peak')), flush=True)
        for row in report['rows']:
            print('{key} n={n} hits={hits} rate={rate} base={base} p={p} coverage={cov} verdict={verdict}'.format(
                key=row['key'], n=row['n'], hits=row['hits'],
                rate=('%.2f%%' % (row['rate'] * 100)) if row['rate'] is not None else '-',
                base=('%.2f%%' % (row['expected'] * 100)) if row['expected'] is not None else '-',
                p=row['p'], cov=('%.2f%%' % (row['coverage'] * 100)) if row['coverage'] is not None else '-',
                verdict=row['verdict']), flush=True)
        window = report.get('rolling_window') or {}
        starts = {item.get('label'): item.get('start_period')
                  for item in window.get('windows') or [] if isinstance(item, dict)}
        print('rolling window end=%s pending=%s start_24h=%s start_7d=%s'
              % (window.get('end_period'), window.get('pending_period'),
                 starts.get('24h'), starts.get('7d')), flush=True)
        for row in report['rows']:
            rolling = row.get('rolling') or {}
            windows = rolling.get('windows') or {}
            parts = []
            for label, _ in ROLLING_WINDOWS:
                item = windows.get(label) or {}
                coverage = item.get('coverage')
                plan_rate = item.get('plan_hit_rate')
                parts.append('%s coverage=%s online=%s plan_hit_rate=%s last_missed=%s'
                             % (label,
                                ('%.2f%%' % (coverage * 100)) if isinstance(coverage, (int, float)) else '-',
                                item.get('online'),
                                ('%.2f%%' % (plan_rate * 100)) if isinstance(plan_rate, (int, float)) else '-',
                                item.get('last_missed_period')))
            print('rolling {key} | {parts}'.format(key=row['key'], parts=' | '.join(parts)), flush=True)
        if report['errors']:
            print('errors: ' + ' | '.join(report['errors']), flush=True)
        print('framework_hitrate_cumulative written: %s rows=%d as_of=%s' % (OUT, len(report['rows']), report['as_of_period']), flush=True)
        # 2026-11-01：主面板隐藏「整组命中率 vs 随机期望」大表后，
        # 由本脚本每 15 分钟把独立统计页 /hitrate 重写一次（页面异常不影响 JSON 产物）。
        try:
            import sys as _sys
            if str(ROOT) not in _sys.path:
                _sys.path.insert(0, str(ROOT))
            import hitrate_page_view
            page_path = hitrate_page_view.write_page()
            print('hitrate page written: %s' % page_path, flush=True)
        except Exception as page_error:
            print('hitrate page write deferred: %s: %s' % (type(page_error).__name__, page_error), flush=True)
        return 0
    except Exception as error:
        print('framework_hitrate_cumulative failed: %s: %s' % (type(error).__name__, error), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
