"""Bounded, read-only operational evidence for the nine active AI paths.

This module does not import model runners, providers, publication writers or
secrets. Missing clock evidence stays None. It never creates predictions,
replays jobs or modifies source ledgers, control state or the panel.
"""
from datetime import datetime, timedelta
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re

from stats_history import (TZ, beijing_now, business_start, collect_records,
                           parse_period, period_slot)
from operational_availability import load_delivery, observation, seal_identity, index_delivery

ROOT = Path('/root/penta5-runner')
VERSION, REVISION = 1, 'runtime-observability-v1'
MAX_JSON_BYTES = 128 * 1024 * 1024
MAX_PANEL_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 800
STAGE_KEYS = ('source_opened_at', 'input_frozen_at', 'request_started_at',
              'response_completed_at', 'published_at')
DURATION_KEYS = ('source_to_input', 'input_to_request', 'request_to_response',
                 'response_to_publish', 'source_to_publish', 'provider_elapsed')
MODELS = {
    'killer': ('Flash 杀码', 'r1_stats.json', 'ai_job_status.json', 'ai_job_killer.log', 'R1_KILLER'),
    'framework': ('Flash 框架', 'r1_framework_seals.json', 'ai_job_status.json', 'ai_job_framework.log', 'R1_FRAMEWORK'),
    'selected6': ('Pro 六项', 'selected_six_seals.json', 'selected_six_status.json', 'selected_six_cron.log', 'SELECTED_SIX_INDEPENDENT'),
    'he3_refine': ('三合五尾', 'he3_refine_seals.json', 'he3_refine_status.json', 'he3_refine_cron.log', 'HE3_REFINE'),
    'cold': ('Pro 冷码框架', None, 'cold_framework_status.json', 'cold_framework_cron.log', 'COLD_FRAMEWORK'),
    'cold_fixed': ('Pro 30期固定冷码', None, 'cold_fixed_framework_status.json', 'cold_fixed_framework_cron.log', 'COLD_FIXED_FRAMEWORK'),
    'cold50': ('Pro 50期冷码', None, 'cold50_framework_status.json', 'cold50_framework_cron.log', 'COLD50_FRAMEWORK'),
    'cold50_fixed': ('Pro 50期固定冷码', None, 'cold50_fixed_framework_status.json', 'cold50_fixed_framework_cron.log', 'COLD50_FIXED_FRAMEWORK'),
}
COLD_FAMILIES = {
    'cold': ('cold_framework', ('cold-framework-v1-30-pos3-tail3',
                               'cold-framework-v2-30-pos3-tail2',
                               'cold-framework-v3-30-pos3-tail2-he2x4-he3x5')),
    'cold_fixed': ('cold_fixed_framework', ('cold-fixed-framework-v1-30-fixed-qbs-all-qbs-he2x4-he3x5',)),
    'cold50': ('cold50_framework', ('cold50-framework-v3-50-pos3-tail2-he2x4-he3x5',)),
    'cold50_fixed': ('cold50_fixed_framework', ('cold50-fixed-framework-v1-50-fixed-qbs-all-qbs-he2x4-he3x5',
                                             'cold50-fixed-framework-v2-50-fixed-qbg-qbg-bsg-he2x4-he3x5')),
}
FIXED_COLD_SPECS = {
    'cold-fixed-framework-v1-30-fixed-qbs-all-qbs-he2x4-he3x5': (frozenset(('q','b','s')),frozenset(('all','qbs'))),
    'cold50-fixed-framework-v1-50-fixed-qbs-all-qbs-he2x4-he3x5': (frozenset(('q','b','s')),frozenset(('all','qbs'))),
    'cold50-fixed-framework-v2-50-fixed-qbg-qbg-bsg-he2x4-he3x5': (frozenset(('q','b','g')),frozenset(('qbg','bsg'))),
}
STATUS_VALUES = frozenset(('current', 'pending', 'await_rule_start', 'manual_off', 'historical', 'late', 'missing', 'failed', 'unknown'))


def _stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return moment.astimezone(TZ) if moment.tzinfo is not None and moment.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _now(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('runtime-invalid-clock')
    return value.astimezone(TZ)


def _period(value):
    parsed = parse_period(value) if isinstance(value, str) else None
    return value if parsed is not None and parsed[0] == value else None


def _identity(slot):
    if slot.hour == 0 and slot.minute == 0:
        return (slot - timedelta(days=1)).strftime('%Y%m%d') + '288'
    return slot.strftime('%Y%m%d') + f'{(slot.hour * 60 + slot.minute) // 5:03d}'


def expected_reference(now):
    now = _now(now)
    return _identity(now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5))


def _target(reference):
    return _identity(period_slot(reference) + timedelta(minutes=5)) if _period(reference) else None


def _reference(target):
    return _identity(period_slot(target) - timedelta(minutes=5)) if _period(target) else None


def _finite(value, *, maximum=86400 * 10):
    return float(value) if type(value) in (int, float) and math.isfinite(value) and 0 <= value <= maximum else None


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('runtime-duplicate-json-key')
        result[key] = value
    return result


