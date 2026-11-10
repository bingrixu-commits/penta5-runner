"""AI 双定位杀码 + 三数共现避开三组 + 二数合/三数合（独立框架，v8）。

口径（面板、封存与统计都按此写死）：
- 由 DeepSeek 每期从 千/百/十/个 四个位置中自行选出恰好 2 个不同位置，每个位置各排除
  1 个数字（杀码），每个位置计 1 注（2 注/期），全部计入整组；
- 定位依据：每个位置给出近 50 期热码 top3 与上期（参考期）该位置重码/邻码（±1，0↔9
  循环），模型须从中选择排除码，不得以冷码（出现次数少、当前遗漏长）为依据；
- 三数共现避开 1/2/3 组：由系统按目标期给定（data/state.json extraKills，与 kill3 同源），
  每组 3 个数字，命中 = 该组 3 个数字未全部出现在 4 球开奖号中（单项基线 97.96%），
  3 组各计 1 项；
- 二数合 / 三数合：直接取 kill10 同目标期已发布封存的 he2_keep（4 码）/ he3_keep（5 码）；
  二数合命中 = 前 4 球任取两位的 6 种和值个位任一落入保留 4 码（单项基线 98.08%）；
  三数合命中 = 前 4 球任取三位的 4 种和值个位任一落入保留 5 码（单项基线 93.75%）；
- 整组命中 = 2 个位置 + 三数共现 3 组 + 二数合 + 三数合同时命中（整组固定 7 项）；
- 整组随机基线：10^4 开奖 × (C(4,2)*10*10) × 120^3 × 5 × 200 = 10^4 × 1,036,800,000,000
  配置空间精确枚举，精确值 0.6997078452825 = 69.9708%（封存登记 0.6997）；
  封存记录 expected_items=7 / expected_group_rate=0.6997 /
  expected_basis='enumerated-10^4-draws-x-600x120^3x5x200-configs'；
   注：120^3 按项目惯例取三组独立同分布枚举（上界口径，与 kill11 一致）；生成器实际三组为
   无放回互斥选择，真实可达空间 600×120·119·118×5×200 的精确基线为 0.6993332523（差 3.75e-4），
   历史 875 期实测无法区分两者，故仍按项目口径登记 0.6997；
- 位置与杀码完全由模型每期自选，不做 hold 沿用；无合尾项、无形态项；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。

历史兼容：v1~v7 旧封存可能含合尾 / 单双形态项或 2~4 个位置，结算时按旧封存口径补齐并
计入整组，保证旧期可结算；今日/本版统计只按当前 RULE_VERSION 计入。

数据落点：
- data/kill1_tail/seals.json        每期封存（同一参考期不可替换）
- data/kill1_tail/settlements.json  开奖后结算（只追加）
"""
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import time

from stats_history import (beijing_now, business_start, load_snapshot, parse_time,
                           period_slot)
from ai_verified_stats import atomic_json, digit, next_period
from ai_http_observability import emit_http_phase
from ai_prediction_guard import PublicationRejected

ROOT = Path('/root/penta5-runner')
DATA_DIR = ROOT / 'data' / 'kill1_tail'
SEALS_PATH = DATA_DIR / 'seals.json'
SETTLEMENTS_PATH = DATA_DIR / 'settlements.json'
STATE_PATH = ROOT / 'data' / 'state.json'

WINDOW = 50
CLOSE_SECONDS = 70
MIN_REQUEST_SECONDS = 20.0
MAX_TOKENS = 12288
REASONING_EFFORT = 'high'
NONRETRYABLE_HTTP_STATUSES = frozenset((400, 401, 402, 422))

POSITIONS = (
    ('q', '千位', 0),
    ('b', '百位', 1),
    ('s', '十位', 2),
    ('g', '个位', 3),
)
POSITION_LABELS = {key: label for key, label, _index in POSITIONS}
POSITION_INDEX = {key: index for key, _label, index in POSITIONS}
POSITION_ORDER = tuple(key for key, _label, _index in POSITIONS)

RULE_VERSION = 'kill1-tail-v8-two-position-three-avoid-he2-he3'
PROMPT_VERSION = 'kill1-tail-prompt-v8-two-position-three-avoid-he2-he3'
POSITION_COUNT = 2

# 三数共现避开 1/2/3 组（与 kill3 同源：data/state.json extraKills）。
THREE_AVOID_KEYS = ('threeDigitAvoid', 'threeDigitHit', 'threeDigitAvoid2')
THREE_AVOID_LABELS = {'threeDigitAvoid': '避开1组',
                      'threeDigitHit': '避开2组',
                      'threeDigitAvoid2': '避开3组'}
THREE_AVOID_ITEMS = 3
THREE_AVOID_SINGLE_BASELINE = 0.9796

# 二数合 / 三数合（与 kill10 同源：取同目标期已发布封存的 he2_keep / he3_keep）。
HE2_SOURCE_DIGITS = '02468'
HE2_KEEP_COUNT = 4
HE2_BASELINE = 0.9808
HE3_KEEP_COUNT = 5
HE3_CANDIDATE_COUNT = 200
HE3_BASELINE = 0.9375
KILL10_SEALS_PATH = ROOT / 'data' / 'kill10_tail' / 'seals.json'

# 整组随机期望：10^4 开奖 × 配置空间全量精确枚举。
# 配置空间 = C(4,2)*10*10（位置 600）× 120^3（共现三组）× 5（he2）× 200（he3）
#          = 1,036,800,000,000；逐开奖按 k=不同数字个数解析计数，
# 精确值 0.6997078452825 → 登记 0.6997。
EXPECTED_BASIS = 'enumerated-10^4-draws-x-600x120^3x5x200-configs'
EXPECTED_ITEMS = 7
EXPECTED_GROUP_RATE = 0.6997

