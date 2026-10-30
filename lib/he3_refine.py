"""An independent, pre-draw 6-to-5 reference. Never rewrite the parent AI."""
from copy import deepcopy
from hashlib import sha256
from itertools import combinations
from pathlib import Path
import tempfile
import json
import math
import os
import re
import time

from ai_prediction_guard import (PublicationRejected, find_seal, publication_rejection,
                                 publish_saved, read_stats, store_prepared)
from ai_request_deadline import MAX_TOKENS, RequestDeadline, request_timeout
from ai_provider_policy import strict_json_object
from ai_operation_window import ERROR_CODES as WINDOW_ERROR_CODES, OperationWindow
from ai_verified_stats import atomic_json, next_period, sync_parent_directory
from ai_call_control import AICallsDisabled, require_ai_enabled
from framework_ai_client import _response_metadata
from framework_hit_stats import _publication
from scripts.run_ai_ready_job import expected_reference
from ai_http_observability import emit_http_phase
from stat50_compare import _archive, history_from_snapshot, validate_history
from stats_history import beijing_now, load_snapshot, parse_time, period_slot

ROOT = Path('/root/penta5-runner')
MARKER = 'HE3_REFINE'
LEGACY_REVISION = 'he3-refine-v1'
REVISION = 'he3-refine-v2-balanced'
def _resolve_model():
    # 2026-10-30: 合尾重要，允许按任务指定模型(PENTA5_MODEL_HE3)；
    # 未配置时保持原来的 flash，行为不变。
    import os as _os
    raw = (_os.environ.get('PENTA5_MODEL_HE3') or '').strip()
    if raw:
        return raw
    try:
        from app_secrets import get_secret
        return get_secret('PENTA5_MODEL_HE3')
    except Exception:
        return 'deepseek-flash'


MODEL = _resolve_model()
WAIT_PARENT_SECONDS = 225.0
# 2026-10-30: 合尾支持按任务切 flash/pro。历史封存是 flash、新封存可能是 pro，
# 校验按「模型族」判断（同一族内允许尾号），不再硬编码只认 flash。
FLASH_FAMILY = re.compile(r'deepseek-(?:v4(?:\.1)?-)?flash(?:-[0-9]{4})?')
PRO_FAMILY = re.compile(r'deepseek-(?:v4(?:\.1)?-)?pro(?:-[0-9]{4})?')


def _model_family(value):
    if isinstance(value, str):
        if FLASH_FAMILY.fullmatch(value):
            return 'flash'
        if PRO_FAMILY.fullmatch(value):
            return 'pro'
    return None


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _log(log, message):
    try:
        log(message)
    except Exception:
        pass


def parity_options(base_keep):
    """Find the closest attainable five-digit parity balance inside six digits."""
    if (not isinstance(base_keep, str) or not re.fullmatch(r'[0-9]{6}', base_keep)
            or len(set(base_keep)) != 6):
        raise ValueError('invalid-original-six-digits')
    odd = sum(int(digit) % 2 for digit in base_keep)
    differences = [(int(digit), abs(2 * (odd - int(digit) % 2) - 5)) for digit in base_keep]
    best = min(difference for _, difference in differences)
    return {'odd_count': odd, 'even_count': 6 - odd,
            'allowed_drops': [digit for digit, difference in differences if difference == best],
            'best_difference': best, 'balanced_possible': best == 1}


def validate_selection(base_keep, payload, *, balanced=False):
    if (not isinstance(base_keep, str) or not re.fullmatch(r'[0-9]{6}', base_keep)
            or len(set(base_keep)) != 6):
        raise ValueError('invalid-original-six-digits')
    if not isinstance(payload, dict) or set(payload) - {'he3_drop', 'reason'}:
        raise ValueError('invalid-refinement-object')
    drop = payload.get('he3_drop')
    if type(drop) is not int or not 0 <= drop <= 9 or str(drop) not in base_keep:
        raise ValueError('drop-must-be-one-original-digit')
    if balanced and drop not in parity_options(base_keep)['allowed_drops']:
        raise ValueError('drop-must-preserve-closest-five-digit-parity-balance')
    reason = payload.get('reason', '')
    if not isinstance(reason, str) or len(reason) > 120:
        raise ValueError('invalid-refinement-reason')
    try:
        reason.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('invalid-refinement-reason') from None
    remaining = ''.join(d for d in base_keep if d != str(drop))
    assert len(remaining) == len(set(remaining)) == 5
    return {'he3_drop': drop, 'he3_keep5': remaining, 'reason': reason}


