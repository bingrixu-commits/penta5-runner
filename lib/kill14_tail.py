"""定位排除（kill14 独立框架，v2，AI 每期选码）。

口径（写死，面板脚注同步展示）：
- 四个位置（千/百/十/个）每期各排除 1 个码（0-9），整组固定 4 项全部计入整组；
- 面板依据：近 70 / 50 / 30 / 15 期各位置冷码榜（窗口内出现次数升序、同次数当前遗漏降序、再按码升序），
  以及按「冷码榜前 3 码杀号」口径统计的逐位置当前连败 / 连中状态（与面板冷码杀号战绩同口径）；
- AI 规则：某位置在 70 / 50 / 30 / 15 任一个窗口处于「连败」时，该位置排除码必须取该位置冷码第 1 个
  （候选 = 所有处于连败窗口的冷码第 1 个，由 AI 任选其一）；四个窗口都不连败的位置由 AI 自行选 1 码；
- 不设沿用 / 换码（hold），每期严格由 AI 重新选择并封存；
- 命中 = 该位置实际开奖号 != 该位置排除码（单项随机 90%）；
- 整组命中 = 4 项同时命中；整组随机基线 = (9/10)^4 = 65.61%，
  用 10^4 开奖空间精确枚举校验（等价闭式 6561/10000）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。

数据落点 data/kill14_tail/{seals,settlements}.json；由 scripts/run_kill14_tail.py 每期运行。
"""
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import time

import requests

from stats_history import beijing_now, business_start, load_snapshot, parse_time, period_slot
from ai_verified_stats import atomic_json, next_period
from ai_prediction_guard import PublicationRejected
from ai_http_observability import emit_http_phase
from ai_call_control import require_ai_enabled
from ai_provider_policy import strict_json_object
from app_secrets import get_secret

ROOT = Path('/root/penta5-runner')
DATA_DIR = ROOT / 'data' / 'kill14_tail'
SEALS_PATH = DATA_DIR / 'seals.json'
SETTLEMENTS_PATH = DATA_DIR / 'settlements.json'
STATE_PATH = ROOT / 'data' / 'state.json'

WINDOW = 70
# 冷码窗口：70 / 50 为 v1 原口径，30 / 15 为用户 2026-11-10 新增的短窗冷码框架（任一窗口连败即强制）。
STATS_WINDOWS = (70, 50, 30, 15)
TOP = 3
CLOSE_SECONDS = 70
MIN_REQUEST_SECONDS = 20.0
MAX_ATTEMPTS = 3
MAX_TOKENS = 12288
REASONING_EFFORT = 'high'
RETRY_INSTRUCTION = ('上一次输出不符合要求（{error}）。请只输出严格 JSON，'
                     '并严格遵守【任务】中的强制规则与格式。')
NONRETRYABLE_HTTP_STATUSES = frozenset((400, 401, 402, 422))
PROVIDER_URL = 'https://api.deepseek.com/chat/completions'

POSITIONS = (
    ('q', '千', 0),
    ('b', '百', 1),
    ('s', '十', 2),
    ('g', '个', 3),
)
POSITION_KEYS = tuple(key for key, _label, _index in POSITIONS)
POSITION_LABELS = {key: label for key, label, _index in POSITIONS}
POSITION_INDEX = {key: index for key, _label, index in POSITIONS}
ATTACK_ORDER = '1234567890'
ATTACK_POSITION_LABELS = {'q': '千', 'b': '百', 's': '十', 'g': '个'}

