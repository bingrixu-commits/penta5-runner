"""AI 四形态排除（面板候选池挑 3 + 全同形态挑 1）+ 三定位排除 + 二数合 + 三数合 框架（kill9 v5，独立封存/结算/统计）。

口径（面板、封存与统计都按此写死）：
- 候选池 = 面板其他 AI 框架本期已发布的四位反向形态汇总去重（kill1 的 form、
  kill3 的 reverse_parity/reverse_size、kill8 的 parity_form/size_form）；每项如实标注
  来源框架、来源参考期/目标期与是否过期（来源目标期 != 本期目标期即过期，仍纳入）；
- 由 DeepSeek 从候选池中挑选 3 个互不相同的四位形态（大小或单双，按千百十个顺序输出 4 字符），
  作为本期「形态排除项」；池内不足 3 个时允许补足同类型形态并在 reason 说明；
  模型不得改写池内形态，也不得凭空自造（本地校验强制 forms ⊆ 池）；
- 再由 DeepSeek 从 4 个全同形态（大大大大/小小小小/双双双双/单单单单）中挑选 1 个作为本期
  第 4 个「形态排除项」（原样使用，不得与上面 3 个池选形态重复）；
- 形态只可能是大小（大/小）或单双（单/双）两类：大=5-9、小=0-4、单=1/3/5/7/9、双=0/2/4/6/8；
- 形态命中 = 目标期实际四位形态 != 封存形态（反向排除项，逐位约束至少一位不满足即命中）；
- 定位排除（v5 起）：由 DeepSeek 从 千/百/十/个 中挑选 3 个互不相同的位置，每个位置恰好排除 1 个数字（0-9）；
  选择为「软结合形态」：先选定 4 个形态排除项，再按这 4 个排除形态在各位的单双/大小方向（该位多数排除形态为「单」
  时该位实际偏「双」，优先排除单类数字；大小同理）结合各位热码/冷码、上期该位重码与邻码自主决定；
  本地只校验结构（3 个不同位置、每位 1 个 0-9 数字），不硬绑定方向，reason 须说明结合方式；
- 定位排除命中 = 目标期实际开奖该位置数字 != 封存的排除码；
- 二数合：he2_keep 从 02468 恰好保留 4 个不同数字；前 4 球任取 2 位 6 种组合和值个位，
  任一落入保留集即命中；
- 三数合：he3_keep 从 0-9 恰好保留 5 个不同数字且 2 奇 3 偶或 3 奇 2 偶；任取 3 位 4 种组合
  和值个位，任一落入保留集即命中；
- 整组命中 = 3 个池选形态 + 1 个全同形态 + 3 个定位排除 + 二数合 + 三数合 共 9 项同时命中；
- 定位排除每期重选、无连用限制；无合尾（每期完全由模型按形态方向/冷热重选，不沿用上期）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与。

数据落点：
- data/kill9_tail/seals.json        每期封存（同一参考期不可替换）
- data/kill9_tail/settlements.json  开奖后结算（只追加）
"""
from datetime import datetime, timedelta
from hashlib import sha256
from itertools import combinations, product
from pathlib import Path
import json
import os
import re
import time

from stats_history import (beijing_now, business_start, load_snapshot, parse_time,
                           period_slot)
from ai_verified_stats import atomic_json, next_period
from ai_http_observability import emit_http_phase
from ai_prediction_guard import PublicationRejected

ROOT = Path('/root/penta5-runner')
DATA_DIR = ROOT / 'data' / 'kill9_tail'
SEALS_PATH = DATA_DIR / 'seals.json'
SETTLEMENTS_PATH = DATA_DIR / 'settlements.json'
STATE_PATH = ROOT / 'data' / 'state.json'

WINDOW = 50
CLOSE_SECONDS = 70
MIN_REQUEST_SECONDS = 20.0
MAX_TOKENS = 12288
REASONING_EFFORT = 'high'
NONRETRYABLE_HTTP_STATUSES = frozenset((400, 401, 402, 422))

FORM_KINDS = ('size', 'parity')
FORM_LABELS = {'size': '大小形态', 'parity': '单双形态'}
FORM_PALETTE = {'size': '大小', 'parity': '单双'}
REVERSE_LABELS = {'size': '大小排除形态', 'parity': '单双排除形态'}

# 全同形态：v4 起作为第 4 个必选「形态排除项」（每期从 4 个中挑 1 个）；质合/中边 常量只用于 v1 旧封存追溯。
ALL_FORM_VALUES = ('大大大大', '小小小小', '双双双双', '单单单单')
ALL_FORM_LABELS = {'大大大大': '全大', '小小小小': '全小', '双双双双': '全双', '单单单单': '全单'}

# PRIME_DIGITS / MIDDLE_DIGITS 仅用于 v1 旧封存的结算追溯（v2 已改用全同/混合，见 ALL_FORM_VALUES）。
PRIME_DIGITS = frozenset((1, 2, 3, 5, 7))
MIDDLE_DIGITS = frozenset((3, 4, 5, 6))