def _loader(root):
    return lambda **kw: load_snapshot(state_path=Path(root) / 'data/state.json',
                                      cache_path=Path(root) / 'data/stats_draw_history.json', **kw)


def _source(root, reference, snapshot, now):
    rejection = publication_rejection(reference, snapshot, now)
    if rejection:
        raise PublicationRejected(rejection)
    parent = find_seal(read_stats(Path(root) / 'data/r1_framework_seals.json'), reference)
    if parent is None or parent.get('publication_state') != 'published':
        return None
    proof = _publication(parent, now)
    if proof['ref_period'] != reference:
        raise ValueError('parent-reference-mismatch')
    base = proof['response']['he3_keep']
    validate_selection(base, {'he3_drop': int(base[0])})
    history = history_from_snapshot(snapshot, reference, now)
    audit = parent.get('model_context_audit')
    archive_sha = _archive(root, 'framework', reference, audit, history,
                           parse_time(parent['generated_at']), now)
    return {'parent': parent, 'original_he3_keep': base, 'history': history,
            'history_sha256': _digest(history), 'parent_seal_sha256': _digest(parent),
            'model_context_audit': deepcopy(audit), 'archive_sha256': archive_sha}


def _wait_parent(root, reference, loader, clock, monotonic, sleep, operation_window=None):
    began = previous = monotonic()
    if not isinstance(began, (int, float)) or isinstance(began, bool) or not math.isfinite(began):
        raise PublicationRejected('invalid-refinement-wait-clock')
    while True:
        require_ai_enabled(root=root, clock=clock)
        current = monotonic()
        if not math.isfinite(current) or current < previous:
            raise PublicationRejected('invalid-refinement-wait-clock')
        previous = current
        now = clock()
        if operation_window is not None:
            operation_window.request_budget(minimum=25)
        if expected_reference(now) != reference:
            raise PublicationRejected('refinement-reference-advanced')
        snapshot = loader(persist=False)
        # The parent may finish while source loading is in progress. Validate
        # its publication against a time observed after loading, not the older
        # timestamp from the start of this poll.
        now = clock()
        if operation_window is not None:
            operation_window.request_budget(minimum=25)
        if expected_reference(now) != reference:
            raise PublicationRejected('refinement-reference-advanced')
        latest = (snapshot.get('latest') or {}).get('period')
        if latest == reference:
            try:
                source = _source(root, reference, snapshot, now)
            except ValueError as error:
                # Reading the parent itself can race its atomic publication
                # after ``now``. Never accept future evidence: only poll again
                # for this timing/proof rejection within the existing bounds.
                # Persistent invalid evidence still expires without a paid call.
                if str(error) != 'invalid-or-future-publication-evidence':
                    raise
                source = None
            if source is not None:
                if monotonic() - began > WAIT_PARENT_SECONDS:
                    raise PublicationRejected('refinement-parent-wait-exhausted')
                return source, snapshot
        elif isinstance(latest, str) and latest > reference:
            raise PublicationRejected('refinement-reference-advanced')
        remaining = min(WAIT_PARENT_SECONDS - (monotonic() - began),
                        ((operation_window.close if operation_window is not None else
                           period_slot(next_period(reference))) - clock()).total_seconds() - 35.0)
        if remaining <= 0:
            raise PublicationRejected('refinement-parent-unavailable')
        sleep(min(1.0, remaining))


