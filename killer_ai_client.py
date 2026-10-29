"""Strict, bounded killer inference; every external effect is injected."""
import json
import re
import time

from ai_call_control import AICallsDisabled, require_ai_enabled
from ai_strategy_validation import strategy_retry_feedback
from ai_http_observability import emit_http_phase
from ai_provider_policy import checked_source_time, strict_json_object, wait_before_retry

from ai_prediction_guard import (PublicationRejected, publication_rejection,
                                 validate_killer_response)
from ai_request_deadline import (MAX_TOKENS, REQUEST_BUDGET_SECONDS,
                                 REQUEST_POLICY_VERSION, RequestDeadline, request_timeout)


def _safe_log(log, message):
    # Observability failures must never repeat a paid provider request.
    try:
        log(message)
    except PublicationRejected:
        raise
    except Exception:
        pass


def _require_enabled(clock, requests_sent):
    try:
        require_ai_enabled(clock=clock)
    except AICallsDisabled as error:
        # A caller can release a reserved tombstone only when no POST was even
        # invoked. Transport failures still count as sent for duplicate safety.
        error.requests_sent = requests_sent
        raise


def _safe_model_name(value, api_key):
    if not isinstance(value, str):
        return None
    if api_key and str(api_key) in value:
        return '[redacted]'
    # A model identifier is metadata, never a place to echo arbitrary provider text.
    return value if re.fullmatch(r'deepseek-(?:chat|reasoner|(?:v4(?:\.1)?-)?(?:flash|pro)(?:-[0-9]{4})?)', value) else '[redacted]'