# v1~v7 旧封存的合尾键 → 位置下标（仅用于旧期结算兼容，新封存不再写入合尾）。
LEGACY_TAIL_INDICES = {
    'tail_all': (0, 1, 2, 3),
    'tail_qbs': (0, 1, 2),
    'tail_qbg': (0, 1, 3),
    'tail_qsg': (0, 2, 3),
    'tail_bsg': (1, 2, 3),
}

RETRY_INSTRUCTION = (
    '上次响应未通过本地校验（{error}）。请严格按格式重新输出完整 JSON：'
    'positions 必须是恰好 2 个对象组成的数组，每个对象为 '
    '{{"key":"q|b|s|g 之一","kill":0 至 9 的整数}}，各 key 必须不同（共 2 个不同位置）；'
    'reason 40 字以内。不要输出任何其他文字、解释或代码块。'
)


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
    """Return all sealed predictions in chronological order."""
    document = _load_json(SEALS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill1_tail seals records must be a list')
    return [item for item in records if isinstance(item, dict)]


def load_settlements():
    document = _load_json(SETTLEMENTS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill1_tail settlements records must be a list')
    return [item for item in records if isinstance(item, dict)]


def _write_seals(records):
    atomic_json(SEALS_PATH, {'version': 1, 'records': records})


def _write_settlements(records):
    atomic_json(SETTLEMENTS_PATH, {'version': 1, 'records': records})


def find_seal(seals, reference):
    reference = str(reference)
    return next((item for item in seals if str(item.get('ref_period')) == reference), None)


def latest_period():
    """Latest drawn period from the cheap state file; empty string when unknown."""
    try:
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        period = str((state.get('latestRecord') or {}).get('period') or '')
        if period:
            return period
    except (OSError, ValueError):
        pass
    try:
        history = json.loads((ROOT / 'data' / 'stats_draw_history.json').read_text(encoding='utf-8'))
        records = history.get('records') or []
        if records:
            return str(records[-1].get('period') or '')
    except (OSError, ValueError):
        pass
    return ''


def rank_digits(values):
    """System cold-code ordering: count ascending, then current omission descending."""
    counts = {number: 0 for number in range(10)}
    last = {}
    for index, value in enumerate(values):
        value = int(value)
        counts[value] += 1
        last[value] = index
    size = len(values)
    items = [(number, counts[number],
              size if last.get(number) is None else size - last[number] - 1)
             for number in range(10)]
    items.sort(key=lambda item: (item[1], -item[2]))
    return items


def rank_digits_hot(values):
    """System hot-code ordering: count descending, then current omission ascending."""
    counts = {number: 0 for number in range(10)}
    last = {}
    for index, value in enumerate(values):
        value = int(value)
        counts[value] += 1
        last[value] = index
    size = len(values)
    items = [(number, counts[number],
              size if last.get(number) is None else size - last[number] - 1)
             for number in range(10)]
    items.sort(key=lambda item: (-item[1], item[2], item[0]))
    return items

ATTACK_ORDER = '1234567890'
ATTACK_POSITION_LABELS = {'q': '千', 'b': '百', 's': '十', 'g': '个'}

_ATTACK_TAIL_LABELS = {'tail_all': '千百十个合', 'tail_qbs': '千百十合',
                       'tail_qbg': '千百个合', 'tail_qsg': '千十个合',
                       'tail_bsg': '百十个合'}
HE2_PAIRS = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
HE3_TRIPLES = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))


def _group_code(value):
    """三数共现组必须是 3 个数字（保留原顺序，便于展示与攻击语）。"""
    text = str(value if value is not None else '').strip()
    if len(text) != 3 or any(char not in '0123456789' for char in text):
        return None
    return text


def pair_tails(numbers):
    """前 4 球任取两位的 6 个和值个位（与 kill10_tail.pair_tails 完全一致）。"""
    values = [int(value) for value in list(numbers)[:4]]
    return sorted({sum(values[index] for index in pair) % 10 for pair in HE2_PAIRS})


def triple_tails(numbers):
    """前 4 球任取三位的 4 个和值个位（与 kill10_tail.triple_tails 完全一致）。"""
    values = [int(value) for value in list(numbers)[:4]]
    return sorted({sum(values[index] for index in triple) % 10 for triple in HE3_TRIPLES})


def _group_avoid_hit(numbers, code):
    """三数共现组命中 = 该组 3 个数字未全部出现在 4 球开奖号中（与 kill3 同源）。"""
    text = _group_code(code)
    if text is None:
        raise ValueError('three-avoid group must be three digits')
    present = {int(value) for value in list(numbers)[:4]}
    return not all(int(char) in present for char in text)


def resolve_three_avoid(target, state=None):
    """取目标期三数共现避开 1/2/3 组（与 kill3 同源；取不到返回 []）。"""
    try:
        from kill3_tail import resolve_three_avoid as _resolve
        raw = _resolve(target, state)
    except Exception:
        return []
    groups = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or '')
        code = _group_code(item.get('value'))
        if key not in THREE_AVOID_KEYS or code is None:
            continue
        groups.append({'key': key,
                       'label': THREE_AVOID_LABELS.get(key, str(item.get('label') or key)),
                       'value': code})
    if len(groups) != THREE_AVOID_ITEMS:
        return []
    return groups


def _validate_he2_keep(value):
    """he2_keep 必须是 02468 中恰好 4 个不同数字（kill10 候选空间 5 个全枚举）。"""
    text = str(value if value is not None else '').strip()
    if len(text) != HE2_KEEP_COUNT or len(set(text)) != HE2_KEEP_COUNT:
        return None
    if any(char not in HE2_SOURCE_DIGITS for char in text):
        return None
    return text


def _validate_he3_keep(value):
    """he3_keep 必须是 0-9 中恰好 5 个不同数字且奇数个数 2~3（kill10 候选 200 个）。"""
    text = str(value if value is not None else '').strip()
    if len(text) != HE3_KEEP_COUNT or len(set(text)) != HE3_KEEP_COUNT:
        return None
    if any(char not in '0123456789' for char in text):
        return None
    odd = sum(1 for char in text if int(char) % 2 == 1)
    if odd not in (2, 3):
        return None
    return text