V1_RULE = 'kill9-tail-v1-four-forms-he2he3'
V2_RULE = 'kill9-tail-v2-size-parity-all-mixed'
V3_RULE = 'kill9-tail-v3-pool-three-forms-he2he3'
V4_RULE = 'kill9-tail-v4-pool-three-plus-all-form-he2he3'
RULE_VERSION = 'kill9-tail-v5-pool-three-all-form-three-kill-he2he3'
COUNTED_FORMS = 3                            # 池选形态数（必须原样取自候选池）
ALL_FORM_PICK = 1                            # 全同形态必选数（4 个全同形态中挑 1 个）
TOTAL_FORMS = COUNTED_FORMS + ALL_FORM_PICK  # 整组形态项数 = 4
POSITION_KEYS = ('q', 'b', 's', 'g')          # 千/百/十/个 四选三
POSITION_LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
POSITION_INDEX = {'q': 0, 'b': 1, 's': 2, 'g': 3}
POSITION_KILL_COUNT = 3                      # 定位排除项数：3 个不同位置，每位恰好 1 码
POSITION_BASIS = 'soft-form-direction-hot-cold-repeat-neighbor'
TOTAL_ITEMS = TOTAL_FORMS + POSITION_KILL_COUNT + 2   # 整组项数 = 4 形态 + 3 定位 + 二数合 + 三数合 = 9
HE2_SOURCE_DIGITS = '02468'
HE2_KEEP_COUNT = 4
HE3_KEEP_COUNT = 5
HE2_PAIRS = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
HE3_TRIPLES = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))

