"""Independent Pro selection of sum keep sets for five user-fixed exclusions.

The user fixes q/b/s positions and all/qbs tails. The provider selects only
both sum keep sets; the cold engine supplies each first ranked exclusion digit.
An independent durable attempt prevents repeated payment for one reference.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from itertools import combinations
import json
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from ai_call_control import AICallsDisabled, require_ai_enabled
from ai_prediction_guard import PublicationRejected
import cold_fixed_framework as core
from killer_ai_client import request_killer_response
from stats_history import TZ, beijing_now, business_start, load_snapshot, period_slot


ROOT = Path(__file__).resolve().parents[1]
MODEL = 'deepseek-v4-pro'
REASONING_EFFORT = 'low'
THINKING_ENABLED = False
MAX_RESPONSE_TOKENS = 2048
REQUEST_TRANSPORT = {'version': 'cold-fixed-pro-fast-2048-qbs-all-qbs-v1', 'model': MODEL,
                     'thinking': 'disabled', 'max_tokens': MAX_RESPONSE_TOKENS,
                     'selection_policy': core.HE3_PARITY_POLICY,
                     'fixed_positions': list(core.FIXED_POSITION_KEYS),
                     'fixed_tails': list(core.FIXED_TAIL_KEYS)}
REQUEST_BUDGET_SECONDS = 120.0
PRO_FAMILY = core.PRO_FAMILY
RETRY_INSTRUCTION = ('只返回完整JSON，恰好he2_keep、he3_keep、reason三个字段：'
                     'he2_keep为从ASCII数字02468中自主选4个不同数字组成的字符串；'
                     'he3_keep为从ASCII数字0123456789中自主选5个不同数字组成的字符串，必须2单3双或3单2双，禁止全单、全双及1比4或4比1；单数13579、双数02468，0是双数。请从输入he3_allowed_keeps中自主重选一项；reason为简短理由。'
                     '定位已由用户固定为千、百、十，合尾固定为千百十个、千百十；不得输出、重选或修改项目键与排除冷码，不得默认补位、添加额外字段或说明。')


def _now(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PublicationRejected('cold-fixed-model-invalid-clock')
    return value.astimezone(TZ)


def _log(log, message):
    try:
        log(message)
    except PublicationRejected:
        raise
    except Exception:
        pass


def validate_selection(payload):
    if (not isinstance(payload, dict)
            or set(payload) != {'he2_keep', 'he3_keep', 'reason'}):
        raise ValueError('cold-fixed-model-invalid-selection')
    selected = {}
    for field in core.KEEP_KEYS:
        selected[field] = core._keep(payload[field], field)
    core.validate_new_he3_keep(selected['he3_keep'])
    reason = payload['reason']
    if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 200:
        raise ValueError('cold-fixed-model-invalid-reason')
    reason.encode('utf-8')
    return {**selected, 'reason': reason.strip()}


def _prompt(context):
    public = {key: context[key] for key in ('revision', 'reference_period', 'target_period', 'sample_count',
              'first_period', 'last_period', 'history_sha256', 'position_candidates', 'tail_candidates')}
    public['records'] = context['input_snapshot']['records']
    public['fixed_positions'] = list(core.FIXED_POSITION_KEYS)
    public['fixed_tails'] = list(core.FIXED_TAIL_KEYS)
    public['he3_allowed_keeps'] = [''.join(values) for values in combinations('0123456789', 5)
        if sum(int(digit) % 2 for digit in values) in (2, 3)]
    return ('你是独立的冷码保留集选择模型。仅依据下列当前统计日最近最多30期真实开奖的前4球，'
            '用户已固定千、百、十3个位置，以及千百十个、千百十2种合尾，合计5项各排第1冷码；不得改选项目或冷码。'
            '你只自主选择二数合保留4码与三数合保留5码，连同5项固定排除共7项。'
            '位置q/b/s/g依次为千百十个；all为四位全合，qbs/qbg/qsg/bsg为对应三位合尾。'
            '二数合：从前4球的6种不同位置二位组合分别求和取个位，任意一种合尾落在he2_keep保留集即该项命中；'
            'he2_keep必须从02468五个偶数中自主保留恰好4个不同数字。'
            '三数合：从前4球的4种不同位置三位组合分别求和取个位，任意一种合尾落在he3_keep保留集即该项命中；'
            'he3_keep必须自主保留恰好5个不同ASCII数字，且必须2单3双或3单2双；禁止全单、全双以及1单4双、4单1双。这是硬性条件，不得违反。单数只有13579，双数只有02468，0是双数。he3_keep必须从输入he3_allowed_keeps合法候选中自主选择一项，原样输出5位字符串；不要增加输出字段。'
            '只使用前4球，第5球不参与。整组7项必须5项排除和2项保留集全部成功。'
            '不要引用其他模型结果，不要承诺未来命中。'
            + RETRY_INSTRUCTION + '\n输入：'
            + json.dumps(public, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


def _loader(root):
    return lambda **kwargs: load_snapshot(state_path=root / 'data/state.json',
                                         cache_path=root / 'data/stats_draw_history.json', **kwargs)


def _context(reference, snapshot, now):
    if core.expected_reference(now) != reference:
        raise PublicationRejected('cold-fixed-model-reference-advanced')
    context = core.candidate_context(snapshot, now)
    if context['reference_period'] != reference:
        raise PublicationRejected('cold-fixed-model-reference-not-ready')
    return context


def _same_input(expected, current):
    fields = ('reference_period', 'target_period', 'history_sha256', 'sample_count', 'first_period',
              'last_period', 'position_candidates', 'tail_candidates', 'revision')
    return all(current.get(key) == expected.get(key) for key in fields)


def _checked_loader(reference, expected, loader, root, clock):
    def checked(**kwargs):
        require_ai_enabled(root=root, clock=clock)
        source = loader(**kwargs)
        current = _context(reference, source, _now(clock()))
        if not _same_input(expected, current):
            raise PublicationRejected('cold-fixed-model-source-changed')
        return source
    return checked


def _budget(context, now):
    remaining = (datetime.fromisoformat(context['deadline']) - now).total_seconds() - 10.0
    if remaining < 20.0:
        raise PublicationRejected('cold-fixed-model-insufficient-close-budget')
    return min(REQUEST_BUDGET_SECONDS, remaining)


def _attempt_path(root, reference):
    target_day = business_start(period_slot(core.next_period(reference))).strftime('%Y-%m-%d')
    return root / 'data' / ('cold_fixed_framework_attempts_' + target_day + '.json')


def _read_attempts(path):
    ledger = core._read(path)
    if ledger is None:
        return {'version': core.VERSION, 'revision': core.REVISION, 'attempts': {}}
    if not isinstance(ledger.get('attempts'), dict):
        raise ValueError('cold-fixed-model-invalid-attempts')
    for reference, attempt in ledger['attempts'].items():
        if (not isinstance(attempt, dict) or attempt.get('reference_period') != reference
                or core._period(reference) != reference
                or ('revision' in attempt and attempt['revision'] not in core.REVISIONS)
                or not isinstance(attempt.get('owner'), str)
                or not re.fullmatch('[0-9a-f]{32}', attempt['owner'])):
            raise ValueError('cold-fixed-model-invalid-attempts')
    return ledger


def _status(root, status, reference, clock, error_code=None):
    core._write(root / 'data/cold_fixed_framework_status.json', {
        'version': core.VERSION, 'revision': core.REVISION,
        'status': status, 'reference_period': reference,
        'target_period': core.next_period(reference) if reference is not None else None,
        'updated_at': _now(clock()).isoformat(), 'requested_model': MODEL,
        'error_code': error_code,
    })


def _safe_failure(error):
    if re.fullmatch(r'Nonretryable killer provider HTTP 402', str(error)):
        return 'provider-payment-required'
    if re.fullmatch(r'Nonretryable killer provider HTTP (400|401|403|404|405|410|422)', str(error)):
        return 'provider-nonretryable-http'
    text = str(error)
    if isinstance(error, PublicationRejected) and re.fullmatch('[a-z][a-z0-9-]{0,99}', text):
        return text
    return 'cold-fixed-model-inference-or-evidence-error'


def run_once(*, root=ROOT, reference=None, post=None, api_key=None, loader=None,
             clock=beijing_now, monotonic=time.monotonic, sleep=time.sleep, writer=None, log=print):
    root = Path(root)
    # This guard intentionally precedes data access, lock creation and secrets.
    try:
        require_ai_enabled(root=root, clock=clock)
    except AICallsDisabled:
        return {'status': 'paused', 'published': False, 'ref_period': reference}
    reference = reference or core.expected_reference(_now(clock()))
    loader = loader or _loader(root)
    observed_posts = 0
    owned = False
    response_received = False
    path, owner, ledger = _attempt_path(root, reference), uuid4().hex, None
    # Separate from the core statistics ledger lock: inference never blocks
    # passive settlement or ordinary panel refresh for its network duration.
    with core._lock(root / 'data/cold_fixed_framework_model.lock'):
        try:
            require_ai_enabled(root=root, clock=clock)
            if core.expected_reference(_now(clock())) != reference:
                raise PublicationRejected('cold-fixed-model-reference-advanced')
            ledger = _read_attempts(path)
            if reference in ledger['attempts']:
                return {'status': 'already-attempted', 'published': False, 'ref_period': reference}
            index = core._read(root / 'data' / core.INDEX_NAME)
            if index is not None:
                if (index.get('revision') != core.REVISION
                        or core.REVISION not in index.get('rule_tracking', {})):
                    raise PublicationRejected('cold-fixed-model-rule-migration-required')
                core._validate_rule_tracking(index, _now(clock()))
                if (period_slot(core.next_period(reference))
                        < period_slot(index['rule_tracking'][core.REVISION]['start_target'])):
                    raise PublicationRejected('cold-fixed-model-rule-not-started')
            source = loader(persist=False)
            input_at = _now(clock())
            # Loading creates its own snapshot timestamp. Validate it against
            # the clock AFTER loading, then freeze a non-future input boundary.
            if (not isinstance(source, dict) or core._aware(source.get('now')) > input_at):
                raise PublicationRejected('cold-fixed-model-future-input')
            source = deepcopy(source)
            source['now'] = input_at
            context = _context(reference, source, input_at)
            checked_loader = _checked_loader(reference, context, loader, root, clock)
            checked_loader(persist=False)
            budget = _budget(context, _now(clock()))
            require_ai_enabled(root=root, clock=clock)
            if api_key is None:
                from app_secrets import get_secret
                api_key = os.getenv('DEEPSEEK_API_KEY') or get_secret('PENTA5_FRAMEWORK_API_KEY')
                if api_key.startswith('sk-sk-'):
                    api_key = api_key[3:]
            if post is None:
                import requests
                post = requests.post
            require_ai_enabled(root=root, clock=clock)
            requested_at = _now(clock())
            _context(reference, source, requested_at)
            budget = _budget(context, requested_at)
            ledger['attempts'][reference] = {
                'revision': core.REVISION,
                'reference_period': reference, 'target_period': context['target_period'],
                'owner': owner, 'requested_model': MODEL, 'started_at': requested_at.isoformat(),
                'input_sha256': context['input_sha256'], 'input_snapshot': context['input_snapshot'],
                'history_sha256': context['history_sha256'], 'request_budget_seconds': budget,
                'request_transport': deepcopy(REQUEST_TRANSPORT),
                'platform_close_at': context['deadline'], 'state': 'reserved',
            }
            core._write(path, ledger)
            owned = True
            _status(root, 'requesting', reference, clock)
            received_metadata, accepted_attempts = {}, []
            def capture(message):
                try:
                    if isinstance(message, str) and message.startswith('AI_RESPONSE_META '):
                        value = json.loads(message[len('AI_RESPONSE_META '):])
                        if (isinstance(value, dict) and value.get('ref') == reference
                                and type(value.get('attempt')) is int and 1 <= value['attempt'] <= 3):
                            received_metadata[value['attempt']] = value
                    elif isinstance(message, str):
                        match = re.fullmatch(r'Killer response validated; ref=([0-9]{11}); attempt=([1-3]); publication pending\.', message)
                        if match and match[1] == reference:
                            accepted_attempts.append(int(match[2]))
                except PublicationRejected:
                    raise
                except (ValueError, TypeError):
                    pass
                _log(log, message)
            def checked_selection(payload):
                try:
                    return validate_selection(payload)
                except ValueError as error:
                    # 2026-10-30: pro often returns an unbalanced he3_keep (e.g. 02468)
                    # even though the prompt states the hard 2-odd/3-odd rule. Retrying
                    # burned ~3x tokens. Locally project the model's own digits onto the
                    # closest allowed balanced keep (deterministic, max overlap), then
                    # re-validate; only fall through to retry when still invalid.
                    if str(error) == 'strategy-response-cold-he3-parity' and isinstance(payload, dict):
                        repaired = core.repair_he3_keep(payload.get('he3_keep'))
                        if repaired is not None:
                            candidate = dict(payload)
                            candidate['he3_keep'] = repaired
                            try:
                                repaired_result = validate_selection(candidate)
                            except ValueError:
                                repaired_result = None
                            if repaired_result is not None:
                                _log(log, 'COLD_HE3_PARITY_REPAIRED ref=' + reference
                                     + ' from=' + str(payload.get('he3_keep')) + ' to=' + repaired)
                                return repaired_result
                    # Emit only our fixed schema/strategy codes, never provider text.
                    code = str(error)
                    allowed = {'cold-fixed-model-invalid-selection', 'cold-fixed-model-invalid-reason',
                        'cold50-model-invalid-selection', 'cold50-model-invalid-reason',
                        'cold-fixed-framework-invalid-he2-keep', 'cold-fixed-framework-invalid-he3-keep',
                        'cold50-framework-invalid-he2-keep', 'cold50-framework-invalid-he3-keep',
                        'strategy-response-cold-he3-parity'}
                    code = code if code in allowed else 'invalid-selection'
                    _log(log, 'COLD_HE3_VALIDATION_REJECTED code=' + code)
                    raise
            actual_post = post
            def counted_post(*args, **kwargs):
                nonlocal observed_posts
                observed_posts += 1  # Uncertain transport outcomes remain paid tombstones.
                return actual_post(*args, **kwargs)
            require_ai_enabled(root=root, clock=clock)
            response, generated_at = request_killer_response(_prompt(context), reference,
                post=counted_post, api_key=api_key, model=MODEL, load_snapshot=checked_loader,
                clock=clock, log=capture, monotonic=monotonic, sleep=sleep,
                validator=checked_selection, retry_instruction=RETRY_INSTRUCTION,
                request_budget_seconds=budget, reasoning_effort=REASONING_EFFORT,
                thinking_enabled=THINKING_ENABLED, max_tokens=MAX_RESPONSE_TOKENS)
            response_received = True
            accepted = received_metadata.get(accepted_attempts[-1], {}) if accepted_attempts else {}
            if (accepted.get('requested_model') != MODEL or accepted.get('finish_reason') != 'stop'
                    or not isinstance(accepted.get('returned_model'), str)
                    or not PRO_FAMILY.fullmatch(accepted['returned_model'])):
                raise PublicationRejected('cold-fixed-model-returned-model-not-pro')
            response = validate_selection(response)
            completed_at = _now(generated_at)
            if completed_at < requested_at or completed_at > _now(clock()):
                raise PublicationRejected('cold-fixed-model-invalid-completion-time')
            metadata = {'requested_model': MODEL, 'returned_model': accepted['returned_model'],
                        'requested_at': requested_at.isoformat(), 'completed_at': completed_at.isoformat(),
                        'input_sha256': context['input_sha256'], 'finish_reason': 'stop',
                        'reason': response['reason'], 'selection_policy': core.HE3_PARITY_POLICY}
            # These are explicit user configuration, never repairs of provider choices.
            selection = {'selected_positions': list(core.FIXED_POSITION_KEYS),
                         'selected_tails': list(core.FIXED_TAIL_KEYS),
                         **{key: response[key] for key in core.KEEP_KEYS}}
            ledger['attempts'][reference].update(state='response-received', selection=selection, metadata=metadata)
            core._write(path, ledger)
            require_ai_enabled(root=root, clock=clock)
            checked_loader(persist=False)
            result = core.prepare_and_publish(source, selection, metadata, root=root, clock=clock, writer=writer)
            require_ai_enabled(root=root, clock=clock)
            prediction = result.get('prediction') if isinstance(result, dict) else None
            published = bool(isinstance(result, dict) and result.get('stats_error') is False
                             and result.get('source_error') is None and prediction
                             and prediction.get('publication_state') == 'published'
                             and prediction.get('reference_period') == reference
                             and prediction.get('target_period') == context['target_period']
                             and prediction.get('input_sha256') == context['input_sha256'])
            ledger['attempts'][reference]['state'] = 'published' if published else 'rejected'
            core._write(path, ledger)
            _status(root, 'published' if published else 'rejected', reference, clock)
            _log(log, 'COLD_FIXED_FRAMEWORK ref=%s target=%s published=%s' % (reference, context['target_period'], published))
            return {'status': 'published' if published else 'rejected', 'published': published, 'ref_period': reference}
        except AICallsDisabled as error:
            # Remove only this invocation's demonstrably unsent reservation.
            # A returned or possibly sent response is never replayed for payment.
            reported = getattr(error, 'requests_sent', observed_posts)
            reported = reported if type(reported) is int and reported >= 0 else 1
            sent = max(observed_posts, reported)
            if owned and sent == 0 and not response_received:
                current = _read_attempts(path)
                if current['attempts'].get(reference, {}).get('owner') == owner:
                    current['attempts'].pop(reference)
                    core._write(path, current)
            _status(root, 'paused', reference, clock, 'manual-api-stopped')
            return {'status': 'paused', 'published': False, 'ref_period': reference}
        except Exception as error:
            code = _safe_failure(error)
            if owned:
                try:
                    current = _read_attempts(path)
                    if current['attempts'].get(reference, {}).get('owner') == owner:
                        current['attempts'][reference].update(state='failed', error_code=code)
                        core._write(path, current)
                except Exception:
                    pass
            try:
                _status(root, 'rejected' if isinstance(error, PublicationRejected) else 'failed', reference, clock, code)
            except Exception:
                pass
            _log(log, 'COLD_FIXED_FRAMEWORK_FAILURE ref=%s code=%s' % (reference, code))
            return {'status': 'rejected' if isinstance(error, PublicationRejected) else 'failed',
                    'published': False, 'ref_period': reference, 'error_code': code}


def main(reference=None):
    try:
        result = run_once(reference=reference)
    except AICallsDisabled:
        return 0
    except Exception as error:
        print('Cold framework job failed; error_type=' + type(error).__name__, flush=True)
        return 1
    return 0 if result['status'] in ('paused', 'published', 'already-attempted') else 1


if __name__ == '__main__':
    raise SystemExit(main())