def kill10_keeps(target, reference=None):
    """取 kill10 同目标期已发布封存的 he2_keep / he3_keep；未就绪返回 None（不编造）。"""
    document = _load_json(KILL10_SEALS_PATH, {'version': 1, 'records': []})
    target = str(target)
    for seal in reversed(document.get('records') or []):
        if not isinstance(seal, dict):
            continue
        if str(seal.get('target_period')) != target:
            continue
        if seal.get('publication_state') != 'published':
            continue
        if reference is not None and str(seal.get('ref_period')) != str(reference):
            continue
        he2 = _validate_he2_keep(seal.get('he2_keep'))
        he3 = _validate_he3_keep(seal.get('he3_keep'))
        if he2 and he3:
            return {'he2_keep': he2,
                    'he3_keep': he3,
                    'ref_period': str(seal.get('ref_period') or ''),
                    'target_period': str(seal.get('target_period') or ''),
                    'published_at': seal.get('published_at')}
    return None


def build_attack(seal):
    """与既有框架同格式的攻击语。

    位置保留码（被杀位置为去掉杀码的 9 码，未选位置保留 10 码）→ 四定 →
    [旧版合尾保留码] → 除对数<1组>-<2组>-<3组> → 二数合<4码>三数合<5码>各。
    """
    if not isinstance(seal, dict):
        raise ValueError('attack requires a sealed prediction')
    positions = seal.get('positions') or {}
    if isinstance(positions, list):
        positions = {str(item.get('key')): item.get('kill')
                     for item in positions if isinstance(item, dict)}

    def remaining(value):
        text = str(value).strip()
        if len(text) != 1 or text not in ATTACK_ORDER:
            raise ValueError('attack kill digit must be 0..9')
        return ''.join(char for char in ATTACK_ORDER if char != text)

    parts = []
    for key, _label, _index in POSITIONS:
        killed = key in positions
        parts.append(ATTACK_POSITION_LABELS[key] +
                     (remaining(positions[key]) if killed else ATTACK_ORDER))
    parts.append('四定')
    text = ''.join(parts)
    tail = seal.get('tail') or {}
    tail_key = tail.get('key') if isinstance(tail, dict) else None
    if tail_key in LEGACY_TAIL_INDICES:
        text += _ATTACK_TAIL_LABELS[tail_key] + remaining(tail.get('kill'))
    sealed_form = seal.get('form')
    if isinstance(sealed_form, str) and len(sealed_form) == 4:
        text += '除' + sealed_form
    codes = []
    for group in seal.get('three_avoid') or []:
        code = _group_code(group.get('value') if isinstance(group, dict) else group)
        if code is None:
            raise ValueError('attack three-avoid group must be three digits')
        codes.append(code)
    if codes:
        text += '除对数' + '-'.join(codes)
    he2 = str(seal.get('he2_keep') or '').strip()
    he3 = str(seal.get('he3_keep') or '').strip()
    if he2 or he3:
        if he2:
            text += '二数合' + he2
        if he3:
            text += '三数合' + he3
        text += '各'
    return text


def _position_key(value):
    text = str(value).strip().lower()
    mapping = {'q': 'q', 'b': 'b', 's': 's', 'g': 'g',
               '千': 'q', '千位': 'q', '百': 'b', '百位': 'b',
               '十': 's', '十位': 's', '个': 'g', '个位': 'g'}
    key = mapping.get(text)
    if key is None:
        raise ValueError('位置键必须是 q/b/s/g 之一')
    return key


def _position_items(response):
    """Accept positions as a list (v3: exactly 3 items) or a legacy-style mapping."""
    raw = response.get('positions')
    if isinstance(raw, dict):
        return [{'key': key, 'kill': value} for key, value in raw.items()]
    if isinstance(raw, list):
        return raw
    raise ValueError('positions 必须是数组')


def normalize_response(response):
    """Strict local validation; never pad, default or invent anything."""
    if not isinstance(response, dict):
        raise ValueError('响应必须是 JSON 对象')
    items = _position_items(response)
    if len(items) != POSITION_COUNT:
        raise ValueError('positions 必须恰好包含 %d 个位置' % POSITION_COUNT)
    kills = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('positions 每项必须是对象')
        key = _position_key(item.get('key'))
        if key in kills:
            raise ValueError('positions 位置不能重复')
        number = digit(item.get('kill'))
        if number is None:
            raise ValueError('位置杀码必须是 0 至 9 的整数')
        kills[key] = str(number)
    if len(kills) != POSITION_COUNT:
        raise ValueError('positions 必须恰好包含 %d 个不同位置' % POSITION_COUNT)
    reason = str(response.get('reason', ''))
    try:
        reason.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('reason 必须是合法 UTF-8 文本') from None
    ordered = [key for key in POSITION_ORDER if key in kills]
    return {
        'positions': {key: kills[key] for key in ordered},
        'counted': ordered,
        'reason': reason[:200],
    }


def _history_rows(snapshot=None):
    snapshot = snapshot or load_snapshot(persist=False)
    records = snapshot.get('records') or []
    rows = [list(record.get('numbers') or [])[:4] for record in records]
    rows = [row for row in rows if len(row) == 4]
    if len(rows) < WINDOW + 1:
        raise ValueError('insufficient-kill1-history')
    return snapshot, rows


def _cold_line(ranks, limit=3):
    return '、'.join(f'{number}(出{count}次/遗漏{omission}期)'
                     for number, count, omission in ranks[:limit])


def _hot_line(ranks, limit=3):
    return '、'.join(f'{number}(出{count}次/遗漏{omission}期)'
                     for number, count, omission in ranks[:limit])