def _request_input(reference, source, *, revision=REVISION):
    if revision not in (LEGACY_REVISION, REVISION):
        raise ValueError('invalid-refinement-revision')
    codes = [''.join(map(str, row['numbers'])) for row in source['history']]
    counts = {d: 0 for d in source['original_he3_keep']}
    for code in codes:
        tails = {sum(int(code[i]) for i in indexes) % 10 for indexes in combinations(range(4), 3)}
        for digit in counts:
            counts[digit] += int(int(digit) in tails)
    prompt = (
        f'独立附加参考；参考期{reference}，只预测下一目标期{next_period(reference)}。\n'
        + ', '.join(codes) + '\n'
        f'原框架已在开奖前封存三数合尾六码：{source["original_he3_keep"]}。\n'
        '原六码、原八项统计和原攻击语一律不修改。本次只在原六码内选择一个整数he3_drop，'
        '作为额外移除参考；系统用原六码减去该数字得到剩余五码。不得选择集合外数字，不得另编五码。\n'
        '历史从旧到新，每条仅前四球，0与前导0有效。三数合尾指前四球任选三位的4个位置组合求和取%10。'
        '本次删除仅缩小独立参考集合，并不额外要求四个实际合尾都不等于删除码。\n'
        f'近50期各候选作为上述四个合尾之一出现的期数：{json.dumps(counts, ensure_ascii=False)}。\n'
        '冷热和遗漏仅描述历史，不能据此声称未来概率优势或保证命中。审慎核查后只输出完整JSON：'
        '{"he3_drop":原六码中的一个0至9整数,"reason":"40字以内的简短理由"}。'
    )
    document = {'version': 1, 'revision': revision, 'reference_period': reference,
            'target_period': next_period(reference),
            'source_parent_sha256': source['parent_seal_sha256'],
            'source_input_sha256': source['archive_sha256'], 'history_codes': codes,
            'original_he3_keep': source['original_he3_keep'], 'prompt': prompt}
    if revision == REVISION:
        options = parity_options(source['original_he3_keep'])
        document['parity_policy'] = {'version': 'balanced-five-v1', **options}
        target = ('剩余5码必须为2奇3偶或3奇2偶，两种结构同等可选。' if options['balanced_possible'] else
                  '原六码无法只删1码达到2奇3偶或3奇2偶；只能在原六码内保留最接近的奇偶结构，不得补入新数字。')
        document['prompt'] += (
            '\n本次额外约束：五码奇偶尽量均衡，0为偶数。'
            f'原六码有{options["odd_count"]}个奇数、{options["even_count"]}个偶数。' + target
            + f'本期he3_drop只能从{json.dumps(options["allowed_drops"], ensure_ascii=False)}中选择一个整数；'
            '结合上面同一近50期数据，在这些候选中选择排除码。'
            '不要输出集合外数字，不得更改原六码；奇偶配比是用户约束，不代表命中率已提高。'
            '仍只返回包含he3_drop和reason的完整JSON。'
        )
    return document


def _checked_loader(root, reference, source, loader, clock):
    def checked(**kwargs):
        snapshot = loader(**kwargs)
        try:
            current = _source(root, reference, snapshot, clock())
            if current is None or any(current[key] != source[key] for key in
                ('parent_seal_sha256', 'history_sha256', 'archive_sha256', 'original_he3_keep')):
                raise ValueError('refinement-source-changed')
        except (ValueError, TypeError, KeyError, OSError, OverflowError) as error:
            raise PublicationRejected('refinement-source-evidence-changed') from error
        return snapshot
    return checked


def _body(seal):
    return {key: deepcopy(seal.get(key)) for key in
            ('ref_period', 'revision', 'original_he3_keep', 'history', 'history_sha256',
             'parent_seal_sha256', 'model_context_audit', 'refinement_input',
             'refinement_input_sha256', 'response', 'model_metadata')}


