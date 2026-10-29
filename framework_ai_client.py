"""Bounded framework inference: strict schema, explicit diagnostics and no late retries.

All effects are injected by the caller so tests never need a network or API key.
"""
import json
import re
import time

from ai_call_control import AICallsDisabled, require_ai_enabled
from ai_provider_policy import checked_source_time, strict_json_object, wait_before_retry
from ai_strategy_validation import strategy_retry_feedback
from ai_http_observability import emit_http_phase

from ai_prediction_guard import (PublicationRejected, normalize_framework_keep,
                                 publication_rejection, validate_framework_response)
from ai_request_deadline import (MAX_TOKENS, REQUEST_BUDGET_SECONDS,
                                 REQUEST_POLICY_VERSION, RequestDeadline, request_timeout)


def _safe_log(log, message):
    # Observability failures must never repeat a paid provider request.
    try:
        log(message)
    except Exception:
        pass


def _require_enabled(clock, requests_sent):
    try:
        require_ai_enabled(clock=clock)
    except AICallsDisabled as error:
        error.requests_sent = requests_sent
        raise


def _safe_model_name(value, api_key):
    if not isinstance(value, str):
        return None
    if api_key and str(api_key) in value:
        return '[redacted]'
    # A model identifier is metadata, never a place to echo arbitrary provider text.
    return value if re.fullmatch(r'deepseek-(?:chat|reasoner|(?:v4(?:\.1)?-)?(?:flash|pro)(?:-[0-9]{4})?)', value) else '[redacted]'


def _response_metadata(log, envelope, reference, attempt, model, api_key, elapsed, *, deadline=None):
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
        record = {'ref': reference, 'attempt': attempt,
                  'requested_model': _safe_model_name(model, api_key),
                  'returned_model': _safe_model_name(body.get('model'), api_key),
                  'finish_reason': reason, 'usage': usage,
                  'thinking': 'enabled', 'reasoning_effort': 'high', 'max_tokens': MAX_TOKENS,
                  'request_policy_version': deadline.policy_version if deadline is not None else REQUEST_POLICY_VERSION,
                  'request_budget_seconds': deadline.requested_budget_seconds if deadline is not None else REQUEST_BUDGET_SECONDS,
                  'elapsed_seconds': round(max(0.0, float(elapsed)), 3)}
        if deadline is not None:
            record['effective_request_budget_seconds'] = deadline.budget_seconds
        _safe_log(log, 'AI_RESPONSE_META ' + json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(',', ':')))
    except Exception:
        # No metadata field, formatter, or logger failure can alter business flow.
        pass



class FrameworkResponseError(ValueError):
    pass


def _invalid_keep_diagnostics(payload, log, safe_error):
    if not isinstance(payload, dict):
        return
    for name, count, allowed in [('he2_keep', 4, '02468'), ('he3_keep', 6, '0123456789')]:
        value = payload.get(name)
        try:
            normalize_framework_keep(value, count, allowed)
        except ValueError:
            _safe_log(log, f'Framework invalid {name}: type={type(value).__name__}; expected_count={count}')


def _parse_content(content):
    if not isinstance(content, str):
        raise ValueError('Framework response content must be text')
    clean = re.sub(r'```json\s*|\s*```', '', content).strip()
    start, end = clean.find('{'), clean.rfind('}')
    if start < 0 or end < start:
        raise ValueError('Framework response did not contain a JSON object')
    return strict_json_object(clean[start:end + 1])


