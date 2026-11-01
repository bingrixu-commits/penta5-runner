"""AI 二位置杀码 + 一合尾 + 二数合/三数合建议（独立框架，数据独立封存/统计）。

口径（面板、封存与统计都按此写死）：
- 由 DeepSeek 从 千/百/十/个 四个位置中选恰好 2 个位置，每个位置排除 1 个数字（杀码），
  其余 2 个位置不参与定位项（攻击语保留全部 10 码）；
- 2 个被杀位置全部计入整组与攻击语（counted）；
- 从五种合尾（千百十个 / 千百十 / 千百个 / 千十个 / 百十个）中选 1 种，排除 1 个和尾数字；
- 尾码稳定化 hold3-until-miss：上期尾码命中且自动沿用未满 3 期时沿用上期尾码；失手或自动沿用
  满 3 期后由模型本期重选（重选可与上期同码）；封存写 tail_policy 追溯；
- 二数合：he2_keep 从 02468 恰好保留 4 个不同数字；前 4 球任取 2 位 6 种组合和值个位，
  任一落入保留集即命中；
- 三数合：he3_keep 从 0-9 恰好保留 5 个不同数字且 2 奇 3 偶或 3 奇 2 偶；任取 3 位 4 种组合
  和值个位，任一落入保留集即命中；
- 另输出两项反向形态（与七项独立框架同口径）：四位单双形态 reverse_parity、四位大小形态
  reverse_size；被杀的 2 个位置取杀码单双/大小的反面，未选 2 个位置的字符由 AI 自选；
- 位置命中 = 该位置实际开奖号 != 杀码；合尾命中 = 该合尾实际和值个位 != 排除码；
- 二数合/三数合命中 = 任一组合和值个位落入保留集；
- 反向形态命中 = 目标期实际四位形态 != 封存形态（反向排除项）；
- 整组命中 = 2 个位置 + 1 个合尾 + 二数合 + 三数合 + 2 项反向形态同时命中（共 7 项）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 同一位置杀码最多连用 3 期（中间未选该位置不清零），满后下一期必须更换；
- 未选位置最多连用 3 期（连续 3 期未选中该位置杀码后，本期必须选中该位置）；
- 只使用前 4 球（千/百/十/个），第 5 球不参与。

数据落点：
- data/kill2_tail/seals.json        每期封存（同一参考期不可替换）
- data/kill2_tail/settlements.json  开奖后结算（只追加）
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
DATA_DIR = ROOT / 'data' / 'kill2_tail'
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

RULE_VERSION = 'kill2-tail-v2-two-positions-he2he3-rot3'
MAX_CONSECUTIVE_PERIODS = 3
COUNTED_POSITIONS = 2
TAIL_HOLD_MAX = 3
TAIL_POLICY = 'hold3-until-miss'
HE2_SOURCE_DIGITS = '02468'
HE2_KEEP_COUNT = 4
HE3_KEEP_COUNT = 5
HE2_PAIRS = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
HE3_TRIPLES = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))

RETRY_INSTRUCTION = (
    '上次响应未通过本地校验（{error}）。请严格按格式重新输出完整 JSON：'
    'positions 恰好 2 个键，键名只能从 q/b/s/g 中选择；每个值为 0 至 9 的整数；'
    '2 个位置全部计入整组；'
    'tail 为 {{"key": 五种之一, "kill": 0 至 9 的整数}}；'
    'he2_keep 为 02468 中 4 个不同数字组成的字符串；'
    'he3_keep 为 0123456789 中 5 个不同数字组成的字符串且必须 2 奇 3 偶或 3 奇 2 偶；'
    'reverse_parity 为四个单/双字符，reverse_size 为四个大/小字符，'
    '2 个被杀位置都必须取杀码形态的反面'
    '（杀码为单则写双、为双则写单；杀码为大则写小、为小则写大），'
    '未选 2 个位置的字符自选；合尾为换码候选（系统会按稳定化规则决定最终封存尾码，可能沿用上期尾码）；'
    '被限制的位置不得继续使用该位置连用满3期的杀码；'
    '连续满3期未选中的位置本期必须选中该位置杀码；'
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
        raise ValueError('kill2_tail seals records must be a list')
    return [item for item in records if isinstance(item, dict)]


def load_settlements():
    document = _load_json(SETTLEMENTS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill2_tail settlements records must be a list')
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


def _keep_set(value, count, allowed, label):
    text = value.strip() if isinstance(value, str) else None
    if (not isinstance(text, str) or len(text) != count
            or len(set(text)) != count
            or any(char not in allowed for char in text)):
        raise ValueError('%s 必须是 %d 个不重复数字' % (label, count))
    return ''.join(sorted(text))


def pair_tails(numbers):
    return sorted({sum(int(numbers[index]) for index in pair) % 10 for pair in HE2_PAIRS})


def triple_tails(numbers):
    return sorted({sum(int(numbers[index]) for index in triple) % 10
                   for triple in HE3_TRIPLES})


def he2_candidates():
    from itertools import combinations
    return [''.join(sorted(item)) for item in combinations(HE2_SOURCE_DIGITS, HE2_KEEP_COUNT)]


def he3_candidates():
    from itertools import combinations
    result = []
    for item in combinations('0123456789', HE3_KEEP_COUNT):
        text = ''.join(sorted(item))
        odd = sum(1 for char in text if int(char) % 2 == 1)
        if 2 <= odd <= 3:
            result.append(text)
    return result


ATTACK_ORDER = '1234567890'
ATTACK_POSITION_LABELS = {'q': '千', 'b': '百', 's': '十', 'g': '个'}
ATTACK_TAIL_LABELS = {'tail_all': '千百十个合', 'tail_qbs': '千百十合',
                      'tail_qbg': '千百个合', 'tail_qsg': '千十个合',
                      'tail_bsg': '百十个合'}


def build_attack(seal):
    """与七项独立框架同格式的攻击语。

    位置保留码（被杀位置为去掉杀码的 9 码，未选位置保留 10 码）→ 四定 →
    合尾保留码（去掉排除码）→ 除<四位单双形态>除<四位大小形态> →
    二数合<4码>三数合<5码>各。
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
    parts = []
    for key, label in (('q', '千'), ('b', '百'), ('s', '十'), ('g', '个')):
        killed = key in positions
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
    he2_keep, he3_keep = seal.get('he2_keep'), seal.get('he3_keep')
    if he2_keep and he3_keep:
        text += '二数合' + str(he2_keep) + '三数合' + str(he3_keep) + '各'
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
    if len(kills) != COUNTED_POSITIONS:
        raise ValueError('必须恰好选择 2 个位置')
    counted = [key for key, _label, _index in POSITIONS if key in kills]
    unselected = [key for key, _label, _index in POSITIONS if key not in kills]
    if len(counted) != COUNTED_POSITIONS or len(unselected) != len(POSITIONS) - COUNTED_POSITIONS:
        raise ValueError('必须恰好选择 2 个位置')
    tail = response.get('tail')
    if not isinstance(tail, dict):
        raise ValueError('tail 必须是对象')
    tail_key = _tail_key(tail.get('key'))
    number = digit(tail.get('kill'))
    if number is None:
        raise ValueError('合尾排除码必须是 0 至 9 的整数')
    he2_keep = _keep_set(response.get('he2_keep'), HE2_KEEP_COUNT, HE2_SOURCE_DIGITS,
                         '二数合保留4码')
    he3_keep = _keep_set(response.get('he3_keep'), HE3_KEEP_COUNT, '0123456789',
                         '三数合保留5码')
    odd = sum(1 for char in he3_keep if int(char) % 2 == 1)
    if not 2 <= odd <= 3:
        raise ValueError('三数合保留5码必须 2 奇 3 偶或 3 奇 2 偶')
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
            raise ValueError('%s 必须是恰好 4 个%s字符' % (field, kind_label))
        patterns[field] = value
    for position_key, kill_text in kills.items():
        index = POSITION_INDEX[position_key]
        parity_label, size_label = reverse_attributes(int(kill_text))
        if patterns['reverse_parity'][index] != parity_label:
            raise ValueError('单双反向形态在%s位置必须取杀码形态的反面' % position_key)
        if patterns['reverse_size'][index] != size_label:
            raise ValueError('大小反向形态在%s位置必须取杀码形态的反面' % position_key)
    return {
        'positions': kills,
        'counted': counted,
        'unselected': unselected,
        'tail': {'key': tail_key, 'label': TAILS[tail_key]['label'], 'kill': str(number)},
        'he2_keep': he2_keep,
        'he3_keep': he3_keep,
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
        raise ValueError('insufficient-kill2-history')
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
        position_lines.append('\u00b7 %s\uff1a%s' % (label, _cold_line(ranks)))
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
        '\u00b7 %s\uff1a候选 %s\uff08近50期出%s次/当前遗漏%s期\uff09，'
        '近50期该尾候选杀号命中 %.2f%%，近30期 %.2f%%'
        % (label, kill, count, omission, hit50 * 100.0, hit30 * 100.0)
        for _key, label, kill, count, omission, hit50, hit30 in tail_rows]
    pair_sets = [set(pair_tails(row)) for row in window]
    triple_sets = [set(triple_tails(row)) for row in window]
    he2_rows = []
    for candidate in he2_candidates():
        digits = {int(char) for char in candidate}
        flags = [bool(value & digits) for value in pair_sets]
        he2_rows.append((candidate, sum(flags), sum(flags[-30:]) / 30.0,
                         sum(flags) / len(window)))
    he2_rows.sort(key=lambda row: (-row[3], -row[2], row[0]))
    he2_lines = [
        '\u00b7 %s\uff1a近50期命中 %.2f%%\uff08%s/50\uff09，近30期 %.2f%%'
        % (candidate, hit50 * 100.0, hits, hit30 * 100.0)
        for candidate, hits, hit30, hit50 in he2_rows]
    he3_rows = []
    for candidate in he3_candidates():
        digits = {int(char) for char in candidate}
        flags = [bool(value & digits) for value in triple_sets]
        he3_rows.append((candidate, sum(flags), sum(flags[-30:]) / 30.0,
                         sum(flags) / len(window)))
    he3_rows.sort(key=lambda row: (-row[3], -row[2], row[0]))
    he3_lines = [
        '\u00b7 %s\uff1a近50期命中 %.2f%%\uff08%s/50\uff09，近30期 %.2f%%'
        % (candidate, hit50 * 100.0, hits, hit30 * 100.0)
        for candidate, hits, hit30, hit50 in he3_rows[:10]]
    if isinstance(previous_tail, dict) and previous_tail.get('key') in TAILS:
        prev_label = TAILS[previous_tail['key']]['label']
        prev_kill = str(previous_tail.get('kill', '-'))
        restriction = ('上一期合尾：%s 杀 %s。'
                       '稳定化规则：上期该尾码命中且连用未满 3 期时，系统直接沿用上期尾码；'
                       '否则采用你本期新选。你的尾项仍会记为换码候选，可正常自主选择。'
                       % (prev_label, prev_kill))
    else:
        restriction = '本期无上一期尾码，按你本期自主选择封存。'
    limit = position_limit if isinstance(position_limit, dict) else {}
    limit_lines = []
    previous_positions = limit.get('previous_positions') or {}
    previous_ref = limit.get('previous_ref_period') or reference
    if previous_positions:
        picked = '\u3001'.join('%s杀%s' % (POSITION_LABELS[key], previous_positions[key])
                               for key, _label, _index in POSITIONS if key in previous_positions)
        limit_lines.append('上一封存（%s）：%s。' % (previous_ref, picked))
    blocked_digits = limit.get('blocked_digits') or {}
    if blocked_digits:
        forced = ['%s不得再杀 %s' % (POSITION_LABELS.get(key, key), value)
                  for key, value in blocked_digits.items()]
        limit_lines.append('本期强制更换：' + '\uff1b'.join(forced) + '\u3002')
    blocked_unselected = limit.get('blocked_unselected') or []
    if blocked_unselected:
        must_select = '、'.join(POSITION_LABELS.get(key, key) for key in blocked_unselected)
        limit_lines.append('本期必须选中（未选位置已连续%d期未选中）：%s。'
                           % (MAX_CONSECUTIVE_PERIODS, must_select))
    limit_lines.append('（同一位置杀码最多连用 %d 期，中间未选该位置不清零；'
                       '未选位置最多连用 %d 期，满后本期必须选中）'
                       % (MAX_CONSECUTIVE_PERIODS, MAX_CONSECUTIVE_PERIODS))
    position_limit_text = '\n'.join(limit_lines)
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
        '以下是真实的最近%d期开奖前四位（按时间先后排序，'
        '最新已开奖参考期号:%s，预测目标期号:%s）：\n%s\n\n'
        '【近50期各位置冷热（出现次数少、当前遗漏长优先；仅历史统计，不代表未来概率）】\n%s\n\n'
        '【五种合尾候选对比（合尾 = 对应位置数字之和的个位；'
        '按该尾候选码近50期真实杀号命中率排序，顺序不代表推荐）】\n%s\n\n'
        '【二数合保留4码候选（任意两位六组和值个位任一落入保留集即命中；'
        '按近50期真实命中率排序，顺序不代表推荐）】\n%s\n\n'
        '【三数合保留5码候选 Top10（任意三位四组和值个位任一落入保留集即命中；'
        '必须 2 奇 3 偶或 3 奇 2 偶；按近50期命中率排序，仅供参考）】\n%s\n\n'
        '【合尾稳定化规则】\n%s\n\n'
        '【位置使用限制】\n%s\n\n'
        '【任务】\n'
        '1. 从 千/百/十/个 四个位置中挑 2 个（恰好 2 个），每个选中位置排除 1 个数字（0-9）；'
        '2 个位置全部计入整组与攻击语；未选中的 2 个位置不参与定位项（攻击语保留全部 10 码）；\n'
        '2. 从上述 5 种合尾中挑 1 种并排除 1 个和尾数字（0-9），作为本期换码候选；'
        '系统按稳定化规则决定最终封存尾码；\n'
        '3. he2_keep：从 02468 中恰好保留 4 个不同数字（字符串），'
        '目标期任取两位的 6 种和值个位任一落入即命中；\n'
        '4. he3_keep：从 0123456789 中恰好保留 5 个不同数字（字符串），'
        '且必须 2 奇 3 偶或 3 奇 2 偶，目标期任取三位的 4 种和值个位任一落入即命中；\n'
        '5. 给出两项四位反向排除形态：reverse_parity 为按千百十个顺序的四位单双形态，'
        'reverse_size 为四位大小形态；被杀的 2 个位置必须取杀码形态的反面'
        '（杀码为单则写双、为双则写单；杀码为大则写小、为小则写大），'
        '未选 2 个位置的字符自选；\n'
        '6. 命中口径：位置 = 目标期该位置实际开奖号不等于排除码（2 个位置全部计入）；'
        '合尾 = 目标期该合尾实际和值个位不等于排除码；'
        '二数合/三数合 = 任一组合和值个位落入保留集；'
        '反向形态 = 目标期实际四位形态不等于这两个封存形态（反向排除项）；'
        '以上 2 个位置 + 1 个合尾 + 二数合 + 三数合 + 2 项形态共 7 项同时命中才算整组命中；\n'
        '7. 冷热、遗漏、重码只是历史特征，不能据此断言未来概率；不要为换而换，不得声称保证命中；\n'
        '8. 只能使用上面给出的历史与面板全量上下文；面板数据只作参考，不得照抄或为避开同号强行换码，'
        '也不得声称保证命中。\n\n'
        '只输出严格 JSON（不要输出分析过程、代码块或额外文字）：\n'
        '{"positions":{"q":<0-9整数>,"b":<0-9整数>},'
        '"tail":{"key":"tail_all|tail_qbs|tail_qbg|tail_qsg|tail_bsg","kill":<0-9整数>},'
        '"he2_keep":"<4位数字>","he3_keep":"<5位数字>",'
        '"reverse_parity":"<四个单/双字符>","reverse_size":"<四个大/小字符>",'
        '"reason":"40字以内"}\n'
        '注意：positions 必须恰好 2 个键（从 q/b/s/g 中选），'
        'he2_keep 必须是 02468 里的 4 个不同数字，'
        'he3_keep 必须是 5 个不同数字且 2 奇 3 偶或 3 奇 2 偶；'
        '两个形态字符串都必须是 4 个字符。'
        % (WINDOW, reference, target, history,
           '\n'.join(position_lines), '\n'.join(tail_lines),
           '\n'.join(he2_lines), '\n'.join(he3_lines),
           restriction, position_limit_text)
    )
    if panel_section:
        _marker = prompt.find('【任务】')
        if _marker != -1:
            prompt = prompt[:_marker] + panel_section + prompt[_marker:]
    audit = {
        'version': 'kill2-tail-prompt-v2-two-positions-he2he3-rot3',
        'reference': reference,
        'target': target,
        'window': WINDOW,
        'history_sha256': sha256(history.encode('utf-8')).hexdigest(),
        'rows': [[str(int(value)) for value in row] for row in window],
        'tail_comparison': [{'key': key, 'label': label, 'candidate': str(kill),
                             'hit50': round(hit50, 6), 'hit30': round(hit30, 6)}
                            for key, label, kill, _count, _omission, hit50, hit30 in tail_rows],
        'he2_top': [{'keep': candidate, 'hit50': round(hit50, 6), 'hit30': round(hit30, 6)}
                    for candidate, _hits, hit30, hit50 in he2_rows],
        'he3_top': [{'keep': candidate, 'hit50': round(hit50, 6), 'hit30': round(hit30, 6)}
                    for candidate, _hits, hit30, hit50 in he3_rows[:10]],
        'previous_tail': previous_tail,
        'tail_policy': TAIL_POLICY,
        'tail_hold_max': TAIL_HOLD_MAX,
        'position_limit': limit,
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
    """Same-kill-digit streak per position; seals not choosing it are skipped.

    A gap must not reset the counter, otherwise the AI can dodge the three-period
    limit by leaving the position unselected for one period and reusing the digit.
    """
    streaks = {}
    for key, _label, _index in POSITIONS:
        value = None
        count = 0
        for seal in reversed(seals):
            positions = seal.get('positions') if isinstance(seal, dict) else None
            if not isinstance(positions, dict):
                break
            if key not in positions:
                continue
            current = str(positions.get(key))
            if value is None:
                value, count = current, 1
            elif current == value:
                count += 1
            else:
                break
        streaks[key] = (value, count)
    return streaks


def _trailing_unselected_streaks(seals):
    """Trailing consecutive periods each position was left unselected.

    A position counts as unselected when it is absent from the seal's
    'positions' dict; selecting (killing) it resets its streak.
    """
    streaks = {}
    for key, _label, _index in POSITIONS:
        count = 0
        for seal in reversed(seals):
            positions = seal.get('positions') if isinstance(seal, dict) else None
            if not isinstance(positions, dict):
                break
            if key in positions:
                break
            count += 1
        streaks[key] = count
    return streaks


def position_restrictions(seals):
    """Consecutive-use limits for position kills."""
    seals = [item for item in (seals or []) if isinstance(item, dict)]
    digit_streaks = _trailing_digit_streaks(seals)
    previous = seals[-1] if seals else {}
    blocked_digits = {}
    for key, (digit, count) in digit_streaks.items():
        if digit is not None and count >= MAX_CONSECUTIVE_PERIODS:
            blocked_digits[key] = digit
    unselected_streaks = _trailing_unselected_streaks(seals)
    blocked_unselected = [key for key, count in unselected_streaks.items()
                          if count >= MAX_CONSECUTIVE_PERIODS]
    return {
        'max_consecutive': MAX_CONSECUTIVE_PERIODS,
        'previous_ref_period': previous.get('ref_period'),
        'previous_target_period': previous.get('target_period'),
        'previous_positions': dict(previous.get('positions') or {}),
        'previous_unselected': list(previous.get('unselected') or []),
        'digit_streaks': {key: {'digit': digit, 'periods': count}
                          for key, (digit, count) in digit_streaks.items() if digit is not None},
        'unselected_streaks': unselected_streaks,
        'blocked_digits': blocked_digits,
        'blocked_unselected': blocked_unselected,
    }


def validate_position_choice(prediction, position_limit=None):
    """Reject over-limit kill codes or a repeatedly unselected position."""
    if not isinstance(position_limit, dict):
        return prediction
    limit = position_limit.get('max_consecutive') or MAX_CONSECUTIVE_PERIODS
    positions = prediction.get('positions') or {}
    for key, value in (position_limit.get('blocked_digits') or {}).items():
        if key in positions and str(positions[key]) == str(value):
            label = POSITION_LABELS.get(key, key)
            raise ValueError('%s已连续%d期使用杀码%s，本期必须换码' % (label, limit, value))
    for key in (position_limit.get('blocked_unselected') or []):
        if key not in positions:
            label = POSITION_LABELS.get(key, key)
            raise ValueError('%s已连续%d期未选中，本期必须选中该位置杀码' % (label, limit))
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
    for name in ('PENTA5_MODEL_KILL2', 'PENTA5_MODEL_KILL3', 'PENTA5_MODEL_KILLER',
                 'PENTA5_DEEPSEEK_MODEL'):
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
        'request_policy_version': 'kill2-high-12k-deadline-v1',
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
            raise PublicationRejected('insufficient-time-for-kill2-request')
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
            log(f'kill2 response validated; ref={reference}; attempt={attempt}; publication pending.')
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
            log(f'kill2 attempt {attempt}/3 rejected; error_type={last_error}{status_note}; not published.')
            if status in NONRETRYABLE_HTTP_STATUSES:
                log(f'kill2 nonretryable provider HTTP {status}; stop retries; not published.')
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
    raise RuntimeError(f'kill2 inference failed after {attempts_used} attempt(s): '
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
        'counted': prediction.get('counted') or [],
        'unselected': prediction.get('unselected') or [],
        'tail': prediction['tail'],
        'tail_policy': prediction.get('tail_policy'),
        'he2_keep': prediction['he2_keep'],
        'he3_keep': prediction['he3_keep'],
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
        raise PublicationRejected('existing-kill2-seal-cannot-be-replaced')
    seals.append(seal)
    _write_seals(seals)
    return seal


def mark_published(reference, published_at=None):
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        raise ValueError('kill2 seal missing before publication')
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
                      'hit': (tail_kill is not None and actual_tail is not None
                              and actual_tail != tail_kill)}
        keep_entries = {}
        for field, tails in (('he2_keep', pair_tails(actual)),
                             ('he3_keep', triple_tails(actual))):
            keep = str(seal.get(field) or '')
            keep_digits = {char for char in keep if char.isdigit()}
            tails_text = ''.join(str(value) for value in tails)
            keep_entries[field] = {
                'keep': keep, 'actual': tails_text,
                'hit': bool(keep_digits) and bool(keep_digits.intersection(
                    str(value) for value in tails)),
            }
        patterns = {}
        for field, kind, palette in (('reverse_parity', 'parity', '单双'),
                                     ('reverse_size', 'size', '大小')):
            sealed_form = seal.get(field)
            actual_form = pattern_of(actual, kind)
            hit = (isinstance(sealed_form, str) and len(sealed_form) == 4
                   and all(char in palette for char in sealed_form)
                   and actual_form != sealed_form)
            patterns[field] = {'form': sealed_form, 'actual': actual_form, 'hit': bool(hit)}
        counted = seal.get('counted') or [key for key in positions]
        group_hit = (all(positions[key]['hit'] for key in counted if key in positions)
                     and tail_entry['hit']
                     and keep_entries['he2_keep']['hit']
                     and keep_entries['he3_keep']['hit']
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
            'counted': list(counted),
            'unselected': list(seal.get('unselected') or []),
            'tail': tail_entry,
            'he2_keep': keep_entries['he2_keep'],
            'he3_keep': keep_entries['he3_keep'],
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
    he2_hits = sum(1 for item in todays
                   if (item.get('he2_keep') or {}).get('hit') is True)
    he3_hits = sum(1 for item in todays
                   if (item.get('he3_keep') or {}).get('hit') is True)
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
        'he2_keep': {'hits': he2_hits, 'bets': total,
                     'rate': (he2_hits / total) if total else None},
        'he3_keep': {'hits': he3_hits, 'bets': total,
                     'rate': (he3_hits / total) if total else None},
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
        raise ValueError('invalid-kill2-target-period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target