def _validate_saved(seal, reference, now, *, root, published=True, parent_seals=None):
    from he3_refine_view import card_fragment
    if (not isinstance(seal, dict) or seal.get('revision') not in (LEGACY_REVISION, REVISION)
            or seal.get('ref_period') != reference or seal.get('target_period') != next_period(reference)
            or seal.get('seal_sha256') != _digest(_body(seal))):
        raise ValueError('invalid-refinement-seal')
    history = validate_history(seal.get('history'), reference, now)
    if seal.get('history_sha256') != _digest(history):
        raise ValueError('refinement-history-changed')
    # Statistics may supply one durable snapshot for this refresh. Live
    # inference/publication callers keep reading the current parent from disk.
    if parent_seals is None:
        parent_seals = read_stats(Path(root) / 'data/r1_framework_seals.json')
    parent = find_seal(parent_seals, reference)
    proof = _publication(parent, now)
    if (seal.get('parent_seal_sha256') != _digest(parent)
            or seal.get('original_he3_keep') != proof['response']['he3_keep']
            or seal.get('model_context_audit') != parent.get('model_context_audit')):
        raise ValueError('refinement-parent-changed')
    response = seal.get('response')
    if (not isinstance(response, dict) or response != validate_selection(seal['original_he3_keep'],
            {key: response.get(key) for key in ('he3_drop', 'reason')},
            balanced=seal['revision'] == REVISION)):
        raise ValueError('refinement-selection-changed')
    source = {'history': history, 'original_he3_keep': seal['original_he3_keep'],
              'parent_seal_sha256': seal['parent_seal_sha256'],
              'archive_sha256': seal['model_context_audit']['input_archive_sha256']}
    original_input = _request_input(reference, source, revision=seal['revision'])
    if (seal.get('refinement_input') != original_input
            or seal.get('refinement_input_sha256') != _digest(original_input)):
        raise ValueError('refinement-input-changed')
    generated = parse_time(seal.get('generated_at'))
    if (type(seal.get('evidence_version')) is not int or seal['evidence_version'] != 2
            or seal.get('reference_open_time') != parent.get('reference_open_time')
            or generated is None or generated < parse_time(parent['published_at'])
            or not generated < period_slot(next_period(reference)) or generated > now):
        raise ValueError('invalid-refinement-generation-evidence')
    _archive(root, 'framework', reference, seal['model_context_audit'], history, generated, now)
    if seal.get('card_html') != card_fragment(reference, seal['original_he3_keep'],
            response['he3_drop'], response['he3_keep5'], response['reason'], generated):
        raise ValueError('refinement-card-changed')
    metadata = seal.get('model_metadata')
    requested_family = _model_family(metadata.get('requested_model')) if isinstance(metadata, dict) else None
    returned_family = _model_family(metadata.get('returned_model')) if isinstance(metadata, dict) else None
    if (not isinstance(metadata, dict) or metadata.get('ref') != reference
            or requested_family is None or returned_family != requested_family
            or metadata.get('finish_reason') != 'stop'):
        raise ValueError('unverified-refinement-model')
    if published or seal.get('publication_state') == 'published':
        at = parse_time(seal.get('published_at'))
        if (seal.get('publication_state') != 'published' or at is None
                or not generated <= at < period_slot(next_period(reference)) or at > now):
            raise ValueError('invalid-refinement-publication-evidence')
    return seal


def _status(root, status, reference, clock, error_code=None):
    atomic_json(Path(root) / 'data/he3_refine_status.json',
        {'version': 1, 'revision': REVISION, 'status': status, 'reference_period': reference,
         'target_period': next_period(reference), 'updated_at': clock().isoformat(), 'error_code': error_code})


def _writer(root, writer):
    if writer is not None:
        return writer
    from he3_refine_view import compose_refine_transform
    from panel_io import update_panel
    return lambda transform: update_panel(lambda html: compose_refine_transform(html, transform),
                                          path=Path(root) / 'panel.html')