def _response_metadata(log, envelope, reference, attempt, model, api_key, elapsed, *, deadline=None,
                       reasoning_effort='high', thinking_enabled=True, max_tokens=MAX_TOKENS):
    try:
        body = envelope if isinstance(envelope, dict) else {}
        choices = body.get('choices')
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        reason = choice.get('finish_reason')
        if reason not in ('stop', 'length', 'tool_calls', 'content_filter', 'insufficient_system_resource'):
            reason = None
        raw_usage = body.get('usage') if isinstance(body.get('usage'), dict) else {}
        usage = {}
        for key in ('prompt_tokens', 'completion_tokens', 'prompt_cache_hit_tokens', 'prompt_cache_miss_tokens'):
            value = raw_usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9007199254740991:
                usage[key] = value
        details = raw_usage.get('completion_tokens_details')
        reasoning = raw_usage.get('reasoning_tokens')
        if reasoning is None and isinstance(details, dict):
            reasoning = details.get('reasoning_tokens')
        if isinstance(reasoning, int) and not isinstance(reasoning, bool) and 0 <= reasoning <= 9007199254740991:
            usage['reasoning_tokens'] = reasoning
        policy_version = deadline.policy_version if deadline is not None else REQUEST_POLICY_VERSION
        if reasoning_effort != 'high':
            policy_version = reasoning_effort + policy_version[len('high'):]
        if not thinking_enabled or max_tokens != MAX_TOKENS:
            mode = reasoning_effort if thinking_enabled else 'disabled'
            scope = ('slot-budget' if deadline is not None
                     and deadline.requested_budget_seconds != REQUEST_BUDGET_SECONDS else 'deadline')
            policy_version = f'{mode}-{max_tokens}-tokens-{scope}-v3'
        record = {'ref': reference, 'attempt': attempt,
                  'requested_model': _safe_model_name(model, api_key),
                  'returned_model': _safe_model_name(body.get('model'), api_key),
                  'finish_reason': reason, 'usage': usage,
                  'thinking': 'enabled' if thinking_enabled else 'disabled',
                  'reasoning_effort': reasoning_effort if thinking_enabled else None, 'max_tokens': max_tokens,
                  'request_policy_version': policy_version,
                  'request_budget_seconds': deadline.requested_budget_seconds if deadline is not None else REQUEST_BUDGET_SECONDS,
                  'elapsed_seconds': round(max(0.0, float(elapsed)), 3)}
        if deadline is not None and deadline.requested_budget_seconds != REQUEST_BUDGET_SECONDS:
            record['effective_request_budget_seconds'] = deadline.budget_seconds
        _safe_log(log, 'AI_RESPONSE_META ' + json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
    except PublicationRejected:
        raise
    except Exception:
        # No metadata field, formatter, or logger failure can alter business flow.
        pass



class KillerResponseError(ValueError):
    pass


def _parse_content(content):
    if not isinstance(content, str):
        raise ValueError('Killer response content must be text')
    clean = re.sub(r'```json\s*|\s*```', '', content).strip()
    start, end = clean.find('{'), clean.rfind('}')
    if start < 0 or end < start:
        raise ValueError('Killer response did not contain a JSON object')
    return strict_json_object(clean[start:end + 1])


def request_killer_response(prompt, reference, *, post, api_key, model,
                            load_snapshot, clock, log=print,
                            monotonic=time.monotonic, sleep=time.sleep,
                            validator=None, retry_instruction=None,
                            request_budget_seconds=REQUEST_BUDGET_SECONDS,
                            reasoning_effort='high', thinking_enabled=True, max_tokens=MAX_TOKENS,
                            operation_window=None):
    """Three calls at most within one frozen, explicitly bounded slot budget.

    Linux real-time alarms bound the network read and JSON decoding even when
    the provider keeps sending whitespace. The outer job watchdog is separate.
    Strict schema and publication checks still run before a response is returned.
    """
    if type(reasoning_effort) is not str or reasoning_effort not in ('low', 'high', 'max'):
        raise ValueError('invalid-killer-reasoning-effort')
    if type(thinking_enabled) is not bool:
        raise ValueError('invalid-killer-thinking-mode')
    if type(max_tokens) is not int or not 1 <= max_tokens <= MAX_TOKENS:
        raise ValueError('invalid-killer-max-tokens')
    requests_sent = 0
    _require_enabled(clock, requests_sent)
    if operation_window is not None:
        if operation_window.reference != reference:
            raise PublicationRejected('operation-window-invalid-reference')
        request_budget_seconds = min(request_budget_seconds, operation_window.request_budget())
    deadline = RequestDeadline(reference, clock=clock, monotonic=monotonic,
                               budget_seconds=request_budget_seconds,
                               cutoff_at=operation_window.request_cutoff if operation_window is not None else None)
    messages = [{'role': 'user', 'content': prompt}]
    final_error = 'no-attempt'
    for attempt in range(1, 4):
        _require_enabled(clock, requests_sent)
        checked_source_time(reference, load_snapshot, clock)
        if operation_window is not None:
            operation_window.request_budget()
        remaining = deadline.remaining()
        timeout = min(80.0, remaining - 1.0)
        if remaining < 20.0:
            raise PublicationRejected('insufficient-time-for-killer-request')
        payload = None
        truncated = False
        validation_failure = False
        response, status = None, None
        try:
            attempt_started = monotonic()
            response = None
            envelope, envelope_error = None, None
            # A requests read timeout is inactivity-only. Bound the entire call,
            # including keepalives and decoding, with an independent Linux alarm.
            with request_timeout(deadline.remaining()):
                try:
                    _require_enabled(clock, requests_sent)
                    if operation_window is not None:
                        operation_window.request_budget()
                    requests_sent += 1
                    emit_http_phase(log, reference, attempt, 'start')
                    try:
                        request_body = {'model': model, 'messages': messages,
                                        'response_format': {'type': 'json_object'},
                                        'thinking': {'type': 'enabled' if thinking_enabled else 'disabled'}}
                        if thinking_enabled:
                            request_body['reasoning_effort'] = reasoning_effort
                        request_body['max_tokens'] = max_tokens
                        if operation_window is not None:
                            operation_window.request_budget()
                            if deadline.remaining() < 20:
                                raise PublicationRejected('operation-window-insufficient-budget')
                        response = post('https://api.deepseek.com/chat/completions',
                            json=request_body,
                            headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
                            timeout=timeout)
                        status = response.status_code
                    finally:
                        emit_http_phase(log, reference, attempt, 'complete', status=status)
                    if status == 200:
                        try:
                            envelope = response.json()
                        except PublicationRejected:
                            raise
                        except Exception as error:
                            envelope_error = error
                finally:
                    if response is not None:
                        close = getattr(response, 'close', None)
                        if callable(close):
                            try:
                                close()
                            except PublicationRejected:
                                raise
                            except Exception:
                                pass
            if status == 200:
                _response_metadata(log, envelope, reference, attempt, model, api_key,
                                   monotonic() - attempt_started, deadline=deadline,
                                   reasoning_effort=reasoning_effort,
                                   thinking_enabled=thinking_enabled, max_tokens=max_tokens)
            deadline.check()
            generated_at = checked_source_time(reference, load_snapshot, clock)
            if status != 200:
                if status in (400, 401, 402, 403, 404, 405, 410, 422):
                    raise KillerResponseError(f'Nonretryable killer provider HTTP {status}')
                raise OSError(f'Killer provider HTTP {status}')
            if envelope_error is not None:
                raise envelope_error
            validation_failure = True
            finish_reason = envelope['choices'][0].get('finish_reason')
            truncated = finish_reason == 'length'
            if finish_reason != 'stop':
                raise ValueError('Provider response did not finish normally')
            validated = (validate_killer_response if validator is None else validator)(
                _parse_content(envelope['choices'][0]['message']['content']))
            _safe_log(log, f'Killer response validated; ref={reference}; attempt={attempt}; publication pending.')
            deadline.check()
            generated_at = checked_source_time(reference, load_snapshot, clock)
            return validated, generated_at
        except AICallsDisabled as error:
            error.requests_sent = requests_sent
            raise
        except (PublicationRejected, KillerResponseError):
            raise
        except Exception as error:
            # Never echo provider bodies, raw model text, reasons, or credentials.
            final_error = type(error).__name__
            _safe_log(log, f'Killer attempt {attempt}/3 rejected; error_type={final_error}; not published.')
            if attempt == 3:
                break
            _require_enabled(clock, requests_sent)
            checked_source_time(reference, load_snapshot, clock)
            if operation_window is not None:
                operation_window.request_budget()
            if deadline.remaining() < 22.0:
                raise PublicationRejected('killer-retry-budget-exhausted')
            if validation_failure:
                retry_prefix = ('上次响应因输出额度耗尽被截断。请停止重复枚举和反复推导，''在有限时间内完成一次核查并返回完整JSON；不得为了凑答案补默认号码。' if truncated else '')
                messages = [{'role': 'user', 'content': prompt}, {'role': 'user', 'content': retry_prefix +
                    (retry_instruction if retry_instruction is not None else
                    '上次响应未通过校验，请重新给出完整JSON。k1/k2/k3/k4均为恰好1个0至9整数的数组；'
                    'he_all/he_q3/he_h3均为0至9整数，不得为布尔值、小数或缺失。'
                    'exclude_patterns须含恰好2个不同的四位形态，各形态只能全用单双、全用奇偶或全用大小字符。'
                    'reason为简短理由。不得输出默认补位、截断JSON或额外说明。')}]
                # Only locally calculated strategy errors supply this feedback.
                # Arbitrary provider bodies and exception messages stay private.
                feedback = strategy_retry_feedback(error)
                if feedback:
                    messages[-1]['content'] += '\n' + feedback
            wait_before_retry(reference, attempt, deadline, clock=clock, sleep=sleep, log=log,
                              response=response, status=status, validation_failure=validation_failure)
    raise KillerResponseError(f'Killer inference failed after 3 attempts: {final_error}; not published')