def build_prompt(reference, target, rows, *, three_avoid=None, panel_snapshot=None):
    """Frozen prompt and its audit metadata; rows are the last WINDOW draws.

    口径 v8：AI 固定选 2 个不同位置各杀 1 码；三数共现 3 组与二数合/三数合由系统附带，整组 7 项。
    """
    window = rows[-WINDOW:]
    history = ', '.join(''.join(str(int(value)) for value in row) for row in window)
    hot_by_position = []
    for key, label, index in POSITIONS:
        ranks = rank_digits_hot([row[index] for row in window])
        hot_by_position.append((key, label, index, ranks))
    position_lines = [f'· {label}：{_hot_line(ranks)}'
                      for _key, label, _index, ranks in hot_by_position]
    latest = window[-1]
    latest_digits = {key: int(latest[index]) for key, _label, index in POSITIONS}
    latest_line = ' · '.join(f'{label} {latest_digits[key]}'
                             for key, label, _index in POSITIONS)
    neighbor_lines = []
    for key, label, _index in POSITIONS:
        base = latest_digits[key]
        neighbor_lines.append(
            f'· {label}：重码 {base}；邻码 {(base - 1) % 10} / {(base + 1) % 10}')
    groups = three_avoid if isinstance(three_avoid, list) else []
    group_lines = []
    for item in groups:
        if not isinstance(item, dict):
            continue
        code = _group_code(item.get('value'))
        if code is None:
            continue
        label = item.get('label') or THREE_AVOID_LABELS.get(item.get('key'), '避开组')
        group_lines.append(
            f'· {label}：{code}（该组 3 个数字未全部出现在 4 球即命中，单项随机基线 97.96%）')
    panel_section = ''
    panel_context_text = None
    panel_context_sha = None
    if isinstance(panel_snapshot, dict):
        from panel_context import prompt_text as _panel_prompt_text
        panel_context_text, panel_context_sha = _panel_prompt_text(panel_snapshot)
        panel_section = (
            '【面板全量上下文（同一面板其他框架最近发布/战绩；只含已发布数据，各条自带'
            ' reference/target；仅供自主决策参考，不是未来概率保证）】'
            + chr(10) + panel_context_text + chr(10) + chr(10))
    prompt = (
        f'以下是真实的最近{WINDOW}期开奖前四位（按时间先后排序，'
        f'最新已开奖参考期号:{reference}，预测目标期号:{target}）：\n'
        f'{history}\n\n'
        '【近50期各位置热码（出现次数多、当前遗漏短优先；仅历史统计，不代表未来概率）】\n'
        + '\n'.join(position_lines) + '\n\n'
        f'【上期开奖前四位（参考期 {reference}）】\n'
        f'· {latest_line}\n\n'
        '【上期重码 / 邻码候选（±1，0↔9 循环；上期号码的重复或相邻数字）】\n'
        + '\n'.join(neighbor_lines) + '\n\n'
        '【本期系统附带项（由系统给定，不需要你输出，仅用于理解整组口径）】\n'
        + ('\n'.join(group_lines) if group_lines else '· 三数共现避开 1/2/3 组：本期尚未就绪')
        + '\n· 二数合保留 4 码 / 三数合保留 5 码：由 kill10 同目标期封存自动读取'
        '（二数合单项 98.08%，三数合单项 93.75%）\n\n'
        '【任务】\n'
        f'1. 本期固定选恰好 {POSITION_COUNT} 个不同位置（从 千/百/十/个 中选 {POSITION_COUNT} 个），'
        '并为每个位置各排除 1 个数字（0-9），作为该位置的定位杀码；'
        f'每个位置只杀 1 码、只计 1 注，{POSITION_COUNT} 个位置全部计入整组，不得多也不得少；'
        '排除码须从该位置的近50期热码 top3、上期重码、上期邻码（±1）中选取，'
        '不得以上期冷码 / 长遗漏码作为定位依据；\n'
        '2. 命中口径：位置 = 目标期该位置实际开奖号不等于该位置的排除码'
        f'（{POSITION_COUNT} 个位置各计 1 注）；{POSITION_COUNT} 个位置同时命中才算整组命中'
        f'（整组固定 {EXPECTED_ITEMS} 项 = {POSITION_COUNT} 个位置 + 三数共现避开 3 组 + 二数合 + 三数合，'
        '随机基线 69.9700%）；\n'
        '3. 位置与杀码不做连用限制，完全由你每期自主选择；\n'
        '4. 热码、重码、邻码、遗漏只是历史特征，不能据此断言未来概率；不要为换而换，不得声称保证命中；\n'
        '5. 只能使用上面给出的历史与面板全量上下文；面板数据只作参考，不得照抄，也不得声称保证命中。\n\n'
        '只输出严格 JSON（不要输出分析过程、代码块或额外文字）：\n'
        '{"positions":[{"key":"q|b|s|g","kill":<0-9整数>},'
        '{"key":"q|b|s|g","kill":<0-9整数>}],"reason":"40字以内"}\n'
        f'注意：positions 必须恰好 {POSITION_COUNT} 项、key 互不相同'
        f'（共 {POSITION_COUNT} 个不同位置），示例仅为格式参考。'
    )
    if panel_section:
        _marker = prompt.find('【任务】')
        if _marker != -1:
            prompt = prompt[:_marker] + panel_section + prompt[_marker:]
    audit = {
        'version': PROMPT_VERSION,
        'reference': reference,
        'target': target,
        'window': WINDOW,
        'history_sha256': sha256(history.encode('utf-8')).hexdigest(),
        'rows': [[str(int(value)) for value in row] for row in window],
        'position_basis': 'hot-repeat-neighbor',
        'position_count': POSITION_COUNT,
        'expected_baseline': {str(EXPECTED_ITEMS): {'items': EXPECTED_ITEMS,
                                                   'group_rate': EXPECTED_GROUP_RATE,
                                                   'basis': EXPECTED_BASIS}},
        'three_avoid': [{'key': item.get('key'),
                         'label': item.get('label'),
                         'value': item.get('value')}
                        for item in groups if isinstance(item, dict)],
        'he2_baseline': HE2_BASELINE,
        'he3_baseline': HE3_BASELINE,
        'hot_ranking': [{'key': key, 'label': label,
                         'top': [{'number': str(number), 'count': count,
                                  'omission': omission}
                                 for number, count, omission in ranks[:3]]}
                        for key, label, _index, ranks in hot_by_position],
        'latest_row': {'period': str(reference),
                       'digits': {key: str(latest_digits[key])
                                  for key, _label, _index in POSITIONS}},
        'panel_context_sha256': panel_context_sha,
        'panel_context': panel_snapshot if isinstance(panel_snapshot, dict) else None,
    }
    return prompt, audit


