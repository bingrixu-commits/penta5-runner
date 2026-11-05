"""AI 四反向冷形态排除框架（kill8，独立封存/结算/统计）。

口径（面板、封存与统计都按此写死，v6 起）：
- 位置排除数字由用户手动完成，不进本框架计算（本框架只负责排除形态）；
- 由 DeepSeek 每期给出 4 个「反方向冷形态排除项」，均按千百十个顺序：
  ① 四位单双形态（单/双，16 选 1）；
  ② 四位大小形态（大/小，16 选 1）；
  ③ 全形形态（大大大大=全大 / 小小小小=全小 / 单单单单=全单 / 双双双双=全双，4 选 1）；
  ④ 跨族混合形态（前两位千/百看大小、后两位十/个看单双，16 选 1，如 大小双双）；
- 冷形态定位依据：近 50 期出现次数少、当前遗漏长（仅历史统计，不代表未来概率）；
- 命中口径（4 项全中才算整组命中，单个排除项按其覆盖号码集合判定）：
  ① 单双形态命中 = 目标期实际四位单双形态 != 封存单双形态；
  ② 大小形态命中 = 目标期实际四位大小形态 != 封存大小形态；
  ③ 全形命中 = 目标期实际号码不属于封存全形（全大=四位全大、全小=四位全小、
     全单=四位全单、全双=四位全双）；
  ④ 跨族混合命中 = 目标期「前两位大小 + 后两位单双」组合 != 封存混合形态；
- ③ 全形方向由排除数字决定（多数锁定）：单双/大小两类各自按已约束位置投票，
  明确多数时锁定该反方向全形（如多数位置排除数字偏双 → 锁定全单）；
  两类同时锁定且方向不同（如同时满足锁全大与锁全单）时，由模型在 2 个候选里按冷热二选一；
  一类都未成型时由模型在 4 种里按冷热自选；
- ④ 混合形态方向：前两位按大小反向约束、后两位按单双反向约束，未约束位由模型按冷热自选；
- 去重：③ 全形若与 ① 单双形态或 ② 大小形态同形且 ①/② 还有自由位，
  把最靠前的自由位翻成反方向（只动自由位，不违反任何排除约束）；四位全被钉死时保留同形；
- 无合尾、无连用限制（每期完全由模型按冷热重选，不沿用上期）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与。

数据落点：
- data/kill8_tail/seals.json        每期封存（同一参考期不可替换）
- data/kill8_tail/settlements.json  开奖后结算（只追加）
"""
from datetime import datetime, timedelta
from hashlib import sha256
from itertools import product
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
DATA_DIR = ROOT / 'data' / 'kill8_tail'
SEALS_PATH = DATA_DIR / 'seals.json'
SETTLEMENTS_PATH = DATA_DIR / 'settlements.json'
STATE_PATH = ROOT / 'data' / 'state.json'

WINDOW = 50
CLOSE_SECONDS = 70
MIN_REQUEST_SECONDS = 20.0
MAX_TOKENS = 12288
REASONING_EFFORT = 'high'
NONRETRYABLE_HTTP_STATUSES = frozenset((400, 401, 402, 422))

FORM_KINDS = ('parity', 'size', 'uniform', 'mixed')
FORM_LABELS = {'parity': '单双形态', 'size': '大小形态',
               'uniform': '全形形态', 'mixed': '跨族混合形态'}
FORM_PALETTE = {'parity': '单双', 'size': '大小'}
FORM_FIELDS = {'parity': 'parity_form', 'size': 'size_form',
               'uniform': 'uniform_form', 'mixed': 'mixed_form'}
FORM_SOURCE = {'parity': 'parity', 'size': 'size',
               'all_size': 'size', 'all_parity': 'parity'}
LEGACY_FORM_KINDS = ('all_size', 'all_parity')
LEGACY_FORM_FIELDS = {'all_size': 'all_size_form', 'all_parity': 'all_parity_form'}
UNIFORM_FORM_VALUES = ('大大大大', '小小小小', '单单单单', '双双双双')
UNIFORM_FORM_SOURCE = {'大大大大': 'size', '小小小小': 'size',
                       '单单单单': 'parity', '双双双双': 'parity'}
UNIFORM_FORM_NAMES = {'大大大大': '全大', '小小小小': '全小',
                      '单单单单': '全单', '双双双双': '全双'}
MIXED_HEAD_PALETTE = '大小'
MIXED_TAIL_PALETTE = '单双'
MIXED_FORM_SPACE = tuple(head[0] + head[1] + tail[0] + tail[1]
                         for head in product(MIXED_HEAD_PALETTE, repeat=2)
                         for tail in product(MIXED_TAIL_PALETTE, repeat=2))
