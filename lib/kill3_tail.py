"""AI 三位置杀码 + 一合尾建议（替代原规则版三位置杀码）。

口径（面板、封存与统计都按此写死）：
- 由 DeepSeek 从 千/百/十/个 四个位置中选 3 个位置，每个位置排除 1 个数字（杀码），
  剩余的 1 个位置为「放弃」；
- 3 个被杀位置全部展示，其中 AI 用 display_only 指定 2 个计入整组与攻击语（counted），
  第 3 个仅展示、不计入整组与攻击语；
- 从五种合尾（千百十个 / 千百十 / 千百个 / 千十个 / 百十个）中选 1 种，排除 1 个和尾数字；
- 尾码与位置/弃位均由 DeepSeek 自主决定（v10 起）：无自动沿用、无连用期数上限、无强制换码，
  模型每期给出的位置杀码、放弃位置、合尾直接封存（不再写 tail_policy 沿用追溯，仅保留旧字段兼容）；
- 另输出两项反向形态（与八项独立框架同口径）：
  四位单双形态 reverse_parity、四位大小形态 reverse_size；
  被杀的 3 个位置取杀码单双/大小的反面，放弃位置的字符由 AI 自选；
- 位置命中 = 该位置实际开奖号 != 杀码；
- 合尾命中 = 该合尾实际和值个位 != 排除码；
- 反向形态命中 = 目标期实际四位形态 != 封存形态（反向排除项）；
- 整组命中 = 2 个计入位置 + 1 个合尾 + 2 项反向形态同时命中（共 5 项）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 位置杀码与放弃位置不再强制轮换：同一位置杀码/放弃位置可连续使用，由模型自主决定；
- 尾码与位置均无「不得与上一期同类 / 连用满 3 期必换」限制，模型完全自主；
- 只使用前 4 球（千/百/十/个），第 5 球不参与。

数据落点：
- data/kill3_tail/seals.json        每期封存（同一参考期不可替换）
- data/kill3_tail/settlements.json  开奖后结算（只追加）
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
DATA_DIR = ROOT / 'data' / 'kill3_tail'
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

TAILS = {
    'tail_all': {'label': '千百十个', 'indices': (0, 1, 2, 3)},
    'tail_qbs': {'label': '千百十', 'indices': (0, 1, 2)},
    'tail_qbg': {'label': '千百个', 'indices': (0, 1, 3)},
    'tail_qsg': {'label': '千十个', 'indices': (0, 2, 3)},
    'tail_bsg': {'label': '百十个', 'indices': (1, 2, 3)},
}
TAIL_KEYS = tuple(TAILS)
REVERSE_KEYS = ('reverse_parity', 'reverse_size')
REVERSE_LABELS = {'reverse_parity': '单双反向形态', 'reverse_size': '大小反向形态'}

RULE_VERSION = 'kill3-tail-v10-two-counted-free'
MAX_CONSECUTIVE_PERIODS = 3
COUNTED_POSITIONS = 2
TAIL_HOLD_MAX = 3
TAIL_POLICY = 'hold3-until-miss'

RETRY_INSTRUCTION = (
    '上次响应未通过本地校验（{error}）。请严格按格式重新输出完整 JSON：'
    'positions 恰好 3 个键，键名只能从 q/b/s/g 中选择；每个值为 0 至 9 的整数；'
    '3 个位置全部展示，其中恰好 2 个计入整组与攻击语，display_only 标明 1 个仅展示位；'
    'tail 为 {{"key": 五种之一, "kill": 0 至 9 的整数}}；'
    'reverse_parity 为四个单/双字符，reverse_size 为四个大/小字符，'
    '3 个被杀位置都必须取杀码形态的反面'
    '（杀码为单则写双、为双则写单；杀码为大则写小、为小则写大），'
    '放弃位置的字符自选；合尾由你自主决定并直接封存；'
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
        raise ValueError('kill3_tail seals records must be a list')
    return [item for item in records if isinstance(item, dict)]


def load_settlements():
    document = _load_json(SETTLEMENTS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill3_tail settlements records must be a list')
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


def tail_value(row, key):
    return sum(int(row[index]) for index in TAILS[key]['indices']) % 10


def tail_actual(numbers, key):
    return sum(int(numbers[index]) for index in TAILS[key]['indices']) % 10


def reverse_attributes(digit):
    """杀码的单双/大小反面，与八项独立框架同口径。"""
    if type(digit) is not int or not 0 <= digit <= 9:
        raise ValueError('reverse attribute digit must be 0..9')
    return ('双' if digit % 2 == 1 else '单'), ('小' if digit >= 5 else '大')


def pattern_of(digits, kind):
    """四位实际形态：parity -> 单/双，size -> 大/小。"""
    values = [int(value) for value in digits]
    if len(values) != 4:
        raise ValueError('pattern requires four digits')
    if kind == 'parity':
        return ''.join('双' if value % 2 == 0 else '单' for value in values)
    if kind == 'size':
        return ''.join('大' if value >= 5 else '小' for value in values)
    raise ValueError('unknown pattern kind')


ATTACK_ORDER = '1234567890'
ATTACK_POSITION_LABELS = {'q': '千', 'b': '百', 's': '十', 'g': '个'}
ATTACK_TAIL_LABELS = {'tail_all': '千百十个合', 'tail_qbs': '千百十合',
                      'tail_qbg': '千百个合', 'tail_qsg': '千十个合',
                      'tail_bsg': '百十个合'}


def build_attack(seal):
    """与八项独立框架同格式的攻击语。

    位置保留码（被杀位置为去掉杀码的 9 码，未选位置保留 10 码）→ 四定 →
    合尾保留码（去掉排除码）→ 除<四位单双形态>除<四位大小形态>。
    """
    if not isinstance(seal, dict):
        raise ValueError('attack requires a sealed prediction')
    order = ATTACK_ORDER

    def remaining(value):
        text = str(value).strip()
        if len(text) != 1 or text not in order:
            raise ValueError('attack kill digit must be 0..9')
        return ''.join(char for char in order if char != text)

    positions = seal.get('positions') or {}
    display_only = seal.get('display_only')
    parts = []
    for key, label in (('q', '千'), ('b', '百'), ('s', '十'), ('g', '个')):
        killed = key in positions and key != display_only
        parts.append(ATTACK_POSITION_LABELS[key] +
                     (remaining(positions[key]) if killed else order))
    parts.append('四定')
    tail = seal.get('tail') or {}
    tail_label = ATTACK_TAIL_LABELS.get(tail.get('key'))
    if tail_label:
        parts.append(tail_label + remaining(tail.get('kill')))
    text = ''.join(parts)
    parity, size = seal.get('reverse_parity'), seal.get('reverse_size')
    if parity and size:
        text += '除' + str(parity) + '除' + str(size)
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


def _tail_key(value):
    text = str(value).strip()
    mapping = {key: key for key in TAIL_KEYS}
    mapping.update({TAILS[key]['label']: key for key in TAIL_KEYS})
    mapping.update({'全四位': 'tail_all', '全部四位': 'tail_all',
                    '前三位': 'tail_qbs', '后三位': 'tail_bsg',
                    '千百十个合尾': 'tail_all', '千百十合尾': 'tail_qbs',
                    '千百个合尾': 'tail_qbg', '千十个合尾': 'tail_qsg',
                    '百十个合尾': 'tail_bsg'})
    key = mapping.get(text)
    if key is None:
        raise ValueError('合尾键必须是五种合尾之一')
    return key


def normalize_response(response):
    """Strict local validation; never pad, default or invent anything."""
    if not isinstance(response, dict):
        raise ValueError('响应必须是 JSON 对象')
    positions = response.get('positions')
    if not isinstance(positions, dict):
        raise ValueError('positions 必须是对象')
    kills = {}
    for raw_key, raw_value in positions.items():
        key = _position_key(raw_key)
        if key in kills:
            raise ValueError('positions 出现重复位置')
        number = digit(raw_value)
        if number is None:
            raise ValueError('位置杀码必须是 0 至 9 的整数')
        kills[key] = str(number)
    if len(kills) != 3:
        raise ValueError('必须恰好选择 3 个位置')
    dropped = [key for key, _label, _index in POSITIONS if key not in kills]
    if len(dropped) != 1:
        raise ValueError('必须恰好放弃 1 个位置')
    raw_display = response.get('display_only')
    if raw_display is None:
        raise ValueError('display_only 必须是所选 3 个位置之一（缺失）')
    display_only = _position_key(raw_display)
    if display_only not in kills:
        raise ValueError('display_only 必须是所选 3 个位置之一')
    counted = [key for key, _label, _index in POSITIONS
               if key in kills and key != display_only]
    if len(counted) != COUNTED_POSITIONS:
        raise ValueError(f'必须恰好 {COUNTED_POSITIONS} 个位置计入整组')
    tail = response.get('tail')
    if not isinstance(tail, dict):
        raise ValueError('tail 必须是对象')
    tail_key = _tail_key(tail.get('key'))
    number = digit(tail.get('kill'))
    if number is None:
        raise ValueError('合尾排除码必须是 0 至 9 的整数')
    reason = str(response.get('reason', ''))
    try:
        reason.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('reason 必须是合法 UTF-8 文本') from None
    patterns = {}
    for field, palette, kind_label in (('reverse_parity', '单双', '单/双'),
                                       ('reverse_size', '大小', '大/小')):
        value = response.get(field)
        if (not isinstance(value, str) or len(value) != 4
                or any(char not in palette for char in value)):
            raise ValueError(f'{field} 必须是恰好 4 个{"单/双" if field == "reverse_parity" else "大/小"}字符')
        patterns[field] = value
    for position_key, kill_text in kills.items():
        index = POSITION_INDEX[position_key]
        parity_label, size_label = reverse_attributes(int(kill_text))
        if patterns['reverse_parity'][index] != parity_label:
            raise ValueError(f'单双反向形态在{position_key}位置必须取杀码形态的反面')
        if patterns['reverse_size'][index] != size_label:
            raise ValueError(f'大小反向形态在{position_key}位置必须取杀码形态的反面')
    return {
        'positions': kills,
        'display_only': display_only,
        'counted': counted,
        'dropped': dropped[0],
        'tail': {'key': tail_key, 'label': TAILS[tail_key]['label'], 'kill': str(number)},
        'reverse_parity': patterns['reverse_parity'],
        'reverse_size': patterns['reverse_size'],
        'reason': reason[:200],
    }


def _history_rows(snapshot=None):
    snapshot = snapshot or load_snapshot(persist=False)
    records = snapshot.get('records') or []
    rows = [list(record.get('numbers') or [])[:4] for record in records]
    rows = [row for row in rows if len(row) == 4]
    if len(rows) < WINDOW + 1:
        raise ValueError('insufficient-kill3-history')
    return snapshot, rows


def _cold_line(ranks, limit=3):
    return '、'.join(f'{number}(出{count}次/遗漏{omission}期)'
                     for number, count, omission in ranks[:limit])


def build_prompt(reference, target, rows, *, previous_tail=None,
                 position_limit=None, panel_snapshot=None):
    """Frozen prompt and its audit metadata; rows are the last WINDOW draws."""
    window = rows[-WINDOW:]
    history = ', '.join(''.join(str(int(value)) for value in row) for row in window)
    position_lines = []
    for key, label, index in POSITIONS:
        ranks = rank_digits([row[index] for row in window])
        position_lines.append(f'· {label}：{_cold_line(ranks)}')
    tail_rows = []
    for key in TAIL_KEYS:
        values = [tail_value(row, key) for row in window]
        ranks = rank_digits(values)
        kill = ranks[0][0]
        hits50 = sum(1 for value in values if value != kill)
        recent = values[-30:]
        hits30 = sum(1 for value in recent if value != kill)
        tail_rows.append((key, TAILS[key]['label'], kill, ranks[0][1], ranks[0][2],
                          hits50 / len(values), hits30 / len(recent)))
    tail_rows.sort(key=lambda row: (-row[5], -row[6], -row[4]))
    tail_lines = [
        f'· {label}：候选 {kill}（近50期出{count}次/当前遗漏{omission}期），'
        f'近50期该尾候选杀号命中 {hit50:.2%}，近30期 {hit30:.2%}'
        for _key, label, kill, count, omission, hit50, hit30 in tail_rows]
    if isinstance(previous_tail, dict) and previous_tail.get('key') in TAILS:
        prev_label = TAILS[previous_tail['key']]['label']
        prev_kill = str(previous_tail.get('kill', '-'))
        restriction = (f'上一期合尾：{prev_label} 杀 {prev_kill}。'
                       '合尾由你自主决定并直接封存，无沿用/换码限制。'
                       '（上一期尾码仅作历史参考，不影响本期选择。）')
    else:
        restriction = '本期无上一期尾码，合尾由你自主决定并直接封存。'
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
        '【近50期各位置冷热（出现次数少、当前遗漏长优先；仅历史统计，不代表未来概率）】\n'
        + '\n'.join(position_lines) + '\n\n'
        '【五种合尾候选对比（合尾 = 对应位置数字之和的个位；'
        '按该尾候选码近50期真实杀号命中率排序，顺序不代表推荐）】\n'
        + '\n'.join(tail_lines) + '\n\n'
        '【合尾决策】\n' + restriction + '\n\n'
        '【任务】\n'
        '1. 从 千/百/十/个 四个位置中挑 3 个（必须放弃 1 个），每个选中位置排除 1 个数字（0-9）；'
        '其中恰好 2 个位置计入整组（攻击语只按这 2 个位置排除），'
        '第 3 个位置用 display_only 标明，只展示、不计入整组与攻击语；\n'
        '2. 从上述 5 种合尾中挑 1 种并排除 1 个和尾数字（0-9），由你自主决定并直接封存；\n'
        '3. 给出两项四位反向排除形态：reverse_parity 为按千百十个顺序的四位单双形态，'
        'reverse_size 为四位大小形态；被杀的 3 个位置（含 display_only 仅展示位置）'
        '都必须取杀码形态的反面'
        '（杀码为单则写双、为双则写单；杀码为大则写小、为小则写大），放弃位置的字符自选；\n'
        '4. 命中口径：位置 = 目标期该位置实际开奖号不等于排除码（只算 2 个计入位置）；'
        '合尾 = 目标期该合尾实际和值个位不等于排除码；'
        '反向形态 = 目标期实际四位形态不等于这两个封存形态（反向排除项）；'
        '以上 5 项（2 个计入位置 + 1 个合尾 + 2 项形态）同时命中才算整组命中；\n'
        '5. 冷热、遗漏、重码只是历史特征，不能据此断言未来概率；不要为换而换，不得声称保证命中；\n'
        '6. 只能使用上面给出的历史与面板全量上下文；面板数据只作参考，不得照抄或为避开同号强行换码，也不得声称保证命中。\n\n'
        '只输出严格 JSON（不要输出分析过程、代码块或额外文字）：\n'
        '{"positions":{"q":<0-9整数>,"b":<0-9整数>,"s":<0-9整数>},'
        '"display_only":"q|b|s|g",'
        '"tail":{"key":"tail_all|tail_qbs|tail_qbg|tail_qsg|tail_bsg","kill":<0-9整数>},'
        '"reverse_parity":"<四个单/双字符>","reverse_size":"<四个大/小字符>",'
        '"reason":"40字以内"}\n'
        '注意：positions 必须恰好 3 个键（从 q/b/s/g 中选），键名与数字都必须合法；'
        'display_only 必须是 positions 的 3 个键之一；'
        '两个形态字符串都必须是 4 个字符。'
    )
    if panel_section:
        _marker = prompt.find('【任务】')
        if _marker != -1:
            prompt = prompt[:_marker] + panel_section + prompt[_marker:]
    audit = {
        'version': 'kill3-tail-prompt-v10-two-counted-free',
        'reference': reference,
        'target': target,
        'window': WINDOW,
        'history_sha256': sha256(history.encode('utf-8')).hexdigest(),
        'rows': [[str(int(value)) for value in row] for row in window],
        'tail_comparison': [{'key': key, 'label': label, 'candidate': str(kill),
                             'hit50': round(hit50, 6), 'hit30': round(hit30, 6)}
                            for key, label, kill, _count, _omission, hit50, hit30 in tail_rows],
        'previous_tail': previous_tail,
        'tail_policy': None,
        'tail_hold_max': None,
        'position_limit': None,
        'panel_context_sha256': panel_context_sha,
        'panel_context': panel_snapshot if isinstance(panel_snapshot, dict) else None,
    }
    return prompt, audit


def validate_tail_choice(prediction, blocked_tail_key=None):
    """本期合尾不得与上一期相同（从上期未使用的四种中选择）。"""
    if not blocked_tail_key:
        return prediction
    chosen = (prediction.get('tail') or {}).get('key')
    if chosen == blocked_tail_key:
        label = TAILS[blocked_tail_key]['label']
        raise ValueError(f'本期合尾不得与上一期相同（上一期为{label}），请从其余四种合尾中选')
    return prediction


def _trailing_digit_streaks(seals):
    """Same-kill-digit streak per position; seals abandoning the position are skipped.

    A gap must not reset the counter, otherwise the AI can dodge the three-period
    limit by abandoning the position for one period and reusing the same digit.
    """
    streaks = {}
    for key, _label, _index in POSITIONS:
        digit = None
        count = 0
        for seal in reversed(seals):
            positions = seal.get('positions') if isinstance(seal, dict) else None
            if not isinstance(positions, dict):
                break
            if key not in positions:
                continue
            value = str(positions.get(key))
            if digit is None:
                digit, count = value, 1
            elif value == digit:
                count += 1
            else:
                break
        streaks[key] = (digit, count)
    return streaks


def _trailing_drop_streak(seals):
    """Trailing consecutive seals that abandoned the same position."""
    drop = None
    count = 0
    for seal in reversed(seals):
        if not isinstance(seal, dict):
            break
        value = seal.get('dropped')
        if value is None:
            break
        value = str(value)
        if drop is None:
            drop, count = value, 1
        elif value == drop:
            count += 1
        else:
            break
    return drop, count


def position_restrictions(seals):
    """Consecutive-use limits for position kills and the abandoned position."""
    seals = [item for item in (seals or []) if isinstance(item, dict)]
    digit_streaks = _trailing_digit_streaks(seals)
    drop, drop_count = _trailing_drop_streak(seals)
    previous = seals[-1] if seals else {}
    blocked_digits = {}
    for key, (digit, count) in digit_streaks.items():
        if digit is not None and count >= MAX_CONSECUTIVE_PERIODS:
            blocked_digits[key] = digit
    return {
        'max_consecutive': MAX_CONSECUTIVE_PERIODS,
        'previous_ref_period': previous.get('ref_period'),
        'previous_target_period': previous.get('target_period'),
        'previous_positions': dict(previous.get('positions') or {}),
        'previous_dropped': previous.get('dropped'),
        'digit_streaks': {key: {'digit': digit, 'periods': count}
                          for key, (digit, count) in digit_streaks.items() if digit is not None},
        'dropped_streak': ({'dropped': drop, 'periods': drop_count}
                           if drop is not None else None),
        'blocked_digits': blocked_digits,
        'blocked_drop': (drop if (drop is not None and drop_count >= MAX_CONSECUTIVE_PERIODS)
                        else None),
    }


def validate_position_choice(prediction, position_limit=None):
    """Reject over-limit kill codes or a repeatedly abandoned position."""
    if not isinstance(position_limit, dict):
        return prediction
    limit = position_limit.get('max_consecutive') or MAX_CONSECUTIVE_PERIODS
    positions = prediction.get('positions') or {}
    for key, digit in (position_limit.get('blocked_digits') or {}).items():
        if key in positions and str(positions[key]) == str(digit):
            label = POSITION_LABELS.get(key, key)
            raise ValueError(f'{label}已连续{limit}期使用杀码{digit}，本期必须换码')
    blocked_drop = position_limit.get('blocked_drop')
    if blocked_drop and prediction.get('dropped') == blocked_drop:
        label = POSITION_LABELS.get(blocked_drop, blocked_drop)
        raise ValueError(f'放弃位置已连续{limit}期放弃{label}，本期必须换放弃位置')
    return prediction


def _trailing_tail_streak(seals):
    """Trailing consecutive seals sharing one tail key+kill."""
    key = kill = None
    count = 0
    for seal in reversed(seals):
        tail = seal.get('tail') if isinstance(seal, dict) else None
        if not isinstance(tail, dict):
            break
        tail_key = tail.get('key')
        tail_kill = tail.get('kill')
        if tail_key not in TAILS or tail_kill is None:
            break
        if key is None:
            key, kill, count = tail_key, str(tail_kill), 1
        elif tail_key == key and str(tail_kill) == kill:
            count += 1
        else:
            break
    previous = None
    if key is not None:
        previous = {'key': key, 'label': TAILS[key]['label'], 'kill': kill}
    return previous, count


def tail_hold_state(seals, settlements):
    """State used by the tail-hold policy; read-only."""
    seals = [item for item in (seals or []) if isinstance(item, dict)]
    settlements = [item for item in (settlements or []) if isinstance(item, dict)]
    previous, streak = _trailing_tail_streak(seals)
    previous_ref = str(seals[-1].get('ref_period') or '') if seals else ''
    previous_target = str(seals[-1].get('target_period') or '') if seals else ''
    previous_hit = None
    if previous_target:
        for item in reversed(settlements):
            if str(item.get('target_period') or '') == previous_target:
                previous_hit = (item.get('tail') or {}).get('hit') is True
                break
    return {'policy': TAIL_POLICY, 'hold_max': TAIL_HOLD_MAX,
            'previous_tail': previous, 'previous_streak': streak,
            'previous_hit': previous_hit,
            'previous_ref_period': previous_ref or None,
            'previous_target_period': previous_target or None}


def apply_tail_policy(prediction, seals, settlements):
    """Return a copy whose sealed tail follows the stability policy."""
    if not isinstance(prediction, dict):
        raise ValueError('prediction must be an object')
    state = tail_hold_state(seals, settlements)
    model_tail = dict(prediction.get('tail') or {})
    previous = state.get('previous_tail')
    reused = False
    if (previous and state.get('previous_hit') is True
            and int(state.get('previous_streak') or 0) < TAIL_HOLD_MAX):
        final = {'key': previous['key'], 'label': TAILS[previous['key']]['label'],
                 'kill': str(previous['kill'])}
        reused = True
        note = '沿用上期尾码（第%s期，上期命中）' % state.get('previous_streak')
    elif previous and state.get('previous_hit') is True:
        final = model_tail
        note = '换码（自动沿用已满%s期，改由模型重选）' % TAIL_HOLD_MAX
        if model_tail.get('key') == previous.get('key') and str(model_tail.get('kill')) == str(previous.get('kill')):
            note += '（模型本次仍选同码）'
    elif previous and state.get('previous_hit') is False:
        final = model_tail
        note = '换码（上期未命中）'
    elif previous:
        final = model_tail
        note = '换码（上一期尚未结算，无法沿用）'
    else:
        final = model_tail
        note = '新选（无上一期尾码）'
    result = dict(prediction)
    result['tail'] = final
    result['tail_policy'] = {
        'policy': TAIL_POLICY, 'hold_max': TAIL_HOLD_MAX,
        'model_tail': {'key': model_tail.get('key'), 'kill': model_tail.get('kill')},
        'reused_previous': reused, 'note': note,
        'previous_tail': previous,
        'previous_streak': state.get('previous_streak'),
        'previous_hit': state.get('previous_hit'),
        'previous_ref_period': state.get('previous_ref_period'),
    }
    if reused:
        model_label = TAILS.get(model_tail.get('key'), {}).get('label', str(model_tail.get('key')))
        original = str(result.get('reason') or '')
        result['reason'] = ('尾码按稳定化沿用上期 %s 杀%s（AI 候选 %s 杀%s）。模型原话：%s' % (
            TAILS[previous['key']]['label'], previous['kill'], model_label,
            model_tail.get('kill'), original))[:200]
    return result


def resolve_model():
    for name in ('PENTA5_MODEL_KILL3', 'PENTA5_MODEL_KILLER', 'PENTA5_DEEPSEEK_MODEL'):
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
        'request_policy_version': 'kill3-high-12k-deadline-v1',
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
            raise PublicationRejected('insufficient-time-for-kill3-request')
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
            log(f'kill3 response validated; ref={reference}; attempt={attempt}; publication pending.')
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
            log(f'kill3 attempt {attempt}/3 rejected; error_type={last_error}{status_note}; not published.')
            if status in NONRETRYABLE_HTTP_STATUSES:
                log(f'kill3 nonretryable provider HTTP {status}; stop retries; not published.')
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
    raise RuntimeError(f'kill3 inference failed after {attempts_used} attempt(s): '
                       f'{last_error}{status_note}; not published')


def create_seal(reference, target, reference_open_time, prediction, prompt, audit,
                generated_at, response_meta, extra=None):
    seal = {
        'version': 1,
        'rule_version': RULE_VERSION,
        'ref_period': str(reference),
        'target_period': str(target),
        'reference_open_time': reference_open_time,
        'generated_at': generated_at.isoformat(),
        'published_at': None,
        'publication_state': 'prepared',
        'positions': prediction['positions'],
        'display_only': prediction.get('display_only'),
        'counted': prediction.get('counted') or [],
        'dropped': prediction['dropped'],
        'tail': prediction['tail'],
        'tail_policy': prediction.get('tail_policy'),
        'reverse_parity': prediction['reverse_parity'],
        'reverse_size': prediction['reverse_size'],
        'reason': prediction['reason'],
        'model': resolve_model(),
        'model_label': model_label(),
        'model_metadata': response_meta,
        'prompt_sha256': sha256(prompt.encode('utf-8')).hexdigest(),
        'input_sha256': audit.get('history_sha256'),
        'panel_context_sha256': audit.get('panel_context_sha256'),
        'panel_context': audit.get('panel_context'),
        'window': WINDOW,
    }
    if isinstance(extra, dict):
        seal.update(extra)
    return seal


def save_seal(seal):
    reference = str(seal.get('ref_period') or '')
    if not reference:
        raise ValueError('seal missing ref_period')
    seals = load_seals()
    if find_seal(seals, reference) is not None:
        raise PublicationRejected('existing-kill3-seal-cannot-be-replaced')
    seals.append(seal)
    _write_seals(seals)
    return seal


def mark_published(reference, published_at=None):
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        raise ValueError('kill3 seal missing before publication')
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
    records = {str(record.get('period')): record for record in (snapshot.get('records') or [])}
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
        positions = {}
        for key, _label, index in POSITIONS:
            kill = seal.get('positions', {}).get(key)
            if kill is None:
                continue
            kill_number = digit(kill)
            positions[key] = {'kill': str(kill_number), 'actual': str(actual[index]),
                              'hit': kill_number is not None and actual[index] != kill_number}
        tail = seal.get('tail') or {}
        tail_key = tail.get('key')
        tail_kill = digit(tail.get('kill'))
        actual_tail = tail_actual(actual, tail_key) if tail_key in TAILS else None
        tail_entry = {'key': tail_key,
                      'label': TAILS[tail_key]['label'] if tail_key in TAILS else '',
                      'kill': str(tail_kill), 'actual': str(actual_tail),
                      'hit': tail_kill is not None and actual_tail is not None and actual_tail != tail_kill}
        patterns = {}
        for field, kind, palette in (('reverse_parity', 'parity', '单双'),
                                     ('reverse_size', 'size', '大小')):
            sealed_form = seal.get(field)
            actual_form = pattern_of(actual, kind)
            hit = (isinstance(sealed_form, str) and len(sealed_form) == 4
                   and all(char in palette for char in sealed_form)
                   and actual_form != sealed_form)
            patterns[field] = {'form': sealed_form, 'actual': actual_form, 'hit': bool(hit)}
        counted = seal.get('counted') or [key for key in positions
                                          if key != seal.get('display_only')]
        group_hit = (all(positions[key]['hit'] for key in counted if key in positions)
                     and tail_entry['hit']
                     and all(item['hit'] for item in patterns.values()))
        published = parse_time(seal.get('published_at'))
        drawn = parse_time(record.get('opentime'))
        verified = (seal.get('publication_state') == 'published' and published is not None
                    and drawn is not None and published < drawn)
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
            'display_only': seal.get('display_only'),
            'counted': list(counted),
            'dropped': seal.get('dropped'),
            'tail': tail_entry,
            'patterns': patterns,
            'group_hit': bool(group_hit),
            'verified': bool(verified),
        }
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
    tail_hits = sum(1 for item in todays if (item.get('tail') or {}).get('hit') is True)
    pattern_stats = {}
    for field in REVERSE_KEYS:
        hits = sum(1 for item in todays
                   if (item.get('patterns') or {}).get(field, {}).get('hit') is True)
        pattern_stats[field] = {'hits': hits, 'bets': total,
                                'rate': (hits / total) if total else None}
    win = loss = max_loss = four_loss = 0
    for item in sorted(todays, key=lambda row: str(row.get('target_period'))):
        if item.get('group_hit') is True:
            win += 1
            loss = 0
        else:
            loss += 1
            win = 0
            max_loss = max(max_loss, loss)
            if loss % 4 == 0:
                four_loss += 1
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
               if item.get('verified') is False and _business_key(item.get('settled_at')) == start)
    return {
        'business_start': start.isoformat(),
        'total': total,
        'group_hits': group_hits,
        'group_rate': (group_hits / total) if total else None,
        'positions': {'bets': position_bets, 'hits': position_hits,
                      'rate': (position_hits / position_bets) if position_bets else None},
        'per_position': per_position,
        'tail': {'hits': tail_hits, 'bets': total,
                 'rate': (tail_hits / total) if total else None},
        'patterns': pattern_stats,
        'current_win_streak': win,
        'current_loss_streak': loss,
        'max_loss_streak': max_loss,
        'four_loss_cycles': four_loss,
        'pending': pending,
        'late': late,
        'legacy': {'total': len(legacy),
                   'group_hits': sum(1 for item in legacy if item.get('group_hit') is True)},
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
        raise ValueError('invalid-kill3-target-period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target