def resolve_model():
    for name in ('PENTA5_MODEL_KILL11', 'PENTA5_MODEL_KILLER', 'PENTA5_DEEPSEEK_MODEL'):
        try:
            from app_secrets import get_secret
            value = get_secret(name)
        except RuntimeError:
            continue
        if value:
            return value
    return 'deepseek-flash'


def model_label(model=None):
    model = model or resolve_model()
    return {'deepseek-flash': 'DeepSeek-V4.1-Flash',
            'deepseek-v4-flash': 'DeepSeek-V4-Flash',
            'deepseek-v4-pro': 'DeepSeek-V4-Pro'}.get(model, model)


def resolve_api_key():
    from app_secrets import get_secret
    value = os.getenv('DEEPSEEK_API_KEY')
    if value:
        return value
    return get_secret('PENTA5_KILLER_API_KEY')


def _extract_json(text):
    if not isinstance(text, str):
        raise ValueError('响应正文不是文本')
    clean = re.sub(r'```json\s*|\s*```', '', text).strip()
    start, end = clean.find('{'), clean.rfind('}')
    if start < 0 or end < start:
        raise ValueError('响应中未找到 JSON 对象')
    from ai_provider_policy import strict_json_object
    return strict_json_object(clean[start:end + 1])


def _safe_model(value, api_key):
    if not isinstance(value, str) or not value:
        return None
    if api_key and api_key in value:
        return '[redacted]'
    return value if re.fullmatch(r'deepseek-(?:chat|reasoner|(?:v4(?:\.1)?-)?(?:flash|pro)(?:-[0-9]{4})?)', value) else '[redacted]'


def _metadata(reference, attempt, model, api_key, envelope, elapsed):
    body = envelope if isinstance(envelope, dict) else {}
    usage_raw = body.get('usage') if isinstance(body.get('usage'), dict) else {}
    usage = {}
    for key in ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'):
        value = usage_raw.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            usage[key] = value
    reasoning = usage_raw.get('reasoning_tokens')
    details = usage_raw.get('completion_tokens_details')
    if reasoning is None and isinstance(details, dict):
        reasoning = details.get('reasoning_tokens')
    if isinstance(reasoning, int) and not isinstance(reasoning, bool):
        usage['reasoning_tokens'] = reasoning
    choices = body.get('choices')
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    return {
        'ref': reference,
        'attempt': attempt,
        'requested_model': model,
        'returned_model': _safe_model(body.get('model'), api_key),
        'finish_reason': choice.get('finish_reason'),
        'usage': usage,
        'thinking': 'enabled',
        'reasoning_effort': REASONING_EFFORT,
        'max_tokens': MAX_TOKENS,
        'elapsed_seconds': round(max(0.0, float(elapsed)), 3),
        'request_policy_version': 'kill1-high-12k-deadline-v1',
    }


class ProviderHTTPError(OSError):
    """Provider HTTP failure carrying the status code for retry decisions."""

    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


