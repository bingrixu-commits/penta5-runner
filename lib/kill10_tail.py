"""AI 二数合 + 三数合框架（kill10 v1，独立封存/结算/统计）。

口径（面板、封存与统计都按此写死）：
- 二数合：he2_keep 从 02468 恰好保留 4 个不同数字；前 4 球任取 2 位 6 种组合和值个位，
  任一落入保留集即命中；
- 三数合：he3_keep 从 0-9 恰好保留 5 个不同数字且 2 奇 3 偶或 3 奇 2 偶；任取 3 位 4 种
  组合和值个位，任一落入保留集即命中；
- 整组命中 = 二数合 + 三数合共 2 项同时命中；
- 整组随机期望 0.92022400（10^4 开奖空间 × 配置空间精确枚举，非单项相乘；二数合 5 种配置、
  三数合 200 种配置）；单项基线 二数合 0.9808、三数合 0.9375；
- 每期重选、无连用限制；无形态排除项、无定位排除项、无合尾；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与。

数据落点：
- data/kill10_tail/seals.json        每期封存（同一参考期不可替换）
- data/kill10_tail/settlements.json  开奖后结算（只追加）
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
DATA_DIR = ROOT / 'data' / 'kill10_tail'
SEALS_PATH = DATA_DIR / 'seals.json'
SETTLEMENTS_PATH = DATA_DIR / 'settlements.json'
STATE_PATH = ROOT / 'data' / 'state.json'

WINDOW = 50
CLOSE_SECONDS = 70
MIN_REQUEST_SECONDS = 20.0
MAX_TOKENS = 12288
REASONING_EFFORT = 'high'
NONRETRYABLE_HTTP_STATUSES = frozenset((400, 401, 402, 422))

RULE_VERSION = 'kill10-tail-v1-he2-he3'
TOTAL_ITEMS = 2                                # 整组项数 = 二数合 + 三数合
GROUP_BASELINE = 0.92022400                    # 整组随机期望（10^4 开奖空间精确枚举）
HE2_BASELINE = 0.9808                          # 二数合单项随机基线（5 种保留集）
HE3_BASELINE = 0.9375                          # 三数合单项随机基线（200 种保留集）
HE2_SOURCE_DIGITS = '02468'
HE2_KEEP_COUNT = 4
HE3_KEEP_COUNT = 5
HE2_PAIRS = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
HE3_TRIPLES = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))

RETRY_INSTRUCTION = (
    '上次响应未通过本地校验（{error}）。请严格按格式重新输出完整 JSON：'
    'he2_keep 为 02468 中 4 个不同数字组成的字符串；'
    'he3_keep 为 0123456789 中 5 个不同数字组成的字符串且必须 2 奇 3 偶或 3 奇 2 偶；'
    'reason 60 字以内，说明保留码如何权衡和值与冷热。不要输出任何其他文字、解释或代码块。'
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
        raise ValueError('kill10_tail seals records must be a list')
    return [item for item in records if isinstance(item, dict)]


def load_settlements():
    document = _load_json(SETTLEMENTS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill10_tail settlements records must be a list')
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


def _history_rows(snapshot=None):
    snapshot = snapshot or load_snapshot(persist=False)
    records = snapshot.get('records') or []
    rows = [list(record.get('numbers') or [])[:4] for record in records]
    rows = [row for row in rows if len(row) == 4]
    if len(rows) < WINDOW + 1:
        raise ValueError('insufficient-kill10-history')
    return snapshot, rows


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
    return [''.join(sorted(item)) for item in combinations(HE2_SOURCE_DIGITS, HE2_KEEP_COUNT)]


def he3_candidates():
    result = []
    for item in combinations('0123456789', HE3_KEEP_COUNT):
        text = ''.join(sorted(item))
        odd = sum(1 for char in text if int(char) % 2 == 1)
        if 2 <= odd <= 3:
            result.append(text)
    return result


def build_prompt(reference, target, rows, *, panel_snapshot=None):
    """Frozen prompt and its audit metadata; rows are the last WINDOW draws."""
    window = rows[-WINDOW:]
    history = ', '.join(''.join(str(int(value)) for value in row) for row in window)
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
    tail_lines = []
    for row in window[-10:]:
        tail_lines.append(
            '· %s：二数合6种和值个位 %s；三数合4种和值个位 %s'
            % (''.join(str(int(value)) for value in row),
               '、'.join(str(value) for value in pair_tails(row)),
               '、'.join(str(value) for value in triple_tails(row))))
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
        '【近 10 期二数合/三数合实际和值个位（只作落点参考，不代表未来概率）】\n'
        + '\n'.join(tail_lines) + '\n\n'
        '【二数合保留4码候选（从 02468 恰好保留 4 个不同数字；前 4 球任取两位的 6 种'
        '和值个位任一落入保留集即命中；按近50期真实命中率排序，顺序不代表推荐）】\n'
        + '\n'.join(he2_lines) + '\n\n'
        '【三数合保留5码候选 Top10（从 0-9 恰好保留 5 个不同数字且 2 奇 3 偶或 3 奇 2 偶；'
        '前 4 球任取三位的 4 种和值个位任一落入保留集即命中；按近50期命中率排序，仅供参考）】\n'
        + '\n'.join(he3_lines) + '\n\n'
        '【任务】\n'
        '1. he2_keep：从 02468 中恰好保留 4 个不同数字（输出字符串，如 "0246"），'
        '目标期前 4 球任取两位的 6 种和值个位任一落入即命中；\n'
        '2. he3_keep：从 0123456789 中恰好保留 5 个不同数字（输出字符串），'
        '且必须 2 奇 3 偶或 3 奇 2 偶，目标期前 4 球任取三位的 4 种和值个位任一落入即命中；\n'
        '3. 命中口径：二数合 + 三数合共 2 项同时命中才算整组命中；\n'
        '4. 冷热、遗漏只是历史特征，不能据此断言未来概率；不要为换而换，不得声称保证命中；\n'
        '5. 只能使用上面给出的历史与面板全量上下文；面板数据只作参考，不得照抄。\n\n'
        '只输出严格 JSON（不要输出分析过程、代码块或额外文字）：\n'
        '{"he2_keep":"<4位数字，从02468选>","he3_keep":"<5位数字，2奇3偶或3奇2偶>",'
        '"reason":"60字以内，说明保留码如何权衡和值与冷热"}\n'
        '注意：he2_keep 必须是 02468 里的 4 个不同数字；'
        'he3_keep 必须是 5 个不同数字且 2 奇 3 偶或 3 奇 2 偶。'
    )
    if panel_section:
        _marker = prompt.find('【任务】')
        if _marker != -1:
            prompt = prompt[:_marker] + panel_section + prompt[_marker:]
    audit = {
        'version': 'kill10-prompt-v1-he2-he3',
        'reference': reference,
        'target': target,
        'window': WINDOW,
        'history_sha256': sha256(history.encode('utf-8')).hexdigest(),
        'rows': [[str(int(value)) for value in row] for row in window],
        'he2_top': [{'keep': candidate, 'hit50': round(hit50, 6), 'hit30': round(hit30, 6)}
                    for candidate, _hits, hit30, hit50 in he2_rows],
        'he3_top': [{'keep': candidate, 'hit50': round(hit50, 6), 'hit30': round(hit30, 6)}
                    for candidate, _hits, hit30, hit50 in he3_rows[:10]],
        'group_baseline': GROUP_BASELINE,
        'panel_context_sha256': panel_context_sha,
        'panel_context': panel_snapshot if isinstance(panel_snapshot, dict) else None,
    }
    return prompt, audit


def normalize_response(response):
    """Strict local validation; never pad, default or invent anything."""
    if not isinstance(response, dict):
        raise ValueError('响应必须是 JSON 对象')
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
    return {
        'he2_keep': he2_keep,
        'he3_keep': he3_keep,
        'reason': reason[:200],
    }


def resolve_model():
    for name in ('PENTA5_MODEL_KILL10', 'PENTA5_MODEL_KILLER', 'PENTA5_DEEPSEEK_MODEL'):
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
        'request_policy_version': 'kill10-high-12k-deadline-v1',
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
            raise PublicationRejected('insufficient-time-for-kill10-request')
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
            prediction = normalize_response(_extract_json(message.get('content')))
            if value_check is not None:
                value_check(prediction)
            meta = _metadata(reference, attempt, model, api_key, envelope,
                             time.monotonic() - started)
            log('AI_RESPONSE_META ' + json.dumps(meta, ensure_ascii=True, allow_nan=False,
                                                separators=(',', ':')))
            log(f'kill10 response validated; ref={reference}; attempt={attempt}; publication pending.')
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
            log(f'kill10 attempt {attempt}/3 rejected; error_type={last_error}{status_note}; not published.')
            if status in NONRETRYABLE_HTTP_STATUSES:
                log(f'kill10 nonretryable provider HTTP {status}; stop retries; not published.')
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
    raise RuntimeError(f'kill10 inference failed after {attempts_used} attempt(s): '
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
        raise PublicationRejected('existing-kill10-seal-cannot-be-replaced')
    seals.append(seal)
    _write_seals(seals)
    return seal


def mark_published(reference, published_at=None):
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        raise ValueError('kill10 seal missing before publication')
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
        group_hit = bool(keep_entries['he2_keep']['hit']
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
    he2_hits = sum(1 for item in todays if (item.get('he2_keep') or {}).get('hit') is True)
    he3_hits = sum(1 for item in todays if (item.get('he3_keep') or {}).get('hit') is True)
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
        'he2_keep': {'hits': he2_hits, 'bets': total,
                     'rate': (he2_hits / total) if total else None},
        'he3_keep': {'hits': he3_hits, 'bets': total,
                     'rate': (he3_hits / total) if total else None},
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
        raise ValueError('invalid-kill10-target-period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target


def build_attack(seal):
    """攻击语：二数合保留4码 + 三数合保留5码 + 各（与 kill3 合尾段同格式）。"""
    if not isinstance(seal, dict):
        raise ValueError('attack requires a sealed prediction')
    he2_keep = seal.get('he2_keep')
    he3_keep = seal.get('he3_keep')
    if not he2_keep or not he3_keep:
        raise ValueError('attack requires he2_keep and he3_keep')
    return '二数合' + str(he2_keep) + '三数合' + str(he3_keep) + '各'
