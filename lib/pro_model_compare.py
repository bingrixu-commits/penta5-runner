"""Prospective Pro comparison using the production framework's exact saved input.

No primary input, prediction or statistic is rewritten. A paid attempt is recorded
before inference and cannot be repeated by a later job after failure or a crash.
"""
from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
from html import escape
from pathlib import Path
import json
import os
import re
import time

from ai_model_context import MAX_INPUT_BYTES, VERSION as INPUT_VERSION, canonical
from ai_prediction_guard import (PublicationRejected, find_seal, publication_rejection,
                                 publish_saved, read_stats, store_prepared)
from ai_verified_stats import atomic_json, next_period, publication_evidence_error
from framework_ai_client import FrameworkResponseError, request_framework_response
from ai_call_control import AICallsDisabled, require_ai_enabled
from framework_hit_stats import _publication
from stats_history import (beijing_now, business_start, load_snapshot, parse_period,
                           parse_time, period_slot, valid_draw_record,
                           validate_conflicting_periods)

ROOT = Path('/root/penta5-runner')
PRO_MODEL = 'deepseek-v4-pro'
MARKER = 'R1_PRO_COMPARE'
VERSION = 1
HASH = re.compile(r'[0-9a-f]{64}')
PRO_FAMILY = re.compile(r'deepseek-(?:v4(?:\.1)?-)?pro(?:-[0-9]{4})?')
FLASH_FAMILY = re.compile(r'deepseek-(?:v4(?:\.1)?-)?flash(?:-[0-9]{4})?')