def _read(path, limit=MAX_JSON_BYTES, *, text=False):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError('runtime-source-too-large')
    if text:
        return data.decode('utf-8')
    return json.loads(data, object_pairs_hook=_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError('runtime-invalid-json-number')))


def _control(document, now):
    """Validate the committed document, without opening/creating its lock file."""
    if document is None:
        return {'enabled': False, 'generation': 0, 'mode': 'manual',
                'introduced_at': None, 'transitions': [], 'history_valid': True}
    required = {'version', 'mode', 'enabled', 'generation', 'updated_at',
                'introduced_at', 'transitions', 'checksum'}
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError('runtime-invalid-control')
    encoded = json.dumps({key: value for key, value in document.items() if key != 'checksum'},
                         ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if (type(document['version']) is not int or document['version'] != 1 or document['mode'] != 'manual'
            or type(document['enabled']) is not bool or type(document['generation']) is not int
            or document['generation'] < 1 or not isinstance(document['transitions'], list)
            or len(document['transitions']) != document['generation']
            or document['checksum'] != sha256(encoded).hexdigest()):
        raise ValueError('runtime-invalid-control')
    introduced, updated = _stamp(document['introduced_at']), _stamp(document['updated_at'])
    if introduced is None or updated is None or not introduced <= updated <= now:
        raise ValueError('runtime-invalid-control-time')
    previous = introduced
    for generation, row in enumerate(document['transitions'], 1):
        if (not isinstance(row, dict) or set(row) != {'generation', 'enabled', 'at'}
                or type(row['generation']) is not int or row['generation'] != generation
                or type(row['enabled']) is not bool):
            raise ValueError('runtime-invalid-control-history')
        moment = _stamp(row['at'])
        if moment is None or not previous <= moment <= now:
            raise ValueError('runtime-invalid-control-history')
        previous = moment
    if (document['transitions'][0]['at'] != document['introduced_at']
            or document['transitions'][-1]['at'] != document['updated_at']
            or document['transitions'][-1]['enabled'] != document['enabled']):
        raise ValueError('runtime-invalid-control-history')
    return {key: document[key] for key in ('enabled', 'generation', 'mode', 'introduced_at', 'transitions')} | {'history_valid': True}


def _eligible(control, reference, now, evidenced=False):
    if not control['history_valid']:
        return None
    start = period_slot(reference)
    close = start + timedelta(minutes=5, seconds=-70)
    end = min(now, close)
    introduced = _stamp(control['introduced_at'])
    if introduced is None:
        return True if evidenced else False
    rows = control['transitions']
    state_at_end = False
    for row in rows:
        if _stamp(row['at']) <= end:
            state_at_end = row['enabled']
    for index, row in enumerate(rows):
        left = _stamp(row['at'])
        right = _stamp(rows[index + 1]['at']) if index + 1 < len(rows) else now
        if row['enabled'] and max(start, left) < min(end, right):
            # A deliberate stop before any request activity cancels the round;
            # later starts do not retroactively turn that cancellation into a
            # missing period. Already sent/uncertain reserved requests remain.
            return True if evidenced or state_at_end else False
    # Pre-introduction activity is retained only when independently evidenced;
    # the absence of a pre-introduction prediction cannot prove a missed job.
    if start < introduced:
        return True if evidenced else None
    return False


def _error_kind(value):
    text = value.lower() if isinstance(value, str) else ''
    if text in ('selected-seven-operation-window-passed', 'selected-seven-insufficient-operation-budget',
                'operation-window-closed','operation-window-insufficient-budget',
                'selected-six-operation-window-passed','selected-six-insufficient-operation-budget'):
        return 'operation-window'
    if 'strategy' in text:
        return 'strategy-rejected'
    if 'timeout' in text or 'deadline-exhausted' in text or 'budget-exhausted' in text:
        return 'provider-timeout'
    if 'balance' in text or 'payment' in text or '402' in text:
        return 'provider-payment'
    if 'source' in text or 'history' in text or 'reference' in text:
        return 'source-not-ready'
    if 'manual' in text or 'api-stopped' in text:
        return 'manual-off'
    return 'job-failed' if text else None


def _compact_seal(raw, reference, model=None):
    if not isinstance(raw, dict) or _period(reference) is None:
        return None
    if raw.get('ref_period', raw.get('reference_period')) != reference or raw.get('target_period') != _target(reference):
        return None
    metadata = raw.get('model_metadata', raw.get('selection_metadata'))
    metadata = metadata if isinstance(metadata, dict) else {}
    audit = raw.get('model_context_audit')
    audit = audit if isinstance(audit, dict) else {}
    body = audit.get('body')
    body = body if isinstance(body, dict) else {}
    snapshot = raw.get('input_snapshot')
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    history = raw.get('history')
    last = history[-1] if isinstance(history, list) and history and isinstance(history[-1], dict) else {}
    return {'reference_period': reference, 'target_period': _target(reference),
            'source_opened_at': raw.get('reference_open_time', last.get('opentime')),
            'input_frozen_at': snapshot.get('now', body.get('as_of')),
            # requested_at is captured before the HTTP boundary. Only an
            # AI_HTTP_PHASE start event can establish the actual POST time.
            'request_started_at': None,
            'request_registered_at': metadata.get('requested_at'),
            'response_completed_at': metadata.get('completed_at', raw.get('generated_at')),
            'published_at': raw.get('published_at') if raw.get('publication_state') == 'published' else None,
            'publication_state': raw.get('publication_state'),
            'provider_elapsed': _finite(metadata.get('elapsed_seconds')),
            'accepted_attempt': metadata.get('attempt') if type(metadata.get('attempt')) is int and 1 <= metadata['attempt'] <= 3 else None,
            'failure_code': None, 'registered': False, 'card_html': raw.get('card_html') if isinstance(raw.get('card_html'), str) else None,
            'delivery_identity': seal_identity(model,raw) if model is not None else None}


def _model_evidence(document, model=None):
    if not isinstance(document, dict):
        raise ValueError('runtime-invalid-model-ledger')
    seals, attempts = document.get('prediction_seals', {}), document.get('request_attempts', {})
    if not isinstance(seals, dict) or not isinstance(attempts, dict):
        raise ValueError('runtime-invalid-model-index')
    references = sorted({ref for ref in (*seals, *attempts) if _period(ref)})[-MAX_RECORDS:]
    result = {}
    for reference in references:
        seal = _compact_seal(seals.get(reference), reference, model=model)
        attempt = attempts.get(reference)
        if seal is None:
            seal = {'reference_period': reference, 'target_period': _target(reference)}
        if isinstance(attempt, dict):
            seal['registered'] = True
            seal['request_registered_at'] = attempt.get('started_at')
            failure = attempt.get('failure')
            if isinstance(failure, dict):
                seal['failure_code'] = _error_kind(failure.get('code'))
            elif isinstance(attempt.get('result'), str):
                seal['failure_code'] = _error_kind(attempt['result'])
        result[reference] = seal
    return result


def _cold_evidence(documents, attempts, model='cold'):
    if model not in COLD_FAMILIES:
        raise ValueError('runtime-invalid-cold-family')
    allowed=COLD_FAMILIES[model][1]
    result = {}
    for document in documents:
        if not isinstance(document, dict) or not isinstance(document.get('predictions'), dict):
            raise ValueError('runtime-invalid-cold-ledger')
        if model in ('cold_fixed','cold50','cold50_fixed') and (type(document.get('version')) is not int or document['version']!=1
                                or document.get('revision') not in allowed):
            raise ValueError('runtime-invalid-'+model+'-family')
        body = {key: value for key, value in document.items() if key != 'checksum'}
        if document.get('checksum') != sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest():
            raise ValueError('runtime-invalid-cold-checksum')
        for raw in document['predictions'].values():
            if model in ('cold_fixed','cold50','cold50_fixed') and (not isinstance(raw,dict) or raw.get('revision') not in allowed):
                raise ValueError('runtime-invalid-'+model+'-family')
            if model in ('cold_fixed','cold50_fixed'):
                positions,tails=FIXED_COLD_SPECS[raw['revision']]
                for field,keys in (('selected_positions',positions),('selected_tails',tails)):
                    values=raw.get(field)
                    if (not isinstance(values,dict) or set(values)!=keys
                            or any(type(value) is not int or not 0<=value<=9 for value in values.values())):
                        raise ValueError('runtime-invalid-cold-fixed-selection')
            reference = raw.get('reference_period') if isinstance(raw, dict) else None
            seal = _compact_seal(raw, reference, model=model)
            if seal is not None:
                result[reference] = seal
    for document in attempts:
        if not isinstance(document, dict) or not isinstance(document.get('attempts'), dict):
            raise ValueError('runtime-invalid-cold-attempts')
        if model in ('cold_fixed','cold50','cold50_fixed') and (type(document.get('version')) is not int or document['version']!=1
                                or document.get('revision') not in allowed):
            raise ValueError('runtime-invalid-'+model+'-family')
        body = {key: value for key, value in document.items() if key != 'checksum'}
        if document.get('checksum') != sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest():
            raise ValueError('runtime-invalid-cold-checksum')
        for reference, attempt in document['attempts'].items():
            if not _period(reference) or not isinstance(attempt, dict):
                continue
            if model in ('cold_fixed','cold50','cold50_fixed') and (attempt.get('revision') not in allowed
                    or attempt.get('reference_period')!=reference or attempt.get('target_period')!=_target(reference)):
                raise ValueError('runtime-invalid-'+model+'-attempt')
            record = result.setdefault(reference, {'reference_period': reference, 'target_period': _target(reference)})
            record['registered'], record['request_registered_at'] = True, attempt.get('started_at')
            metadata = attempt.get('metadata')
            if isinstance(metadata, dict):
                if _stamp(metadata.get('requested_at')):
                    record['request_registered_at'] = metadata['requested_at']
                record['response_completed_at'] = metadata.get('completed_at')
            if attempt.get('state') in ('failed', 'rejected'):
                record['failure_code'] = _error_kind(attempt.get('error_code')) or 'job-failed'
    return {key: result[key] for key in sorted(result)[-MAX_RECORDS:]}


def parse_log(text):
    """Keep only fixed numeric/ref diagnostics, never provider bodies/reasons."""
    result, active, attempt = {}, None, None
    def row(reference):
        return result.setdefault(reference, {'strategy_rejected': set(), 'api_timeout': set(),
                                              'attempts': set(), 'started': False, 'finished': False,
                                              'http': {}})
    for line in text.splitlines():
        start = re.search(r'^[A-Z_]*(?:FETCH_START|READY) ref=([0-9]{11})\b', line)
        if start and _period(start[1]):
            active, attempt = start[1], None
            row(active)['started'] = True
        if line.startswith('AI_HTTP_PHASE '):
            try:
                value = json.loads(line[len('AI_HTTP_PHASE '):])
            except (ValueError, TypeError):
                continue
            reference = _period(value.get('ref')) if isinstance(value, dict) else None
            number = value.get('attempt') if isinstance(value, dict) else None
            phase = value.get('phase') if isinstance(value, dict) else None
            moment = _stamp(value.get('at')) if isinstance(value, dict) else None
            if reference and type(number) is int and 1 <= number <= 3 and phase in ('start', 'complete') and moment:
                active, attempt = reference, number
                event = row(active)
                event['attempts'].add(number)
                event['started'] = True
                points = event['http'].setdefault(number, {})
                # Duplicate or conflicting stage events are not averaged or
                # resolved by choosing an attractive duration.
                stamp = moment.isoformat()
                if phase in points and points[phase] != stamp:
                    points['conflict'] = True
                points[phase] = stamp
        if line.startswith(('AI_RESPONSE_META ', 'AI_RETRY_META ')):
            try:
                value = json.loads(line.split(' ', 1)[1])
            except (ValueError, TypeError):
                continue
            reference = _period(value.get('ref')) if isinstance(value, dict) else None
            number = value.get('attempt') if isinstance(value, dict) else None
            if reference and type(number) is int and 1 <= number <= 3:
                active, attempt = reference, number
                row(active)['attempts'].add(number)
        rejected = re.fullmatch(r'(?:Killer|Framework) attempt ([1-3])/3 rejected; error_type=([A-Za-z]+); not published\.', line)
        if rejected and active:
            number, kind = int(rejected[1]), rejected[2]
            row(active)['attempts'].add(number)
            if kind == 'StrategyValidationError':
                row(active)['strategy_rejected'].add(number)
            if kind in ('Timeout', 'ReadTimeout', 'ConnectTimeout'):
                row(active)['api_timeout'].add(number)
        finished = re.search(r'\bref=([0-9]{11})\b.*\bpublished=(?:True|False)\b', line)
        if finished and _period(finished[1]):
            row(finished[1])['finished'] = True
        failed = re.search(r'^[A-Z_]*FAILURE ref=([0-9]{11})\b', line)
        if failed and _period(failed[1]):
            row(failed[1])['finished'] = True
    return {reference: {'strategy_rejected': len(value['strategy_rejected']),
                        'api_timeout': len(value['api_timeout']),
                        'observed_attempt': max(value['attempts']) if value['attempts'] else None,
                        'complete': value['started'] and value['finished'], 'http_attempts': value['http']}
            for reference, value in sorted(result.items())[-MAX_RECORDS:]}


class _Panel(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.scripts, self.current, self.buffer = {}, {}, None, []
    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        identity = attributes.get('id')
        if identity:
            self.ids.setdefault(identity, []).append(attributes)
        if tag == 'script' and attributes.get('type') == 'application/json' and identity:
            self.current, self.buffer = identity, []
    def handle_data(self, data):
        if self.current is not None:
            self.buffer.append(data)
    def handle_endtag(self, tag):
        if tag == 'script' and self.current is not None:
            try:
                value = json.loads(''.join(self.buffer), object_pairs_hook=_pairs)
                self.scripts.setdefault(self.current, []).append(value)
            except (ValueError, TypeError):
                self.scripts.setdefault(self.current, []).append(None)
            self.current, self.buffer = None, []


def _panel_targets(html):
    parser = _Panel()
    parser.feed(html)
    result = {}
    for model, identity in (('selected6', 'six-independent-attack-data'),
                             ('he3_refine', 'he3-retained-data'), ('cold', 'cold-framework-data'),
                             ('cold_fixed', 'cold-fixed-framework-data'),
                             ('cold50', 'cold50-framework-data'),
                             ('cold50_fixed', 'cold50-fixed-framework-data')):
        values = parser.scripts.get(identity, [])
        data = values[0] if len(values) == 1 and isinstance(values[0], dict) else {}
        if model=='cold50_fixed':
            revision=data.get('revision')
            if (revision!=COLD_FAMILIES[model][1][-1]
                    or data.get('prediction_revision',revision)!=revision):
                data={}
        if model in COLD_FAMILIES:
            data = data.get('prediction') if isinstance(data.get('prediction'), dict) else {}
        result[model] = _period(data.get('target_period'))
    for model, identity in (('pro4', 'pro4-active'),):
        values = parser.ids.get(identity, [])
        reference = values[0].get('data-reference') if len(values) == 1 else None
        result[model] = _target(reference)
    for model in ('killer', 'framework'):
        rows = re.findall(r'<[^>]+class=[\'"][^\'"]*ai-job-row[^\'"]*[\'"][^>]*>', html)
        matching = [tag for tag in rows if re.search(r'\bdata-ai-kind=[\'"]' + model + r'[\'"]', tag)]
        target = re.findall(r'\bdata-ai-target=[\'"]([0-9]{11})[\'"]', matching[0]) if len(matching) == 1 else []
        result[model] = _period(target[0]) if len(target) == 1 else None
    return result


def _quantiles(values):
    values = sorted(value for value in values if value is not None and math.isfinite(value))
    def percentile(q):
        if not values:
            return None
        index = (len(values) - 1) * q
        left, right = math.floor(index), math.ceil(index)
        return round(values[left] + (values[right] - values[left]) * (index - left), 3)
    return {'sample_count': len(values), 'p50': percentile(.5), 'p95': percentile(.95)}


def _timing(record, actual, now):
    stages = {}
    for key in STAGE_KEYS:
        raw = record.get(key) or (actual.get('opentime') if key == 'source_opened_at' and actual else None)
        value = _stamp(raw)
        stages[key] = value.isoformat() if value is not None and value <= now else None
    def elapsed(first, last):
        start, end = _stamp(stages[first]), _stamp(stages[last])
        return round((end - start).total_seconds(), 3) if start is not None and end is not None and end >= start else None
    durations = {key: elapsed(first, last) for key, first, last in (
        ('source_to_input', 'source_opened_at', 'input_frozen_at'),
        ('input_to_request', 'input_frozen_at', 'request_started_at'),
        ('request_to_response', 'request_started_at', 'response_completed_at'),
        ('response_to_publish', 'response_completed_at', 'published_at'),
        ('source_to_publish', 'source_opened_at', 'published_at'))}
    durations['provider_elapsed'] = _finite(record.get('provider_elapsed'))
    return stages, durations


def _period_row(reference, evidence, actuals, control, diagnostics, now, status=None):
    target = _target(reference)
    record = dict(evidence.get(reference, {}))
    diagnostic = diagnostics.get(reference, {})
    accepted = record.get('accepted_attempt')
    chosen_attempt = accepted if type(accepted) is int else diagnostic.get('observed_attempt')
    points = diagnostic.get('http_attempts', {}).get(chosen_attempt, {})
    if not points.get('conflict'):
        started, completed = _stamp(points.get('start')), _stamp(points.get('complete'))
        if started is not None and started <= now:
            record['request_started_at'] = started.isoformat()
            if completed is not None and started <= completed <= now:
                record['response_completed_at'] = completed.isoformat()
                record['provider_elapsed'] = round((completed - started).total_seconds(), 3)
    stages, durations = _timing(record, actuals.get(reference), now)
    registered = _stamp(record.get('request_registered_at'))
    if registered is not None and registered > now:
        registered = None
    evidenced = registered is not None or any(stages[key] for key in (
        'request_started_at', 'response_completed_at', 'published_at'))
    eligible = _eligible(control, reference, now, evidenced)
    close = period_slot(target) - timedelta(seconds=70)
    published = _stamp(stages['published_at'])
    # A publication identity must follow its real reference and the response.
    opened, completed = _stamp(stages['source_opened_at']), _stamp(stages['response_completed_at'])
    publication_valid = (published is not None and opened is not None and completed is not None
                         and period_slot(reference) <= opened <= completed <= published)
    if not publication_valid:
        stages['published_at'] = None
        durations['response_to_publish'] = durations['source_to_publish'] = None
        published = None
    margin = round((close - published).total_seconds(), 3) if published is not None else None
    state = status if isinstance(status, dict) else {}
    failure = record.get('failure_code')
    if state.get('reference_period', state.get('current_ref')) == reference and state.get('status') in ('failed', 'timeout', 'rejected', 'unverified', 'stale'):
        failure = _error_kind(state.get('error_code', state.get('error'))) or 'job-failed'
    if eligible is False:
        classification = 'manual_off'
    elif eligible is None:
        classification = 'unknown'
    elif published is not None:
        classification = 'current' if published < close else 'late'
    elif failure:
        classification = 'failed'
    else:
        classification = 'pending' if now < close else 'missing'
    first_success = accepted == 1 and published is not None
    known = bool(diagnostic.get('complete')) or first_success
    retries = accepted - 1 if type(accepted) is int and 1 <= accepted <= 3 else (
        diagnostic['observed_attempt'] - 1 if known and type(diagnostic.get('observed_attempt')) is int else 0 if not evidenced else None)
    strategy_count = diagnostic.get('strategy_rejected', 0) if known else (0 if not evidenced else None)
    timeout_count = diagnostic.get('api_timeout', 0) if known else (0 if not evidenced else None)
    if failure == 'strategy-rejected' and strategy_count == 0:
        strategy_count = None  # The kind is known, but not how many attempts failed.
    if failure == 'provider-timeout' and timeout_count == 0:
        timeout_count = None
    return {'reference_period': reference, 'target_period': target, 'eligible': eligible,
            'closed': now >= close, 'source_ready': reference in actuals or opened is not None,
            'stages': stages, 'durations_seconds': durations,
            'request_registered_at': registered.isoformat() if registered else None,
            'close_remaining_seconds': margin, 'status': classification, 'error_code': failure,
            'published': published is not None, 'on_time': published is not None and published < close,
            'strategy_rejected_count': strategy_count, 'api_timeout_count': timeout_count, 'retry_count': retries}


def _quality(rows):
    eligible = [row for row in rows if row['eligible'] is True]
    closed = [row for row in eligible if row['closed']]
    def total(key):
        values = [row[key] for row in eligible]
        return sum(values) if all(type(value) is int for value in values) else None
    result = {'expected_count': len(rows), 'eligible_count': len(eligible), 'closed_eligible_count': len(closed),
              'manual_off_count': sum(row['eligible'] is False for row in rows),
              'unknown_count': sum(row['eligible'] is None for row in rows),
              'source_ready_count': sum(row['source_ready'] for row in eligible),
              'request_started_count': sum(row['stages']['request_started_at'] is not None for row in eligible),
              'response_completed_count': sum(row['stages']['response_completed_at'] is not None for row in eligible),
              'published_count': sum(row['published'] for row in eligible),
              'on_time_count': sum(row['on_time'] for row in closed),
              'late_count': sum(row['status'] == 'late' for row in closed),
              'missing_count': sum(not row['published'] for row in closed),
              'pending_count': sum(row['status'] == 'pending' for row in eligible),
              'failed_count': sum(row['status'] == 'failed' for row in eligible),
              'strategy_rejected_count': total('strategy_rejected_count'),
              'api_timeout_count': total('api_timeout_count'), 'retry_count': total('retry_count'),
              'margin_under90_count': sum(row['close_remaining_seconds'] is not None and row['close_remaining_seconds'] < 90 for row in eligible),
              'durations': {key: _quantiles([row['durations_seconds'][key] for row in eligible]) for key in DURATION_KEYS},
              'close_margin': _quantiles([row['close_remaining_seconds'] for row in eligible])}
    result['on_time_rate'] = round(100 * result['on_time_count'] / len(closed), 2) if closed else None
    return result


def build_summary(bundle, now):
    """Pure summary over compact, bounded snapshots; public strings are fixed."""
    now = _now(now)
    delivery=index_delivery(bundle.get('delivery'))
    start, reference = business_start(now), expected_reference(now)
    errors = list(bundle.get('errors', []))
    try:
        control = _control(bundle.get('control'), now)
    except (ValueError, TypeError, KeyError, OverflowError):
        control = {'enabled': None, 'generation': None, 'mode': 'unknown', 'introduced_at': None,
                   'transitions': [], 'history_valid': False}
        errors.append({'source': 'control', 'code': 'invalid-control'})
    records = bundle.get('records', [])[-MAX_RECORDS:]
    actuals = {row['period']: row for row in records}
    latest = max(actuals, key=period_slot) if actuals else None
    targets = _panel_targets(bundle.get('panel_html', ''))
    horizon_start = min(start - timedelta(days=2), period_slot(reference) - timedelta(minutes=5 * (MAX_RECORDS - 1)))
    slots = [horizon_start + timedelta(minutes=5 * index)
             for index in range(int((period_slot(reference) - horizon_start).total_seconds() // 300) + 1)][-MAX_RECORDS:]
    model_rows, round_data = {}, {'completed_count': 0, 'total_count': len(MODELS), 'error_count': 0,
                                 'pending_count': 0, 'manual_off_count': 0}
    for key, config in MODELS.items():
        evidence = bundle.get('evidence', {}).get(key, {})
        diagnostics = bundle.get('diagnostics', {}).get(key, {})
        state = bundle.get('statuses', {}).get(key, {})
        lower = bundle.get('model_starts', {}).get(key)
        lower = period_slot(lower) if _period(lower) else None
        rows = [_period_row(_identity(slot), evidence, actuals, control, diagnostics, now, state)
                for slot in slots if lower is None or period_slot(_target(_identity(slot))) >= lower]
        for row in rows:
            record=evidence.get(row['reference_period'],{})
            row['availability']=observation(key,record,delivery,now,
                                           identity=record.get('delivery_identity'))
        current = next((row for row in reversed(rows) if row['reference_period'] == reference),
                       _period_row(reference, {}, actuals, control, {}, now))
        current = dict(current)
        target = _target(reference)
        panel_target = targets.get(key)
        matches = panel_target == target if panel_target is not None else None
        active_start = _period(bundle.get('active_rule_starts', {}).get(key)) if key=='cold50_fixed' else None
        awaiting_rule = (active_start is not None and period_slot(target)<period_slot(active_start)
                         and current['status'] not in ('manual_off','unknown')
                         and key not in bundle.get('unavailable_models', ()))
        if current['status'] == 'current' and matches is not True:
            current['status'] = 'pending' if not current['closed'] else 'failed'
            current['error_code'] = 'panel-mismatch' if current['closed'] else None
        if key in bundle.get('unavailable_models', ()):
            current['status'], current['error_code'] = 'unknown', 'evidence-unavailable'
        if awaiting_rule:
            # Keep original period rows for historical quality. Only the active
            # display waits for its prospective rule span instead of treating
            # the prior version's still-current sealed target as a new failure.
            current['status'], current['error_code'], matches = 'await_rule_start', None, None
        phase = next((phase for name, phase in reversed(tuple(zip(STAGE_KEYS, ('source', 'input', 'request', 'response', 'published'))))
                      if current['stages'][name] is not None), 'expected')
        if current['status'] == 'current' and matches is True:
            phase = 'panel'
        if awaiting_rule:
            phase = 'rule_start'
        today = [row for row in rows if business_start(period_slot(row['target_period'])) == start]
        recent = [row for row in rows if row['eligible'] is True][-50:]
        published_refs = [ref for ref, event in evidence.items() if event.get('publication_state') == 'published']
        last = max(published_refs, key=period_slot) if published_refs else None
        model_rows[key] = {**{name: current[name] for name in ('status', 'reference_period', 'target_period', 'stages',
                              'durations_seconds', 'close_remaining_seconds', 'error_code', 'request_registered_at')},
                           'label': config[0], 'phase': phase, 'server_panel_target_period': panel_target,
                           'panel_matches': matches,
                           **({'active_rule_start_target': active_start} if key=='cold50_fixed' else {}),
                           'availability': current.get('availability',observation(key,{},None,now)),
                           'latest_published_reference': last, 'latest_published_target': _target(last),
                           'quality': {'today': _quality(today), 'recent50': _quality(recent)},
                           'recent_gaps': [{'target_period': row['target_period'], 'status': row['status'],
                                            'error_code': row['error_code'] or ('source-not-ready' if not row['source_ready'] else 'publication-missing')}
                                           for row in today if row['closed'] and row['eligible'] is True and not row['on_time']][-20:]}
        state_name = current['status']
        round_data['completed_count'] += int(state_name == 'current' and matches is True)
        round_data['pending_count'] += int(state_name in ('pending','await_rule_start'))
        round_data['manual_off_count'] += int(state_name == 'manual_off')
        round_data['error_count'] += int(state_name in ('late', 'missing', 'failed') or
                                          state_name == 'unknown' and current['eligible'] is True)
    public_control = {key: control[key] for key in ('enabled', 'generation', 'mode', 'introduced_at', 'history_valid')}
    return {'version': VERSION, 'revision': REVISION, 'updated_at': now.isoformat(),
            'biz_date': start.strftime('%Y-%m-%d'), 'business_start': start.isoformat(),
            'stats_error': any(row['source'] in ('state', 'control') for row in errors), 'errors': errors,
            'api': public_control, 'source': {'expected_reference': reference, 'latest_reference': latest,
             'latest_open_time': actuals[latest]['opentime'] if latest else None,
             'source_ready': latest == reference if latest is not None else False,
             'loaded_draw_count': len(actuals), 'conflict_count': len(bundle.get('conflicts', []))},
            'round': round_data, 'models': model_rows,
            'evidence_limits': {'max_draw_records': MAX_RECORDS, 'max_records_per_model': MAX_RECORDS,
                                'max_log_tail_bytes': MAX_LOG_BYTES}}


def _log_history(path):
    """Rotation keeps an active job's file descriptor on .1 until it exits."""
    pieces = []
    for source in (Path(str(path) + '.1'), Path(path)):
        try:
            with source.open('rb') as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - MAX_LOG_BYTES))
                pieces.append(stream.read(MAX_LOG_BYTES).decode('utf-8', 'replace'))
        except OSError:
            continue
    return parse_log('\n'.join(pieces))


def collect(*, root=ROOT, clock=beijing_now):
    """Read each fixed source once, sequentially compact large ledgers, no writes."""
    root = Path(root)
    data = root / 'data'
    initial = _now(clock())
    errors, loaded, failed_reads = [], {}, set()
    def read(name, *, optional=False, limit=MAX_JSON_BYTES, text=False):
        if name in loaded:
            return loaded[name]
        try:
            value = _read(root / name, limit, text=text)
        except FileNotFoundError:
            value = None
            if not optional:
                errors.append({'source': 'state' if name == 'data/state.json' else 'panel' if name == 'panel.html' else 'model', 'code': 'missing-source'})
        except (OSError, ValueError, TypeError, UnicodeError, OverflowError):
            value = None
            failed_reads.add(name)
            errors.append({'source': 'state' if name == 'data/state.json' else 'panel' if name == 'panel.html' else 'control' if name == 'data/ai_api_control.json' else 'model', 'code': 'invalid-source'})
        loaded[name] = value
        return value
    state, cache = read('data/state.json'), read('data/stats_draw_history.json', optional=True)
    control = read('data/ai_api_control.json', optional=True, limit=16 * 1024 * 1024)
    conflicts = set()
    try:
        records, _ = collect_records(state, cache.get('records', []) if isinstance(cache, dict) else [], _now(clock()), conflicts_out=conflicts)
    except (ValueError, TypeError, KeyError, OverflowError):
        records = []
        errors.append({'source': 'state', 'code': 'invalid-draw-source'})
    loaded.pop('data/state.json', None)
    loaded.pop('data/stats_draw_history.json', None)
    del state, cache
    shared_status = read('data/ai_job_status.json', optional=True)
    evidence, statuses, diagnostics, starts, active_rule_starts, unavailable = {}, {}, {}, {}, {}, []
    for key, (_, filename, status_file, logfile, _) in MODELS.items():
        if key in COLD_FAMILIES:
            continue
        document = read('data/' + filename, optional=True)
        try:
            evidence[key] = _model_evidence(document or {},model=key)
        except (ValueError, TypeError, KeyError):
            evidence[key] = {}
            unavailable.append(key)
        if 'data/' + filename in failed_reads:
            unavailable.append(key)
        loaded.pop('data/' + filename, None)
        document = None
        if key in ('killer', 'framework'):
            statuses[key] = shared_status.get('jobs', {}).get(key, {}) if isinstance(shared_status, dict) else {}
        else:
            statuses[key] = read('data/' + status_file, optional=True) or {}
    # Each cold family owns its index, daily seals, attempts, status and log.
    # No fallback to another family's ledgers when the new model has no history.
    for key, (prefix, revisions) in COLD_FAMILIES.items():
        index_name = 'data/' + prefix + '_ledger.json'
        index = read(index_name, optional=True)
        cold_days, cold_attempts = [], []
        if index_name in failed_reads:
            unavailable.append(key)
        if isinstance(index, dict):
            body = {name: value for name, value in index.items() if name != 'checksum'}
            valid_index = (type(index.get('version')) is int and index['version'] == 1
                           and index.get('revision') in revisions
                           and index.get('checksum') == sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                           and isinstance(index.get('days'), list)
                           and all(isinstance(day, str) for day in index['days'])
                           and sorted(set(index['days'])) == index['days'])
            if not valid_index:
                index = None
                unavailable.append(key)
                errors.append({'source': 'model', 'code': 'invalid-' + key + '-index'})
        if isinstance(index, dict):
            starts[key] = index.get('tracking_start_target')
            if key=='cold50_fixed':
                tracking=index.get('rule_tracking')
                active=tracking.get(revisions[-1]) if isinstance(tracking,dict) else None
                if isinstance(active,dict) and _period(active.get('start_target')):
                    active_rule_starts[key]=active['start_target']
            for day in index['days'][-4:]:
                if re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
                    for suffix, destination in (('_', cold_days), ('_attempts_', cold_attempts)):
                        name = 'data/' + prefix + suffix + day + '.json'
                        value = read(name, optional=True)
                        if value is not None:
                            destination.append(value)
                        if name in failed_reads:
                            unavailable.append(key)
        try:
            evidence[key] = _cold_evidence(cold_days, cold_attempts, model=key)
        except (ValueError, TypeError, KeyError):
            evidence[key] = {}
            unavailable.append(key)
            errors.append({'source': 'model', 'code': 'invalid-' + key + '-evidence'})
        statuses[key] = read('data/' + prefix + '_status.json', optional=True) or {}
    for key, (_, _, _, logfile, _) in MODELS.items():
        diagnostics[key] = _log_history(data / logfile)
    # Two classic input archives have explicit freeze timestamps. Read only the
    # current archive; older seals already carry their frozen context timestamp.
    reference = expected_reference(_now(clock()))
    for key in ('killer', 'framework'):
        archive = read('data/ai_model_inputs/' + key + '-' + reference + '.json', optional=True, limit=128 * 1024)
        if isinstance(archive, dict) and archive.get('reference_period') == reference:
            event = evidence[key].setdefault(reference, {'reference_period': reference, 'target_period': _target(reference)})
            event['input_frozen_at'] = archive.get('observed_at')
    panel = read('panel.html', limit=MAX_PANEL_BYTES, text=True) or ''
    delivery=load_delivery(root,_now(clock()))
    comparison=read('data/strategy_comparison_stats.json',optional=True,limit=4*1024*1024)
    version_comparison=comparison.get('version_comparison') if isinstance(comparison,dict) else None
    bundle = {'control': {} if 'data/ai_api_control.json' in failed_reads else control, 'records': records, 'conflicts': sorted(conflicts), 'evidence': evidence,
              'statuses': statuses, 'diagnostics': diagnostics, 'panel_html': panel, 'errors': errors,
              'model_starts': starts, 'active_rule_starts': active_rule_starts,
              'unavailable_models': unavailable,'delivery':delivery}
    result=build_summary(bundle, _now(clock()))
    # This separately validated strategy appendix is optional. Never reinterpret
    # a stale or malformed comparison as the current day's performance.
    if (isinstance(version_comparison,dict) and type(version_comparison.get('version')) is int
            and version_comparison['version']==1 and version_comparison.get('biz_date')==result['biz_date']
            and _stamp(version_comparison.get('updated_at')) is not None
            and _stamp(version_comparison['updated_at'])<=_now(clock())):
        result['version_comparison']=version_comparison
    return result