def _restore_unissued_attempt(path, original):
    """Undo only this process's attempt when no HTTP request was started.

    The caller owns the existing refinement job lock. Preserve exact prior
    bytes rather than serializing or changing historical publication evidence.
    """
    if original is None:
        path.unlink(missing_ok=True)
        sync_parent_directory(path)
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            temporary = handle.name
            handle.write(original)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        sync_parent_directory(path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _paused(root, reference, clock):
    _status(root, 'paused', reference, clock)
    return {'status': 'paused', 'published': False, 'ref_period': reference}


def run_once(*, root=ROOT, post=None, api_key=None, loader=None, clock=beijing_now,
             monotonic=time.monotonic, sleep=time.sleep, writer=None, log=print):
    from he3_refine_view import card_fragment
    root = Path(root)
    reference = expected_reference(clock())
    operation_window = OperationWindow(reference, clock)
    try:
        require_ai_enabled(root=root, clock=clock)
    except AICallsDisabled:
        return _paused(root, reference, clock)
    loader = loader or _loader(root)
    path = root / 'data/he3_refine_seals.json'
    ledger = read_stats(path)
    if ledger.get('version', 1) != 1:
        raise ValueError('invalid-refinement-ledger-version')
    ledger.setdefault('version', 1)
    attempts = ledger.setdefault('attempts', {})
    seals = ledger.setdefault('prediction_seals', {})
    if not isinstance(attempts, dict) or not isinstance(seals, dict):
        raise ValueError('invalid-refinement-index')
    attempt_committed, request_sent, original_ledger_raw = False, False, None
    try:
        existing = find_seal(ledger, reference)
        if existing is not None:
            _validate_saved(existing, reference, clock(), root=root, published=False)
            if existing.get('publication_state') == 'published' and clock() >= operation_window.close:
                _status(root, 'preserved', reference, clock)
                return {'status': 'preserved', 'published': True, 'ref_period': reference}
            source, _ = _wait_parent(root, reference, loader, clock, monotonic, sleep)
            published = publish_saved(ledger, path, reference, MARKER,
                _checked_loader(root, reference, source, loader, clock), writer=_writer(root, writer), clock=clock,
                operation_window=operation_window)
            _status(root, 'preserved' if published else 'rejected', reference, clock)
            return {'status': 'preserved' if published else 'rejected', 'published': published, 'ref_period': reference}
        if reference in attempts:
            raise PublicationRejected('refinement-already-attempted')
        _status(root, 'waiting', reference, clock)
        source, snapshot = _wait_parent(root, reference, loader, clock, monotonic, sleep, operation_window)
        input_doc = _request_input(reference, source)
        request_budget = operation_window.request_budget(minimum=25)
        deadline = RequestDeadline(reference, clock, monotonic, budget_seconds=request_budget,
                                   cutoff_at=operation_window.request_cutoff)
        if deadline.remaining() < 25:
            raise PublicationRejected('insufficient-time-for-refinement-request')
        if api_key is None:
            from app_secrets import get_secret
            api_key = os.getenv('DEEPSEEK_API_KEY') or get_secret('PENTA5_FRAMEWORK_API_KEY')
            if api_key.startswith('sk-sk-'):
                api_key = api_key[3:]
        if post is None:
            import requests
            post = requests.post
        original_ledger_raw = path.read_bytes() if path.exists() else None
        require_ai_enabled(root=root, clock=clock)
        attempts[reference] = {'started_at': clock().isoformat(), 'revision': REVISION,
                               'refinement_input': input_doc, 'refinement_input_sha256': _digest(input_doc)}
        atomic_json(path, ledger)  # Commit once before any paid inference.
        attempt_committed = True
        _status(root, 'requesting', reference, clock)
        response = None
        http_status = None
        request_started = monotonic()
        with request_timeout(deadline.remaining()):
            try:
                require_ai_enabled(root=root, clock=clock)
                operation_window.request_budget(minimum=25)
                request_sent = True
                http_timeout = min(80.0, deadline.remaining() - 1)
                emit_http_phase(log, reference, 1, 'start')
                try:
                    operation_window.request_budget(minimum=25)
                    if deadline.remaining() < 25:
                        raise PublicationRejected('operation-window-insufficient-budget')
                    response = post('https://api.deepseek.com/chat/completions',
                        json={'model': MODEL, 'messages': [{'role': 'user', 'content': input_doc['prompt']}],
                              'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high',
                              'response_format': {'type': 'json_object'}, 'max_tokens': MAX_TOKENS},
                        headers={'Authorization': 'Bearer ' + api_key, 'Content-Type': 'application/json'},
                        timeout=http_timeout)
                    http_status = response.status_code
                finally:
                    emit_http_phase(log, reference, 1, 'complete', status=http_status)
                envelope = response.json() if http_status == 200 else None
            finally:
                close = getattr(response, 'close', None)
                if callable(close):
                    try:
                        close()
                    except PublicationRejected:
                        raise
                    except Exception:
                        pass
        deadline.check()
        if http_status != 200:
            _status(root, 'failed', reference, clock,
                    'provider-insufficient-balance' if http_status == 402 else 'provider-http-error')
            raise PublicationRejected('provider-insufficient-balance' if http_status == 402 else 'provider-http-error')
        metadata = []
        def capture(message):
            if isinstance(message, str) and message.startswith('AI_RESPONSE_META '):
                metadata.append(json.loads(message[len('AI_RESPONSE_META '):]))
            _log(log, message)
        _response_metadata(capture, envelope, reference, 1, MODEL, api_key, monotonic() - request_started,
                           deadline=deadline)
        if envelope['choices'][0].get('finish_reason') != 'stop':
            raise ValueError('incomplete-refinement-response')
        content = envelope['choices'][0]['message']['content']
        payload = strict_json_object(content) if isinstance(content, str) else None
        selection = validate_selection(source['original_he3_keep'], payload, balanced=True)
        deadline.check()
        generated = clock()
        candidate = {'revision': REVISION, 'ref_period': reference, 'response': selection,
                     'original_he3_keep': source['original_he3_keep'], 'history': source['history'],
                     'history_sha256': source['history_sha256'], 'parent_seal_sha256': source['parent_seal_sha256'],
                     'model_context_audit': source['model_context_audit'], 'refinement_input': input_doc,
                     'refinement_input_sha256': _digest(input_doc), 'model_metadata': metadata[-1] if metadata else {}}
        operation_window.require_open()
        candidate['seal_sha256'] = _digest(_body(candidate))
        checked_loader = _checked_loader(root, reference, source, loader, clock)
        card = card_fragment(reference, source['original_he3_keep'], selection['he3_drop'],
                             selection['he3_keep5'], selection['reason'], generated)
        prepared = store_prepared(ledger, path, checked_loader(persist=False), candidate, card,
                                  generated_at=generated, enqueue=False)
        _validate_saved(prepared, reference, clock(), root=root, published=False)
        published = publish_saved(ledger, path, reference, MARKER, checked_loader,
                                  writer=_writer(root, writer), clock=clock,
                operation_window=operation_window)
        _status(root, 'published' if published else 'rejected', reference, clock)
        _log(log, f'HE3_REFINE ref={reference} target={next_period(reference)} published={published}')
        return {'status': 'published' if published else 'rejected', 'published': published, 'ref_period': reference}
    except AICallsDisabled:
        if attempt_committed and not request_sent:
            _restore_unissued_attempt(path, original_ledger_raw)
        return _paused(root, reference, clock)
    except Exception as error:
        code = (str(error) if isinstance(error, PublicationRejected) and str(error) in WINDOW_ERROR_CODES else
                'provider-insufficient-balance' if isinstance(error, PublicationRejected)
                and str(error) == 'provider-insufficient-balance' else
                'publication-rejected' if isinstance(error, PublicationRejected) else 'inference-or-evidence-error')
        _status(root, 'failed' if code == 'provider-insufficient-balance' else
                'rejected' if isinstance(error, PublicationRejected) else 'failed', reference, clock, code)
        raise


def select_current(root, snapshot):
    reference = (snapshot.get('latest') or {}).get('period')
    now = snapshot.get('now') or beijing_now()
    try:
        if publication_rejection(reference, snapshot, now):
            return None
        seal = find_seal(read_stats(Path(root) / 'data/he3_refine_seals.json'), reference)
        _validate_saved(seal, reference, now, root=root)
        source = _source(root, reference, snapshot, now)
        if source is None or any(source[key] != seal[key] for key in
            ('parent_seal_sha256', 'history_sha256', 'original_he3_keep', 'model_context_audit')):
            return None
        return seal
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        return None


def main():
    import fcntl
    try:
        require_ai_enabled(root=ROOT, clock=beijing_now)
    except AICallsDisabled:
        _paused(ROOT, expected_reference(beijing_now()), beijing_now)
        return 0
    with (ROOT / 'data/he3_refine_job.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            result = run_once()
            code = 0 if result['published'] or result.get('status') == 'paused' else 1
        except Exception as error:
            import traceback
            _log(print, 'Additional he3 reference not published; error_type=' + type(error).__name__
                 + '; error=' + str(error)[:200])
            traceback.print_exc()
            code = 1
        try:
            from he3_refine_view import refresh_he3_refine_panel
            refresh_he3_refine_panel(root=ROOT)
        except Exception as error:
            _log(print, 'Additional he3 display deferred; error_type=' + type(error).__name__)
        return code