ALL_FORM_VALUES = {'all_size': ('大大大大', '小小小小'),
                   'all_parity': ('单单单单', '双双双双')}
ALL_FORM_NAMES = {'大大大大': '全大', '小小小小': '全小',
                  '单单单单': '全单', '双双双双': '全双'}
REVERSE_LABELS = {'parity': '单双反向冷形态', 'size': '大小反向冷形态',
                  'uniform': '全形反向冷形态', 'mixed': '跨族混合反向冷形态'}

RULE_VERSION = 'kill8-tail-v6-merged-four-form-mixed'
COUNTED_FORMS = 4
POSITION_EXCLUSIONS_PATH = ROOT / 'data' / 'kill8_position_exclusions.json'
POSITION_KEYS = ('q', 'b', 's', 'g')
POSITION_LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
POSITION_INDEX = {'q': 0, 'b': 1, 's': 2, 'g': 3}
POSITION_INDEX_LABELS = {0: '千位', 1: '百位', 2: '十位', 3: '个位'}

RETRY_INSTRUCTION = (
    '上次响应未通过本地校验（{error}）。请严格按格式重新输出完整 JSON：'
    'parity_form 必须是恰好 4 个单/双字符（按千百十个顺序）；'
    'size_form 必须是恰好 4 个大/小字符（按千百十个顺序）；'
    'uniform_form 必须是「大大大大」「小小小小」「单单单单」「双双双双」之一；'
    'mixed_form 必须是 4 个字符：前两位为大小字符、后两位为单双字符（如 大小双双）；'
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
        raise ValueError('kill8_tail seals records must be a list')
    return [item for item in records if isinstance(item, dict)]


def load_settlements():
    document = _load_json(SETTLEMENTS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill8_tail settlements records must be a list')
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


def form_space(kind):
    """形态空间：单双/大小各 16 种；全形 4 种；跨族混合 16 种；旧全大/全小、全单/全双各 2 种。"""
    if kind in FORM_PALETTE:
        return [''.join(chars) for chars in product(FORM_PALETTE[kind], repeat=4)]
    if kind == 'uniform':
        return list(UNIFORM_FORM_VALUES)
    if kind == 'mixed':
        return list(MIXED_FORM_SPACE)
    if kind in ALL_FORM_VALUES:
        return list(ALL_FORM_VALUES[kind])
    raise ValueError('unknown form kind')
def pattern_of_mixed(digits):
    """跨族混合形态：前两位取大小、后两位取单双（如 大小双双）。"""
    values = [int(value) for value in digits]
    if len(values) != 4:
        raise ValueError('pattern requires four digits')
    head = ''.join('大' if value >= 5 else '小' for value in values[:2])
    tail = ''.join('双' if value % 2 == 0 else '单' for value in values[2:])
    return head + tail


def rank_forms(rows, kind):
    """形态冷排序：出现次数升序、当前遗漏降序（最冷在前）。

    单双/大小按各自 4 位形态统计；全形 4 种按所覆盖的形态统计
    （全大/全小看大小形态、全单/全双看单双形态）；
    跨族混合 16 种按「前两位大小 + 后两位单双」统计；
    旧全大/全小、全单/全双 2 种为历史口径保留（仅测试与旧版追溯使用）。
    """
    if kind == 'uniform':
        space = list(UNIFORM_FORM_VALUES)
        counts = {form: 0 for form in space}
        last = {}
        for index, row in enumerate(rows):
            size = pattern_of(row, 'size')
            parity = pattern_of(row, 'parity')
            for form in space:
                actual = size if UNIFORM_FORM_SOURCE[form] == 'size' else parity
                if actual == form:
                    counts[form] += 1
                    last[form] = index
    elif kind == 'mixed':
        space = list(MIXED_FORM_SPACE)
        counts = {form: 0 for form in space}
        last = {}
        for index, row in enumerate(rows):
            form = pattern_of_mixed(row)
            if form in counts:
                counts[form] += 1
                last[form] = index
    else:
        source = FORM_SOURCE.get(kind)
        if source is None:
            raise ValueError('unknown form kind')
        space = form_space(kind)
        counts = {form: 0 for form in space}
        last = {}
        for index, row in enumerate(rows):
            form = pattern_of(row, source)
            if form in counts:
                counts[form] += 1
                last[form] = index
    size = len(rows)
    items = [(form, counts[form],
              size if last.get(form) is None else size - last[form] - 1)
             for form in space]
    items.sort(key=lambda item: (item[1], -item[2]))
    return items
def _history_rows(snapshot=None):
    snapshot = snapshot or load_snapshot(persist=False)
    records = snapshot.get('records') or []
    rows = [list(record.get('numbers') or [])[:4] for record in records]
    rows = [row for row in rows if len(row) == 4]
    if len(rows) < WINDOW + 1:
        raise ValueError('insufficient-kill8-history')
    return snapshot, rows


def _cold_line(ranks, limit=3):
    return '、'.join(f'{form}(出{count}次/遗漏{omission}期)'
                     for form, count, omission in ranks[:limit])


def load_position_exclusions():
    """读取用户手动填写的各位置排除数字（千/百/十/个，每位置 0-9 数字串）。"""
    document = _load_json(POSITION_EXCLUSIONS_PATH, {})
    if not isinstance(document, dict):
        return {}
    return {key: str(document.get(key) or '') for key in POSITION_KEYS}


def _majority_lock(votes, direction_labels):
    """按已约束位置做多数锁定：返回 (反方向形态 char*4, 说明) 或 (None, 说明)。

    votes: {位置索引: 该位置要写的形态字符}；direction_labels: 形态字符 -> 形态字符。
    单票即锁定（与 v5 的 ③④ 语义一致）；平票或未填返回 (None, 说明)。
    """
    counts = {}
    for value in votes.values():
        counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None, '未填排除数字'
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    top_value, top_count = ranked[0]
    if len(ranked) > 1 and ranked[1][1] == top_count:
        return None, '平票：' + '、'.join('%s%s' % (key, value) for key, value in ranked)
    detail = '、'.join('%s取反写%s' % (POSITION_INDEX_LABELS[index], votes[index])
                      for index in sorted(votes)) + ' → 锁%s' % (direction_labels[top_value] * 4)
    return direction_labels[top_value] * 4, detail


def _uniform_candidates(parity, size):
    """全形 4 选 1 的反向候选：单双/大小两类各自按已约束位置多数投票。

    两类同时锁定且方向不同 → 2 个候选（由模型按冷热二选一）；
    只有一类锁定 → 强制该形态；都未成型 → 4 种里自选。
    """
    size_form, size_detail = _majority_lock(size, {'大': '大', '小': '小'})
    parity_form, parity_detail = _majority_lock(parity, {'单': '单', '双': '双'})
    candidates = []
    details = []
    if parity_form:
        candidates.append(parity_form)
        details.append('%s（%s）' % (parity_form, parity_detail))
    if size_form:
        candidates.append(size_form)
        details.append('%s（%s）' % (size_form, size_detail))
    return candidates, details


def _mixed_constraints(parity, size):
    """跨族混合反向约束：前两位（千/百）取大小反方向、后两位（十/个）取单双反方向。"""
    result = {'size': {}, 'parity': {}}
    for index in (0, 1):
        if index in size:
            result['size'][index] = size[index]
    for index in (2, 3):
        if index in parity:
            result['parity'][index] = parity[index]
    return result


def _uniform_constraint_text(candidates, details):
    if not candidates:
        return '（无：单双/大小均未形成明确多数方向，4 种里按冷热自选）'
    if len(candidates) == 1:
        return '必须为「%s」（%s）' % (candidates[0], details[0])
    return '两类方向同时锁定：%s；由你在 %s 中按冷热二选一' % (
        '；'.join(details), '、'.join('「%s」' % item for item in candidates))


def _mixed_constraint_text(constraints):
    parts = []
    for index in sorted(constraints.get('size') or {}):
        parts.append('%s写「%s」(大小)' % (POSITION_INDEX_LABELS[index], constraints['size'][index]))
    for index in sorted(constraints.get('parity') or {}):
        parts.append('%s写「%s」(单双)' % (POSITION_INDEX_LABELS[index], constraints['parity'][index]))
    if not parts:
        return '（无：前两位未形成大小方向、后两位未形成单双方向，16 种里按冷热自选）'
    return '、'.join(parts) + '；其余位置由你按冷热自选'


def reverse_constraints(exclusions=None, with_details=False):
    """反向约束（v6）：单双/大小逐位反方向 + 全形 4 选 1 候选 + 跨族混合逐位反方向。"""
    exclusions = exclusions if isinstance(exclusions, dict) else {}
    parity = {}
    size = {}
    for key, index in POSITION_INDEX.items():
        raw = str(exclusions.get(key) or '')
        digits = [int(char) for char in raw if char.isdigit()]
        if not digits:
            continue
        parities = {0 if digit % 2 == 0 else 1 for digit in digits}
        sizes = {0 if digit < 5 else 1 for digit in digits}
        if len(parities) == 1:
            parity[index] = '单' if 0 in parities else '双'
        if len(sizes) == 1:
            size[index] = '大' if 0 in sizes else '小'
    candidates, details = _uniform_candidates(parity, size)
    mixed = _mixed_constraints(parity, size)
    uniform_note = _uniform_constraint_text(candidates, details)
    mixed_note = _mixed_constraint_text(mixed)
    legacy_size = ''
    legacy_parity = ''
    for form in candidates:
        if form in ALL_FORM_VALUES['all_size']:
            legacy_size = form
        elif form in ALL_FORM_VALUES['all_parity']:
            legacy_parity = form
    result = {
        'parity': parity,
        'size': size,
        'uniform': {'allowed': candidates or list(UNIFORM_FORM_VALUES),
                    'forced': candidates[0] if len(candidates) == 1 else None,
                    'note': uniform_note},
        'mixed': {'constraints': mixed, 'note': mixed_note},
        'all_size': legacy_size,
        'all_parity': legacy_parity,
    }
    if with_details:
        return result, {'uniform': uniform_note, 'mixed': mixed_note}
    return result


def _constraint_text(constraints):
    if not constraints:
        return '（无：10 选 1 里按冷热自选）'
    parts = []
    for index in sorted(constraints):
        parts.append('%s写「%s」' % (POSITION_INDEX_LABELS[index], constraints[index]))
    return ('、'.join(parts) + '；其余位置由你按冷热自选')
def build_prompt(reference, target, rows, *, panel_snapshot=None, exclusions=None):
    """Frozen prompt and its audit metadata; rows are the last WINDOW draws."""
    window = rows[-WINDOW:]
    constraints = reverse_constraints(exclusions)
    parity_constraint = _constraint_text(constraints['parity'])
    size_constraint = _constraint_text(constraints['size'])
    uniform_constraint = constraints['uniform']['note']
    mixed_constraint = constraints['mixed']['note']
    history = ', '.join(''.join(str(int(value)) for value in row) for row in window)
    parity_ranks = rank_forms(window, 'parity')
    size_ranks = rank_forms(window, 'size')
    uniform_ranks = rank_forms(window, 'uniform')
    mixed_ranks = rank_forms(window, 'mixed')
    parity_lines = ['· %s（出%s次/遗漏%s期）' % (form, count, omission)
                    for form, count, omission in parity_ranks]
    size_lines = ['· %s（出%s次/遗漏%s期）' % (form, count, omission)
                  for form, count, omission in size_ranks]
    uniform_lines = ['· %s（%s）（出%s次/遗漏%s期）'
                     % (UNIFORM_FORM_NAMES.get(form, form), form, count, omission)
                     for form, count, omission in uniform_ranks]
    mixed_lines = ['· %s（出%s次/遗漏%s期）' % (form, count, omission)
                   for form, count, omission in mixed_ranks]
    fence = chr(96) * 3
    panel_section = ''
    panel_context_sha = None
    if isinstance(panel_snapshot, dict):
        raw = json.dumps(panel_snapshot, ensure_ascii=False, sort_keys=True)
        panel_context_sha = sha256(raw.encode('utf-8')).hexdigest()
        panel_section = ('【面板全量上下文（只读参考，不得照抄，不得声称保证命中）】\n'
                         + fence + 'json\n' + raw + '\n' + fence + '\n\n')
    prompt = (
        '以下是真实的最近%s期开奖前四位（按时间先后排序，'
        '最新已开奖参考期号:%s，预测目标期号:%s）：\n'
        '%s\n\n'
        '【四位单双形态冷热统计（16 种，按出现次数升序、当前遗漏降序排列；仅历史统计，不代表未来概率）】\n'
        '%s\n\n'
        '【四位大小形态冷热统计（16 种，按出现次数升序、当前遗漏降序排列；仅历史统计，不代表未来概率）】\n'
        '%s\n\n'
        '【四位全形形态冷热统计（4 种：全大=大大大大、全小=小小小小、全单=单单单单、全双=双双双双；'
        '全大/全小按大小形态统计、全单/全双按单双形态统计；按出现次数升序、当前遗漏降序排列；'
        '仅历史统计，不代表未来概率）】\n'
        '%s\n\n'
        '【四位跨族混合形态冷热统计（16 种：前两位千/百看大小、后两位十/个看单双，如 大小双双；'
        '按出现次数升序、当前遗漏降序排列；仅历史统计，不代表未来概率）】\n'
        '%s\n\n'
        '【反向约束（必须严格遵守，这是「反向」的核心）】\n'
        '你已手动排除了各位置数字，排除数字决定形态的「反方向」：\n'
        '· 单双形态反向约束：%s\n'
        '· 大小形态反向约束：%s\n'
        '· 全形形态反向约束：%s\n'
        '· 跨族混合形态反向约束：%s\n'
        '约束位置必须取排除数字的反方向（排除双写单、排除单写双；排除大写小、排除小写大）；'
        '未列出的位置由你按冷热自选。'
        '全形形态（第 3 项）从 大大大大 / 小小小小 / 单单单单 / 双双双双 中 4 选 1：'
        '单双或大小任一类按已约束位置多数投票锁定该反方向全形（单票也算锁定）；'
        '两类同时锁定但方向不同时，由你在 2 个候选里按冷热二选一；'
        '都未成型时由你在 4 种里按冷热自选。'
        '跨族混合形态（第 4 项）前两位（千/百）按大小反向约束、后两位（十/个）按单双反向约束，'
        '未约束位由你按冷热自选。'
        '去重：若③全形与①单双形态或②大小形态同形且①/②还有自由位，系统会把最靠前的自由位翻成反方向；'
        '请优先直接给出不同的形态。\n\n'
        '【任务】\n'
        '1. 从 16 种四位单双形态中选 1 种作为本期「单双反向冷形态排除项」（严格遵守上面的单双反向约束，其余位置优先选冷）；\n'
        '2. 从 16 种四位大小形态中选 1 种作为本期「大小反向冷形态排除项」（严格遵守上面的大小反向约束，其余位置优先选冷）；\n'
        '3. 从 4 种全形形态（大大大大 / 小小小小 / 单单单单 / 双双双双）中选 1 种作为本期「全形反向冷形态排除项」'
        '（严格遵守上面的全形反向约束，未锁定时按冷热自选）；\n'
        '4. 从 16 种跨族混合形态（前两位大小 + 后两位单双）中选 1 种作为本期「跨族混合反向冷形态排除项」'
        '（严格遵守上面的跨族混合反向约束，未约束位按冷热自选）；\n'
        '5. 命中口径：目标期实际四位单双形态 != 封存单双形态，且实际四位大小形态 != 封存大小形态，'
        '且实际号码不属于封存全形（全大=四位全大、全小=四位全小、全单=四位全单、全双=四位全双），'
        '且实际「前两位大小 + 后两位单双」组合 != 封存混合形态；4 项全中才算整组命中'
        '（③全形与①/②默认自动去重，四位全被钉死时才允许同形）；\n'
        '6. 冷热、遗漏只是历史特征，不能据此断言未来概率；不要为换而换，不得声称保证命中；\n'
        '7. 只能使用上面给出的历史与面板全量上下文；面板数据只作参考，不得照抄，也不得声称保证命中。\n\n'
        '只输出严格 JSON（不要输出分析过程、代码块或额外文字）：\n'
        '{"parity_form":"<4个单/双字符，按千百十个顺序>",'
        '"size_form":"<4个大/小字符，按千百十个顺序>",'
        '"uniform_form":"<大大大大 / 小小小小 / 单单单单 / 双双双双 之一>",'
        '"mixed_form":"<4个字符：前两位大/小、后两位单/双，按千百十个顺序>",'
        '"reason":"40字以内"}\n'
        '注意：parity_form 必须恰好 4 个单/双字符；size_form 必须恰好 4 个大/小字符；'
        'uniform_form 必须是 大大大大、小小小小、单单单单、双双双双 之一；'
        'mixed_form 必须恰好 4 个字符且前两位是大/小、后两位是单/双。'
    ) % (WINDOW, reference, target, history,
         '\n'.join(parity_lines), '\n'.join(size_lines),
         '\n'.join(uniform_lines), '\n'.join(mixed_lines),
         parity_constraint, size_constraint, uniform_constraint, mixed_constraint)
    if panel_section:
        marker = prompt.find('【任务】')
        if marker != -1:
            prompt = prompt[:marker] + panel_section + prompt[marker:]
    audit = {
        'version': 'kill8-prompt-v5-merged-four-form-mixed',
        'reference': reference,
        'target': target,
        'window': WINDOW,
        'history_sha256': sha256(history.encode('utf-8')).hexdigest(),
        'rows': [[str(int(value)) for value in row] for row in window],
        'parity_ranking': [list(item) for item in parity_ranks],
        'size_ranking': [list(item) for item in size_ranks],
        'uniform_ranking': [list(item) for item in uniform_ranks],
        'mixed_ranking': [list(item) for item in mixed_ranks],
        'uniform_allowed': list(constraints['uniform']['allowed']),
        'mixed_constraints': {
            'size': {str(index): value for index, value
                     in constraints['mixed']['constraints']['size'].items()},
            'parity': {str(index): value for index, value
                       in constraints['mixed']['constraints']['parity'].items()},
        },
        'panel_context_sha256': panel_context_sha,
        'panel_context': panel_snapshot if isinstance(panel_snapshot, dict) else None,
        'position_exclusions': dict(exclusions) if isinstance(exclusions, dict) else {},
    }
    return prompt, audit
def _dedupe_forms(forms, constraints):
    """③ 全形若与 ①/② 同形且①/②还有自由位，把最靠前的自由位翻成反方向。"""
    uniform = forms.get('uniform_form')
    source = UNIFORM_FORM_SOURCE.get(uniform)
    if source is None:
        return forms
    field = FORM_FIELDS[source]
    if forms.get(field) != uniform:
        return forms
    free = [index for index in range(4) if index not in constraints.get(source, {})]
    if not free:
        return forms
    palette = FORM_PALETTE[source]
    value = list(forms[field])
    index = free[0]
    value[index] = palette[1] if value[index] == palette[0] else palette[0]
    forms[field] = ''.join(value)
    return forms


def normalize_response(response, *, exclusions=None):
    """Strict local validation; never pad, default or invent anything."""
    if not isinstance(response, dict):
        raise ValueError('响应必须是 JSON 对象')
    parity_form = response.get('parity_form')
    if (not isinstance(parity_form, str) or len(parity_form) != 4
            or any(char not in '单双' for char in parity_form)):
        raise ValueError('parity_form 必须是恰好 4 个单/双字符')
    size_form = response.get('size_form')
    if (not isinstance(size_form, str) or len(size_form) != 4
            or any(char not in '大小' for char in size_form)):
        raise ValueError('size_form 必须是恰好 4 个大/小字符')
    uniform_form = response.get('uniform_form')
    if uniform_form not in UNIFORM_FORM_VALUES:
        raise ValueError('uniform_form 必须是「大大大大」「小小小小」「单单单单」「双双双双」之一')
    mixed_form = response.get('mixed_form')
    if (not isinstance(mixed_form, str) or len(mixed_form) != 4
            or any(char not in MIXED_HEAD_PALETTE for char in mixed_form[:2])
            or any(char not in MIXED_TAIL_PALETTE for char in mixed_form[2:])):
        raise ValueError('mixed_form 必须是恰好 4 个字符且前两位是大/小、后两位是单/双')
    constraints = reverse_constraints(exclusions)
    for index, char in constraints['parity'].items():
        if parity_form[index] != char:
            raise ValueError('parity_form 在%s必须取排除数字的反方向「%s」'
                             % (POSITION_INDEX_LABELS[index], char))
    for index, char in constraints['size'].items():
        if size_form[index] != char:
            raise ValueError('size_form 在%s必须取排除数字的反方向「%s」'
                             % (POSITION_INDEX_LABELS[index], char))
    allowed_uniform = constraints['uniform']['allowed']
    if uniform_form not in allowed_uniform:
        raise ValueError('uniform_form 必须取全形反向约束的允许形态「%s」'
                         % '、'.join('「%s」' % item for item in allowed_uniform))
    mixed_constraints = constraints['mixed']['constraints']
    for index, char in mixed_constraints['size'].items():
        if mixed_form[index] != char:
            raise ValueError('mixed_form 在%s必须取排除数字的反方向「%s」'
                             % (POSITION_INDEX_LABELS[index], char))
    for index, char in mixed_constraints['parity'].items():
        if mixed_form[index] != char:
            raise ValueError('mixed_form 在%s必须取排除数字的反方向「%s」'
                             % (POSITION_INDEX_LABELS[index], char))
    forms = _dedupe_forms({'parity_form': parity_form, 'size_form': size_form,
                           'uniform_form': uniform_form, 'mixed_form': mixed_form},
                          constraints)
    reason = str(response.get('reason', ''))
    try:
        reason.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('reason 必须是合法 UTF-8 文本') from None
    forms['reason'] = reason[:200]
    return forms
def resolve_model():
    for name in ('PENTA5_MODEL_KILL8', 'PENTA5_MODEL_KILLER', 'PENTA5_DEEPSEEK_MODEL'):
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
    return value if re.fullmatch(r'deepseek-(?:chat|reasoner|(?:v4(?:\\.1)?-)?(?:flash|pro)(?:-[0-9]{4})?)', value) else '[redacted]'


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
        'request_policy_version': 'kill8-high-12k-deadline-v1',
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


def request_prediction(prompt, reference, target, deadline, log=print, value_check=None, exclusions=None):
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
            raise PublicationRejected('insufficient-time-for-kill8-request')
        timeout = max(10.0, min(90.0, remaining - 10.0))
        started = time.monotonic()
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
            prediction = normalize_response(_extract_json(message.get('content')), exclusions=exclusions)
            if value_check is not None:
                value_check(prediction)
            meta = _metadata(reference, attempt, model, api_key, envelope,
                             time.monotonic() - started)
            log('AI_RESPONSE_META ' + json.dumps(meta, ensure_ascii=True, allow_nan=False,
                                                separators=(',', ':')))
            log(f'kill8 response validated; ref={reference}; attempt={attempt}; publication pending.')
            return prediction, beijing_now(), meta
        except Exception as error:
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
            log(f'kill8 attempt {attempt}/3 rejected; error_type={last_error}{status_note}; not published.')
            if status in NONRETRYABLE_HTTP_STATUSES:
                log(f'kill8 nonretryable provider HTTP {status}; stop retries; not published.')
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
    raise RuntimeError(f'kill8 inference failed after {attempts_used} attempt(s): '
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
        'parity_form': prediction['parity_form'],
        'size_form': prediction['size_form'],
        'uniform_form': prediction['uniform_form'],
        'mixed_form': prediction['mixed_form'],
        'reason': prediction['reason'],
        'model': resolve_model(),
        'model_label': model_label(),
        'model_metadata': response_meta,
        'prompt_sha256': sha256(prompt.encode('utf-8')).hexdigest(),
        'input_sha256': audit.get('history_sha256'),
        'panel_context_sha256': audit.get('panel_context_sha256'),
        'panel_context': audit.get('panel_context'),
        'window': WINDOW,
        'position_exclusions': audit.get('position_exclusions') if isinstance(audit, dict) else {},
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
        raise PublicationRejected('existing-kill8-seal-cannot-be-replaced')
    seals.append(seal)
    _write_seals(seals)
    return seal


def mark_published(reference, published_at=None):
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        raise ValueError('kill8 seal missing before publication')
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
    """Append settlements for sealed predictions whose target period has drawn.

    向后兼容：v2 旧封存只有 parity_form/size_form，按 2 项结算；
    v3–v5 旧封存含 all_size_form/all_parity_form，按旧 4 项口径结算；
    v6 起封存含 uniform_form/mixed_form，按新 4 项口径结算且 group 需 4 项全中。
    """
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
        actual_parity = pattern_of(actual, 'parity')
        actual_size = pattern_of(actual, 'size')
        sealed_parity = seal.get('parity_form')
        sealed_size = seal.get('size_form')
        sealed_uniform = seal.get('uniform_form')
        sealed_mixed = seal.get('mixed_form')
        parity_hit = (isinstance(sealed_parity, str) and len(sealed_parity) == 4
                      and all(char in '单双' for char in sealed_parity)
                      and actual_parity != sealed_parity)
        size_hit = (isinstance(sealed_size, str) and len(sealed_size) == 4
                    and all(char in '大小' for char in sealed_size)
                    and actual_size != sealed_size)
        uniform_ok = sealed_uniform in UNIFORM_FORM_VALUES
        mixed_ok = (isinstance(sealed_mixed, str) and len(sealed_mixed) == 4
                    and all(char in MIXED_HEAD_PALETTE for char in sealed_mixed[:2])
                    and all(char in MIXED_TAIL_PALETTE for char in sealed_mixed[2:]))
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
            'counted_forms': COUNTED_FORMS,
            'parity': {'form': sealed_parity, 'actual': actual_parity, 'hit': bool(parity_hit)},
            'size': {'form': sealed_size, 'actual': actual_size, 'hit': bool(size_hit)},
            'group_hit': False,
            'verified': False,
        }
        published = parse_time(seal.get('published_at'))
        drawn = parse_time(record.get('opentime'))
        verified = (seal.get('publication_state') == 'published' and published is not None
                    and drawn is not None and published < drawn)
        if uniform_ok and mixed_ok:
            uniform_source = UNIFORM_FORM_SOURCE.get(sealed_uniform)
            actual_uniform = actual_size if uniform_source == 'size' else actual_parity
            actual_mixed = actual_size[:2] + actual_parity[2:]
            uniform_hit = bool(actual_uniform != sealed_uniform)
            mixed_hit = bool(actual_mixed != sealed_mixed)
            group_hit = bool(parity_hit and size_hit and uniform_hit and mixed_hit)
            settlement['group_hit'] = group_hit
            settlement['verified'] = bool(verified)
            settlement['uniform'] = {'form': sealed_uniform, 'actual': actual_uniform,
                                     'hit': uniform_hit}
            settlement['mixed'] = {'form': sealed_mixed, 'actual': actual_mixed,
                                   'hit': mixed_hit}
        else:
            sealed_all_size = seal.get('all_size_form')
            sealed_all_parity = seal.get('all_parity_form')
            all_size_ok = sealed_all_size in ALL_FORM_VALUES['all_size']
            all_parity_ok = sealed_all_parity in ALL_FORM_VALUES['all_parity']
            counted = COUNTED_FORMS if (all_size_ok and all_parity_ok) else 2
            all_size_hit = bool(all_size_ok and actual_size != sealed_all_size)
            all_parity_hit = bool(all_parity_ok and actual_parity != sealed_all_parity)
            group_hit = bool(parity_hit and size_hit
                             and (all_size_hit and all_parity_hit
                                  if counted == COUNTED_FORMS else True))
            settlement['counted_forms'] = counted
            settlement['group_hit'] = group_hit
            settlement['verified'] = bool(verified)
            if counted == COUNTED_FORMS:
                settlement['all_size'] = {'form': sealed_all_size, 'actual': actual_size,
                                          'hit': all_size_hit}
                settlement['all_parity'] = {'form': sealed_all_parity,
                                            'actual': actual_parity, 'hit': all_parity_hit}
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
    """Streak stats for settlement rows: (win, loss, max_loss, four_loss_cycles)."""
    win = loss = max_loss = four_loss = 0
    for item in sorted(rows, key=lambda row: str(row.get('target_period'))):
        if item.get('group_hit') is True:
            win += 1
            loss = 0
        else:
            loss += 1
            win = 0
            max_loss = max(max_loss, loss)
            if loss % 4 == 0:
                four_loss += 1
    return win, loss, max_loss, four_loss


def today_stats(now=None):
    """Verified today's aggregate plus pending count; never mixes late records in."""
    now = now or beijing_now()
    start = business_start(now)
    settlements = load_settlements()
    seals = load_seals()
    verified = [item for item in settlements
                if item.get('verified') is True
                and _business_key(item.get('settled_at')) == start]
    verified_all = [item for item in settlements if item.get('verified') is True]
    seal_rule = {str(seal.get('ref_period')): seal.get('rule_version')
                 for seal in seals if isinstance(seal, dict)}

    def _rule_version(item):
        return item.get('rule_version') or seal_rule.get(str(item.get('ref_period')))

    todays = [item for item in verified if _rule_version(item) == RULE_VERSION]
    legacy = [item for item in verified if _rule_version(item) != RULE_VERSION]
    total = len(todays)
    group_hits = sum(1 for item in todays if item.get('group_hit') is True)
    parity_hits = sum(1 for item in todays if (item.get('parity') or {}).get('hit') is True)
    size_hits = sum(1 for item in todays if (item.get('size') or {}).get('hit') is True)
    uniform_hits = sum(1 for item in todays if (item.get('uniform') or {}).get('hit') is True)
    mixed_hits = sum(1 for item in todays if (item.get('mixed') or {}).get('hit') is True)
    all_size_hits = sum(1 for item in todays
                        if (item.get('all_size') or {}).get('hit') is True)
    all_parity_hits = sum(1 for item in todays
                          if (item.get('all_parity') or {}).get('hit') is True)
    win, loss, max_loss, four_loss = _streak_stats(todays)
    four_loss_total = _streak_stats(
        [item for item in verified_all if _rule_version(item) == RULE_VERSION])[3]
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
        'parity': {'hits': parity_hits, 'bets': total,
                   'rate': (parity_hits / total) if total else None},
        'size': {'hits': size_hits, 'bets': total,
                 'rate': (size_hits / total) if total else None},
        'uniform': {'hits': uniform_hits, 'bets': total,
                    'rate': (uniform_hits / total) if total else None},
        'mixed': {'hits': mixed_hits, 'bets': total,
                  'rate': (mixed_hits / total) if total else None},
        'all_size': {'hits': all_size_hits, 'bets': total,
                     'rate': (all_size_hits / total) if total else None},
        'all_parity': {'hits': all_parity_hits, 'bets': total,
                       'rate': (all_parity_hits / total) if total else None},
        'current_win_streak': win,
        'current_loss_streak': loss,
        'max_loss_streak': max_loss,
        'four_loss_cycles': four_loss,
        'four_loss_cycles_total': four_loss_total,
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
        raise ValueError('invalid-kill8-target-period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target


def build_attack(seal):
    """反向冷形态攻击语：位置排除由用户手动完成，本框架只给出 4 个形态排除项。"""
    if not isinstance(seal, dict):
        raise ValueError('attack requires a sealed prediction')
    if isinstance(seal.get('uniform_form'), str) or isinstance(seal.get('mixed_form'), str):
        forms = [seal.get(FORM_FIELDS[kind]) for kind in FORM_KINDS]
    else:
        forms = [seal.get('parity_form'), seal.get('size_form'),
                 seal.get('all_size_form'), seal.get('all_parity_form')]
    parts = ['除' + form for form in forms if isinstance(form, str) and len(form) == 4]
    return ''.join(parts)
