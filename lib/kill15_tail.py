"""分位杀号决策引擎（kill15 独立框架，v1，AI 每期选码）。

口径（写死，与 design/kill15-契约草案.md 一致）：
- 四个号位（千/百/十/个）每期各杀 1 码（0-9），整组固定 4 项全部计入整组；
- 后端预先计算并下发：每号位合法候选池、当前遗漏、近 30 期热度、近 15 期 Hit-A/Hit-B、
  近 3 期深冷开出枚数、顺局预警、上期分配；模型只做 A/B 号位分配与池内选码；
- 候选池：该号位当前遗漏 >= 8 期的码；不足 5 个时按遗漏降序补足 5 个；
- A（遗漏系）= 池内遗漏最大者；B 三子策略 B1 热码杀（近 30 期最热）、
  B2 反弹杀（近 3 期深冷刚开出）、B3 形态对冲（单双/大小失衡反向侧遗漏最大者）；
- A 号位上限：冷反弹活跃（近 3 期深冷 >=3）->1；否则 2；Hit-A<70% 或 >88% 再压 1；
  Hit-A、Hit-B 双低 -> 1；下限 1；
- Hit-B < 70% 时 B 子策略必须与上期不同；同一套分配连续命中 14 期（顺局）时强制打乱分配；
- 防塌方 v2（长期运行核心规则）：A/B 号位组合禁止直接沿用上期（每次必须重新推导）；
  A 组下限 1 个号位（禁止 A=0）；B 占多个号位时子策略不得重复（分散）；
  runaway_warning 时必须打乱 A/B 分布并轮换全部 B 子策略；
  每期必须输出 self_check 五项自检且全为 true，缺项或 false 一律拒收重试；
- 防塌方 v3（惯性护栏）：完整方案（A/B 分配 + B 子策略映射）不得与最近 2 期重复，
  用于打破“两套分配交替”式拟合惯性；与 v2 规则叠加执行；
- 命中 = 该号位实际开奖号 != 该号位杀码（单项随机 90%）；整组随机基线 (9/10)^4 = 65.61%；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零。

数据落点 data/kill15_tail/{seals,settlements}.json；由 scripts/run_kill15_tail.py 每期运行。
v1 为影子跑：只封存与结算，不写面板、不投注。
"""
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import time

import requests

from stats_history import beijing_now, load_snapshot, period_slot
from ai_verified_stats import atomic_json, next_period
from ai_prediction_guard import PublicationRejected
from ai_http_observability import emit_http_phase
from ai_call_control import require_ai_enabled
from ai_provider_policy import strict_json_object
from app_secrets import get_secret

ROOT = Path('/root/penta5-runner')
DATA_DIR = ROOT / 'data' / 'kill15_tail'
SEALS_PATH = DATA_DIR / 'seals.json'
SETTLEMENTS_PATH = DATA_DIR / 'settlements.json'
STATE_PATH = ROOT / 'data' / 'state.json'
HISTORY_PATH = ROOT / 'data' / 'stats_draw_history.json'

WINDOW = 70
HOT_WINDOW = 30
HIT_WINDOW = 15
CLOSE_SECONDS = 70
MIN_REQUEST_SECONDS = 20.0
MAX_ATTEMPTS = 3
MAX_TOKENS = 8192
REASONING_EFFORT = 'high'
NONRETRYABLE_HTTP_STATUSES = frozenset((400, 401, 402, 422))
PROVIDER_URL = 'https://api.deepseek.com/chat/completions'
RETRY_INSTRUCTION = ('上一次输出不符合要求（{error}）。请只输出严格 JSON，'
                     '并严格遵守【A/B 配比规则】【长期运行·防塌方核心规则】'
                     '【每期强制自检清单（不可跳过）】与输出格式。')

POOL_MIN_OMISSION = 8
POOL_SIZE = 5
DEEP_COLD_OMISSION = 25
RUNAWAY_LIMIT = 14
SCHEME_REPEAT_WINDOW = 2
B_SUBS = ('B1', 'B2', 'B3')

POSITIONS = (
    ('q', '千', 0),
    ('b', '百', 1),
    ('s', '十', 2),
    ('g', '个', 3),
)
POSITION_KEYS = tuple(key for key, _label, _index in POSITIONS)
POSITION_LABELS = {key: label for key, label, _index in POSITIONS}
POSITION_INDEX = {key: index for key, _label, index in POSITIONS}

RULE_VERSION = 'kill15-tail-v3-inertia-guard'
PROMPT_VERSION = 'kill15-tail-prompt-v3-inertia-guard'
# 旧代际记录原样保留、不迁移；统计与回放按 rule_version 切分。
LEGACY_RULES = (
    'kill15-tail-v1-ab-position-kill',
    'kill15-tail-v2-anticollapse',
)
SELF_CHECK_KEYS = (
    'allocation_rederived',
    'runaway_shuffled',
    'dispersed',
    'cold_rebound_adjusted',
    'pool_confirmed',
)
EXPECTED_ITEMS = 4
EXPECTED_GROUP_RATE = 0.6561
EXPECTED_BASIS = 'enumerated-10^4-draws-x-10^4-configs'


# ---------------------------------------------------------------------------
# 基础读写
# ---------------------------------------------------------------------------
def _load_json(path, default, strict=False):
    path = Path(path)
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError:
        return default
    try:
        value = json.loads(raw)
    except ValueError:
        if strict:
            raise ValueError(f'invalid-json:{path.name}')
        return default
    if not isinstance(value, dict):
        if strict:
            raise ValueError(f'invalid-object:{path.name}')
        return default
    return value