RETRY_INSTRUCTION = (
    '上次响应未通过本地校验（{error}）。请严格按格式重新输出完整 JSON：'
    'forms 必须是恰好 3 个互不相同的四位形态组成的数组，每项 4 个 大/小 字符或 4 个 单/双 字符'
    '（按千百十个顺序），且必须原样来自【本期候选池】（池内不足 3 个时才允许补足同类型形态）；'
    'all_form 必须原样取自 大大大大/小小小小/双双双双/单单单单 之一，且不得与 forms 重复；'
    'positions 必须恰好 3 个键（从 q/b/s/g 中选，值 0-9 整数，三位互不相同）；'
    'he2_keep 为 02468 中 4 个不同数字组成的字符串；'
    'he3_keep 为 0123456789 中 5 个不同数字组成的字符串且必须 2 奇 3 偶或 3 奇 2 偶；'
    'reason 60 字以内，且必须说明定位排除如何结合形态。不要输出任何其他文字、解释或代码块。'
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
        raise ValueError('kill9_tail seals records must be a list')
    return [item for item in records if isinstance(item, dict)]


def load_settlements():
    document = _load_json(SETTLEMENTS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill9_tail settlements records must be a list')
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
    """四位实际形态：size 大/小，parity 单/双，prime 质/合（v1 追溯），middle 中/边（v1 追溯）。"""
    values = [int(value) for value in digits]
    if len(values) != 4:
        raise ValueError('pattern requires four digits')
    if kind == 'parity':
        return ''.join('双' if value % 2 == 0 else '单' for value in values)
    if kind == 'size':
        return ''.join('大' if value >= 5 else '小' for value in values)
    if kind == 'prime':
        return ''.join('质' if value in PRIME_DIGITS else '合' for value in values)
    if kind == 'middle':
        return ''.join('中' if value in MIDDLE_DIGITS else '边' for value in values)
    raise ValueError('unknown pattern kind')


def constraint_ok(char, digit):
    """单字符约束：大=5-9，小=0-4，单=1/3/5/7/9，双=0/2/4/6/8。"""
    if char == '大':
        return digit >= 5
    if char == '小':
        return digit < 5
    if char == '单':
        return digit % 2 == 1
    if char == '双':
        return digit % 2 == 0
    raise ValueError('unknown form char: %s' % char)


def form_satisfied(form, digits):
    """四位是否逐位满足该形态约束（size/parity/all/mixed 通用）。"""
    values = [int(value) for value in digits]
    if len(values) != 4 or not isinstance(form, str) or len(form) != 4:
        return False
    try:
        return all(constraint_ok(form[i], values[i]) for i in range(4))
    except ValueError:
        return False


def form_hit(sealed_form, actual):
    """形态命中 = 目标期实际四位形态 != 封存形态（反向排除项）。

    size/parity/all/mixed 统一逐位约束：至少一位不满足即命中（排除项成立）。
    """
    if not isinstance(sealed_form, str) or len(sealed_form) != 4:
        return False
    values = [int(value) for value in actual]
    if len(values) != 4:
        return False
    try:
        return not all(constraint_ok(sealed_form[i], values[i]) for i in range(4))
    except ValueError:
        return False


def form_space(kind):
    """形态空间：size/parity 各 16 种、all 4 种、mixed 256 种。"""
    if kind in ('size', 'parity'):
        return [''.join(chars) for chars in product(FORM_PALETTE[kind], repeat=4)]
    if kind == 'all':
        return list(ALL_FORM_VALUES)
    if kind == 'mixed':
        return [''.join(chars) for chars in product('大小单双', repeat=4)]
    raise ValueError('unknown form kind')


def rank_forms(rows, kind):
    """形态冷排序：满足次数升序，当前遗漏降序（最冷在前）。

    size/parity 用唯一实际形态计数；all/mixed 逐位约束判定满足（一期可能满足多个候选）。
    """
    space = form_space(kind)
    counts = {form: 0 for form in space}
    last = {}
    if kind in ('size', 'parity'):
        for index, row in enumerate(rows):
            form = pattern_of(row, kind)
            counts[form] += 1
            last[form] = index
    else:
        for index, row in enumerate(rows):
            for form in space:
                if form_satisfied(form, row):
                    counts[form] += 1
                    last[form] = index
    size = len(rows)
    items = [(form, counts[form],
              size if last.get(form) is None else size - last[form] - 1)
             for form in space]
    items.sort(key=lambda item: (item[1], -item[2]))
    return items


POOL_FRAMEWORKS = (
    ('kill1', 'kill1_tail', ('form',)),
    ('kill3', 'kill3_tail', ('reverse_parity', 'reverse_size')),
    ('kill8', 'kill8_tail', ('parity_form', 'size_form')),
)
POOL_SOURCE_LABELS = {
    'kill1': 'kill1 单位置杀码+一形态',
    'kill3': 'kill3 三位置杀码',
    'kill8': 'kill8 双反向冷形态',
}


def form_kind(form):
    """按字符集判定四位形态类型：只含单/双 = parity，只含大/小 = size，其他返回 None。"""
    if not isinstance(form, str) or len(form) != 4:
        return None
    chars = set(form)
    if chars <= {'单', '双'}:
        return 'parity'
    if chars <= {'大', '小'}:
        return 'size'
    return None


def _latest_published_seal(dirname):
    """Read the newest published seal of a sibling framework (read-only)."""
    document = _load_json(ROOT / 'data' / dirname / 'seals.json', {'version': 1, 'records': []})
    records = [item for item in (document.get('records') or [])
               if isinstance(item, dict) and item.get('publication_state') == 'published']
    return records[-1] if records else None


def build_form_pool(reference=None, target=None):
    """面板各框架已发布四位反向形态汇总（kill1/kill3/kill8），按 form 去重后作为本期候选池。

    每项：form / kind / sources / source_labels / ref_period / target_period /
    published_at / stale（来源目标期 != 本期目标期即过期，仍纳入但如实标注）。
    """
    reference = str(reference or '')
    target = str(target or '')
    if not target and reference:
        target = next_period(reference)
    entries = []
    index = {}
    for source, dirname, fields in POOL_FRAMEWORKS:
        seal = _latest_published_seal(dirname)
        if seal is None:
            continue
        ref = str(seal.get('ref_period') or '')
        tgt = str(seal.get('target_period') or '')
        for field in fields:
            form = seal.get(field)
            kind = form_kind(form)
            if kind is None:
                continue
            entry = index.get(form)
            if entry is None:
                entry = {'form': form, 'kind': kind, 'sources': [],
                         'source_labels': [], 'ref_period': ref,
                         'target_period': tgt, 'published_at': seal.get('published_at')}
                entries.append(entry)
                index[form] = entry
            if source not in entry['sources']:
                entry['sources'].append(source)
                entry['source_labels'].append(POOL_SOURCE_LABELS[source])
    for entry in entries:
        entry['stale'] = bool(target) and str(entry.get('target_period') or '') != target
    return entries


def pool_forms(pool):
    """池内形态字符串（保持池顺序）。"""
    result = []
    for entry in (pool or []):
        if isinstance(entry, dict):
            form = entry.get('form')
            if form_kind(form) and form not in result:
                result.append(form)
    return result


def pool_sha256(pool):
    """候选池内容指纹（封存追溯用）。"""
    text = json.dumps([{'form': entry.get('form'), 'kind': entry.get('kind'),
                        'sources': entry.get('sources'),
                        'target_period': entry.get('target_period')}
                       for entry in (pool or []) if isinstance(entry, dict)],
                      ensure_ascii=False, sort_keys=True)
    return sha256(text.encode('utf-8')).hexdigest()


def _history_rows(snapshot=None):
    snapshot = snapshot or load_snapshot(persist=False)
    records = snapshot.get('records') or []
    rows = [list(record.get('numbers') or [])[:4] for record in records]
    rows = [row for row in rows if len(row) == 4]
    if len(rows) < WINDOW + 1:
        raise ValueError('insufficient-kill9-history')
    return snapshot, rows


def _cold_line(ranks, limit=3):
    return '、'.join(f'{form}(出{count}次/遗漏{omission}期)'
                     for form, count, omission in ranks[:limit])


def _keep_set(value, count, allowed, label):
    text = value.strip() if isinstance(value, str) else None
    if (not isinstance(text, str) or len(text) != count
            or len(set(text)) != count
            or any(char not in allowed for char in text)):
        raise ValueError('%s 必须是 %d 个不重复数字' % (label, count))
    return ''.join(sorted(text))


def position_evidence(window):
    """Per-position hot/cold digits plus previous-draw repeat/neighbour evidence."""
    rows = [[int(value) for value in row] for row in (window or [])]
    if not rows:
        raise ValueError('position evidence requires history rows')
    result = {}
    for key, index in POSITION_INDEX.items():
        values = [row[index] for row in rows]
        counts = {digit: values.count(digit) for digit in range(10)}
        last = {}
        for offset, digit in enumerate(values):
            last[digit] = offset
        size = len(values)
        omission = {digit: (size if last.get(digit) is None else size - last[digit] - 1)
                    for digit in range(10)}
        hot = sorted(range(10), key=lambda digit: (-counts[digit], omission[digit], digit))[:3]
        cold = sorted(range(10), key=lambda digit: (counts[digit], -omission[digit], digit))[:3]
        previous = values[-1]
        result[key] = {
            'label': POSITION_LABELS[key],
            'hot': [{'digit': digit, 'count': counts[digit], 'omission': omission[digit]}
                    for digit in hot],
            'cold': [{'digit': digit, 'count': counts[digit], 'omission': omission[digit]}
                     for digit in cold],
            'previous': previous,
            'repeat': previous,
            'neighbor': [(previous - 1) % 10, (previous + 1) % 10],
        }
    return result


def pair_tails(numbers):
    return sorted({sum(int(numbers[index]) for index in pair) % 10 for pair in HE2_PAIRS})


def triple_tails(numbers):
    return sorted({sum(int(numbers[index]) for index in triple) % 10
                   for triple in HE3_TRIPLES})


def he2_candidates():
    return [''.join(sorted(item)) for item in combinations(HE2_SOURCE_DIGITS, HE2_KEEP_COUNT)]


def he3_candidates():
    result = []
    for item in combinations('0123456789', HE3_KEEP_COUNT):
        text = ''.join(sorted(item))
        odd = sum(1 for char in text if int(char) % 2 == 1)
        if 2 <= odd <= 3:
            result.append(text)
    return result


def build_prompt(reference, target, rows, *, panel_snapshot=None, pool=None):
    """Frozen prompt and its audit metadata; rows are the last WINDOW draws."""
    window = rows[-WINDOW:]
    history = ', '.join(''.join(str(int(value)) for value in row) for row in window)
    ranking_lines = {}
    for kind in FORM_KINDS:
        ranks = rank_forms(window, kind)
        ranking_lines[kind] = [
            f'· {form}（出{count}次/遗漏{omission}期）'
            for form, count, omission in ranks]
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
        '· %s：近50期命中 %.2f%%（%s/50），近30期 %.2f%%'
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
        '· %s：近50期命中 %.2f%%（%s/50），近30期 %.2f%%'
        % (candidate, hit50 * 100.0, hits, hit30 * 100.0)
        for candidate, hits, hit30, hit50 in he3_rows[:10]]
    position_evidence_map = position_evidence(window)
    position_lines = []
    for position_key in POSITION_KEYS:
        position_info = position_evidence_map[position_key]
        hot_text = '、'.join('%d（%d次/遗漏%d）' % (item['digit'], item['count'],
                                                  item['omission'])
                            for item in position_info['hot'])
        cold_text = '、'.join('%d（%d次/遗漏%d）' % (item['digit'], item['count'],
                                                   item['omission'])
                             for item in position_info['cold'])
        position_lines.append(
            '· %s：上期该位 %d（重码候选 %d），邻码 %d/%d；近%d期热码 %s；冷码 %s'
            % (POSITION_LABELS[position_key], position_info['previous'],
               position_info['repeat'], position_info['neighbor'][0],
               position_info['neighbor'][1], WINDOW, hot_text, cold_text))
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
    pool_entries = [entry for entry in (pool or []) if isinstance(entry, dict)]
    pool_section = ''
    if pool_entries:
        pool_lines = []
        for entry in pool_entries:
            form = entry.get('form') or ''
            kind = entry.get('kind') or form_kind(form) or '?'
            sources = '、'.join(entry.get('source_labels') or entry.get('sources') or [])
            note = ''
            if entry.get('stale'):
                note = '（来源期 %s，目标期 %s，非本期）' % (
                    entry.get('ref_period') or '-', entry.get('target_period') or '-')
            pool_lines.append('· %s（%s，来源 %s）%s'
                              % (form, FORM_LABELS.get(kind, kind), sources or '-', note))
        pool_section = ('【本期候选池（面板各框架本期已发布四位反向形态去重，共 %d 个；'
                        '必须从池中挑选）】\n' % len(pool_entries)) + '\n'.join(pool_lines) + '\n\n'
    all_lines = ['· %s（%s）' % (form, ALL_FORM_LABELS.get(form, form))
                 for form in ALL_FORM_VALUES]
    all_section = ('【本期全同形态（4 个，必须从中挑选 1 个作为第 4 个形态排除项；'
                   '原样使用，且不得与上面挑出的 3 个池选形态重复）】\n'
                   + '\n'.join(all_lines) + '\n\n')
    position_section = (
        '【本期定位排除依据（千/百/十/个 前四位；从 4 个位置中选 3 个，每位排除 1 个数字 0-9）】\n'
        + '\n'.join(position_lines) + '\n\n'
        '说明（定位排除与形态排除的「软结合」规则）：先按第 1、2 步选定 4 个形态排除项，再统计这 4 个排除形态'
        '在每一位上出现的单双/大小方向（只统计该位带该类字符的排除形态）：若某位多数排除形态为「单」'
        '（如 3/4 个），说明该位实际落在「单」方向的可能性被削弱、整体偏「双」，可优先在该位排除单类数字；'
        '大小同理；方向均分则无偏好。软结合只作参考、由你自主决定（本地不做硬绑定校验），'
        '但必须在 reason 里写明如何结合形态。\n\n')
    prompt = (
        f'以下是真实的最近{WINDOW}期开奖前四位（按时间先后排序，'
        f'最新已开奖参考期号:{reference}，预测目标期号:{target}）：\n'
        f'{history}\n\n'
        '【四位大小形态冷热统计（16 种，按出现次数升序、当前遗漏降序排列；'
        '仅历史统计，不代表未来概率）】\n'
        + '\n'.join(ranking_lines['size']) + '\n\n'
        '【四位单双形态冷热统计（16 种，按出现次数升序、当前遗漏降序排列；'
        '仅历史统计，不代表未来概率）】\n'
        + '\n'.join(ranking_lines['parity']) + '\n\n'
        + pool_section
        + all_section
         + position_section
        + '【二数合保留4码候选（任意两位六组和值个位任一落入保留集即命中；'
        '按近50期真实命中率排序，顺序不代表推荐）】\n'
        + '\n'.join(he2_lines) + '\n\n'
        '【三数合保留5码候选 Top10（任意三位四组和值个位任一落入保留集即命中；'
        '必须 2 奇 3 偶或 3 奇 2 偶；按近50期命中率排序，仅供参考）】\n'
        + '\n'.join(he3_lines) + '\n\n'
        '【任务】\n'
        '1. 先从【本期候选池】中挑选 3 个互不相同的四位形态作为本期 3 个「形态排除项」；'
        '不得改写池内形态、不得自造池外形态；若池内不足 3 个，可补足同类型形态并说明；\n'
        '2. 再从【本期全同形态】的 4 个全同形态中恰好挑选 1 个作为本期第 4 个「形态排除项」；'
        '必须原样使用，且不得与第 1 步挑出的 3 个形态重复；\n'
        '3. 再从【本期定位排除依据】的 千/百/十/个 中挑选恰好 3 个互不相同的位置作为本期 3 个「定位排除项」，'
        '每个位置恰好排除 1 个数字（0-9，输出整数）；选择时必须先按第 1、2 步选出的 4 个形态排除项'
        '在各位的单双/大小方向做软结合（该位多数排除形态为「单」，说明该位实际偏「双」，优先排除单类数字；'
        '大小同理；均分则无偏好；只统计该位带该类字符的排除形态），并结合各位热码/冷码、'
        '上期该位重码与邻码自主权衡；3 个位置必须互不相同，剩余 1 个位置不排除；\n'
        '4. 每个形态命中 = 目标期实际四位形态 != 该形态（排除项，至少一位不满足）；\n'
        '5. 每个定位排除项命中 = 目标期实际开奖该位置数字 != 封存的排除码；\n'
        '6. he2_keep：从 02468 中恰好保留 4 个不同数字（字符串），'
        '目标期任取两位的 6 种和值个位任一落入即命中；\n'
        '7. he3_keep：从 0123456789 中恰好保留 5 个不同数字（字符串），'
        '且必须 2 奇 3 偶或 3 奇 2 偶，目标期任取三位的 4 种和值个位任一落入即命中；\n'
        '8. 命中口径：3 个池选形态 + 1 个全同形态 + 3 个定位排除 + 二数合 + 三数合共 9 项'
        '同时命中才算整组命中；\n'
        '9. 冷热、遗漏只是历史特征，不能据此断言未来概率；不要为换而换，不得声称保证命中；\n'
        '10. 只能使用上面给出的历史、候选池、全同形态、定位排除依据与面板全量上下文；'
        '面板数据只作参考，不得照抄，也不得声称保证命中。\n\n'
        '只输出严格 JSON（不要输出分析过程、代码块或额外文字）：\n'
        '{"forms":["<4个 大/小 字符，按千百十个顺序>","<4个 大/小 字符>",'
        '"<4个 单/双 字符或 4个 大/小 字符>"],'
        '"all_form":"<大大大大/小小小小/双双双双/单单单单 之一>",'
        '"positions":{"q":<0-9整数>,"b":<0-9整数>,"s":<0-9整数>},'
        '"he2_keep":"<4位数字，从02468选>","he3_keep":"<5位数字，2奇3偶或3奇2偶>",'
        '"reason":"60字以内，须说明定位排除如何结合形态"}\n'
        '注意：forms 必须恰好 3 个互不相同的四位形态，每一项都必须是 4 个 大/小 字符'
        '或 4 个 单/双 字符，且必须原样取自【本期候选池】；'
        'all_form 必须是 大大大大/小小小小/双双双双/单单单单 之一且不得与 forms 重复；'
        'positions 必须恰好 3 个键（从 q/b/s/g 中选，q=千位、b=百位、s=十位、g=个位；'
        '每个值为 0-9 的整数，3 个位置互不相同）；'
        'he2_keep 必须是 02468 里的 4 个不同数字；'
        'he3_keep 必须是 5 个不同数字且 2 奇 3 偶或 3 奇 2 偶。'
    )
    if panel_section:
        _marker = prompt.find('【任务】')
        if _marker != -1:
            prompt = prompt[:_marker] + panel_section + prompt[_marker:]
    audit = {
        'version': 'kill9-prompt-v5-pool-three-all-form-three-kill-he2he3',
        'all_form_values': list(ALL_FORM_VALUES),
        'reference': reference,
        'target': target,
        'window': WINDOW,
        'history_sha256': sha256(history.encode('utf-8')).hexdigest(),
        'rows': [[str(int(value)) for value in row] for row in window],
        'parity_ranking': [{'form': form, 'count': count, 'omission': omission}
                           for form, count, omission in rank_forms(window, 'parity')],
        'size_ranking': [{'form': form, 'count': count, 'omission': omission}
                         for form, count, omission in rank_forms(window, 'size')],
        'he2_top': [{'keep': candidate, 'hit50': round(hit50, 6), 'hit30': round(hit30, 6)}
                    for candidate, _hits, hit30, hit50 in he2_rows],
        'he3_top': [{'keep': candidate, 'hit50': round(hit50, 6), 'hit30': round(hit30, 6)}
                    for candidate, _hits, hit30, hit50 in he3_rows[:10]],
        'panel_context_sha256': panel_context_sha,
        'panel_context': panel_snapshot if isinstance(panel_snapshot, dict) else None,
        'form_pool': [{'form': entry.get('form'), 'kind': entry.get('kind'),
                       'sources': entry.get('sources'),
                       'ref_period': entry.get('ref_period'),
                       'target_period': entry.get('target_period'),
                       'published_at': entry.get('published_at'),
                       'stale': entry.get('stale')}
                      for entry in pool_entries],
        'pool_sha256': pool_sha256(pool_entries),
        'positions_basis': POSITION_BASIS,
        'position_evidence': position_evidence_map,
    }
    return prompt, audit


def normalize_response(response, pool=None):
    """Strict local validation; never pad, default or invent anything."""
    if not isinstance(response, dict):
        raise ValueError('响应必须是 JSON 对象')
    raw_forms = response.get('forms')
    if not isinstance(raw_forms, list) or len(raw_forms) != COUNTED_FORMS:
        raise ValueError('forms 必须是恰好 %d 个四位形态组成的数组' % COUNTED_FORMS)
    forms = []
    kinds = []
    for index, value in enumerate(raw_forms, start=1):
        kind = form_kind(value)
        if kind is None:
            raise ValueError('forms[%d] 必须是 4 个 大/小 字符或 4 个 单/双 字符' % index)
        if value in forms:
            raise ValueError('forms 不能重复：%s' % value)
        forms.append(value)
        kinds.append(kind)
    known = pool_forms(pool)
    if known:
        outside = [form for form in forms if form not in known]
        if outside and len(known) >= COUNTED_FORMS:
            raise ValueError('forms 必须原样取自候选池：%s' % '、'.join(outside))
        if outside and len(known) < COUNTED_FORMS:
            missing = [form for form in known if form not in forms]
            if missing:
                raise ValueError('候选池不足 %d 个时必须先选完池内形态：%s'
                                 % (COUNTED_FORMS, '、'.join(missing)))
    all_form = response.get('all_form')
    if not isinstance(all_form, str) or all_form not in ALL_FORM_VALUES:
        raise ValueError('all_form 必须是 4 个全同形态之一：%s'
                         % '、'.join(ALL_FORM_VALUES))
    if all_form in forms:
        raise ValueError('all_form 不能与 forms 重复：%s' % all_form)
    he2_keep = _keep_set(response.get('he2_keep'), HE2_KEEP_COUNT, HE2_SOURCE_DIGITS,
                         '二数合保留4码')
    he3_keep = _keep_set(response.get('he3_keep'), HE3_KEEP_COUNT, '0123456789',
                         '三数合保留5码')
    odd = sum(1 for char in he3_keep if int(char) % 2 == 1)
    if not 2 <= odd <= 3:
        raise ValueError('三数合保留5码必须 2 奇 3 偶或 3 奇 2 偶')
    raw_positions = response.get('positions')
    if not isinstance(raw_positions, dict):
        raise ValueError('positions 必须恰好 3 个位置键对象（q/b/s/g）')
    positions = {}
    for raw_key, raw_value in raw_positions.items():
        key = str(raw_key).strip().lower()
        if key not in POSITION_KEYS:
            raise ValueError('positions 键名只能是 q/b/s/g：%s' % raw_key)
        if key in positions:
            raise ValueError('positions 出现重复位置：%s' % key)
        try:
            digit = int(str(raw_value).strip())
        except (TypeError, ValueError):
            raise ValueError('positions[%s] 必须是 0-9 的整数' % key) from None
        if not 0 <= digit <= 9:
            raise ValueError('positions[%s] 必须是 0-9 的整数' % key)
        positions[key] = str(digit)
    if len(positions) != POSITION_KILL_COUNT:
        raise ValueError('positions 必须恰好 %d 个互不相同的位置（千/百/十/个 中选 3）'
                         % POSITION_KILL_COUNT)
    ordered_positions = {key: positions[key] for key in POSITION_KEYS if key in positions}
    reason = str(response.get('reason', ''))
    try:
        reason.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('reason 必须是合法 UTF-8 文本') from None
    return {
        'forms': forms,
        'form_kinds': kinds,
        'all_form': all_form,
        'positions': ordered_positions,
        'he2_keep': he2_keep,
        'he3_keep': he3_keep,
        'reason': reason[:200],
    }


def resolve_model():
    for name in ('PENTA5_MODEL_KILL9', 'PENTA5_MODEL_KILLER', 'PENTA5_DEEPSEEK_MODEL'):
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
        'request_policy_version': 'kill9-high-12k-deadline-v1',
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


def request_prediction(prompt, reference, target, deadline, log=print, value_check=None,
                       pool=None):
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
            raise PublicationRejected('insufficient-time-for-kill9-request')
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
            prediction = normalize_response(_extract_json(message.get('content')), pool)
            if value_check is not None:
                value_check(prediction)
            meta = _metadata(reference, attempt, model, api_key, envelope,
                             time.monotonic() - started)
            log('AI_RESPONSE_META ' + json.dumps(meta, ensure_ascii=True, allow_nan=False,
                                                separators=(',', ':')))
            log(f'kill9 response validated; ref={reference}; attempt={attempt}; publication pending.')
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
            log(f'kill9 attempt {attempt}/3 rejected; error_type={last_error}{status_note}; not published.')
            if status in NONRETRYABLE_HTTP_STATUSES:
                log(f'kill9 nonretryable provider HTTP {status}; stop retries; not published.')
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
    raise RuntimeError(f'kill9 inference failed after {attempts_used} attempt(s): '
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
        'forms': list(prediction['forms']),
        'form_kinds': list(prediction['form_kinds']),
        'all_form': prediction['all_form'],
        'positions': dict(prediction['positions']),
        'position_basis': POSITION_BASIS,
        'position_evidence': audit.get('position_evidence'),
        'form_pool': audit.get('form_pool'),
        'pool_sha256': audit.get('pool_sha256'),
        'he2_keep': prediction['he2_keep'],
        'he3_keep': prediction['he3_keep'],
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
        raise PublicationRejected('existing-kill9-seal-cannot-be-replaced')
    seals.append(seal)
    _write_seals(seals)
    return seal


def mark_published(reference, published_at=None):
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        raise ValueError('kill9 seal missing before publication')
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
        rule = seal.get('rule_version')
        is_v1 = rule == V1_RULE
        sealed_positions = (seal.get('positions')
                            if isinstance(seal.get('positions'), dict) else {})
        is_v5 = rule == RULE_VERSION or bool(sealed_positions)
        is_v4 = (not is_v5) and (rule == V4_RULE or (
            isinstance(seal.get('all_form'), str) and isinstance(seal.get('forms'), list)))
        is_v3 = (not is_v5) and (not is_v4) and (bool(seal.get('forms')) or rule == V3_RULE)
        position_entries = {}
        if is_v4 or is_v5:
            forms = []
            sealed_forms = seal.get('forms') if isinstance(seal.get('forms'), list) else []
            sealed_kinds = (seal.get('form_kinds')
                            if isinstance(seal.get('form_kinds'), list) else [])
            for index, sealed_form in enumerate(sealed_forms):
                kind = sealed_kinds[index] if index < len(sealed_kinds) else form_kind(sealed_form)
                actual_form = pattern_of(actual, kind) if kind in ('size', 'parity') else '-'
                forms.append({'form': sealed_form, 'kind': kind or '-',
                              'actual': actual_form,
                              'hit': bool(form_hit(sealed_form, actual))})
            all_form = seal.get('all_form')
            forms.append({'form': all_form, 'kind': 'all',
                          'actual': pattern_of(actual, 'size') + '/' + pattern_of(actual, 'parity'),
                          'hit': bool(form_hit(all_form, actual))})
            if is_v5:
                for key in POSITION_KEYS:
                    if key not in sealed_positions:
                        continue
                    kill_text = str(sealed_positions.get(key)).strip()
                    actual_text = str(actual[POSITION_INDEX[key]])
                    position_entries[key] = {
                        'kill': kill_text,
                        'actual': actual_text,
                        'hit': bool(kill_text != actual_text),
                    }
        elif is_v3:
            forms = []
            sealed_forms = seal.get('forms') if isinstance(seal.get('forms'), list) else []
            sealed_kinds = (seal.get('form_kinds')
                            if isinstance(seal.get('form_kinds'), list) else [])
            for index, sealed_form in enumerate(sealed_forms):
                kind = sealed_kinds[index] if index < len(sealed_kinds) else form_kind(sealed_form)
                actual_form = pattern_of(actual, kind) if kind in ('size', 'parity') else '-'
                forms.append({'form': sealed_form, 'kind': kind or '-',
                              'actual': actual_form,
                              'hit': bool(form_hit(sealed_form, actual))})
        elif is_v1:
            forms = {}
            for key, field, palette in (('parity', 'parity_form', '单双'),
                                        ('size', 'size_form', '大小'),
                                        ('prime', 'prime_form', '质合'),
                                        ('middle', 'middle_form', '中边')):
                sealed_form = seal.get(field)
                actual_form = pattern_of(actual, key)
                hit = (isinstance(sealed_form, str) and len(sealed_form) == 4
                       and all(char in palette for char in sealed_form)
                       and actual_form != sealed_form)
                forms[key] = {'form': sealed_form, 'actual': actual_form, 'hit': bool(hit)}
        else:
            forms = {}
            for key, field in (('size', 'size_form'), ('parity', 'parity_form'),
                               ('all', 'all_form'), ('mixed', 'mixed_form')):
                sealed_form = seal.get(field)
                hit = form_hit(sealed_form, actual)
                actual_form = (pattern_of(actual, key) if key in ('size', 'parity')
                               else pattern_of(actual, 'size') + '/' + pattern_of(actual, 'parity'))
                forms[key] = {'form': sealed_form, 'actual': actual_form, 'hit': bool(hit)}
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
        hit_list = [item['hit'] for item in
                    (forms if isinstance(forms, list) else forms.values())]
        if is_v5:
            position_ok = (len(position_entries) == POSITION_KILL_COUNT
                           and all(entry.get('hit') is True
                                   for entry in position_entries.values()))
            group_hit = bool(len(hit_list) == TOTAL_FORMS and all(hit_list) and position_ok
                             and keep_entries['he2_keep']['hit']
                             and keep_entries['he3_keep']['hit'])
        else:
            expected_items = TOTAL_FORMS if is_v4 else (COUNTED_FORMS if is_v3 else 4)
            group_hit = bool(len(hit_list) == expected_items and all(hit_list)
                             and keep_entries['he2_keep']['hit']
                             and keep_entries['he3_keep']['hit'])
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
            'he2_keep': keep_entries['he2_keep'],
            'he3_keep': keep_entries['he3_keep'],
            'group_hit': group_hit,
            'verified': bool(verified),
        }
        if isinstance(forms, list):
            settlement['forms'] = forms
        else:
            settlement.update(forms)
        if is_v5:
            settlement['positions'] = position_entries
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

    def _form_entries(item):
        forms = item.get('forms')
        if isinstance(forms, list):
            return forms
        entries = []
        for key in ('size', 'parity', 'all', 'mixed', 'prime', 'middle'):
            value = item.get(key)
            if isinstance(value, dict):
                entries.append(value)
        return entries

    form_hits = [0] * TOTAL_FORMS
    for item in todays:
        for index, entry in enumerate(_form_entries(item)[:TOTAL_FORMS]):
            if isinstance(entry, dict) and entry.get('hit') is True:
                form_hits[index] += 1
    he2_hits = sum(1 for item in todays if (item.get('he2_keep') or {}).get('hit') is True)
    he3_hits = sum(1 for item in todays if (item.get('he3_keep') or {}).get('hit') is True)
    position_totals = {key: {'bets': 0, 'hits': 0} for key in POSITION_KEYS}
    for item in todays:
        entries = item.get('positions')
        if not isinstance(entries, dict):
            continue
        for key in POSITION_KEYS:
            entry = entries.get(key)
            if not isinstance(entry, dict) or entry.get('hit') is None:
                continue
            position_totals[key]['bets'] += 1
            if entry.get('hit') is True:
                position_totals[key]['hits'] += 1
    position_bets = sum(entry['bets'] for entry in position_totals.values())
    position_hits = sum(entry['hits'] for entry in position_totals.values())
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
        'forms': [{'hits': form_hits[index], 'bets': total,
                   'rate': (form_hits[index] / total) if total else None}
                  for index in range(TOTAL_FORMS)],
        'he2_keep': {'hits': he2_hits, 'bets': total,
                     'rate': (he2_hits / total) if total else None},
        'he3_keep': {'hits': he3_hits, 'bets': total,
                     'rate': (he3_hits / total) if total else None},
        'positions': {key: {'bets': position_totals[key]['bets'],
                            'hits': position_totals[key]['hits'],
                            'rate': ((position_totals[key]['hits'] / position_totals[key]['bets'])
                                     if position_totals[key]['bets'] else None)}
                      for key in POSITION_KEYS},
        'position_total': position_bets,
        'position_hits': position_hits,
        'position_rate': (position_hits / position_bets) if position_bets else None,
        'item_total': TOTAL_ITEMS,
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
        raise ValueError('invalid-kill9-target-period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target


def build_attack(seal):
    """攻击语：3 个池选形态 + 1 个全同形态 + 3 个定位排除 + 二数合保留4码 + 三数合保留5码。"""
    if not isinstance(seal, dict):
        raise ValueError('attack requires a sealed prediction')
    parts = []
    forms = seal.get('forms')
    if isinstance(forms, list):
        for value in forms:
            if isinstance(value, str) and len(value) == 4:
                parts.append('除' + value)
    else:
        for field in ('size_form', 'parity_form', 'all_form', 'mixed_form'):
            value = seal.get(field)
            if isinstance(value, str) and len(value) == 4:
                parts.append('除' + value)
    if isinstance(forms, list):
        all_form = seal.get('all_form')
        if isinstance(all_form, str) and len(all_form) == 4:
            parts.append('除' + all_form)
    positions = seal.get('positions')
    if isinstance(positions, dict):
        for key in POSITION_KEYS:
            value = positions.get(key)
            if value is None:
                continue
            parts.append(POSITION_LABELS[key] + '除' + str(value))
    he2_keep = seal.get('he2_keep')
    he3_keep = seal.get('he3_keep')
    if he2_keep and he3_keep:
        parts.append('二数合' + str(he2_keep) + '三数合' + str(he3_keep) + '各')
    return ''.join(parts)
