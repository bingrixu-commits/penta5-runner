"""AI 双反向冷形态排除框架（kill8，独立封存/结算/统计）。

口径（面板、封存与统计都按此写死）：
- 位置排除数字由用户手动完成，不进本框架计算（本框架只负责排除形态）；
- 由 DeepSeek 从 16 种四位单双形态（单/双，按千百十个顺序）中选 1 种、
  从 16 种四位大小形态（大/小，按千百十个顺序）中选 1 种，作为本期
  「冷形态反向排除项」；
- 冷形态定位依据：近 50 期出现次数少、当前遗漏长（仅历史统计，不代表未来概率）；
- 单双形态命中 = 目标期实际四位单双形态 != 封存单双形态（反向排除项）；
- 大小形态命中 = 目标期实际四位大小形态 != 封存大小形态（反向排除项）；
- 整组命中 = 单双 + 大小 2 项同时命中；
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

FORM_KINDS = ('parity', 'size')
FORM_LABELS = {'parity': '单双形态', 'size': '大小形态'}
FORM_PALETTE = {'parity': '单双', 'size': '大小'}
REVERSE_LABELS = {'parity': '单双反向冷形态', 'size': '大小反向冷形态'}

RULE_VERSION = 'kill8-tail-v2-reverse-cold-forms'
COUNTED_FORMS = 2
POSITION_EXCLUSIONS_PATH = ROOT / 'data' / 'kill8_position_exclusions.json'
POSITION_KEYS = ('q', 'b', 's', 'g')
POSITION_LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
POSITION_INDEX = {'q': 0, 'b': 1, 's': 2, 'g': 3}
POSITION_INDEX_LABELS = {0: '千位', 1: '百位', 2: '十位', 3: '个位'}

RETRY_INSTRUCTION = (
    '上次响应未通过本地校验（{error}）。请严格按格式重新输出完整 JSON：'
    'parity_form 必须是恰好 4 个单/双字符（按千百十个顺序）；'
    'size_form 必须是恰好 4 个大/小字符（按千百十个顺序）；'
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
    """16 种四位形态，按调色板字典序（单双 或 大小）。"""
    if kind not in FORM_PALETTE:
        raise ValueError('unknown form kind')
    return [''.join(chars) for chars in product(FORM_PALETTE[kind], repeat=4)]


def rank_forms(rows, kind):
    """16 种形态冷排序：出现次数升序，当前遗漏降序（最冷在前）。"""
    space = form_space(kind)
    counts = {form: 0 for form in space}
    last = {}
    for index, row in enumerate(rows):
        form = pattern_of(row, kind)
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


def reverse_constraints(exclusions=None):
    """反向约束：{kind: {位置下标: 反方向字符}}。

    反方向 = 排除数字的相反单双/大小（排除双写单、排除大写小）。
    某位置排除数字方向混杂（单双混杂或大小混杂）或未排除时，该位置不约束。
    """
    exclusions = exclusions if isinstance(exclusions, dict) else {}
    parity = {}
    size = {}
    for key, index in POSITION_INDEX.items():
        raw = str(exclusions.get(key) or '')
        digits = [int(ch) for ch in raw if ch.isdigit()]
        if not digits:
            continue
        parities = {d % 2 for d in digits}
        sizes = {0 if d < 5 else 1 for d in digits}
        if len(parities) == 1:
            parity[index] = '单' if 0 in parities else '双'
        if len(sizes) == 1:
            size[index] = '大' if 0 in sizes else '小'
    return {'parity': parity, 'size': size}


def _constraint_text(constraints):
    """把反向约束渲染成 prompt 可读文本；空约束返回「（无）」。"""
    if not constraints:
        return '（无）'
    parts = []
    for index in sorted(constraints):
        parts.append('%s写「%s」' % (POSITION_INDEX_LABELS[index], constraints[index]))
    return '、'.join(parts)


def build_prompt(reference, target, rows, *, panel_snapshot=None, exclusions=None):
    """Frozen prompt and its audit metadata; rows are the last WINDOW draws."""
    window = rows[-WINDOW:]
    constraints = reverse_constraints(exclusions)
    parity_constraint = _constraint_text(constraints['parity'])
    size_constraint = _constraint_text(constraints['size'])
    history = ', '.join(''.join(str(int(value)) for value in row) for row in window)
    parity_ranks = rank_forms(window, 'parity')
    size_ranks = rank_forms(window, 'size')
    parity_lines = [f'· {form}（出{count}次/遗漏{omission}期）'
                    for form, count, omission in parity_ranks]
    size_lines = [f'· {form}（出{count}次/遗漏{omission}期）'
                  for form, count, omission in size_ranks]
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
        '【四位单双形态冷热统计（16 种，按出现次数升序、当前遗漏降序排列；'
        '仅历史统计，不代表未来概率）】\n'
        + '\n'.join(parity_lines) + '\n\n'
        '【四位大小形态冷热统计（16 种，按出现次数升序、当前遗漏降序排列；'
        '仅历史统计，不代表未来概率）】\n'
        + '\n'.join(size_lines) + '\n\n'
        '【反向约束（必须严格遵守，这是「反向」的核心）】\n'
        '你已手动排除了各位置数字，排除数字决定形态的「反方向」：\n'
        f'· 单双形态反向约束：{parity_constraint}\n'
        f'· 大小形态反向约束：{size_constraint}\n'
        '约束位置必须取排除数字的反方向（排除双写单、排除单写双；排除大写小、排除小写大）；'
        '未列出的位置由你按冷热自选。\n\n'
        '【任务】\n'
        '1. 从 16 种四位单双形态中选 1 种作为本期「反方向冷形态排除项」'
        '（严格遵守上面的单双反向约束，其余位置优先选冷）；\n'
        '2. 从 16 种四位大小形态中选 1 种作为本期「反方向冷形态排除项」'
        '（严格遵守上面的大小反向约束，其余位置优先选冷）；\n'
        '3. 命中口径：目标期实际四位单双形态 != 封存单双形态，'
        '且目标期实际四位大小形态 != 封存大小形态；2 项全中才算整组命中；\n'
        '4. 冷热、遗漏只是历史特征，不能据此断言未来概率；不要为换而换，不得声称保证命中；\n'
        '5. 只能使用上面给出的历史与面板全量上下文；面板数据只作参考，不得照抄，也不得声称保证命中。\n\n'
        '只输出严格 JSON（不要输出分析过程、代码块或额外文字）：\n'
        '{"parity_form":"<4个单/双字符，按千百十个顺序>",'
        '"size_form":"<4个大/小字符，按千百十个顺序>",'
        '"reason":"40字以内"}\n'
        '注意：parity_form 必须恰好 4 个单/双字符；size_form 必须恰好 4 个大/小字符。'
    )
    if panel_section:
        _marker = prompt.find('【任务】')
        if _marker != -1:
            prompt = prompt[:_marker] + panel_section + prompt[_marker:]
    audit = {
        'version': 'kill8-prompt-v1-cold-forms',
        'reference': reference,
        'target': target,
        'window': WINDOW,
        'history_sha256': sha256(history.encode('utf-8')).hexdigest(),
        'rows': [[str(int(value)) for value in row] for row in window],
        'parity_ranking': [{'form': form, 'count': count, 'omission': omission}
                           for form, count, omission in parity_ranks],
        'size_ranking': [{'form': form, 'count': count, 'omission': omission}
                         for form, count, omission in size_ranks],
        'panel_context_sha256': panel_context_sha,
        'panel_context': panel_snapshot if isinstance(panel_snapshot, dict) else None,
        'position_exclusions': dict(exclusions) if isinstance(exclusions, dict) else {},
    }
    return prompt, audit


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
    constraints = reverse_constraints(exclusions)
    for index, char in constraints['parity'].items():
        if parity_form[index] != char:
            raise ValueError('parity_form 在%s必须取排除数字的反方向「%s」'
                             % (POSITION_INDEX_LABELS[index], char))
    for index, char in constraints['size'].items():
        if size_form[index] != char:
            raise ValueError('size_form 在%s必须取排除数字的反方向「%s」'
                             % (POSITION_INDEX_LABELS[index], char))
    reason = str(response.get('reason', ''))
    try:
        reason.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('reason 必须是合法 UTF-8 文本') from None
    return {'parity_form': parity_form, 'size_form': size_form, 'reason': reason[:200]}


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
        actual_parity = pattern_of(actual, 'parity')
        actual_size = pattern_of(actual, 'size')
        sealed_parity = seal.get('parity_form')
        sealed_size = seal.get('size_form')
        parity_hit = (isinstance(sealed_parity, str) and len(sealed_parity) == 4
                      and all(char in '单双' for char in sealed_parity)
                      and actual_parity != sealed_parity)
        size_hit = (isinstance(sealed_size, str) and len(sealed_size) == 4
                    and all(char in '大小' for char in sealed_size)
                    and actual_size != sealed_size)
        group_hit = bool(parity_hit and size_hit)
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
            'parity': {'form': sealed_parity, 'actual': actual_parity, 'hit': bool(parity_hit)},
            'size': {'form': sealed_size, 'actual': actual_size, 'hit': bool(size_hit)},
            'group_hit': group_hit,
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
    parity_hits = sum(1 for item in todays if (item.get('parity') or {}).get('hit') is True)
    size_hits = sum(1 for item in todays if (item.get('size') or {}).get('hit') is True)
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
        'parity': {'hits': parity_hits, 'bets': total,
                   'rate': (parity_hits / total) if total else None},
        'size': {'hits': size_hits, 'bets': total,
                 'rate': (size_hits / total) if total else None},
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
        raise ValueError('invalid-kill8-target-period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target


def build_attack(seal):
    """反向冷形态攻击语：位置排除由用户手动完成，本框架只给出两个形态排除项。"""
    if not isinstance(seal, dict):
        raise ValueError('attack requires a sealed prediction')
    parts = []
    parity_form = seal.get('parity_form')
    size_form = seal.get('size_form')
    if isinstance(parity_form, str) and len(parity_form) == 4:
        parts.append('除' + parity_form)
    if isinstance(size_form, str) and len(size_form) == 4:
        parts.append('除' + size_form)
    return ''.join(parts)