def request_framework_response(prompt, reference, *, post, api_key, model,
                               load_snapshot, clock, safe_error,
                               log=print, monotonic=time.monotonic, sleep=time.sleep,
                               validator=None, retry_instruction=None, operation_window=None):
    """Three calls at most within a frozen draw-aware 250s total budget.

    Linux real-time alarms bound the network read and JSON decoding even when
    the provider keeps sending whitespace. The outer job watchdog is separate.
    Strict schema and publication checks still run before a response is returned.
    """
    requests_sent = 0
    _require_enabled(clock, requests_sent)
    budget = REQUEST_BUDGET_SECONDS
    if operation_window is not None:
        if operation_window.reference != reference:
            raise PublicationRejected('operation-window-invalid-reference')
        budget = min(budget, operation_window.request_budget())
    deadline = RequestDeadline(reference, clock=clock, monotonic=monotonic,
                               budget_seconds=budget,
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
            raise PublicationRejected('insufficient-time-for-framework-request')
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
                        if operation_window is not None:
                            operation_window.request_budget()
                            if deadline.remaining() < 20:
                                raise PublicationRejected('operation-window-insufficient-budget')
                        response = post('https://api.deepseek.com/chat/completions',
                            json={'model': model, 'messages': messages,
                                  'response_format': {'type': 'json_object'},
                                  'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high',
                                  'max_tokens': MAX_TOKENS},
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
                                   monotonic() - attempt_started,
                                   deadline=deadline if operation_window is not None else None)
            deadline.check()
            generated_at = checked_source_time(reference, load_snapshot, clock)
            if status != 200:
                # Credentials/configuration/account failures cannot improve by
                # repeating the exact request. Do not consume three paid calls.
                if status in (400, 401, 402, 403, 404, 405, 410, 422):
                    raise FrameworkResponseError(f'Nonretryable framework provider HTTP {status}')
                raise OSError(f'Framework provider HTTP {status}')
            if envelope_error is not None:
                raise envelope_error
            validation_failure = True
            finish_reason = envelope['choices'][0].get('finish_reason')
            truncated = finish_reason == 'length'
            if finish_reason != 'stop':
                raise ValueError('Provider response did not finish normally')
            content = envelope['choices'][0]['message']['content']
            payload = _parse_content(content)
            validated = (validate_framework_response if validator is None else validator)(payload)
            _safe_log(log, 'Framework keep normalized: '
                f'he2_keep input_type={type(payload.get("he2_keep")).__name__} normalized_count={len(validated["he2_keep"])}; '
                f'he3_keep input_type={type(payload.get("he3_keep")).__name__} normalized_count={len(validated["he3_keep"])}.')
            _safe_log(log, f'Framework response validated; ref={reference}; attempt={attempt}; publication pending.')
            deadline.check()
            generated_at = checked_source_time(reference, load_snapshot, clock)
            return validated, generated_at
        except AICallsDisabled as error:
            error.requests_sent = requests_sent
            raise
        except (PublicationRejected, FrameworkResponseError):
            raise
        except Exception as error:
            final_error = type(error).__name__
            _invalid_keep_diagnostics(payload, log, safe_error)
            # Exception type is enough for non-schema failures. Do not print
            # raw provider payloads, authorization headers or the model reason.
            _safe_log(log, f'Framework attempt {attempt}/3 rejected; error_type={final_error}; not published.')
            if attempt == 3:
                break
            _require_enabled(clock, requests_sent)
            checked_source_time(reference, load_snapshot, clock)
            if operation_window is not None:
                operation_window.request_budget()
            if deadline.remaining() < 22.0:
                raise PublicationRejected('framework-retry-budget-exhausted')
            if validation_failure:
                retry_prefix = ('上次响应因输出额度耗尽被截断。请停止重复枚举和反复推导，''在有限时间内完成一次核查并返回完整JSON；不得为了凑答案补默认号码。' if truncated else '')
                messages = [{'role': 'user', 'content': prompt}, {'role': 'user', 'content': retry_prefix +
                    (retry_instruction if retry_instruction is not None else
                    '上次响应未通过校验，请重新给出完整JSON。he2_keep必须恰好4个不同ASCII数字且均来自02468；'
                    'he3_keep必须恰好6个不同ASCII数字且均来自0123456789。推荐使用字符串并保留前导0；'
                    'pos_kills的q/b/s/g均必须恰好2个不同的0至9整数。不得输出布尔值、小数、缺失字段、补位或默认号码。') +
                    strategy_retry_feedback(error)}]
            wait_before_retry(reference, attempt, deadline, clock=clock, sleep=sleep, log=log,
                              response=response, status=status, validation_failure=validation_failure)
    raise FrameworkResponseError(f'Framework inference failed after 3 attempts: {final_error}; not published')