def load_seals():
    document = _load_json(SEALS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill15 seals records must be a list')
    return [item for item in records if isinstance(item, dict)]


def load_settlements():
    document = _load_json(SETTLEMENTS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill15 settlements records must be a list')
    return [item for item in records if isinstance(item, dict)]


def _write_seals(records):
    atomic_json(SEALS_PATH, {'version': 1, 'records': records})


def _write_settlements(records):
    atomic_json(SETTLEMENTS_PATH, {'version': 1, 'records': records})


def find_seal(seals, reference):
    reference = str(reference)
    return next((item for item in seals if str(item.get('ref_period')) == reference), None)


def latest_period():
    try:
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        period = str((state.get('latestRecord') or {}).get('period') or '')
        if period:
            return period
    except (OSError, ValueError):
        pass
    try:
        history = json.loads(HISTORY_PATH.read_text(encoding='utf-8'))
        records = history.get('records') or []
        if records:
            return str(records[-1].get('period') or '')
    except (OSError, ValueError):
        pass
    return ''


# ---------------------------------------------------------------------------
# 后端预计算：候选池 / 遗漏 / 热度 / 深冷 / Hit-A-Hit-B / 顺局
# ---------------------------------------------------------------------------
def _normalize_rows(rows):
    normalized = []
    for row in rows or []:
        try:
            values = [int(value) for value in list(row)[:4]]
        except (TypeError, ValueError):
            continue
        if len(values) != 4 or any(value < 0 or value > 9 for value in values):
            continue
        normalized.append(values)
    return normalized


def omission_map(rows, index):
    """Current omission (periods since last appearance) for every digit at one position."""
    total = len(rows)
    result = {}
    for digit in range(10):
        gap = total
        for offset in range(1, total + 1):
            if rows[total - offset][index] == digit:
                gap = offset - 1
                break
        result[digit] = gap
    return result


def hot_map(rows, index, window=HOT_WINDOW):
    counts = {digit: 0 for digit in range(10)}
    for row in rows[-window:]:
        counts[row[index]] += 1
    return counts


def build_pool(omission, hot):
    """Legal kill candidates: omission >= 8; fill up to POOL_SIZE by omission then coldness."""
    pool = [digit for digit in range(10) if omission[digit] >= POOL_MIN_OMISSION]
    if len(pool) < POOL_SIZE:
        rest = [digit for digit in range(10) if digit not in pool]
        rest.sort(key=lambda digit: (-omission[digit], hot[digit], digit))
        pool.extend(rest[:POOL_SIZE - len(pool)])
    pool.sort(key=lambda digit: (-omission[digit], hot[digit], digit))
    return pool


def deep_cold_burst(rows, draws=3, threshold=DEEP_COLD_OMISSION):
    """How many deep-cold digits appeared in the last N draws (all positions)."""
    total = 0
    start = max(0, len(rows) - draws)
    for index in range(start, len(rows)):
        history = rows[:index]
        for position in range(4):
            digit = rows[index][position]
            gap = len(history)
            for offset in range(1, len(history) + 1):
                if history[len(history) - offset][position] == digit:
                    gap = offset - 1
                    break
            if gap >= threshold:
                total += 1
    return total


def _allocation_key(record):
    allocation = record.get('allocation') or {}
    a = tuple(sorted(str(key) for key in allocation.get('a') or []))
    b = tuple(sorted(str(key) for key in allocation.get('b') or []))
    subs = allocation.get('b_sub') or {}
    sub_key = tuple(sorted((str(key), str(value)) for key, value in subs.items()))
    return a, b, sub_key


def _scheme_key(allocation):
    """Full inertia key: A/B position split + B sub-strategy mapping."""
    allocation = allocation or {}
    a = tuple(sorted(str(key) for key in allocation.get('a') or []))
    b = tuple(sorted(str(key) for key in allocation.get('b') or []))
    subs = allocation.get('b_sub') or {}
    sub_key = tuple(sorted((str(key), str(value).upper()) for key, value in subs.items()))
    return a, b, sub_key


def performance_from_settlements(settlements, window=HIT_WINDOW):
    """Rolling Hit-A / Hit-B over the last N settled periods."""
    recent = [row for row in settlements if isinstance(row.get('positions'), dict)][-window:]
    a_bets = a_hits = b_bets = b_hits = 0
    for row in recent:
        allocation = row.get('allocation') or {}
        a_keys = set(str(key) for key in allocation.get('a') or [])
        for key, item in (row.get('positions') or {}).items():
            if not isinstance(item, dict):
                continue
            hit = item.get('hit') is True
            if key in a_keys:
                a_bets += 1
                a_hits += 1 if hit else 0
            else:
                b_bets += 1
                b_hits += 1 if hit else 0
    return {
        'window': window,
        'periods': len(recent),
        'hit_a_15': (a_hits / float(a_bets)) if a_bets else None,
        'hit_b_15': (b_hits / float(b_bets)) if b_bets else None,
        'a_n': a_bets,
        'b_n': b_bets,
    }


def runaway_state(settlements):
    """顺局预警：同一套 A/B 分配连续命中（整组全中）的期数。

    安全规则原文：runaway_warning = 连续 14 期同一套分配命中。
    任一条件被打破（分配变化 / 该期未整组命中）即中断连击，重新计数。
    """
    rows = [row for row in settlements
            if isinstance(row.get('allocation'), dict) and 'group_hit' in row]
    if not rows:
        return {'consecutive_same_allocation': 0, 'runaway_warning': False}
    last_key = _allocation_key(rows[-1])
    streak = 0
    for row in reversed(rows):
        if _allocation_key(row) != last_key:
            break
        if row.get('group_hit') is not True:
            break
        streak += 1
    return {'consecutive_same_allocation': streak,
            'runaway_warning': streak >= RUNAWAY_LIMIT}


def a_cap(regime_burst, hit_a, hit_b):
    """A-group position cap from regime and rolling performance."""
    cap = 1 if regime_burst >= 3 else 2
    low_a = isinstance(hit_a, (int, float)) and hit_a < 0.70
    high_a = isinstance(hit_a, (int, float)) and hit_a > 0.88
    low_b = isinstance(hit_b, (int, float)) and hit_b < 0.70
    if low_a or high_a:
        cap -= 1
    if low_a and low_b:
        cap = 1
    return max(1, min(cap, 3))


def build_context(rows, settlements=None):
    rows = _normalize_rows(rows)
    if len(rows) < WINDOW:
        raise ValueError('insufficient-kill15-history')
    settlements = settlements if settlements is not None else load_settlements()
    positions = {}
    for key, label, index in POSITIONS:
        omission = omission_map(rows, index)
        hot = hot_map(rows, index)
        positions[key] = {
            'key': key,
            'label': label,
            'pool': build_pool(omission, hot),
            'omission': {str(digit): omission[digit] for digit in range(10)},
            'hot30': {str(digit): hot[digit] for digit in range(10)},
        }
    burst = deep_cold_burst(rows)
    perf = performance_from_settlements(settlements)
    runaway = runaway_state(settlements)
    cap = a_cap(burst, perf['hit_a_15'], perf['hit_b_15'])
    last = settlements[-1] if settlements else None
    last_kills = {
        str(key): int(item.get('kill'))
        for key, item in ((last or {}).get('positions') or {}).items()
        if isinstance(item, dict) and item.get('kill') is not None
    }
    recent = [row.get('allocation') for row in settlements[-SCHEME_REPEAT_WINDOW:]
              if isinstance(row.get('allocation'), dict)]
    return {
        'positions': positions,
        'regime': {'cold_burst_3': burst,
                   'label': '冷反弹活跃' if burst >= 3 else '冷沉寂'},
        'performance': perf,
        'runaway': runaway,
        'a_cap': cap,
        'last_allocation': (last or {}).get('allocation'),
        'last_kills': last_kills,
        'recent_allocations': recent,
        'history_rows': len(rows),
        'latest_digits': {key: str(rows[-1][index]) for key, _label, index in POSITIONS},
    }


# ---------------------------------------------------------------------------
# AI prompt
# ---------------------------------------------------------------------------
def _pool_line(item, context):
    parts = []
    for digit in item['pool']:
        parts.append('%d（遗漏 %s / 近30期 %s 次）'
                     % (digit, context['positions'][item['key']]['omission'][str(digit)],
                        context['positions'][item['key']]['hot30'][str(digit)]))
    return '｜'.join(parts)


def _pct(value):
    return ('%.1f%%' % (100 * value)) if isinstance(value, (int, float)) else '无样本'


def _scheme_text(allocation):
    """Human-readable A/B allocation + B sub-strategy mapping."""
    if not isinstance(allocation, dict) or not allocation:
        return '无'
    a = '、'.join(POSITION_LABELS.get(key, key) for key in allocation.get('a') or []) or '无'
    b = '、'.join(POSITION_LABELS.get(key, key) for key in allocation.get('b') or []) or '无'
    sub = '、'.join('%s=%s' % (POSITION_LABELS.get(key, key), value)
                    for key, value in (allocation.get('b_sub') or {}).items()) or '无'
    return 'A %s／B %s／子策略 %s' % (a, b, sub)


def build_prompt(reference, target, rows, *, context=None):
    """Frozen prompt v3: anti-collapse rules + anti-inertia scheme guard, numbers only."""
    rows = _normalize_rows(rows)
    context = context or build_context(rows)
    pool_lines = []
    for key, label, _index in POSITIONS:
        item = context['positions'][key]
        pool_lines.append('· %s位候选池（只能从这里选）：%s' % (label, _pool_line(item, context)))

    perf = context['performance']
    regime = context['regime']
    runaway = context['runaway']
    last = context['last_allocation'] or {}
    rotation = (isinstance(perf['hit_b_15'], (int, float)) and perf['hit_b_15'] < 0.70)
    runaway_hit = runaway.get('runaway_warning') is True
    recent = context.get('recent_allocations') or []
    two_ago_text = _scheme_text(recent[0]) if len(recent) >= 2 else '无'

    header = (
        '你是「分位杀号决策引擎」。四个号位：千位、百位、十位、个位。'
        '每期每个号位，在下面给出的合法候选池中选 1 个杀码。\n'
        '所有开奖历史、遗漏运算、候选池、深冷统计、滚动命中率、顺局标记均由后端预先计算完成，'
        '你不得自行遍历历史、自行统计期数、自行计算遗漏或命中率，一律以下发数值为准。\n\n'
        '【后端下发数据（参考期 %s → 目标期 %s）】' % (reference, target)
    )
    data_lines = [
        '\n'.join(pool_lines),
        '· 盘面：%s（近3期深冷开出 %d 枚）' % (regime['label'], regime['cold_burst_3']),
        '· 近15期绩效：Hit-A %s（样本 %d）／Hit-B %s（样本 %d）'
        % (_pct(perf['hit_a_15']), perf['a_n'], _pct(perf['hit_b_15']), perf['b_n']),
        '· 顺局预警：%s（同一套分配连续命中 %d 期）'
        % ('是' if runaway_hit else '否', runaway.get('consecutive_same_allocation')),
        '· 上期方案：%s' % _scheme_text(last),
        '· 前第2期方案（本期禁止重复）：%s' % two_ago_text,
        '· 本期 A 组号位上限：%d 个（下限 1 个，禁止 A=0）' % context['a_cap'],
        '· 本期 B 子策略强制轮换：%s'
        % ('是（Hit-B 低于 70% 或顺局预警）' if (rotation or runaway_hit) else '否'),
    ]
    alloc_rules = (
        '【A/B 配比规则】\n'
        '1. A 组号位数量必须在 1 到本期上限之间；禁止四个号位全部给 A；\n'
        '2. 遗漏系 A 适合冷号延续；对冲 B 用于分散，B 必须标明子策略 B1/B2/B3；\n'
        '3. Hit-A 偏低或过高都要减少 A；Hit-A、Hit-B 同时低于 70% 时 A 只留 1 个；\n'
        '4. Hit-B 低于 70% 时，全部 B 子策略必须与上期不同（强制轮换）；\n'
        '5. 四个号位的策略尽量错落，不要高度趋同；B 占多个号位时子策略不得重复。\n\n'
    )
    hard_rules = (
        '【长期运行·防塌方核心规则（每期必须执行，禁止惯性）】\n'
        '1. 禁止惯性复制上期方案：本期 A/B 组合必须依据本期指标重新推导，不得沿用上期搭配；'
        '完整方案（A/B 分配 + B 子策略映射）不得与最近 2 期重复——上期组合直接拒收，'
        '与前第2期方案完全相同同样拒收；\n'
        '2. 顺局风控：顺局预警为「是」（连续同分配 ≥14 期）时，必须立即打乱 A/B 号位分布，'
        '并轮换全部 B 子策略，打破长期拟合惯性，规避拐点塌方；\n'
        '3. 分散原则：A、B 策略错落分布在四个号位，避免策略高度趋同，'
        '防止行情一变多个号位同时失效；\n'
        '4. 冷反弹阶段：盘面为「冷反弹活跃」时，主动降低遗漏系 A 占比（取上限内最小值 1 个），'
        '提高对冲 B 权重，抵御冷号批量爆发带来的连续出错；\n'
        '5. 候选池纪律：四个杀码严格取自对应号位候选池；池内无合适标的时不得自选、'
        '不得放宽或收紧排除标准，理由标注「候选不足」；\n'
        '6. 杀码去重：四个杀码不得与上期四个杀码完全相同——即使 A/B 分配变了，'
        '四码原样照搬同样拒收，必须按本期指标重新选码。\n\n'
    )
    checklist = (
        '【每期强制自检清单（不可跳过，必须写入 self_check，全部为 true 才允许发布）】\n'
        '1. allocation_rederived：A/B 组合已按本期指标重新推导——与上期组合不同，'
        '且完整方案（含 B 子策略映射）不与最近 2 期重复；\n'
        '2. runaway_shuffled：顺局预警时已打乱 A/B 分布并轮换 B 子策略（未触发也填 true）；\n'
        '3. dispersed：B 占多个号位时子策略不重复，四号位策略错落；\n'
        '4. cold_rebound_adjusted：冷反弹活跃时 A 已压到上限内且保留对冲 B（未触发也填 true）；\n'
        '5. pool_confirmed：四个杀码全部在对应号位候选池内，无池外码。\n'
        '自检缺项或任一项为 false，后端直接拒收并重试。\n\n'
    )
    output_spec = (
        '只输出严格 JSON（不要输出分析过程、代码块或额外文字）：\n'
        '{"allocation":{"a":["q"],"b":["b","s","g"],'
        '"b_sub":{"b":"B1","s":"B2","g":"B3"}},'
        '"kills":{"q":9,"b":0,"s":8,"g":2},'
        '"self_check":{"allocation_rederived":true,"runaway_shuffled":true,'
        '"dispersed":true,"cold_rebound_adjusted":true,"pool_confirmed":true},'
        '"reason":"40字以内"}\n'
        '注意：allocation.a 与 allocation.b 合起来必须恰好是 q/b/s/g 四个键；'
        'b_sub 的键必须与 allocation.b 一致；kills 的四个值必须取自对应号位候选池；'
        'A 组号位数量必须在 1..上限 之间；完整方案（分配+B 子策略）不得与最近 2 期重复；'
        '四个杀码不得与上期完全相同。'
    )
    prompt = '\n'.join([header] + data_lines + ['', alloc_rules + hard_rules + checklist + output_spec])

    audit = {
        'version': PROMPT_VERSION,
        'reference': reference,
        'target': target,
        'window': WINDOW,
        'history_sha256': sha256(json.dumps(rows[-WINDOW:], separators=(',', ':')).encode('utf-8')).hexdigest(),
        'rows': [[str(value) for value in row] for row in rows[-WINDOW:]],
        'candidate_pools': {key: context['positions'][key]['pool'] for key in POSITION_KEYS},
        'omission': {key: context['positions'][key]['omission'] for key in POSITION_KEYS},
        'hot30': {key: context['positions'][key]['hot30'] for key in POSITION_KEYS},
        'regime': context['regime'],
        'performance': context['performance'],
        'runaway': context['runaway'],
        'a_cap': context['a_cap'],
        'a_min': 1,
        'last_allocation': context['last_allocation'],
        'recent_schemes': [
            {'a': list(alloc.get('a') or []), 'b': list(alloc.get('b') or []),
             'b_sub': dict(alloc.get('b_sub') or {})}
            for alloc in recent if isinstance(alloc, dict)
        ],
        'scheme_repeat_window': SCHEME_REPEAT_WINDOW,
        'rotation_required': rotation or runaway_hit,
        'runaway_shuffle_required': runaway_hit,
        'allocation_reuse_forbidden': True,
        'scheme_reuse_forbidden': True,
        'b_sub_dispersion_required': True,
        'self_check_required': list(SELF_CHECK_KEYS),
    }
    return prompt, audit


# ---------------------------------------------------------------------------
# 响应校验
# ---------------------------------------------------------------------------
def _kill_digit(value):
    if isinstance(value, bool):
        raise ValueError('kill15 kill must be a digit 0-9')
    if isinstance(value, int) and 0 <= value <= 9:
        return value
    text = str(value if value is not None else '').strip()
    if len(text) == 1 and text.isdigit():
        return int(text)
    raise ValueError('kill15 kill must be a digit 0-9: %r' % (value,))


def normalize_response(response, context=None, audit=None):
    if not isinstance(response, dict):
        raise ValueError('kill15 output must be a JSON object')
    allocation = response.get('allocation')
    kills = response.get('kills')
    if not isinstance(allocation, dict) or not isinstance(kills, dict):
        raise ValueError('kill15 output requires allocation and kills objects')

    a_keys = allocation.get('a')
    b_keys = allocation.get('b')
    b_sub = allocation.get('b_sub') or {}
    if not isinstance(a_keys, list) or not isinstance(b_keys, list):
        raise ValueError('kill15 allocation.a/b must be lists')
    a_keys = [str(key).strip().lower() for key in a_keys]
    b_keys = [str(key).strip().lower() for key in b_keys]
    if len(set(a_keys)) != len(a_keys) or len(set(b_keys)) != len(b_keys):
        raise ValueError('kill15 allocation has duplicate keys')
    if sorted(a_keys + b_keys) != sorted(POSITION_KEYS):
        raise ValueError('kill15 allocation must cover q/b/s/g exactly once')
    if not b_keys:
        raise ValueError('kill15 allocation requires at least one B position')
    if set(b_sub) != set(b_keys):
        raise ValueError('kill15 b_sub keys must equal allocation.b')
    for key, value in b_sub.items():
        if str(value).strip().upper() not in B_SUBS:
            raise ValueError('kill15 b_sub must be B1/B2/B3: %r' % (value,))

    parsed_kills = {}
    for key in POSITION_KEYS:
        if key not in kills:
            raise ValueError('kill15 missing kill for %s' % key)
        parsed_kills[key] = _kill_digit(kills.get(key))

    current_subs = {key: str(b_sub[key]).strip().upper() for key in b_keys}

    if context is not None:
        cap = int(context.get('a_cap') or 2)
        last = context.get('last_allocation') or {}
        perf = context.get('performance') or {}
        hit_b = perf.get('hit_b_15') if isinstance(perf, dict) else None
        runaway = context.get('runaway') or {}
        runaway_hit = runaway.get('runaway_warning') is True

        # 防塌方 v2 · 规则1：禁止惯性复制上期 A/B 组合
        last_a = set(str(key) for key in (last.get('a') or []))
        last_b = set(str(key) for key in (last.get('b') or []))
        if (last_a or last_b) and set(a_keys) == last_a and set(b_keys) == last_b:
            raise ValueError('禁止惯性复制上期方案：A/B 号位组合与上期相同，'
                             '必须按本期下发指标重新推导')

        # 防塌方 v3 · 惯性护栏：完整方案（分配 + B 子策略）不得与最近 2 期重复
        recent = [alloc for alloc in (context.get('recent_allocations') or [])
                  if isinstance(alloc, dict)]
        scheme = _scheme_key({'a': a_keys, 'b': b_keys, 'b_sub': current_subs})
        for offset, alloc in enumerate(reversed(recent), 1):
            if _scheme_key(alloc) != scheme:
                continue
            raise ValueError('禁止惯性方案复用：完整方案（A/B 分配 + B 子策略）'
                             '与前第 %d 期完全相同，必须重新推导' % offset)

        # 防塌方 v2 · 规则4：A 组下限 1，冷反弹活跃时上限为 1（由 a_cap 给出）
        if len(a_keys) < 1:
            raise ValueError('A 组至少保留 1 个遗漏系号位（禁止 A=0）')
        if len(a_keys) > cap:
            raise ValueError('A 组号位 %d 个超过本期上限 %d 个' % (len(a_keys), cap))

        # 防塌方 v2 · 规则3：B 子策略不得重复（策略趋同）
        if len(current_subs) >= 2 and len(set(current_subs.values())) < len(current_subs):
            raise ValueError('B 子策略不得重复（%s），四个号位策略必须错落分散'
                             % '、'.join('%s=%s' % (POSITION_LABELS[key], sub)
                                          for key, sub in current_subs.items()))

        # 防塌方 v2 · 规则5：杀码必须在后端候选池内
        for key in POSITION_KEYS:
            pool = [int(digit) for digit in context['positions'][key]['pool']]
            if parsed_kills[key] not in pool:
                raise ValueError('%s位杀码 %d 不在候选池 %s 内'
                                 % (POSITION_LABELS[key], parsed_kills[key], pool))

        # 防塌方 v3 · 规则7：四个杀码与上期完全相同，同样视为照搬
        last_kills = context.get('last_kills') or {}
        if len(last_kills) == len(POSITION_KEYS):
            same = [POSITION_LABELS[key] for key in POSITION_KEYS
                    if str(parsed_kills[key]) == str(last_kills.get(key))]
            if len(same) == len(POSITION_KEYS):
                raise ValueError('四个杀码与上期完全相同（%s），必须按本期指标重新选码'
                                 % '、'.join('%s%d' % (POSITION_LABELS[k], parsed_kills[k])
                                             for k in POSITION_KEYS))

        # 防塌方 v2 · 规则2：runaway 或 Hit-B 偏低时，B 子策略必须逐位轮换
        if runaway_hit or (isinstance(hit_b, (int, float)) and hit_b < 0.70):
            last_sub = {str(k): str(v).upper() for k, v in (last.get('b_sub') or {}).items()}
            unchanged = [POSITION_LABELS.get(key, key) for key, sub in current_subs.items()
                         if last_sub.get(key) == sub]
            if unchanged:
                raise ValueError('B 子策略必须轮换：%s位子策略与上期相同'
                                 % '、'.join(unchanged))

    # 强制自检清单：缺项或任一项非 true 一律拒收
    self_check = response.get('self_check')
    failed = []
    if not isinstance(self_check, dict):
        failed = list(SELF_CHECK_KEYS)
    else:
        failed = [key for key in SELF_CHECK_KEYS if self_check.get(key) is not True]
    if failed:
        raise ValueError('kill15 强制自检未通过或缺失：%s' % '、'.join(failed))

    return {
        'allocation': {
            'a': [key for key in POSITION_KEYS if key in a_keys],
            'b': [key for key in POSITION_KEYS if key in b_keys],
            'b_sub': {key: current_subs[key] for key in POSITION_KEYS if key in b_keys},
        },
        'kills': parsed_kills,
        'self_check': {key: True for key in SELF_CHECK_KEYS},
        'reason': str(response.get('reason') or '').strip()[:200],
    }


# ---------------------------------------------------------------------------
# DeepSeek 调用
# ---------------------------------------------------------------------------
class ProviderHTTPError(OSError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def _config(name):
    value = os.getenv(name)
    if not value:
        try:
            value = get_secret(name)
        except Exception:
            value = None
    return str(value or '').strip()


def resolve_model():
    for name in ('PENTA5_MODEL_KILL15', 'PENTA5_MODEL_KILLER', 'PENTA5_DEEPSEEK_MODEL'):
        value = _config(name)
        if value:
            return value
    return 'deepseek-flash'


def model_label(name):
    text = str(name or '')
    return text[len('deepseek-'):] if text.startswith('deepseek-') else text


def resolve_api_key():
    return (os.getenv('DEEPSEEK_API_KEY') or _config('PENTA5_KILLER_API_KEY')
            or _config('DEEPSEEK_API_KEY'))


def _safe_model(value):
    text = str(value or '')
    return text if re.fullmatch(r'deepseek-(?:chat|reasoner|(?:v4(?:\.1)?-)?(?:flash|pro)(?:-[0-9]{4})?)',
                                text) else model_label(text)


def _metadata(reference, attempt, model_name, envelope, elapsed_seconds):
    usage = (envelope.get('usage') or {}) if isinstance(envelope, dict) else {}
    usage_clean = {}
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        if isinstance(usage.get(key), int):
            usage_clean[key] = usage[key]
    return {
        'version': 1,
        'reference': reference,
        'attempt': attempt,
        'model': _safe_model(model_name),
        'model_label': model_label(model_name),
        'requested_model': str(model_name),
        'usage': usage_clean,
        'reasoning_effort': REASONING_EFFORT,
        'max_tokens': MAX_TOKENS,
        'elapsed_seconds': round(float(elapsed_seconds), 3),
        'request_policy': PROMPT_VERSION,
    }


def _post(messages, timeout):
    model_name = resolve_model()
    api_key = resolve_api_key()
    if not api_key:
        raise RuntimeError('kill15 missing DeepSeek api key')
    payload = {
        'model': model_name,
        'messages': messages,
        'response_format': {'type': 'json_object'},
        'thinking': {'type': 'enabled'},
        'reasoning_effort': REASONING_EFFORT,
        'max_tokens': MAX_TOKENS,
    }
    response = requests.post(
        PROVIDER_URL,
        json=payload,
        headers={'Authorization': 'Bearer ' + api_key,
                 'Content-Type': 'application/json'},
        timeout=(10, timeout),
    )
    try:
        status = response.status_code
        if status != 200:
            raise ProviderHTTPError('provider-http-%d' % status, status)
        document = response.json()
    finally:
        response.close()
    choices = document.get('choices') if isinstance(document, dict) else None
    if not choices:
        raise RuntimeError('kill15 empty provider response')
    message = (choices[0] or {}).get('message') or {}
    content = str(message.get('content') or '')
    if not content:
        raise RuntimeError('kill15 empty provider content')
    return {
        'content': content,
        'model': (document.get('model') or model_name) if isinstance(document, dict) else model_name,
        'usage': (document.get('usage') or {}) if isinstance(document, dict) else {},
    }


def request_prediction(prompt, reference, target, deadline, context, log=print):
    """Ask DeepSeek for the A/B allocation and four kill digits before close."""
    messages = [{'role': 'user', 'content': prompt}]
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        require_ai_enabled(clock=beijing_now)
        remaining = (deadline - beijing_now()).total_seconds()
        if remaining < MIN_REQUEST_SECONDS:
            raise PublicationRejected('insufficient-time-for-kill15-request')
        timeout = max(10.0, min(90.0, remaining - 10.0))
        emit_http_phase(log, reference, attempt, 'start')
        started = time.monotonic()
        try:
            envelope = _post(messages, timeout)
        except ProviderHTTPError as error:
            emit_http_phase(log, reference, attempt, 'complete', status=error.status)
            last_error = str(error)
            if error.status in NONRETRYABLE_HTTP_STATUSES:
                break
        except Exception as error:
            emit_http_phase(log, reference, attempt, 'complete', status='error')
            last_error = '%s: %s' % (type(error).__name__, str(error)[:120])
        else:
            emit_http_phase(log, reference, attempt, 'complete', status=200)
            try:
                payload = strict_json_object(envelope['content'])
                prediction = normalize_response(payload, context)
            except ValueError as error:
                last_error = str(error)
            else:
                generated_at = beijing_now()
                meta = _metadata(reference, attempt, envelope.get('model') or resolve_model(),
                                 envelope, time.monotonic() - started)
                log('AI_RESPONSE_META ' + json.dumps(meta, ensure_ascii=False,
                                                     sort_keys=True, separators=(',', ':')))
                return prediction, generated_at, meta
        if attempt >= MAX_ATTEMPTS:
            break
        remaining = (deadline - beijing_now()).total_seconds()
        if remaining < MIN_REQUEST_SECONDS:
            raise PublicationRejected('insufficient-time-for-kill15-request')
        pause = max(0.0, min(4.0, remaining - 25.0))
        if pause:
            time.sleep(pause)
        messages = [{'role': 'user',
                     'content': prompt + chr(10) + chr(10)
                     + RETRY_INSTRUCTION.format(error=last_error or 'unknown')}]
    raise RuntimeError('kill15 inference failed after %d attempt(s): %s'
                       % (MAX_ATTEMPTS, last_error or 'unknown'))


def request_prediction_anytime(prompt, reference, target, context, log=print):
    """Replay variant: same request/validation, without the pre-close window gate.

    Only for historical replay where the target period has already been drawn;
    the prompt still contains nothing but data up to `reference`.
    """
    messages = [{'role': 'user', 'content': prompt}]
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        require_ai_enabled(clock=beijing_now)
        emit_http_phase(log, reference, attempt, 'start')
        started = time.monotonic()
        try:
            envelope = _post(messages, 120.0)
        except ProviderHTTPError as error:
            emit_http_phase(log, reference, attempt, 'complete', status=error.status)
            last_error = str(error)
            if error.status in NONRETRYABLE_HTTP_STATUSES:
                break
        except Exception as error:
            emit_http_phase(log, reference, attempt, 'complete', status='error')
            last_error = '%s: %s' % (type(error).__name__, str(error)[:120])
        else:
            emit_http_phase(log, reference, attempt, 'complete', status=200)
            try:
                payload = strict_json_object(envelope['content'])
                prediction = normalize_response(payload, context)
            except ValueError as error:
                last_error = str(error)
            else:
                meta = _metadata(reference, attempt, envelope.get('model') or resolve_model(),
                                 envelope, time.monotonic() - started)
                return prediction, meta
        if attempt >= MAX_ATTEMPTS:
            break
        time.sleep(3.0)
    raise RuntimeError('kill15 replay inference failed after %d attempt(s): %s'
                       % (MAX_ATTEMPTS, last_error or 'unknown'))


# ---------------------------------------------------------------------------
# 封存 / 发布 / 结算
# ---------------------------------------------------------------------------
def create_seal(reference, target, reference_open_time, prediction, rows, *,
                context=None, prompt=None, audit=None, generated_at=None,
                response_meta=None, extra=None):
    reference = str(reference)
    target = str(target)
    if not reference or not target:
        raise ValueError('kill15 reference/target period required')
    rows = _normalize_rows(rows)
    if len(rows) < WINDOW:
        raise ValueError('insufficient-kill15-history')
    context = context or build_context(rows)
    kills = prediction['kills']
    allocation = prediction['allocation']
    positions = {}
    for key, label, index in POSITIONS:
        kill = int(kills[key])
        omission = int(context['positions'][key]['omission'][str(kill)])
        hot = int(context['positions'][key]['hot30'][str(kill)])
        positions[key] = {
            'key': key,
            'label': label,
            'kill': kill,
            'group': 'A' if key in allocation['a'] else 'B',
            'b_sub': allocation['b_sub'].get(key),
            'omission': omission,
            'hot30': hot,
            'in_pool': kill in context['positions'][key]['pool'],
        }
    generated_at = generated_at or beijing_now()
    window_rows = rows[-WINDOW:]
    payload = json.dumps(window_rows, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'))
    audit = audit or {}
    seal = {
        'version': 1,
        'rule_version': RULE_VERSION,
        'prompt_version': audit.get('version') or PROMPT_VERSION,
        'ref_period': reference,
        'target_period': target,
        'reference_open_time': reference_open_time,
        'generated_at': generated_at.isoformat(),
        'published_at': None,
        'publication_state': 'prepared',
        'allocation': allocation,
        'positions': positions,
        'a_cap': context['a_cap'],
        'regime': context['regime'],
        'performance': context['performance'],
        'runaway': context['runaway'],
        'candidate_pools': {key: context['positions'][key]['pool'] for key in POSITION_KEYS},
        'self_check': prediction.get('self_check') or {},
        'policy': {
            'a_min': 1,
            'allocation_reuse_forbidden': True,
            'scheme_reuse_forbidden': True,
            'scheme_repeat_window': SCHEME_REPEAT_WINDOW,
            'b_sub_dispersion_required': True,
            'runaway_shuffle_required': bool(audit.get('runaway_shuffle_required')),
            'rotation_required': bool(audit.get('rotation_required')),
        },
        'reason': str(prediction.get('reason') or '')[:200],
        'window': len(window_rows),
        'expected_items': EXPECTED_ITEMS,
        'expected_group_rate': EXPECTED_GROUP_RATE,
        'expected_basis': EXPECTED_BASIS,
        'input_sha256': sha256(payload.encode('utf-8')).hexdigest(),
        'prompt_sha256': sha256(prompt.encode('utf-8')).hexdigest() if prompt else None,
        'model': (response_meta or {}).get('model'),
        'model_label': (response_meta or {}).get('model_label'),
        'model_metadata': response_meta,
        'data_source': 'data/stats_draw_history.json:first-four-balls',
        'mode': 'shadow',
    }
    if isinstance(extra, dict):
        for key, value in extra.items():
            if key not in seal:
                seal[key] = value
    return seal


def save_seal(seal):
    reference = str(seal.get('ref_period') or '')
    if not reference:
        raise ValueError('kill15 seal requires ref_period')
    seals = load_seals()
    if find_seal(seals, reference) is not None:
        raise PublicationRejected('existing-kill15-seal-cannot-be-replaced')
    seals.append(seal)
    _write_seals(seals)


def mark_published(reference, published_at=None):
    reference = str(reference)
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        raise ValueError('kill15-seal-not-found')
    if seal.get('publication_state') == 'published' and seal.get('published_at'):
        return seal
    seal['publication_state'] = 'published'
    seal['published_at'] = (published_at or beijing_now()).isoformat()
    _write_seals(seals)
    return seal


def mark_publication_failed(reference, error):
    """回滚发布状态：面板写入失败时把封存退回 prepared，便于下一轮重试。"""
    reference = str(reference)
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        return None
    seal['publication_state'] = 'prepared'
    seal['publication_error'] = str(error)[:120]
    _write_seals(seals)
    return seal


def settle_pending(snapshot=None, now=None):
    """Settle every sealed target period that has opened; append-only."""
    snapshot = snapshot or load_snapshot(persist=False)
    records = {}
    for record in snapshot.get('records') or []:
        if isinstance(record, dict):
            records[str(record.get('period'))] = record
    seals = load_seals()
    settlements = load_settlements()
    settled = {str(item.get('target_period')) for item in settlements}
    added = []
    for seal in seals:
        target = str(seal.get('target_period') or '')
        if not target or target in settled:
            continue
        record = records.get(target)
        if not record:
            continue
        numbers = [int(value) for value in (record.get('numbers') or [])[:4]]
        if len(numbers) != 4:
            continue
        positions = {}
        hits = []
        for key, label, index in POSITIONS:
            item = (seal.get('positions') or {}).get(key) or {}
            kill = int(item.get('kill'))
            actual = numbers[index]
            hit = actual != kill
            hits.append(hit)
            positions[key] = {
                'key': key, 'label': label, 'kill': kill, 'actual': actual,
                'hit': hit, 'group': item.get('group'), 'b_sub': item.get('b_sub'),
            }
        settlement = {
            'version': 1,
            'rule_version': seal.get('rule_version'),
            'ref_period': seal.get('ref_period'),
            'target_period': target,
            'open_time': record.get('opentime'),
            'settled_at': (now or beijing_now()).isoformat(),
            'allocation': seal.get('allocation'),
            'positions': positions,
            'position_hits': sum(1 for hit in hits if hit),
            'group_hit': all(hits),
            'expected_group_rate': seal.get('expected_group_rate'),
            'seal_generated_at': seal.get('generated_at'),
            'mode': seal.get('mode'),
        }
        settlements.append(settlement)
        added.append(settlement)
    if added:
        _write_settlements(settlements)
    return added


def deadline_for(reference):
    target = next_period(reference)
    if not target:
        raise ValueError('kill15 invalid reference period')
    slot = period_slot(target)
    if slot is None:
        raise ValueError('kill15 invalid target period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target


def today_stats(now=None):
    now = now or beijing_now()
    settlements = load_settlements()
    rows = [row for row in settlements if row.get('rule_version') == RULE_VERSION]
    total = len(rows)
    hits = sum(1 for row in rows if row.get('group_hit') is True)
    position_bets = position_hits = 0
    for row in rows:
        for item in (row.get('positions') or {}).values():
            position_bets += 1
            if item.get('hit') is True:
                position_hits += 1
    perf = performance_from_settlements(settlements)
    return {
        'total': total,
        'group_hits': hits,
        'group_rate': (hits / float(total)) if total else None,
        'position': {'bets': position_bets, 'hits': position_hits,
                     'rate': (position_hits / float(position_bets)) if position_bets else None},
        'hit_a_15': perf['hit_a_15'],
        'hit_b_15': perf['hit_b_15'],
        'pending': len([seal for seal in load_seals()
                        if seal.get('publication_state') == 'published']),
        'latest_period': latest_period(),
    }