def _aware(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        from datetime import datetime
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parse_time(value) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _hashes(seal, reference):
    audit = seal.get('model_context_audit')
    expected = f'ai_model_inputs/framework-{reference}.json'
    if not isinstance(audit, dict) or audit.get('input_archive') != expected:
        raise ValueError('missing-comparison-input')
    values = tuple(audit.get(k) for k in ('input_archive_sha256', 'initial_request_prompt_sha256'))
    if any(not isinstance(v, str) or not HASH.fullmatch(v) for v in values):
        raise ValueError('invalid-comparison-input-hash')
    return values


def validate_shared_input(encoded, reference, snapshot, now, flash_seal=None):
    """Validate the original archive without normalizing or constructing a prompt."""
    if not isinstance(encoded, bytes) or len(encoded) > MAX_INPUT_BYTES:
        raise ValueError('invalid-input-size')
    payload = json.loads(encoded)
    if not isinstance(payload, dict):
        raise ValueError('invalid-input-object')
    parsed = parse_period(reference)
    target = next_period(reference)
    latest = snapshot.get('latest') or {}
    actual = valid_draw_record(latest.get('period'), latest.get('numbers'), latest.get('opentime'), now)
    rejection = publication_rejection(reference, snapshot, now)
    if not parsed or reference != parsed[0] or rejection or actual is None:
        raise PublicationRejected('invalid-current-comparison-target')
    observed = _aware(payload.get('observed_at'))
    opened = parse_time(actual['opentime'])
    prompt, audit = payload.get('initial_prompt'), payload.get('audit')
    if (payload.get('version') != INPUT_VERSION or payload.get('model_kind') != 'framework'
            or payload.get('reference_period') != reference or payload.get('target_period') != target
            or observed is None or not opened <= observed <= now < period_slot(target)
            or not isinstance(prompt, str) or not isinstance(audit, dict)
            or audit.get('version') != INPUT_VERSION
            or audit.get('context_applied') not in (True, False)
            or audit.get('initial_request_prompt_sha256') != sha256(prompt.encode('utf-8')).hexdigest()
            or audit.get('context_sha256') != sha256(canonical(audit.get('body')).encode('utf-8')).hexdigest()):
        raise ValueError('invalid-original-input-evidence')
    # The primary framework puts its complete ASCII four-ball history on line 2.
    # Reject unknown/expanded formats rather than silently rebuilding its input.
    lines = prompt.splitlines()
    if len(lines) < 2 or not re.fullmatch(r'[0-9]{4}(?:, [0-9]{4}){0,49}', lines[1]):
        raise ValueError('invalid-four-ball-history-window')
    history = lines[1].split(', ')
    if history[-1] != ''.join(map(str, actual['numbers'])) or reference not in lines[0]:
        raise ValueError('input-reference-history-mismatch')
    recent = snapshot.get('recent')
    if not isinstance(recent, (list, tuple)) or not 1 <= len(recent) <= 50:
        raise ValueError('missing-comparison-history-evidence')
    expected_history = []
    for item in recent:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError('invalid-comparison-history-evidence')
        _, numbers = item
        if (not isinstance(numbers, (list, tuple)) or len(numbers) != 4
                or any(type(d) is not int or not 0 <= d <= 9 for d in numbers)):
            raise ValueError('invalid-comparison-history-digits')
        expected_history.append(''.join(map(str, numbers)))
    if history != expected_history:
        raise ValueError('original-input-history-changed')
    marker = '\n\n【后台结构化参考与本模型真实已封存战绩】\n'
    if audit.get('context_applied') is True:
        if marker not in prompt:
            raise ValueError('missing-original-context-boundary')
        base_prompt = prompt.split(marker, 1)[0]
    else:
        base_prompt = prompt
    if audit.get('base_prompt_sha256') != sha256(base_prompt.encode('utf-8')).hexdigest():
        raise ValueError('invalid-original-base-prompt-hash')
    result = deepcopy(audit)
    result.update(input_archive=f'ai_model_inputs/framework-{reference}.json',
                  input_archive_sha256=sha256(encoded).hexdigest())
    if flash_seal is not None and _hashes(flash_seal, reference) != _hashes({'model_context_audit': result}, reference):
        raise ValueError('primary-input-archive-mismatch')
    return prompt, result


def wait_for_shared_input(reference, *, root, loader, clock, sleep, monotonic, wait_seconds=15.0):
    """Wait only for the first framework archive; no new primary input is made."""
    deadline = monotonic() + min(15.0, max(0.0, wait_seconds))
    path = Path(root) / 'data' / 'ai_model_inputs' / f'framework-{reference}.json'
    while True:
        snapshot, now = loader(persist=False), clock()
        rejection = publication_rejection(reference, snapshot, now)
        if rejection:
            raise PublicationRejected(rejection)
        try:
            with path.open('rb') as handle:
                encoded = handle.read(MAX_INPUT_BYTES + 1)
        except FileNotFoundError:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise PublicationRejected('original-framework-input-unavailable')
            sleep(min(0.5, remaining))
            continue
        flash = read_stats(Path(root) / 'data' / 'r1_framework_seals.json')
        return validate_shared_input(encoded, reference, snapshot, now, find_seal(flash, reference)), snapshot


def _valid_model(seal):
    metadata = seal.get('model_metadata')
    return (seal.get('comparison_version') == VERSION and isinstance(metadata, dict)
            and metadata.get('requested_model') == PRO_MODEL
            and metadata.get('ref') == seal.get('ref_period')
            and type(metadata.get('attempt')) is int and 1 <= metadata['attempt'] <= 3
            and isinstance(metadata.get('returned_model'), str)
            and PRO_FAMILY.fullmatch(metadata['returned_model']) is not None
            and metadata.get('finish_reason') == 'stop')


def _valid_flash_model(seal):
    metadata = seal.get('model_metadata')
    return (isinstance(metadata, dict) and metadata.get('ref') == seal.get('ref_period')
            and type(metadata.get('attempt')) is int and 1 <= metadata['attempt'] <= 3
            and isinstance(metadata.get('requested_model'), str)
            and FLASH_FAMILY.fullmatch(metadata['requested_model']) is not None
            and isinstance(metadata.get('returned_model'), str)
            and FLASH_FAMILY.fullmatch(metadata['returned_model']) is not None
            and metadata.get('finish_reason') == 'stop')


def summarize_comparison(flash_seals, pro_seals, records, now, *, conflicting_periods=()):
    """Pure current-business-day paired outcomes; never import aggregate rates."""
    if not getattr(now, 'tzinfo', None):
        raise ValueError('comparison-clock-must-be-aware')
    start = business_start(now)
    if not isinstance(pro_seals, dict):
        raise ValueError('invalid-pro-comparison-ledger')
    pro_publications = pro_seals.get('prediction_seals', {})
    pro_attempts = pro_seals.get('request_attempts', {})
    if not isinstance(pro_publications, dict) or not isinstance(pro_attempts, dict):
        raise ValueError('invalid-pro-comparison-index')
    cohort = {ref for ref in set(pro_publications) | set(pro_attempts)
              if isinstance(ref, str) and parse_period(ref) and ref == parse_period(ref)[0]
              and business_start(period_slot(next_period(ref))) == start}
    cohort_targets = {next_period(ref) for ref in cohort}
    if not isinstance(records, (list, tuple)):
        raise ValueError('comparison-records-must-be-sequence')
    actuals, conflicts = {}, validate_conflicting_periods(conflicting_periods)
    for raw in records:
        if not isinstance(raw, dict):
            continue
        record = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if record:
            old = actuals.get(record['period'])
            if old and old['numbers'] != record['numbers']:
                conflicts.add(record['period'])
            actuals[record['period']] = record
    rejected, sides = set(), []
    for side, ledger in (('flash', flash_seals), ('pro', pro_seals)):
        if not isinstance(ledger, dict) or not isinstance(ledger.get('prediction_seals', {}), dict):
            raise ValueError('invalid-comparison-ledger')
        checked = {}
        for reference, seal in ledger.get('prediction_seals', {}).items():
            if reference not in cohort:
                continue
            try:
                raw_target = seal.get('target_period') if isinstance(seal, dict) else None
                if parse_period(raw_target) and business_start(period_slot(raw_target)) != start:
                    continue
                proof = _publication(seal, now)
                target = proof['target_period']
                if reference != proof['ref_period']:
                    raise ValueError('incorrect-comparison-map-reference')
                if business_start(period_slot(target)) != start:
                    continue
                hashes = _hashes(seal, reference)
                if side == 'pro' and not _valid_model(seal):
                    raise ValueError('unverified-pro-returned-model')
                if side == 'flash' and not _valid_flash_model(seal):
                    raise ValueError('unverified-flash-returned-model')
                if target in checked:
                    raise ValueError('duplicate-comparison-target')
                checked[target] = (seal, proof, hashes)
            except (ValueError, TypeError, KeyError, OverflowError):
                # Invalid historical entries cannot provide comparison evidence.
                rejected.add((side, str(reference)))
        sides.append(checked)
    flash, pro = sides
    metrics = {key: {'total': 0, 'flash_hits': 0, 'pro_hits': 0,
                     'flash_percent': None, 'pro_percent': None}
               for key in ('q_kill1', 'b_kill1', 'all_positions')}
    matched, pending, unpaired = [], set(), cohort_targets - (set(flash) & set(pro))
    for target in sorted(set(flash) & set(pro)):
        _, fproof, fhash = flash[target]
        _, pproof, phash = pro[target]
        if fproof['ref_period'] != pproof['ref_period'] or fhash != phash:
            unpaired.add(target)
            rejected.add(('pair', target))
            continue
        actual = actuals.get(target)
        if target in conflicts:
            rejected.add(('draw', target))
            continue
        if not actual:
            pending.add(target)
            continue
        if not start <= parse_time(actual['opentime']) <= now:
            continue
        if publication_evidence_error(fproof, actual) or publication_evidence_error(pproof, actual):
            rejected.add(('publication', target))
            continue
        digits = actual['numbers']
        fresponse, presponse = fproof['response'], pproof['response']
        fresults = (digits[0] != fresponse['q_kill1'], digits[1] != fresponse['b_kill1'])
        presults = (digits[0] != presponse['q_kill1'], digits[1] != presponse['b_kill1'])
        for key, fhit, phit in zip(metrics, (*fresults, all(fresults)), (*presults, all(presults))):
            metrics[key]['total'] += 1
            metrics[key]['flash_hits'] += int(fhit)
            metrics[key]['pro_hits'] += int(phit)
        matched.append(target)
    for metric in metrics.values():
        if metric['total']:
            metric['flash_percent'] = round(100 * metric['flash_hits'] / metric['total'], 2)
            metric['pro_percent'] = round(100 * metric['pro_hits'] / metric['total'], 2)
    return {'version': VERSION, 'business_start': start.isoformat(), 'biz_date': start.strftime('%Y-%m-%d'),
            'matched_count': len(matched), 'matched_targets': matched, 'metrics': metrics,
            'pending_count': len(pending), 'unpaired_count': len(unpaired),
            'rejected_count': len(rejected), 'latest_settled_period': matched[-1] if matched else None}


def _loader(root):
    return lambda **kw: load_snapshot(state_path=Path(root) / 'data' / 'state.json',
                                      cache_path=Path(root) / 'data' / 'stats_draw_history.json', **kw)


def refresh_comparison_stats(snapshot=None, root=ROOT):
    root = Path(root)
    snapshot = snapshot if snapshot is not None else _loader(root)(persist=False)
    summary = summarize_comparison(read_stats(root / 'data' / 'r1_framework_seals.json'),
                                   read_stats(root / 'data' / 'pro_compare_seals.json'),
                                   snapshot['records'], snapshot['now'],
                                   conflicting_periods=snapshot.get('conflicting_periods', ()))
    atomic_json(root / 'data' / 'pro_compare_stats.json', summary)
    return summary


def _status(root, status, reference, clock, error_code=None, audit=None):
    body = {'version': VERSION, 'updated_at': clock().isoformat(), 'status': status,
            'reference_period': reference, 'target_period': next_period(reference),
            'requested_model': PRO_MODEL, 'error_code': error_code}
    if audit:
        body.update({key: audit[key] for key in ('input_archive_sha256', 'initial_request_prompt_sha256')})
    atomic_json(Path(root) / 'data' / 'pro_compare_status.json', body)


def _card(reference, target, response):
    return (f'<!-- {MARKER}_START -->\n'
            '<div style="margin:20px auto;max-width:1000px;padding:16px;background:#fff;border:1px solid #64748b;border-radius:8px">'
            '<b>DeepSeek V4 Pro 独立影子对照</b>'
            f'<div>参考期：{escape(reference)}；预测目标期：{escape(target)}</div>'
            f'<div>千位排除：{response["q_kill1"]}；百位排除：{response["b_kill1"]}</div>'
            '<div>复用生产框架同一封存输入；仅作对照，保留原 Flash 预测。历史成绩须等待共同目标真实开奖后核验。</div>'
            f'</div>\n<!-- {MARKER}_END -->')


def run_once(*, root=ROOT, post=None, api_key=None, loader=None, clock=beijing_now,
             monotonic=time.monotonic, sleep=time.sleep, writer=None, log=print):
    """One caller-owned locked prospective cycle; injectable network for tests."""
    root = Path(root)
    try:
        require_ai_enabled(root=root, clock=clock)
    except AICallsDisabled:
        return {'status': 'paused', 'published': False, 'ref_period': None}
    loader = loader or _loader(root)
    snapshot = loader(persist=False)
    reference = (snapshot.get('latest') or {}).get('period')
    if not isinstance(reference, str) or not parse_period(reference):
        raise PublicationRejected('no-current-comparison-reference')
    path = root / 'data' / 'pro_compare_seals.json'
    ledger = read_stats(path)
    existing = find_seal(ledger, reference)
    if existing is not None:
        if (not _valid_model(existing) or existing.get('target_period') != next_period(reference)
                or existing.get('ref_period') != reference):
            _status(root, 'rejected', reference, clock, 'invalid-saved-pro-evidence')
            return {'status': 'rejected', 'published': False, 'ref_period': reference}
        published = publish_saved(ledger, path, reference, MARKER, loader, writer=writer, clock=clock)
        state = 'preserved' if published else 'rejected'
        _status(root, state, reference, clock, None if published else 'publication-rejected')
        return {'status': state, 'published': published, 'ref_period': reference}
    attempts = ledger.setdefault('request_attempts', {})
    if not isinstance(attempts, dict):
        raise ValueError('invalid-comparison-attempt-index')
    if reference in attempts:
        _status(root, 'rejected', reference, clock, 'inference-already-attempted')
        return {'status': 'rejected', 'published': False, 'ref_period': reference}
    _status(root, 'waiting', reference, clock)
    owned_attempt, response_received = False, False
    try:
        # The wait helper also serves a pure statistical control. Restrict the
        # manual API gate to this paid caller rather than disabling statistics.
        def guarded_loader(**kwargs):
            require_ai_enabled(root=root, clock=clock)
            return loader(**kwargs)
        (prompt, audit), snapshot = wait_for_shared_input(reference, root=root, loader=guarded_loader,
                                                        clock=clock, sleep=sleep, monotonic=monotonic)
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
        attempts[reference] = {'started_at': clock().isoformat(),
                               'input_archive_sha256': audit['input_archive_sha256'],
                               'initial_request_prompt_sha256': audit['initial_request_prompt_sha256']}
        atomic_json(path, ledger)  # Durable tombstone before a paid attempt.
        owned_attempt = True
        _status(root, 'requesting', reference, clock, audit=audit)
        metadata, validated_attempts = {}, []
        def safe_log(message):
            if isinstance(message, str) and message.startswith('AI_RESPONSE_META '):
                candidate = json.loads(message[len('AI_RESPONSE_META '):])
                if (isinstance(candidate, dict) and candidate.get('ref') == reference
                        and type(candidate.get('attempt')) is int and 1 <= candidate['attempt'] <= 3):
                    metadata[candidate['attempt']] = candidate
            elif isinstance(message, str):
                accepted = re.fullmatch(r'Framework response validated; ref=([0-9]{11}); attempt=([1-3]); publication pending\.', message)
                if accepted and accepted.group(1) == reference:
                    validated_attempts.append(int(accepted.group(2)))
            try:
                log(message)
            except Exception:
                pass
        response, generated_at = request_framework_response(prompt, reference, post=post,
            api_key=api_key, model=PRO_MODEL, load_snapshot=loader, clock=clock,
            safe_error=lambda error: type(error).__name__, log=safe_log,
            monotonic=monotonic, sleep=sleep)
        model_metadata = metadata.get(validated_attempts[-1], {}) if validated_attempts else {}
        candidate = {'ref_period': reference, 'response': response,
                     'model_context_audit': audit, 'comparison_version': VERSION,
                     'model_metadata': model_metadata}
        if not _valid_model(candidate):
            # Retain only sanitized provider metadata, never a fake Pro result.
            attempts[reference]['model_metadata'] = model_metadata
            attempts[reference]['result'] = 'returned-model-not-pro'
            atomic_json(path, ledger)
            raise PublicationRejected('returned-model-not-pro')
        response_received = True
        require_ai_enabled(root=root, clock=clock)
        publication_snapshot = loader(persist=False)
        store_prepared(ledger, path, publication_snapshot, candidate,
                       _card(reference, next_period(reference), response),
                       generated_at=generated_at, enqueue=False)
        published = publish_saved(ledger, path, reference, MARKER, loader, writer=writer, clock=clock)
        _status(root, 'published' if published else 'rejected', reference, clock,
                None if published else 'publication-rejected', audit)
        return {'status': 'published' if published else 'rejected',
                'published': published, 'ref_period': reference}
    except AICallsDisabled as error:
        if (owned_attempt and getattr(error, 'requests_sent', 1) == 0
                and not response_received and find_seal(ledger, reference) is None):
            attempts.pop(reference, None)
            atomic_json(path, ledger)
        _status(root, 'paused', reference, clock, 'manual-api-stopped')
        return {'status': 'paused', 'published': False, 'ref_period': reference}
    except Exception as error:
        code = ('provider-insufficient-balance' if isinstance(error, FrameworkResponseError)
                and str(error) == 'Nonretryable framework provider HTTP 402' else
                'returned-model-not-pro' if isinstance(error, PublicationRejected)
                and str(error) == 'returned-model-not-pro' else
                'publication-rejected' if isinstance(error, PublicationRejected) else
                'inference-or-evidence-error')
        _status(root, 'rejected' if isinstance(error, PublicationRejected) else 'failed',
                reference, clock, code)
        raise


def main():
    """Cron entry: an independent job lock never blocks the original Flash jobs."""
    import fcntl
    data = ROOT / 'data'
    with (data / 'pro_compare_job.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            result = run_once()
            return_code = 0 if result['status'] in ('published', 'preserved', 'paused') else 1
        except Exception as error:
            print('Pro comparison not published; error_type=' + type(error).__name__)
            return_code = 1
        try:
            from pro_compare_view import refresh_comparison_panel
            refresh_comparison_panel(root=ROOT)
        except Exception as error:
            print('Pro comparison view refresh deferred; error_type=' + type(error).__name__)
        return return_code


if __name__ == '__main__':
    raise SystemExit(main())