RULE_VERSION = 'kill14-tail-v2-four-position-cold-multi-window'
PROMPT_VERSION = 'kill14-tail-prompt-v2-four-position-cold-multi-window'
# v1 封存 / 结算按自身 rule_version 保留，不回填、不删除。
LEGACY_RULES = ('kill14-tail-v1-four-position-cold-on-loss',)
EXPECTED_ITEMS = 4
# 10^4 开奖空间 x 每位置 10 码配置，精确枚举：4 位置各自 (9/10) 独立 -> 6561/10000。
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
    """Return all sealed predictions in chronological order."""
    document = _load_json(SEALS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill14 seals records must be a list')
    return [item for item in records if isinstance(item, dict)]


def load_settlements():
    document = _load_json(SETTLEMENTS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill14 settlements records must be a list')
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


# ---------------------------------------------------------------------------
# 冷码榜 / 连败状态（与面板冷码杀号战绩同口径）
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


def build_rankings(rows, window=WINDOW):
    """Rank the 10 digits of each position over the trailing window, coldest first.

    cold order = (occurrences asc, current omission desc, digit asc).
    """
    rows = _normalize_rows(rows)
    if len(rows) < window:
        raise ValueError('insufficient-kill14-history')
    window_rows = rows[-window:]
    size = len(window_rows)
    ranked_by_position = {}
    for key, label, index in POSITIONS:
        values = [row[index] for row in window_rows]
        counts = [0] * 10
        last_index = {}
        for position, value in enumerate(values):
            counts[value] += 1
            last_index[value] = position
        ranked = []
        for code in range(10):
            count = counts[code]
            omission = (size - 1 - last_index[code]) if code in last_index else size
            ranked.append({
                'key': key,
                'label': label,
                'code': str(code),
                'count': count,
                'omission': omission,
            })
        ranked.sort(key=lambda item: (item['count'], -item['omission'], int(item['code'])))
        for rank, item in enumerate(ranked, 1):
            item['rank'] = rank
        ranked_by_position[key] = ranked
    return ranked_by_position


def _public_item(item):
    return {
        'key': item['key'], 'label': item['label'], 'code': item['code'],
        'count': item['count'], 'omission': item['omission'], 'rank': item['rank'],
    }


def _streak(values):
    win = loss = max_win = max_loss = 0
    for hit in values:
        if hit:
            win += 1
            loss = 0
            max_win = max(max_win, win)
        else:
            loss += 1
            win = 0
            max_loss = max(max_loss, loss)
    return {
        'current_win_streak': win,
        'current_loss_streak': loss,
        'max_win_streak': max_win,
        'max_loss_streak': max_loss,
    }


def position_state(rows, window=WINDOW, top=TOP):
    """Current win/loss streak per position under the panel's cold-top3 kill rule."""
    rows = _normalize_rows(rows)
    if len(rows) <= window:
        raise ValueError('insufficient-kill14-history')
    hits = {key: [] for key, _label, _index in POSITIONS}
    for index in range(window, len(rows)):
        trailing = rows[index - window:index]
        ranked = build_rankings(trailing, window=window)
        for key, _label, position in POSITIONS:
            order = tuple(item['code'] for item in ranked[key][:top])
            hits[key].append(str(rows[index][position]) not in order)
    positions = {}
    for key, _label, _index in POSITIONS:
        state = _streak(hits[key])
        state['samples'] = len(hits[key])
        positions[key] = state
    return {'window': window, 'top': top, 'samples': len(rows) - window,
            'positions': positions}


def build_context(rows):
    """Cold rankings, panel-equivalent streak state and enforcement list for one reference."""
    rows = _normalize_rows(rows)
    if len(rows) < max(STATS_WINDOWS):
        raise ValueError('insufficient-kill14-history')
    windows = {}
    for window in STATS_WINDOWS:
        ranked = build_rankings(rows[-window:], window=window)
        windows[str(window)] = {
            'window': window,
            'cold_top3': {key: [_public_item(item) for item in ranked[key][:TOP]]
                          for key, _label, _index in POSITIONS},
            'cold_first': {key: ranked[key][0]['code'] for key, _label, _index in POSITIONS},
        }
    states = {str(window): position_state(rows, window=window) for window in STATS_WINDOWS}
    enforced = []
    for key, label, _index in POSITIONS:
        loss_streak = {str(window): states[str(window)]['positions'][key]['current_loss_streak']
                       for window in STATS_WINDOWS}
        losing_windows = [window for window in STATS_WINDOWS
                          if loss_streak[str(window)] > 0]
        if not losing_windows:
            continue
        allowed = []
        for window in losing_windows:
            code = windows[str(window)]['cold_first'][key]
            if code not in allowed:
                allowed.append(code)
        enforced.append({
            'key': key,
            'label': label,
            'allowed': allowed,
            'loss_streak': loss_streak,
            'losing_windows': losing_windows,
        })
    latest_digits = {key: str(rows[-1][POSITION_INDEX[key]]) for key, _label, _index in POSITIONS}
    return {
        'windows': windows,
        'states': states,
        'enforced': enforced,
        'latest_digits': latest_digits,
        'history_rows': len(rows),
    }


# ---------------------------------------------------------------------------
# AI prompt
# ---------------------------------------------------------------------------
def _cold_line(items):
    marks = ('①', '②', '③')
    parts = []
    for index, item in enumerate(items):
        mark = marks[index] if index < len(marks) else str(index + 1)
        parts.append('%s %s（出%s次 / 遗漏%s期）'
                     % (mark, item['code'], item['count'], item['omission']))
    return '｜'.join(parts)


def _state_line(state):
    if state['current_loss_streak'] > 0:
        return '连败 %d' % state['current_loss_streak']
    if state['current_win_streak'] > 0:
        return '连中 %d' % state['current_win_streak']
    return '—'


def build_prompt(reference, target, rows, *, context=None, previous_seal=None,
                 panel_snapshot=None):
    """Frozen prompt and audit metadata; rows are the chronological first-four draws."""
    rows = _normalize_rows(rows)
    context = context or build_context(rows)
    window_rows = rows[-WINDOW:]
    history = ', '.join(''.join(str(value) for value in row) for row in window_rows)
    cold_lines = []
    for window in STATS_WINDOWS:
        block = context['windows'][str(window)]
        cold_lines.append('【近 %d 期各位置冷码榜（出现次数升序、同次数当前遗漏降序、再按码升序）】'
                          % window)
        for key, label, _index in POSITIONS:
            cold_lines.append('· %s：%s' % (label, _cold_line(block['cold_top3'][key])))
        cold_lines.append('')
    state_lines = []
    for window in STATS_WINDOWS:
        state = context['states'][str(window)]
        state_lines.append('【近 %d 期冷码杀号战绩口径的逐位置当前状态（每位置杀冷码榜前 %d 码）】'
                           % (window, TOP))
        for key, label, _index in POSITIONS:
            state_lines.append('· %s：%s' % (label, _state_line(state['positions'][key])))
        state_lines.append('')
    enforced_lines = []
    for item in context.get('enforced') or []:
        allowed = ' / '.join(str(code) for code in item['allowed'])
        losing = item.get('losing_windows') or []
        streak_text = '、'.join('%d 期连败 %d' % (window, item['loss_streak'][str(window)])
                                for window in losing)
        enforced_lines.append(
            '· %s：%s → 排除码必须取该位置冷码第 1 个，允许 %s'
            '（候选为上述连败窗口的冷码第 1 个，任选其一）'
            % (item['label'], streak_text, allowed))
    if not enforced_lines:
        enforced_lines.append('· 本期没有处于连败的窗口位置：四个位置都由你自主选 1 码。')
    forced_keys = {item['key'] for item in context.get('enforced') or []}
    free_labels = '、'.join(label for key, label, _index in POSITIONS if key not in forced_keys) or '无'
    latest = rows[-1]
    latest_line = ' · '.join('%s %d' % (label, latest[index])
                             for _key, label, index in POSITIONS)
    previous_line = ''
    if isinstance(previous_seal, dict) and previous_seal.get('positions'):
        parts = []
        for item in previous_seal.get('positions') or []:
            if isinstance(item, dict):
                parts.append('%s杀%s' % (item.get('label') or '-', item.get('kill')))
        previous_line = ('上期封存（参考期 %s → 目标期 %s）：%s'
                         % (previous_seal.get('ref_period'), previous_seal.get('target_period'),
                            ' · '.join(parts)))
    panel_section = ''
    panel_context_text = None
    panel_context_sha = None
    if isinstance(panel_snapshot, dict):
        from panel_context import prompt_text as _panel_prompt_text
        panel_context_text, panel_context_sha = _panel_prompt_text(panel_snapshot)
        panel_section = (
            '【面板全量上下文（同一面板其他框架最近发布 / 战绩；只含已发布数据，各条自带 '
            'reference/target；仅供自主决策参考，不是未来概率保证）】'
            + chr(10) + panel_context_text + chr(10) + chr(10))
    prompt = (
        '以下是真实的最近 %d 期开奖前四位（按时间先后排序，'
        '最新已开奖参考期号:%s，预测目标期号:%s）：\n'
        '%s\n\n'
        '【各位置冷码榜（%s，各位置 Top3）】\n'
        '%s\n'
        '%s'
        '【最新一期开奖前四位（参考期 %s）】\n'
        '· %s\n\n'
        '【本期强制规则（来自面板连败判定）】\n'
        '%s\n\n'
        '【任务】\n'
        '1. 对 千 / 百 / 十 / 个 四个位置各排除 1 个数字（0-9），固定 4 项全部计入整组，'
        '不得少于也不得多于 4 项；\n'
        '2. 强制规则：上面列出的「连败位置」的排除码必须取该位置冷码第 1 个'
        '（必须等于给出的允许值之一；允许值来自该位置处于连败的窗口的冷码第 1 个）；'
        '其余位置（%s）由你结合面板数据自主选 1 码；\n'
        '3. 命中口径：目标期该位置实际开奖号不等于该位置排除码即该项命中（单项随机 90%%）；'
        '4 项同时命中才算整组命中（整组随机基线 65.61%%）；\n'
        '4. 冷码、连败 / 连中、遗漏只是历史特征，不能据此断言未来概率；不要为换而换，'
        '不得声称保证命中；\n'
        '5. 只能使用上面给出的历史与面板全量上下文；面板数据只作参考，不得照抄。\n\n'
        '只输出严格 JSON（不要输出分析过程、代码块或额外文字）：\n'
        '{"positions":[{"key":"q","kill":3},{"key":"b","kill":0},'
        '{"key":"s","kill":8},{"key":"g","kill":2}],"reason":"40字以内"}\n'
        '注意：positions 必须恰好 4 项，key 分别为 q / b / s / g（顺序不限），'
        'kill 为 0-9 整数；不需要输出其它字段。'
    ) % (WINDOW, reference, target, history,
         ' / '.join('%d 期窗口' % window for window in STATS_WINDOWS),
         '\n'.join(cold_lines), '\n'.join(state_lines), reference, latest_line,
         '\n'.join(enforced_lines), free_labels)
    if panel_section:
        marker = prompt.find('【任务】')
        if marker != -1:
            prompt = prompt[:marker] + panel_section + prompt[marker:]
    audit = {
        'version': PROMPT_VERSION,
        'reference': reference,
        'target': target,
        'window': WINDOW,
        'history_sha256': sha256(history.encode('utf-8')).hexdigest(),
        'rows': [[str(value) for value in row] for row in window_rows],
        'windows': sorted(STATS_WINDOWS),
        'cold_ranking': {str(window): context['windows'][str(window)]['cold_top3']
                         for window in STATS_WINDOWS},
        'cold_first': {str(window): context['windows'][str(window)]['cold_first']
                       for window in STATS_WINDOWS},
        'position_state': {str(window): context['states'][str(window)]['positions']
                           for window in STATS_WINDOWS},
        'enforced_positions': [item['key'] for item in context.get('enforced') or []],
        'enforced_allowed': {item['key']: item['allowed']
                             for item in context.get('enforced') or []},
        'latest_row': {'period': str(reference), 'digits': context['latest_digits']},
        'previous_seal': {'ref_period': previous_seal.get('ref_period'),
                          'target_period': previous_seal.get('target_period')}
                         if isinstance(previous_seal, dict) else None,
        'panel_context_sha256': panel_context_sha,
        'panel_context': panel_context_text,
    }
    return prompt, audit


# ---------------------------------------------------------------------------
# 响应校验（固定 4 项 + 连败位置强制冷码第 1）
# ---------------------------------------------------------------------------
def _kill_digit(value):
    if isinstance(value, bool):
        raise ValueError('kill14 kill must be a digit 0-9')
    if isinstance(value, int) and 0 <= value <= 9:
        return str(value)
    text = str(value if value is not None else '').strip()
    if len(text) == 1 and text.isdigit():
        return text
    raise ValueError('kill14 kill must be a digit 0-9: %r' % (value,))


def _check_enforced(positions, context):
    mapping = {item['key']: str(item['kill']) for item in positions}
    for item in context.get('enforced') or []:
        key = item['key']
        allowed = [str(code) for code in item['allowed']]
        if mapping.get(key) not in allowed:
            raise ValueError(
                '连败位置 %s 的排除码必须取该位置冷码第 1 个（允许 %s），实际 %s'
                % (POSITION_LABELS.get(key, key), '/'.join(allowed), mapping.get(key)))


def normalize_response(response, context=None):
    if not isinstance(response, dict):
        raise ValueError('kill14 output must be a JSON object')
    raw = response.get('positions')
    if not isinstance(raw, list) or len(raw) != EXPECTED_ITEMS:
        raise ValueError('kill14 positions must be exactly four items')
    items = {}
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError('kill14 position item must be an object')
        key = str(item.get('key') or '').strip().lower()
        if key not in POSITION_KEYS:
            raise ValueError('kill14 unknown position key: %r' % (key,))
        if key in items:
            raise ValueError('kill14 duplicate position key: %s' % key)
        items[key] = {'key': key, 'label': POSITION_LABELS[key],
                      'kill': _kill_digit(item.get('kill'))}
    missing = [key for key in POSITION_KEYS if key not in items]
    if missing:
        raise ValueError('kill14 missing positions: %s' % ','.join(missing))
    positions = [items[key] for key in POSITION_KEYS]
    if context is not None:
        _check_enforced(positions, context)
    reason = str(response.get('reason') or '').strip()[:200]
    return {'positions': positions, 'reason': reason}


# ---------------------------------------------------------------------------
# DeepSeek 调用（与既有 AI 框架同策略：3 次尝试、失败可重试、关盘前预留）
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
    for name in ('PENTA5_MODEL_KILL14', 'PENTA5_MODEL_KILLER', 'PENTA5_DEEPSEEK_MODEL'):
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


def _extract_json(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('kill14 empty provider content')
    payload = strict_json_object(text)
    if not isinstance(payload, dict):
        raise ValueError('kill14 response must be a JSON object')
    return payload


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
        raise RuntimeError('kill14 missing DeepSeek api key')
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
        raise RuntimeError('kill14 empty provider response')
    message = (choices[0] or {}).get('message') or {}
    content = str(message.get('content') or '')
    if not content:
        raise RuntimeError('kill14 empty provider content')
    return {
        'content': content,
        'model': (document.get('model') or model_name) if isinstance(document, dict) else model_name,
        'usage': (document.get('usage') or {}) if isinstance(document, dict) else {},
    }


def request_prediction(prompt, reference, target, deadline, log=print, value_check=None):
    """Ask DeepSeek for the four exclusion digits within the publishing window."""
    messages = [{'role': 'user', 'content': prompt}]
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        require_ai_enabled(clock=beijing_now)
        remaining = (deadline - beijing_now()).total_seconds()
        if remaining < MIN_REQUEST_SECONDS:
            raise PublicationRejected('insufficient-time-for-kill14-request')
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
                prediction = normalize_response(_extract_json(envelope['content']))
                if value_check is not None:
                    value_check(prediction)
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
            raise PublicationRejected('insufficient-time-for-kill14-request')
        pause = max(0.0, min(4.0, remaining - 25.0))
        if pause:
            time.sleep(pause)
        messages = [{'role': 'user',
                     'content': prompt + chr(10) + chr(10)
                     + RETRY_INSTRUCTION.format(error=last_error or 'unknown')}]
    raise RuntimeError('kill14 inference failed after %d attempt(s): %s'
                       % (MAX_ATTEMPTS, last_error or 'unknown'))


# ---------------------------------------------------------------------------
# 封存 / 发布 / 结算
# ---------------------------------------------------------------------------
def _enrich_positions(prediction, context):
    enforced_keys = {item['key'] for item in context.get('enforced') or []}
    enriched = []
    for item in prediction.get('positions') or []:
        key = item['key']
        kill = str(item['kill'])
        basis = 'ai'
        detail = None
        for window in STATS_WINDOWS:
            block = context['windows'][str(window)]
            if kill == str(block['cold_first'].get(key)):
                basis = 'cold%d-rank1' % window
                detail = next((entry for entry in block['cold_top3'].get(key) or []
                               if str(entry.get('code')) == kill), None)
                break
        if detail is None:
            for window in STATS_WINDOWS:
                detail = next((entry for entry in
                               context['windows'][str(window)]['cold_top3'].get(key) or []
                               if str(entry.get('code')) == kill), None)
                if detail is not None:
                    break
        enriched.append({
            'key': key,
            'label': POSITION_LABELS[key],
            'kill': kill,
            'basis': basis,
            'enforced': key in enforced_keys,
            'count': (detail or {}).get('count'),
            'omission': (detail or {}).get('omission'),
            'rank': (detail or {}).get('rank'),
        })
    return enriched


def create_seal(reference, target, reference_open_time, prediction, rows, *,
                context=None, prompt=None, audit=None, generated_at=None,
                response_meta=None, extra=None):
    """Freeze the AI selection before the target period opens."""
    reference = str(reference)
    target = str(target)
    if not reference or not target:
        raise ValueError('kill14 reference/target period required')
    rows = _normalize_rows(rows)
    if len(rows) < WINDOW:
        raise ValueError('insufficient-kill14-history')
    context = context or build_context(rows)
    if not isinstance(prediction, dict) or not isinstance(prediction.get('positions'), list):
        raise ValueError('kill14 prediction requires positions')
    if len(prediction['positions']) != EXPECTED_ITEMS:
        raise ValueError('kill14 prediction requires four positions')
    positions = _enrich_positions(prediction, context)
    generated_at = generated_at or beijing_now()
    window_rows = rows[-WINDOW:]
    payload = json.dumps(window_rows, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'))
    audit = audit or {}
    panel_context_text = audit.get('panel_context')
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
        'positions': positions,
        'position_count': len(positions),
        'enforced_positions': [item['key'] for item in context.get('enforced') or []],
        'enforced_allowed': {item['key']: item['allowed']
                             for item in context.get('enforced') or []},
        'cold_ranking': {str(window): context['windows'][str(window)]['cold_top3']
                         for window in STATS_WINDOWS},
        'position_state': {str(window): context['states'][str(window)]['positions']
                           for window in STATS_WINDOWS},
        'reason': str((prediction or {}).get('reason') or '')[:200],
        'window': len(window_rows),
        'expected_items': EXPECTED_ITEMS,
        'expected_group_rate': EXPECTED_GROUP_RATE,
        'expected_basis': EXPECTED_BASIS,
        'input_sha256': sha256(payload.encode('utf-8')).hexdigest(),
        'prompt_sha256': sha256(prompt.encode('utf-8')).hexdigest() if prompt else None,
        'panel_context_sha256': audit.get('panel_context_sha256'),
        'panel_context': panel_context_text,
        'model': (response_meta or {}).get('model'),
        'model_label': (response_meta or {}).get('model_label'),
        'model_metadata': response_meta,
        'data_source': 'data/stats_draw_history.json:first-four-balls',
    }
    if isinstance(extra, dict):
        for key, value in extra.items():
            if key not in seal:
                seal[key] = value
    return seal


def save_seal(seal):
    reference = str(seal.get('ref_period') or '')
    if not reference:
        raise ValueError('kill14 seal requires ref_period')
    seals = load_seals()
    if find_seal(seals, reference) is not None:
        raise PublicationRejected('existing-kill14-seal-cannot-be-replaced')
    seals.append(seal)
    _write_seals(seals)


def mark_published(reference, published_at=None):
    reference = str(reference)
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        raise ValueError('kill14-seal-not-found')
    if seal.get('publication_state') == 'published' and seal.get('published_at'):
        return seal
    seal['publication_state'] = 'published'
    seal['published_at'] = (published_at or beijing_now()).isoformat()
    _write_seals(seals)
    return seal


def mark_publication_failed(reference, error):
    reference = str(reference)
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        return
    seal['publication_state'] = 'prepared'
    seal['publication_error'] = str(error)[:120]
    _write_seals(seals)


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
        target = str(seal.get('target_period') or next_period(seal.get('ref_period')) or '')
        if not target or target in settled:
            continue
        record = records.get(target)
        if record is None:
            continue
        numbers = [str(value) for value in (record.get('numbers') or [])]
        if len(numbers) < 4:
            continue
        actual = [int(numbers[index]) for index in range(4)]
        positions = []
        for item in seal.get('positions') or []:
            if not isinstance(item, dict):
                continue
            key = item.get('key')
            kill = str(item.get('kill') or '')
            if key not in POSITION_KEYS or len(kill) != 1 or not kill.isdigit():
                continue
            index = POSITION_INDEX[key]
            actual_code = actual[index]
            positions.append({
                'key': key,
                'label': item.get('label') or ATTACK_POSITION_LABELS[key],
                'kill': kill,
                'actual': str(actual_code),
                'hit': actual_code != int(kill),
                'basis': item.get('basis'),
                'enforced': item.get('enforced') is True,
            })
        if len(positions) != EXPECTED_ITEMS:
            continue
        state = str(seal.get('publication_state') or '')
        published = parse_time(seal.get('published_at'))
        drawn = parse_time(record.get('opentime'))
        verified = bool(state == 'published' and published is not None
                        and drawn is not None and published < drawn)
        settlement = {
            'version': 1,
            'rule_version': seal.get('rule_version') or RULE_VERSION,
            'ref_period': seal.get('ref_period'),
            'target_period': target,
            'published_at': seal.get('published_at'),
            'publication_state': state,
            'drawn_at': record.get('opentime'),
            'settled_at': (now or beijing_now()).isoformat(),
            'actual': [str(value) for value in actual],
            'positions': positions,
            'position_count': len(positions),
            'counted': [item['key'] for item in positions],
            'enforced_positions': seal.get('enforced_positions') or [],
            'expected_items': seal.get('expected_items', EXPECTED_ITEMS),
            'expected_group_rate': seal.get('expected_group_rate', EXPECTED_GROUP_RATE),
            'expected_basis': seal.get('expected_basis', EXPECTED_BASIS),
            'group_hit': all(item['hit'] for item in positions),
            'verified': verified,
        }
        settlements.append(settlement)
        settled.add(target)
        added.append(settlement)
    if added:
        _write_settlements(settlements)
    return added


def _business_key(value):
    moment = parse_time(value)
    return business_start(moment) if moment is not None else None


def _streak_stats(rows):
    ordered = sorted((row for row in rows if isinstance(row, dict)),
                     key=lambda row: str(row.get('target_period') or ''))
    win = loss = max_win = max_loss = 0
    for row in ordered:
        if row.get('group_hit'):
            win += 1
            loss = 0
            max_win = max(max_win, win)
        else:
            loss += 1
            win = 0
            max_loss = max(max_loss, loss)
    return {
        'current_win_streak': win,
        'current_loss_streak': loss,
        'max_win_streak': max_win,
        'max_loss_streak': max_loss,
    }


def today_stats(now=None):
    """Verified business-day (05:00) aggregate plus pending/late counts."""
    now = now or beijing_now()
    day = business_start(now)
    settlements = load_settlements()
    verified = [row for row in settlements
                if row.get('verified') is True
                and _business_key(row.get('settled_at')) == day]
    verified_all = [row for row in settlements if row.get('verified') is True]
    todays = [row for row in verified if row.get('rule_version') == RULE_VERSION]
    total = len(todays)
    hits = sum(1 for row in todays if row.get('group_hit') is True)
    item_bets = item_hits = 0
    enforced_bets = enforced_hits = 0
    per_position = {}
    for row in todays:
        for item in row.get('positions') or []:
            if not isinstance(item, dict):
                continue
            key = item.get('key')
            slot = per_position.setdefault(key, {'label': item.get('label'), 'bets': 0,
                                                 'hits': 0, 'rate': None})
            slot['bets'] += 1
            item_bets += 1
            if item.get('hit') is True:
                slot['hits'] += 1
                item_hits += 1
            if item.get('enforced') is True:
                enforced_bets += 1
                if item.get('hit') is True:
                    enforced_hits += 1
    for slot in per_position.values():
        slot['rate'] = (slot['hits'] / float(slot['bets'])) if slot['bets'] else None
    streak = _streak_stats(todays)
    version_rows = [row for row in verified_all if row.get('rule_version') == RULE_VERSION]
    all_streak = _streak_stats(version_rows)
    latest = latest_period()
    settled = {str(row.get('target_period')) for row in settlements}
    pending = 0
    for seal in load_seals():
        if seal.get('publication_state') != 'published':
            continue
        target = str(seal.get('target_period') or '')
        if target and target > latest and target not in settled:
            pending += 1
    late = sum(1 for row in settlements
               if row.get('verified') is False and _business_key(row.get('settled_at')) == day)
    expected_sum = 0.0
    expected_records = 0
    expected_items = set()
    for row in todays:
        rate = row.get('expected_group_rate')
        if not isinstance(rate, (int, float)) or not 0 < float(rate) < 1:
            rate = EXPECTED_GROUP_RATE
        expected_sum += float(rate)
        expected_records += 1
        try:
            expected_items.add(int(row.get('expected_items') or EXPECTED_ITEMS))
        except (TypeError, ValueError):
            expected_items.add(EXPECTED_ITEMS)
    return {
        'business_start': day.isoformat(),
        'total': total,
        'group_hits': hits,
        'group_rate': (hits / float(total)) if total else None,
        'position': {'bets': item_bets, 'hits': item_hits,
                     'rate': (item_hits / float(item_bets)) if item_bets else None},
        'per_position': per_position,
        'enforced': {'bets': enforced_bets, 'hits': enforced_hits,
                     'rate': (enforced_hits / float(enforced_bets)) if enforced_bets else None},
        'current_win_streak': streak['current_win_streak'],
        'current_loss_streak': streak['current_loss_streak'],
        'max_win_streak': all_streak['max_win_streak'],
        'max_loss_streak': all_streak['max_loss_streak'],
        'pending': pending,
        'late': late,
        'expected': {
            'records': expected_records,
            'group_rate': (expected_sum / expected_records) if expected_records else None,
            'group_expected': round(expected_sum, 2),
            'items_min': min(expected_items) if expected_items else None,
            'items_max': max(expected_items) if expected_items else None,
            'basis': EXPECTED_BASIS,
        },
        'version_total': len(version_rows),
        'version_hits': sum(1 for row in version_rows if row.get('group_hit') is True),
        'latest_period': latest,
    }


def version_info():
    seals = load_seals()
    published = [seal for seal in seals
                 if seal.get('publication_state') == 'published'
                 and seal.get('rule_version') == RULE_VERSION]
    return {
        'rule_version': RULE_VERSION,
        'count': len(published),
        'first_ref': published[0].get('ref_period') if published else None,
        'last_ref': published[-1].get('ref_period') if published else None,
    }


def current_summary(now=None):
    seals = load_seals()
    settlements = load_settlements()
    seal = seals[-1] if seals else None
    settlement = None
    if seal is not None:
        target = str(seal.get('target_period') or '')
        for row in reversed(settlements):
            if str(row.get('target_period') or '') == target:
                settlement = row
                break
    previous = seals[-2] if len(seals) >= 2 else None
    return {
        'latest_period': latest_period(),
        'seal': seal,
        'settlement': settlement,
        'previous': previous,
        'stats': today_stats(now),
    }


def deadline_for(reference):
    target = next_period(reference)
    if not target:
        raise ValueError('kill14 invalid reference period')
    slot = period_slot(target)
    if slot is None:
        raise ValueError('kill14 invalid target period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target


def _remaining(value):
    text = str(value).strip()
    if len(text) != 1 or text not in ATTACK_ORDER:
        raise ValueError('attack kill digit must be 0..9')
    return ''.join(char for char in ATTACK_ORDER if char != text)


def build_attack(seal):
    """四位置各保留 9 码 → 四定（与既有位置框架同格式）。"""
    if not isinstance(seal, dict):
        raise ValueError('attack requires a sealed prediction')
    mapping = {}
    for item in seal.get('positions') or []:
        if isinstance(item, dict):
            mapping[str(item.get('key') or '')] = str(item.get('kill') or '')
    parts = []
    for key, label in (('q', '千'), ('b', '百'), ('s', '十'), ('g', '个')):
        kill = mapping.get(key)
        if kill is None:
            raise ValueError('attack requires all four positions')
        parts.append(label + _remaining(kill))
    parts.append('四定')
    return ''.join(parts)
