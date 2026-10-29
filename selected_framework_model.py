"""Independent prospective Pro selection with three fixed position exclusions."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import time

from ai_prediction_guard import (PublicationRejected, find_seal, publication_rejection,
                                 publish_saved, read_stats, store_prepared)
from ai_verified_stats import atomic_json, next_period
from killer_ai_client import KillerResponseError, request_killer_response
from selected_framework_contract import (REVISION, LEDGER_REVISION, SUPPORTED_REVISIONS, MODEL, MARKER,
    build_input_doc as _input_doc, validate_response)
from scripts.run_ai_ready_job import expected_reference
from stat50_compare import history_from_snapshot, validate_history
from stats_history import beijing_now, load_snapshot, parse_period, parse_time, period_slot
from selected_strategy_validation import (selected_validator, validation_report,
                                           SEVEN_RETRY_ADDITION)

ROOT = Path('/root/penta5-runner')
SELECTED_REQUEST_BUDGET_SECONDS = 110.0
# Only this independently requested framework uses the fast transport. The
# immutable v6 logical input and every historical seal keep their old bytes.
SELECTED_REASONING_EFFORT = 'low'
SELECTED_MAX_TOKENS = 2048
FAST_TRANSPORT_VERSION = 'selected-seven-fast-transport-v1'
PLATFORM_CLOSE_SECONDS = 70
PUBLICATION_RESERVE_SECONDS = 10
OPERATION_RESERVE_SECONDS = 90
PRO_FAMILY = re.compile(r'deepseek-(?:v4(?:\.1)?-)?pro(?:-[0-9]{4})?')
RETRY_INSTRUCTION = (
    '上次响应未通过校验。只返回完整JSON对象，恰好含selected_positions、selected_tails、he2_keep、he3_keep、reason。'
    'selected_positions从q/b/s/g恰好选三个不同键，每个值为一个0至9整数；'
    'selected_tails从all/qbs/qbg/qsg/bsg恰好选两个不同键，每个值为一个0至9整数。'
    'he2_keep恰好4个不重复ASCII数字且仅从02468选择；he3_keep恰好5个不重复ASCII数字。'
    'reason为简短字符串；禁止布尔、小数、默认补位、重复键、额外字段和分析过程。')

# Only application-owned codes may reach operator status/logs. A regex alone
# would accept arbitrary provider text such as a key-shaped lowercase string.
_DIAGNOSTIC_CODES = frozenset((
    'selected-six-reference-advanced', 'selected-six-source-evidence-changed',
    'selected-six-history-changed', 'selected-six-already-attempted',
    'returned-model-not-pro', 'invalid-reference-period',
    'reference-period-advanced', 'target-already-opened',
    'publication-deadline-passed', 'invalid-reference-open-time',
    'provider-wall-clock-deadline-exhausted',
    'invalid-provider-wall-clock-timeout', 'invalid-provider-request-budget', 'invalid-request-wall-clock',
    'invalid-request-monotonic-clock', 'provider-wall-clock-timer-unavailable',
    'provider-wall-clock-timer-already-armed',
    'insufficient-time-for-killer-request', 'killer-retry-budget-exhausted',
    'history-must-have-exactly-50-draws', 'invalid-history-record',
    'invalid-history-first-four', 'invalid-or-conflicting-history-draw',
    'nonconsecutive-history', 'history-reference-mismatch',
    'invalid-recent-history', 'recent-history-not-real-record',
    'latest-reference-history-mismatch',
    'invalid-selected-six-seal', 'invalid-selected-six-generation-evidence',
    'invalid-selected-six-history', 'invalid-selected-six-input',
    'invalid-selected-six-response', 'unverified-selected-six-model',
    'invalid-selected-six-card', 'invalid-selected-six-publication-evidence',
    'unpublished-selected-six-state', 'invalid-selected-six-ledger',
    'invalid-selected-six-index',
    'unknown-selected-framework-revision',
    'selected-seven-platform-close-passed', 'selected-seven-insufficient-close-budget',
    'selected-seven-operation-window-passed', 'selected-seven-insufficient-operation-budget',
    'invalid-selected-seven-request-transport',
))
_DIAGNOSTIC_TYPES = frozenset((
    'PublicationRejected', 'KillerResponseError', 'ValueError', 'TypeError',
    'KeyError', 'OSError', 'IOError', 'TimeoutError', 'OverflowError',
    'RuntimeError', 'JSONDecodeError', 'ConnectionError', 'ReadTimeout',
    'ConnectTimeout', 'HTTPError', 'SSLError',
))


def _machine_code(error):
    # Exception arguments can contain raw provider bodies or credentials.
    args = getattr(error, 'args', ())
    value = args[0] if isinstance(args, tuple) and len(args) == 1 else None
    return (value if isinstance(value, str) and re.fullmatch(r'[a-z0-9-]{1,100}', value)
            and value in _DIAGNOSTIC_CODES else None)


def _error_diagnostics(error):
    code = ('provider-insufficient-balance' if isinstance(error, KillerResponseError)
            and error.args == ('Nonretryable killer provider HTTP 402',) else
            _machine_code(error) or
            ('publication-rejected' if isinstance(error, PublicationRejected)
             else 'inference-or-evidence-error'))
    kind = type(error).__name__
    kind = kind if kind in _DIAGNOSTIC_TYPES else 'Exception'
    cause, seen, cause_code = getattr(error, '__cause__', None), {id(error)}, None
    for _ in range(5):
        if cause is None or id(cause) in seen:
            break
        seen.add(id(cause))
        candidate = _machine_code(cause)
        if candidate is not None:
            cause_code = candidate
        cause = getattr(cause, '__cause__', None)
    return code, kind, cause_code


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _aware(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parse_time(value) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _close(reference):
    slot = period_slot(next_period(reference))
    if slot is None:
        raise PublicationRejected('invalid-reference-period')
    return slot - timedelta(seconds=PLATFORM_CLOSE_SECONDS)


def _request_budget(reference, now):
    """One shared retry budget leaves 90 seconds for the operator plus commit."""
    remaining = (_operation_deadline(reference) - now).total_seconds() - PUBLICATION_RESERVE_SECONDS
    if remaining < 20:
        raise PublicationRejected('selected-seven-insufficient-operation-budget')
    return min(SELECTED_REQUEST_BUDGET_SECONDS, remaining)


def _operation_deadline(reference):
    return _close(reference) - timedelta(seconds=OPERATION_RESERVE_SECONDS)


def _transport_prompt(input_doc, context):
    """The exact wire prompt is reconstructible without changing a v6 input."""
    return (input_doc['prompt'] + '\n【本次快速请求的独立计算候选与校验说明】\n'
            + json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))


def _request_transport(input_doc, context):
    prompt = _transport_prompt(input_doc, context)
    evidence = {'version': FAST_TRANSPORT_VERSION, 'input_sha256': _digest(input_doc),
                'context': deepcopy(context), 'context_sha256': _digest(context),
                'prompt_sha256': sha256(prompt.encode('utf-8')).hexdigest(),
                'operation_reserve_seconds': OPERATION_RESERVE_SECONDS}
    return prompt, evidence


def _valid_transport(value, input_doc):
    if (not isinstance(value, dict) or set(value) != {'version', 'input_sha256', 'context',
            'context_sha256', 'prompt_sha256', 'operation_reserve_seconds'}
            or value.get('version') != FAST_TRANSPORT_VERSION
            or value.get('input_sha256') != _digest(input_doc)
            or type(value.get('operation_reserve_seconds')) is not int
            or value['operation_reserve_seconds'] != OPERATION_RESERVE_SECONDS
            or not isinstance(value.get('context'), dict)
            or value.get('context_sha256') != _digest(value['context'])):
        return False
    context = value['context']
    if (context.get('reference_period') != input_doc['reference_period']
            or context.get('target_period') != input_doc['target_period']
            or context.get('input_sha256') != _digest(input_doc)
            or context.get('history_sha256') != _digest(input_doc['history'])):
        return False
    from selected_request_options import validate_request_context
    try:
        validate_request_context(input_doc, context)
    except (ValueError, TypeError, KeyError, OverflowError):
        return False
    return value.get('prompt_sha256') == sha256(_transport_prompt(input_doc, context).encode('utf-8')).hexdigest()


def _log(log, message):
    try:
        log(message)
    except PublicationRejected:
        raise
    except Exception:
        pass


def _body(seal):
    return {key: deepcopy(seal.get(key)) for key in
            ('revision', 'ref_period', 'history', 'history_sha256',
             'input_doc', 'input_sha256', 'response', 'model_metadata')}


def _valid_metadata(metadata, reference):
    return (isinstance(metadata, dict) and metadata.get('ref') == reference
            and metadata.get('requested_model') == MODEL
            and type(metadata.get('attempt')) is int and 1 <= metadata['attempt'] <= 3
            and metadata.get('finish_reason') == 'stop'
            and isinstance(metadata.get('returned_model'), str)
            and PRO_FAMILY.fullmatch(metadata['returned_model']) is not None)


def validate_publication(seal, reference, now, *, published=True):
    """Self-contained immutable evidence; independent of rolling source files."""
    from selected_framework_view import card_fragment
    if (not isinstance(now, datetime) or now.tzinfo is None
            or not isinstance(reference, str) or not parse_period(reference)
            or reference != parse_period(reference)[0]
            or not isinstance(seal, dict) or not isinstance(seal.get('revision'), str)
            or seal['revision'] not in SUPPORTED_REVISIONS
            or seal.get('ref_period') != reference
            or seal.get('target_period') != next_period(reference)
            or type(seal.get('evidence_version')) is not int or seal['evidence_version'] != 2
            or seal.get('seal_sha256') != _digest(_body(seal))):
        raise ValueError('invalid-selected-six-seal')
    generated, opened = _aware(seal.get('generated_at')), _aware(seal.get('reference_open_time'))
    # New active requests obey the earlier platform close. Historical seals
    # retain their original draw-slot cutoff during immutable reconstruction.
    cutoff = (_close(reference) if seal['revision'] == REVISION
              else period_slot(next_period(reference)))
    reference_slot = period_slot(reference)
    if (generated is None or opened is None or not reference_slot <= opened < reference_slot + timedelta(minutes=1)
            or not opened <= generated < cutoff or generated > now):
        raise ValueError('invalid-selected-six-generation-evidence')
    history = validate_history(seal.get('history'), reference, generated)
    if (seal.get('history') != history or seal.get('history_sha256') != _digest(history)
            or seal.get('reference_open_time') != history[-1]['opentime']):
        raise ValueError('invalid-selected-six-history')
    revision = seal['revision']
    original_input = _input_doc(reference, history, revision=revision)
    saved_input = seal.get('input_doc')
    if (not isinstance(saved_input, dict) or _digest(saved_input) != _digest(original_input)
            or seal.get('input_sha256') != _digest(saved_input)):
        raise ValueError('invalid-selected-six-input')
    response = validate_response(seal.get('response'), revision=revision)
    if seal.get('response') != response:
        raise ValueError('invalid-selected-six-response')
    metadata = seal.get('model_metadata')
    if not _valid_metadata(metadata, reference):
        raise ValueError('unverified-selected-six-model')
    transport = metadata.get('request_transport')
    if transport is not None:
        if (not _valid_transport(transport, saved_input)
                or metadata.get('thinking') != 'disabled'
                or metadata.get('reasoning_effort') is not None
                or metadata.get('max_tokens') != SELECTED_MAX_TOKENS
                or generated >= _operation_deadline(reference)):
            raise ValueError('invalid-selected-seven-request-transport')
    elif metadata.get('thinking') == 'disabled':
        raise ValueError('invalid-selected-seven-request-transport')
    if seal.get('card_html') != card_fragment(reference, response, generated, revision=revision):
        raise ValueError('invalid-selected-six-card')
    state = seal.get('publication_state')
    if published or state == 'published':
        published_at = _aware(seal.get('published_at'))
        if (state != 'published' or published_at is None
                or not generated <= published_at < cutoff or published_at > now):
            raise ValueError('invalid-selected-six-publication-evidence')
        if transport is not None and published_at >= _operation_deadline(reference):
            raise ValueError('invalid-selected-seven-request-transport')
    elif state != 'prepared':
        raise ValueError('unpublished-selected-six-state')
    return seal


def _loader(root):
    return lambda **kw: load_snapshot(state_path=Path(root) / 'data/state.json',
                                      cache_path=Path(root) / 'data/stats_draw_history.json', **kw)


def _source(reference, snapshot, now):
    if expected_reference(now) != reference:
        raise PublicationRejected('selected-six-reference-advanced')
    rejection = publication_rejection(reference, snapshot, now)
    if rejection:
        raise PublicationRejected(rejection)
    if now >= _close(reference):
        raise PublicationRejected('selected-seven-platform-close-passed')
    return history_from_snapshot(snapshot, reference, now)


def _checked_loader(reference, history, loader, clock, *, operation_deadline=None):
    def checked(**kwargs):
        snapshot = loader(**kwargs)
        checked_at = clock()
        if operation_deadline is not None and checked_at >= operation_deadline:
            raise PublicationRejected('selected-seven-operation-window-passed')
        try:
            if _source(reference, snapshot, checked_at) != history:
                raise ValueError('selected-six-history-changed')
        except (ValueError, TypeError, KeyError, OSError, OverflowError) as error:
            raise PublicationRejected('selected-six-source-evidence-changed') from error
        return snapshot
    return checked


def _status(root, status, reference, clock, error_code=None, *, error_type=None, cause_code=None):
    atomic_json(Path(root) / 'data/selected_framework_status.json',
        {'version': 1, 'revision': REVISION, 'status': status, 'reference_period': reference,
         'target_period': next_period(reference), 'updated_at': clock().isoformat(),
         'requested_model': MODEL, 'error_code': error_code,
         'error_type': error_type, 'cause_code': cause_code})


def _writer(root, writer):
    if writer is not None:
        return writer
    from selected_framework_view import compose_selected_transform
    from panel_io import update_panel
    return lambda transform: update_panel(lambda html: compose_selected_transform(html, transform),
                                          path=Path(root) / 'panel.html')


def _publish(ledger, path, reference, loader, *, writer, clock, operation_deadline=None):
    """Retain the atomic common publisher while enforcing our earlier close."""
    from model_retirement import is_retired
    if is_retired('selected7'):return False
    published = publish_saved(ledger, path, reference, MARKER, loader, writer=writer, clock=clock)
    seal = find_seal(ledger, reference)
    if published and isinstance(seal, dict):
        stamp = _aware(seal.get('published_at'))
        if stamp is None or stamp >= _close(reference) or (operation_deadline is not None and stamp >= operation_deadline):
            seal['publication_state'] = 'late-unverified'
            seal['publication_error'] = ('selected-seven-operation-window-passed' if operation_deadline is not None
                                         and stamp is not None and stamp >= operation_deadline
                                         else 'selected-seven-platform-close-passed')
            from ai_prediction_guard import _sync_pending
            _sync_pending(ledger, seal)
            atomic_json(path, ledger)
            return False
    return published


def run_once(*, root=ROOT, post=None, api_key=None, loader=None, clock=beijing_now,
             monotonic=time.monotonic, sleep=time.sleep, writer=None, log=print, reference=None):
    from model_retirement import is_retired
    if is_retired('selected7'):return {'status':'retired','published':False,'ref_period':reference}
    from ai_call_control import AICallsDisabled,require_ai_enabled
    from selected_framework_view import card_fragment
    root, loader = Path(root), loader or _loader(root)
    try:require_ai_enabled(root=root,clock=clock)
    except AICallsDisabled:return {'status':'paused','published':False,'ref_period':reference}
    if reference is None:
        reference = expected_reference(clock())
    if reference != expected_reference(clock()):
        raise PublicationRejected('selected-six-reference-advanced')
    path = root / 'data/selected_framework_seals.json'
    ledger = read_stats(path)
    if (type(ledger.get('version', 1)) is not int or ledger.get('version', 1) != 1
            or ledger.get('revision', LEDGER_REVISION) != LEDGER_REVISION):
        raise ValueError('invalid-selected-six-ledger')
    ledger.update(version=1, revision=LEDGER_REVISION)
    attempts, seals = ledger.setdefault('request_attempts', {}), ledger.setdefault('prediction_seals', {})
    if not isinstance(attempts, dict) or not isinstance(seals, dict):
        raise ValueError('invalid-selected-six-index')
    owned_attempt = False;response_received=False
    try:
        existing = find_seal(ledger, reference)
        if existing is not None:
            validate_publication(existing, reference, clock(), published=False)
            if existing['revision'] != REVISION:
                # A rule change cannot regenerate or republish this reference's
                # old sealed prediction. Start with the next new reference.
                _status(root, 'preserved', reference, clock)
                return {'status': 'preserved', 'published': False, 'ref_period': reference}
            history = validate_history(existing['history'], reference, clock())
            saved_deadline = (_operation_deadline(reference) if existing.get('publication_state') == 'prepared'
                              and isinstance(existing.get('model_metadata', {}).get('request_transport'), dict) else None)
            published = _publish(ledger, path, reference,
                _checked_loader(reference, history, loader, clock, operation_deadline=saved_deadline),
                writer=_writer(root, writer), clock=clock, operation_deadline=saved_deadline)
            _status(root, 'preserved' if published else 'rejected', reference, clock)
            return {'status': 'preserved' if published else 'rejected', 'published': published, 'ref_period': reference}
        if reference in attempts:
            raise PublicationRejected('selected-six-already-attempted')
        _status(root, 'waiting', reference, clock)
        history = _source(reference, loader(persist=False), clock())
        input_doc = _input_doc(reference, history)
        _request_budget(reference, clock())
        from selected_request_options import build_request_options
        context = build_request_options(input_doc)
        actual_prompt, transport = _request_transport(input_doc, context)
        if not _valid_transport(transport, input_doc):
            raise ValueError('invalid-selected-seven-request-transport')
        operation_deadline = _operation_deadline(reference)
        checked_loader = _checked_loader(reference, history, loader, clock,
                                          operation_deadline=operation_deadline)
        checked_loader(persist=False)
        request_budget = _request_budget(reference, clock())
        if api_key is None:
            from app_secrets import get_secret
            api_key = os.getenv('DEEPSEEK_API_KEY') or get_secret('PENTA5_FRAMEWORK_API_KEY')
            if api_key.startswith('sk-sk-'):
                api_key = api_key[3:]
        if post is None:
            import requests
            post = requests.post
        require_ai_enabled(root=root,clock=clock)
        attempts[reference] = {'started_at': clock().isoformat(), 'revision': REVISION,
                               'input_doc': input_doc, 'input_sha256': _digest(input_doc),
                               'request_transport': deepcopy(transport), 'request_prompt': actual_prompt,
                               'request_budget_seconds': request_budget,
                               'operation_deadline_at': operation_deadline.isoformat(),
                               'platform_close_at': _close(reference).isoformat()}
        atomic_json(path, ledger)  # Durable tombstone before any paid attempt.
        owned_attempt = True
        _status(root, 'requesting', reference, clock)
        metadata, validated_attempts = {}, []
        def capture(message):
            try:
                if isinstance(message, str) and message.startswith('AI_RESPONSE_META '):
                    candidate = json.loads(message[len('AI_RESPONSE_META '):])
                    if (isinstance(candidate, dict) and candidate.get('ref') == reference
                            and type(candidate.get('attempt')) is int and 1 <= candidate['attempt'] <= 3):
                        metadata[candidate['attempt']] = candidate
                elif isinstance(message, str):
                    accepted = re.fullmatch(r'Killer response validated; ref=([0-9]{11}); attempt=([1-3]); publication pending\.', message)
                    if accepted and accepted.group(1) == reference:
                        validated_attempts.append(int(accepted.group(2)))
            except PublicationRejected:
                raise
            except (ValueError, TypeError, KeyError):
                pass
            _log(log, message)
        response_validator = selected_validator(input_doc, validate_response, kind='seven', revision=REVISION)
        response, generated = request_killer_response(actual_prompt, reference,
            post=post, api_key=api_key, model=MODEL, load_snapshot=checked_loader, clock=clock,
            log=capture, monotonic=monotonic, sleep=sleep,
            validator=response_validator, retry_instruction=RETRY_INSTRUCTION + SEVEN_RETRY_ADDITION,
            request_budget_seconds=request_budget,
            reasoning_effort=SELECTED_REASONING_EFFORT, thinking_enabled=False,
            max_tokens=SELECTED_MAX_TOKENS)
        model_metadata = metadata.get(validated_attempts[-1], {}) if validated_attempts else {}
        if not _valid_metadata(model_metadata, reference):
            attempts[reference]['model_metadata'] = model_metadata
            attempts[reference]['result'] = 'returned-model-not-pro'
            atomic_json(path, ledger)
            raise PublicationRejected('returned-model-not-pro')
        response_received=True
        if (model_metadata.get('thinking') != 'disabled' or model_metadata.get('reasoning_effort') is not None
                or model_metadata.get('max_tokens') != SELECTED_MAX_TOKENS):
            raise ValueError('invalid-selected-seven-request-transport')
        model_metadata = dict(model_metadata, request_transport=deepcopy(transport))
        report = validation_report(response_validator)
        if report:
            model_metadata = dict(model_metadata, strategy_validation=report)
        require_ai_enabled(root=root,clock=clock)
        candidate = {'revision': REVISION, 'ref_period': reference, 'history': history,
                     'history_sha256': _digest(history), 'input_doc': input_doc,
                     'input_sha256': _digest(input_doc), 'response': response, 'model_metadata': model_metadata}
        candidate['seal_sha256'] = _digest(_body(candidate))
        prepared = store_prepared(ledger, path, checked_loader(persist=False), candidate,
            card_fragment(reference, response, generated, revision=REVISION),
            generated_at=generated, enqueue=False)
        validate_publication(prepared, reference, clock(), published=False)
        published = _publish(ledger, path, reference, checked_loader,
                             writer=_writer(root, writer), clock=clock,
                             operation_deadline=operation_deadline)
        _status(root, 'published' if published else 'rejected', reference, clock)
        _log(log, f'SELECTED_SIX ref={reference} target={next_period(reference)} published={published}')
        return {'status': 'published' if published else 'rejected', 'published': published, 'ref_period': reference}
    except AICallsDisabled as error:
        if owned_attempt and getattr(error,'requests_sent',1)==0 and not response_received and reference not in seals:
            attempts.pop(reference,None);atomic_json(path,ledger)
        _status(root,'paused',reference,clock,'manual-api-stopped')
        return {'status':'paused','published':False,'ref_period':reference}
    except Exception as error:
        code, kind, cause_code = _error_diagnostics(error)
        # Record only a failed request started by this invocation. Previously
        # sealed/prepared predictions and historical request tombstones remain
        # unchanged, and an observability failure must never replay a paid call.
        if owned_attempt and reference not in seals:
            try:
                failed_at = clock()
                started_at = _aware(attempts[reference].get('started_at'))
                if (isinstance(failed_at, datetime) and failed_at.tzinfo is not None
                        and failed_at.utcoffset() is not None and started_at is not None
                        and failed_at >= started_at):
                    attempts[reference]['failure'] = {'code': code, 'error_type': kind,
                        'cause_code': cause_code, 'failed_at': failed_at.isoformat()}
                    atomic_json(path, ledger)
            except Exception:
                _log(log, 'SELECTED_SIX_FAILURE_RECORD ref=%s code=diagnostic-write-failed' % reference)
        _status(root, 'rejected' if isinstance(error, PublicationRejected) else 'failed', reference, clock,
                code, error_type=kind, cause_code=cause_code)
        _log(log, 'SELECTED_SIX_FAILURE ref=%s code=%s error_type=%s cause_code=%s' %
             (reference, code, kind, cause_code or 'none'))
        raise


def select_current(root, snapshot):
    reference, now = (snapshot.get('latest') or {}).get('period'), snapshot.get('now') or beijing_now()
    try:
        history = _source(reference, snapshot, now)
        seal = find_seal(read_stats(Path(root) / 'data/selected_framework_seals.json'), reference)
        if not isinstance(seal, dict) or seal.get('revision') != REVISION:
            return None
        validate_publication(seal, reference, now)
        return seal if seal['history'] == history else None
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        return None


def main(reference=None):
    from model_retirement import is_retired
    if is_retired('selected7'):return 0
    import fcntl
    with (ROOT / 'data/selected_framework_job.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            result = run_once(reference=reference)
            code = 0 if result['published'] or result.get('status')=='paused' else 1
        except Exception as error:
            _log(print, 'Selected six-item not published; error_type=' + type(error).__name__)
            code = 1
        try:
            from selected_framework_view import refresh_selected_attack_panel
            refresh_selected_attack_panel(root=ROOT, reference=reference)
        except Exception as error:
            _log(print, 'Selected six-item display deferred; error_type=' + type(error).__name__)
        return code


if __name__ == '__main__':
    raise SystemExit(main())