def _post(messages, timeout):
    import requests
    api_key = resolve_api_key()
    payload = {
        'model': resolve_model(),
        'messages': messages,
        'response_format': {'type': 'json_object'},
        'thinking': {'type': 'enabled'},
        'reasoning_effort': REASONING_EFFORT,
        'max_tokens': MAX_TOKENS,
    }
    response = requests.post(
        'https://api.deepseek.com/chat/completions',
        json=payload,
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        timeout=(10, timeout),
    )
    try:
        if response.status_code != 200:
            raise ProviderHTTPError(f'provider-http-{response.status_code}',
                                    response.status_code)
        return response.json()
    finally:
        close = getattr(response, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def request_prediction(prompt, reference, target, deadline, log=print, value_check=None):
    """At most three paid calls inside the frozen publication window."""
    from ai_call_control import require_ai_enabled

    require_ai_enabled(clock=beijing_now)
    model = resolve_model()
    api_key = resolve_api_key()
    messages = [{'role': 'user', 'content': prompt}]
    last_error = 'no-attempt'
    last_status = None
    attempts_used = 0
    for attempt in (1, 2, 3):
        attempts_used = attempt
        require_ai_enabled(clock=beijing_now)
        remaining = (deadline - beijing_now()).total_seconds()
        if remaining < MIN_REQUEST_SECONDS:
            raise PublicationRejected('insufficient-time-for-kill1-request')
        timeout = max(10.0, min(90.0, remaining - 10.0))
        started = time.monotonic()
        # HTTP boundary telemetry: best-effort, never changes retry/publication semantics.
        http_phase_completed = False
        emit_http_phase(log, reference, attempt, 'start')
        try:
            envelope = _post(messages, timeout)
            emit_http_phase(log, reference, attempt, 'complete', status=200)
            http_phase_completed = True
            choices = envelope.get('choices')
            if not isinstance(choices, list) or not choices:
                raise ValueError('响应缺少 choices')
            choice = choices[0] if isinstance(choices[0], dict) else {}
            if choice.get('finish_reason') != 'stop':
                raise ValueError('响应未正常结束')
            message = choice.get('message') if isinstance(choice.get('message'), dict) else {}
            prediction = normalize_response(_extract_json(message.get('content')))
            if value_check is not None:
                value_check(prediction)
            meta = _metadata(reference, attempt, model, api_key, envelope,
                             time.monotonic() - started)
            log('AI_RESPONSE_META ' + json.dumps(meta, ensure_ascii=True, allow_nan=False,
                                                separators=(',', ':')))
            log(f'kill1 response validated; ref={reference}; attempt={attempt}; publication pending.')
            return prediction, beijing_now(), meta
        except Exception as error:
            # Provider bodies and raw model text are never echoed or retried blindly.
            if not http_phase_completed:
                phase_status = getattr(error, 'status', None)
                emit_http_phase(log, reference, attempt, 'complete',
                                status=phase_status if type(phase_status) is int else None)
                http_phase_completed = True
            last_error = type(error).__name__
            status = getattr(error, 'status', None)
            if type(status) is not int:
                status = None
            last_status = status
            status_note = '' if status is None else f'; http_status={status}'
            log(f'kill1 attempt {attempt}/3 rejected; error_type={last_error}{status_note}; not published.')
            if status in NONRETRYABLE_HTTP_STATUSES:
                log(f'kill1 nonretryable provider HTTP {status}; stop retries; not published.')
                break
            if attempt == 3:
                break
            detail = str(error)[:100]
            messages = [{'role': 'user', 'content': prompt},
                        {'role': 'user', 'content': RETRY_INSTRUCTION.format(error=detail)}]
            pause = max(0.0, min(4.0, (deadline - beijing_now()).total_seconds() - 25.0))
            if pause:
                time.sleep(pause)
    status_note = '' if last_status is None else f'; http_status={last_status}'
    raise RuntimeError(f'kill1 inference failed after {attempts_used} attempt(s): '
                       f'{last_error}{status_note}; not published')


def _validate_seal_items(seal):
    """v8 封存前自检：2 个位置 + 3 组合法共现组 + he2/he3 合法；不合法直接拒绝发布。"""
    positions = seal.get('positions') or {}
    if not isinstance(positions, dict) or len(positions) != POSITION_COUNT:
        raise ValueError('v8 seal requires exactly %d positions' % POSITION_COUNT)
    groups = seal.get('three_avoid')
    if not isinstance(groups, list) or len(groups) != THREE_AVOID_ITEMS:
        raise ValueError('v8 seal requires %d three-avoid groups' % THREE_AVOID_ITEMS)
    for item in groups:
        if not isinstance(item, dict) or _group_code(item.get('value')) is None:
            raise ValueError('v8 seal three-avoid group must be three digits')
    if _validate_he2_keep(seal.get('he2_keep')) is None:
        raise ValueError('v8 seal he2_keep must be 4 distinct digits from 02468')
    if _validate_he3_keep(seal.get('he3_keep')) is None:
        raise ValueError('v8 seal he3_keep must be 5 distinct digits with 2~3 odd')


def create_seal(reference, target, reference_open_time, prediction, prompt, audit,
                generated_at, response_meta, extra=None):
    """Build the immutable sealed record for the panel; the panel publishes it later."""
    prediction = normalize_response(prediction)
    seal = {
        'version': 1,
        'rule_version': RULE_VERSION,
        'prompt_version': PROMPT_VERSION,
        'ref_period': str(reference),
        'target_period': str(target or next_period(reference)),
        'reference_open_time': reference_open_time,
        'generated_at': generated_at.isoformat(),
        'published_at': None,
        'publication_state': 'prepared',
        'positions': prediction['positions'],
        'counted': prediction.get('counted') or list(prediction['positions']),
        'reason': prediction['reason'],
        'model': resolve_model(),
        'model_label': model_label(),
        'model_metadata': response_meta,
        'prompt_sha256': sha256(prompt.encode('utf-8')).hexdigest(),
        'input_sha256': audit.get('history_sha256'),
        'panel_context_sha256': audit.get('panel_context_sha256'),
        'panel_context': audit.get('panel_context'),
        'window': WINDOW,
        'position_count': POSITION_COUNT,
        'expected_items': EXPECTED_ITEMS,
        'expected_group_rate': EXPECTED_GROUP_RATE,
        'expected_basis': EXPECTED_BASIS,
    }
    if isinstance(extra, dict):
        seal.update(extra)
    _validate_seal_items(seal)
    return seal


def save_seal(seal):
    reference = str(seal.get('ref_period') or '')
    if not reference:
        raise ValueError('seal missing ref_period')
    seals = load_seals()
    if find_seal(seals, reference) is not None:
        raise PublicationRejected('existing-kill1-seal-cannot-be-replaced')
    seals.append(seal)
    _write_seals(seals)
    return seal


def mark_published(reference, published_at=None):
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        raise ValueError('kill1 seal missing before publication')
    if seal.get('publication_state') == 'published':
        return seal
    seal['publication_state'] = 'published'
    seal['published_at'] = (published_at or beijing_now()).isoformat()
    _write_seals(seals)
    return seal


def mark_publication_failed(reference, error):
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        return None
    seal['publication_state'] = 'prepared'
    seal['publication_error'] = str(error)[:120]
    _write_seals(seals)
    return seal


def settle_pending(snapshot=None, now=None):
    """Append settlements for sealed predictions whose target period has drawn."""
    now = now or beijing_now()
    snapshot = snapshot or load_snapshot(persist=False)
    records = {str(record.get('period')): record
               for record in (snapshot.get('records') or [])}
    seals = load_seals()
    settlements = load_settlements()
    settled = {str(item.get('target_period')) for item in settlements}
    changed = 0
    for seal in seals:
        target = str(seal.get('target_period') or next_period(seal.get('ref_period')))
        if not target or target in settled:
            continue
        record = records.get(target)
        if record is None:
            continue
        actual = [int(value) for value in (record.get('numbers') or [])[:4]]
        if len(actual) != 4:
            continue
        raw_positions = seal.get('positions') or {}
        if isinstance(raw_positions, list):
            raw_positions = {str(item.get('key')): item.get('kill')
                             for item in raw_positions if isinstance(item, dict)}
        positions = {}
        for key, _label, index in POSITIONS:
            kill = raw_positions.get(key)
            if kill is None:
                continue
            kill_number = digit(kill)
            positions[key] = {
                'kill': '' if kill_number is None else str(kill_number),
                'actual': str(actual[index]),
                'hit': kill_number is not None and actual[index] != kill_number,
            }
        counted = [key for key in (seal.get('counted') or list(positions))
                   if key in positions]
        group_hit = bool(counted) and all(positions[key]['hit'] for key in counted)
        settlement = {
            'version': 1,
            'rule_version': seal.get('rule_version'),
            'ref_period': str(seal.get('ref_period') or ''),
            'target_period': target,
            'published_at': seal.get('published_at'),
            'publication_state': seal.get('publication_state'),
            'drawn_at': record.get('opentime'),
            'settled_at': now.isoformat(),
            'actual': [str(value) for value in actual],
            'positions': positions,
            'counted': counted,
            'position_count': seal.get('position_count'),
            'expected_items': seal.get('expected_items'),
            'expected_group_rate': seal.get('expected_group_rate'),
            'expected_basis': seal.get('expected_basis'),
            'group_hit': group_hit,
            'verified': False,
        }
        # 历史兼容：v1/v2 旧封存仍含合尾 / 单双形态项，按旧封存口径补齐后计入整组，
        # 保证旧期可正常结算；新封存不再写入这两类字段。
        tail = seal.get('tail') or {}
        tail_key = tail.get('key') if isinstance(tail, dict) else None
        if tail_key in LEGACY_TAIL_INDICES:
            tail_kill = digit(tail.get('kill'))
            actual_tail = sum(actual[index] for index in LEGACY_TAIL_INDICES[tail_key]) % 10
            tail_entry = {
                'key': tail_key,
                'kill': '' if tail_kill is None else str(tail_kill),
                'actual': str(actual_tail),
                'hit': tail_kill is not None and actual_tail != tail_kill,
            }
            settlement['tail'] = tail_entry
            group_hit = group_hit and tail_entry['hit']
        sealed_form = seal.get('form')
        if (isinstance(sealed_form, str) and len(sealed_form) == 4
                and all(char in '单双' for char in sealed_form)):
            actual_form = ''.join(
                '双' if actual[index] % 2 == 0 else '单' for index in range(4))
            form_entry = {
                'type': seal.get('form_type'),
                'form': sealed_form,
                'actual': actual_form,
                'hit': actual_form != sealed_form,
            }
            settlement['form_type'] = seal.get('form_type')
            settlement['form'] = form_entry
            group_hit = group_hit and form_entry['hit']
        # v8：三数共现 3 组 + 二数合 / 三数合（旧封存无这些字段，走上面的历史兼容分支）。
        groups = seal.get('three_avoid')
        if isinstance(groups, list) and groups:
            rows = []
            for group in groups:
                if not isinstance(group, dict):
                    continue
                code = _group_code(group.get('value'))
                if code is None:
                    continue
                rows.append({'key': str(group.get('key') or ''),
                             'label': str(group.get('label') or
                                          THREE_AVOID_LABELS.get(group.get('key'), '')),
                             'value': code,
                             'hit': _group_avoid_hit(actual, code)})
            if len(rows) == THREE_AVOID_ITEMS:
                settlement['three_avoid'] = rows
                group_hit = group_hit and all(row['hit'] for row in rows)
            else:
                group_hit = False
        he2_keep = _validate_he2_keep(seal.get('he2_keep'))
        if he2_keep:
            he2_tails = pair_tails(actual)
            he2_entry = {'keep': he2_keep,
                         'actual': ''.join(str(value) for value in he2_tails),
                         'tails': he2_tails,
                         'hit': any(value in {int(char) for char in he2_keep}
                                    for value in he2_tails)}
            settlement['he2_keep'] = he2_entry
            group_hit = group_hit and he2_entry['hit']
        he3_keep = _validate_he3_keep(seal.get('he3_keep'))
        if he3_keep:
            he3_tails = triple_tails(actual)
            he3_entry = {'keep': he3_keep,
                         'actual': ''.join(str(value) for value in he3_tails),
                         'hit': any(value in {int(char) for char in he3_keep}
                                    for value in he3_tails)}
            settlement['he3_keep'] = he3_entry
            group_hit = group_hit and he3_entry['hit']
        settlement['group_hit'] = bool(group_hit)
        published = parse_time(seal.get('published_at'))
        drawn = parse_time(record.get('opentime'))
        settlement['verified'] = bool(
            seal.get('publication_state') == 'published'
            and published is not None and drawn is not None and published < drawn)
        settlements.append(settlement)
        settled.add(target)
        changed += 1
    if changed:
        _write_settlements(settlements)
    return changed


def _business_key(value):
    moment = parse_time(value)
    if moment is None:
        return None
    return business_start(moment)


def _streak_stats(rows):
    """Streak stats for settlement rows: (win, loss, max_loss)."""
    win = loss = max_loss = 0
    for item in sorted(rows, key=lambda row: str(row.get('target_period'))):
        if item.get('group_hit') is True:
            win += 1
            loss = 0
        else:
            loss += 1
            win = 0
            max_loss = max(max_loss, loss)
    return win, loss, max_loss


def today_stats(now=None):
    """Verified today's aggregate plus pending count; never mixes late records in."""
    now = now or beijing_now()
    start = business_start(now)
    settlements = load_settlements()
    seals = load_seals()
    verified = [item for item in settlements
                if item.get('verified') is True
                and _business_key(item.get('settled_at')) == start]
    seal_rule = {str(seal.get('ref_period')): seal.get('rule_version')
                 for seal in seals if isinstance(seal, dict)}

    def _rule_version(item):
        return item.get('rule_version') or seal_rule.get(str(item.get('ref_period')))

    todays = [item for item in verified if _rule_version(item) == RULE_VERSION]
    legacy = [item for item in verified if _rule_version(item) != RULE_VERSION]
    total = len(todays)
    group_hits = sum(1 for item in todays if item.get('group_hit') is True)
    position_bets = position_hits = 0
    per_position = {key: {'bets': 0, 'hits': 0} for key, _label, _index in POSITIONS}
    for item in todays:
        for key in (item.get('counted') or []):
            entry = (item.get('positions') or {}).get(key)
            if key not in per_position or not isinstance(entry, dict):
                continue
            per_position[key]['bets'] += 1
            position_bets += 1
            if entry.get('hit') is True:
                per_position[key]['hits'] += 1
                position_hits += 1
    avoid_bets = avoid_hits = 0
    merged_bets = merged_hits = 0
    per_avoid = {key: {'label': THREE_AVOID_LABELS[key], 'bets': 0, 'hits': 0}
                 for key in THREE_AVOID_KEYS}
    he2_bets = he2_hits = he3_bets = he3_hits = 0
    for item in todays:
        groups = item.get('three_avoid')
        if isinstance(groups, list) and len(groups) == THREE_AVOID_ITEMS:
            merged_bets += 1
            if all(isinstance(group, dict) and group.get('hit') is True
                   for group in groups):
                merged_hits += 1
        for entry in groups or []:
            if not isinstance(entry, dict):
                continue
            key = str(entry.get('key') or '')
            if key not in per_avoid:
                continue
            per_avoid[key]['bets'] += 1
            avoid_bets += 1
            if entry.get('hit') is True:
                per_avoid[key]['hits'] += 1
                avoid_hits += 1
        he2_entry = item.get('he2_keep')
        if isinstance(he2_entry, dict) and he2_entry:
            he2_bets += 1
            if he2_entry.get('hit') is True:
                he2_hits += 1
        he3_entry = item.get('he3_keep')
        if isinstance(he3_entry, dict) and he3_entry:
            he3_bets += 1
            if he3_entry.get('hit') is True:
                he3_hits += 1
    win, loss, max_loss = _streak_stats(todays)
    latest = latest_period()
    settled_targets = {str(item.get('target_period')) for item in settlements}
    pending = 0
    for seal in seals:
        if seal.get('publication_state') != 'published':
            continue
        target = str(seal.get('target_period') or '')
        if target and target > latest and target not in settled_targets:
            pending += 1
    late = sum(1 for item in settlements
               if item.get('verified') is False
               and _business_key(item.get('settled_at')) == start)
    expected = {'records': 0, 'group_rate': None, 'group_expected': 0.0,
                'items_min': None, 'items_max': None, 'basis': EXPECTED_BASIS}
    expected_sum = 0.0
    expected_items = []
    for item in todays:
        rate = item.get('expected_group_rate')
        if (not isinstance(rate, (int, float)) or isinstance(rate, bool)
                or not 0 < float(rate) < 1):
            rate = EXPECTED_GROUP_RATE
        expected_sum += float(rate)
        count = item.get('expected_items')
        try:
            count = int(count) if count is not None else EXPECTED_ITEMS
        except (TypeError, ValueError):
            count = EXPECTED_ITEMS
        expected_items.append(count)
        expected['records'] += 1
    if expected['records']:
        expected['group_rate'] = expected_sum / expected['records']
        expected['group_expected'] = expected_sum
        expected['items_min'] = min(expected_items)
        expected['items_max'] = max(expected_items)
    return {
        'business_start': start.isoformat(),
        'total': total,
        'group_hits': group_hits,
        'group_rate': (group_hits / total) if total else None,
        'positions': {'bets': position_bets, 'hits': position_hits,
                      'rate': (position_hits / position_bets) if position_bets else None},
        'per_position': per_position,
        'three_avoid': {'merged': {'bets': merged_bets, 'hits': merged_hits,
                                  'rate': (merged_hits / merged_bets) if merged_bets else None},
                        'per_group': per_avoid,
                        'bets': avoid_bets, 'hits': avoid_hits,
                        'rate': (avoid_hits / avoid_bets) if avoid_bets else None},
        'he2_keep': {'bets': he2_bets, 'hits': he2_hits,
                     'rate': (he2_hits / he2_bets) if he2_bets else None},
        'he3_keep': {'bets': he3_bets, 'hits': he3_hits,
                     'rate': (he3_hits / he3_bets) if he3_bets else None},
        'current_win_streak': win,
        'current_loss_streak': loss,
        'max_loss_streak': max_loss,
        'pending': pending,
        'late': late,
        'expected': expected,
        'legacy': {'total': len(legacy),
                   'group_hits': sum(1 for item in legacy
                                     if item.get('group_hit') is True)},
        'latest_period': latest,
    }


def version_info():
    """Current rule revision coverage: first/last published ref and seal count."""
    seals = load_seals()
    rule = RULE_VERSION
    refs = [str(seal.get('ref_period')) for seal in seals
            if isinstance(seal, dict) and seal.get('rule_version') == rule
            and seal.get('publication_state') == 'published' and seal.get('ref_period')]
    return {'rule_version': rule, 'count': len(refs),
            'first_ref': refs[0] if refs else None,
            'last_ref': refs[-1] if refs else None}


def current_summary(now=None):
    """The recommendation for the latest drawn reference period, if published."""
    now = now or beijing_now()
    latest = latest_period()
    seals = load_seals()
    settlements = load_settlements()
    by_ref = {str(item.get('ref_period')): item for item in seals}
    seal = by_ref.get(latest) if latest else None
    by_target = {str(item.get('target_period')): item for item in settlements}
    settlement = by_target.get(str(seal.get('target_period'))) if seal else None
    previous = None
    if seal is None and seals:
        previous = seals[-1]
    return {'latest_period': latest, 'seal': seal, 'settlement': settlement,
            'previous': previous, 'stats': today_stats(now)}


def deadline_for(reference):
    target = next_period(reference)
    slot = period_slot(target)
    if slot is None:
        raise ValueError('invalid-kill1-target-period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target

