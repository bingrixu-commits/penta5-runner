"""Independent prospective cold-code framework; no providers or financial writes.

The source of ranking truth is cold_observation.  Only a successful, timely
panel delivery makes a prepared seal eligible for subsequent verification.
Evidence is kept by business day so a normal refresh has bounded work.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from itertools import combinations
import json
import os
from pathlib import Path
import re
import tempfile

from ai_verified_stats import next_period
from cold_observation import build_observation
from stats_history import (TZ, beijing_now, business_start, parse_period,
                           parse_time, period_slot, valid_draw_record,
                           validate_conflicting_periods)

ROOT = Path(__file__).resolve().parents[1]
VERSION = 1
LEGACY_REVISION = 'cold-framework-v1-30-pos3-tail3'
FIVE_REVISION = 'cold-framework-v2-30-pos3-tail2'
REVISION = 'cold-framework-v3-30-pos3-tail2-he2x4-he3x5'
REVISIONS = (LEGACY_REVISION, FIVE_REVISION, REVISION)
POSITION_KEYS = ('q', 'b', 's', 'g')
TAIL_INDEXES = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
                'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}
BASE_METRIC_KEYS = tuple('position_' + key for key in POSITION_KEYS) + tuple(
    'tail_' + key for key in TAIL_INDEXES)
KEEP_KEYS = ('he2_keep', 'he3_keep')
HE3_PARITY_POLICY = 'he3-five-odd2or3-v1'
METRIC_KEYS = BASE_METRIC_KEYS + KEEP_KEYS
INDEX_NAME = 'cold_framework_ledger.json'
PRO_FAMILY = re.compile(r'deepseek-(?:v4(?:\.1)?-)?pro(?:-[0-9]{4})?')
FLASH_FAMILY = re.compile(r'deepseek-(?:v4(?:\.1)?-)?flash(?:-[0-9]{4})?')

def _model_family(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r'deepseek-(?:v4(?:\.1)?-)?(flash|pro)(?:-[0-9]{4})?', value)
    return match.group(1) if match else None


def _api_enabled(root):
    from ai_call_control import read_control
    try:
        return read_control(root=root)['enabled']
    except (ValueError, OSError):
        return False


def _require_enabled(root, clock):
    from ai_call_control import require_ai_enabled
    return require_ai_enabled(root=root, clock=clock)


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _aware(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError('cold-framework-invalid-time') from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('cold-framework-invalid-time')
    return value.astimezone(TZ)


def expected_reference(now):
    now = _aware(now)
    index = (now.hour * 60 + now.minute) // 5
    if index == 0:
        return (now - timedelta(days=1)).strftime('%Y%m%d') + '288'
    return now.strftime('%Y%m%d') + f'{index:03d}'


def _period(value):
    if not isinstance(value, str) or parse_period(value) is None or parse_period(value)[0] != value:
        raise ValueError('cold-framework-invalid-period')
    return value


def _date(slot):
    return business_start(slot).strftime('%Y-%m-%d')


def _canonical_source(snapshot, now):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('records'), (list, tuple)):
        raise ValueError('cold-framework-invalid-snapshot')
    conflicts = validate_conflicting_periods(snapshot.get('conflicting_periods', ()))
    if any(period_slot(period) > now for period in conflicts):
        raise ValueError('cold-framework-invalid-conflicts')
    records = {}
    for row in snapshot['records']:
        if not isinstance(row, dict):
            raise ValueError('cold-framework-invalid-record')
        record = valid_draw_record(row.get('period'), row.get('numbers'), row.get('opentime'), now)
        if record is None:
            raise ValueError('cold-framework-invalid-record')
        period = record['period']
        previous = records.get(period)
        if previous and previous['numbers'] != record['numbers']:
            conflicts.add(period)
        elif previous is None or parse_time(record['opentime']) < parse_time(previous['opentime']):
            records[period] = record
    return records, conflicts


def _tail_count(revision):
    if revision not in REVISIONS:
        raise ValueError('cold-framework-invalid-revision')
    return 3 if revision == LEGACY_REVISION else 2


def metric_keys(revision=REVISION):
    _tail_count(revision)
    return METRIC_KEYS if revision == REVISION else BASE_METRIC_KEYS


def _keep(value, field):
    count, allowed = (4, '02468') if field == 'he2_keep' else (5, '0123456789')
    if (not isinstance(value, str) or len(value) != count
            or any(digit not in allowed for digit in value) or len(set(value)) != count):
        raise ValueError('cold-framework-invalid-' + field.replace('_', '-'))
    return value


_ALLOWED_HE3_KEEPS = tuple(''.join(values) for values in combinations('0123456789', 5)
                           if sum(int(digit) % 2 for digit in values) in (2, 3))


def repair_he3_keep(value):
    """Deterministically enforce the user's hard parity constraint on AI output.

    2026-10-30: pro keeps returning unbalanced 5-digit sets (e.g. 02468) although
    the prompt states the 2-odd/3-odd rule, so each period burned ~3 attempts.
    This projects the model's own digits onto the allowed balanced keeps with
    maximum overlap (lexicographic tie-break, fully deterministic). Returns None
    if the value is structurally invalid and cannot be repaired.
    """
    if (not isinstance(value, str) or len(value) != 5 or len(set(value)) != 5
            or any(digit not in '0123456789' for digit in value)):
        return None
    odd = sum(int(digit) % 2 for digit in value)
    if odd in (2, 3):
        return value
    best = None
    for candidate in _ALLOWED_HE3_KEEPS:
        overlap = len(set(candidate) & set(value))
        if best is None or overlap > best[0] or (overlap == best[0] and candidate < best[1]):
            best = (overlap, candidate)
    return best[1] if best else None


def validate_new_he3_keep(value):
    """Require balanced new proposals; historical seal replay stays unchanged."""
    value = _keep(value, 'he3_keep')
    odd = sum(int(digit) % 2 for digit in value)
    if odd not in (2, 3):
        from ai_strategy_validation import StrategyValidationError
        raise StrategyValidationError('strategy-response-cold-he3-parity',
            f'he3_keep单双比例未通过：本次为{odd}单{5-odd}双。'
            '单数仅为13579，双数为02468，0属于双数。'
            '请从输入he3_allowed_keeps中重新自主选择1个完整5码字符串，'
            '必须2单3双或3单2双，保持原有JSON字段，不得补默认号。')
    return value


def candidate_context(snapshot, now, *, revision=REVISION):
    """Return the sealed-input candidates.  Selecting their keys requires AI."""
    _tail_count(revision)
    now = _aware(now)
    input_time = _aware(snapshot.get('now')) if isinstance(snapshot, dict) else now
    if input_time > now:
        raise ValueError('cold-framework-future-input')
    records, conflicts = _canonical_source(snapshot, input_time)
    source = {'now': input_time, 'records': list(records.values()),
              'conflicting_periods': sorted(conflicts)}
    observation = build_observation(source)
    if observation['stats_error']:
        raise ValueError(observation['error_code'])
    ref = observation['reference_period']
    latest = snapshot.get('latest')
    latest_record = (valid_draw_record(latest.get('period'), latest.get('numbers'),
                                      latest.get('opentime'), input_time)
                     if isinstance(latest, dict) else None)
    if (ref != expected_reference(now) or latest_record is None
            or latest_record['period'] != ref or ref not in records
            or latest_record['numbers'] != records[ref]['numbers']):
        raise ValueError('cold-framework-reference-not-ready')
    if not 1 <= observation['sample_count'] <= 30 or observation['last_period'] != ref:
        raise ValueError('cold-framework-no-current-day-samples')
    target = next_period(ref)
    deadline = period_slot(target) - timedelta(seconds=70)
    if now >= deadline:
        raise ValueError('cold-framework-publication-too-late')
    selected = [records[period] for period in sorted(records, key=period_slot)
                if observation['first_period'] <= period <= observation['last_period']]
    input_snapshot = {'now': input_time.isoformat(), 'records': selected,
                      'conflicting_periods': [], 'latest': records[ref]}
    if _hash(selected) != observation['history_sha256']:
        raise ValueError('cold-framework-input-mismatch')
    return {
        'version': VERSION, 'revision': revision,
        'reference_period': ref, 'target_period': target,
        'sample_count': len(selected), 'first_period': selected[0]['period'],
        'last_period': selected[-1]['period'],
        'history_sha256': observation['history_sha256'],
        'input_snapshot': input_snapshot, 'input_sha256': _hash(input_snapshot),
        'deadline': deadline.isoformat(),
        'position_candidates': {key: int(observation['positions'][key]['cold'][0])
                                for key in POSITION_KEYS},
        'tail_candidates': {key: int(observation['tails'][key]['cold'][0])
                           for key in TAIL_INDEXES},
    }


def _selection(selection, revision=REVISION):
    tail_count = _tail_count(revision)
    fields = {'selected_positions', 'selected_tails'} | (set(KEEP_KEYS) if revision == REVISION else set())
    if not isinstance(selection, dict) or set(selection) != fields:
        raise ValueError('cold-framework-invalid-selection')
    result = {}
    for field, keys, count in (('selected_positions', POSITION_KEYS, 3),
                              ('selected_tails', tuple(TAIL_INDEXES), tail_count)):
        values = selection.get(field)
        if (not isinstance(values, list) or len(values) != count
                or any(not isinstance(value, str) for value in values)
                or len(set(values)) != count or not set(values).issubset(keys)):
            raise ValueError('cold-framework-invalid-selection')
        result[field] = [key for key in keys if key in values]
    if revision == REVISION:
        result.update({field: _keep(selection[field], field) for field in KEEP_KEYS})
    return result


def _metadata(metadata, context, now, required=False):
    if metadata is None and not required:
        return None
    if not isinstance(metadata, dict):
        raise ValueError('cold-framework-missing-ai-metadata')
    required_fields = {'requested_model', 'returned_model', 'requested_at',
                       'completed_at', 'input_sha256', 'finish_reason'}
    optional_fields = {'request_id', 'reason', 'reference_period', 'selection_policy',
                       'panel_context_sha256', 'panel_context',
                       'he3_keep_raw', 'he3_parity_repaired', 'he3_repair_policy',
                       'tail_hold3'}
    if (not required_fields.issubset(metadata)
            or not set(metadata).issubset(required_fields | optional_fields)
            or _model_family(metadata['requested_model']) is None
            or not isinstance(metadata['returned_model'], str)
            or _model_family(metadata['returned_model']) != _model_family(metadata['requested_model'])
            or metadata['finish_reason'] != 'stop'
            or metadata.get('selection_policy', HE3_PARITY_POLICY) != HE3_PARITY_POLICY
            or metadata.get('reference_period', context['reference_period']) != context['reference_period']
            or metadata['input_sha256'] != context['input_sha256']):
        raise ValueError('cold-framework-invalid-ai-metadata')
    requested, completed = _aware(metadata['requested_at']), _aware(metadata['completed_at'])
    if not _aware(context['input_snapshot']['now']) <= requested <= completed <= now:
        raise ValueError('cold-framework-invalid-ai-request-time')
    for field in ('request_id', 'reason'):
        if field in metadata and (not isinstance(metadata[field], str) or len(metadata[field]) > 500):
            raise ValueError('cold-framework-invalid-ai-metadata')
    if 'he3_keep_raw' in metadata:
        raw = metadata['he3_keep_raw']
        if (not isinstance(raw, str) or len(raw) != 5 or len(set(raw)) != 5
                or any(digit not in '0123456789' for digit in raw)):
            raise ValueError('cold-framework-invalid-ai-metadata')
        if (metadata.get('he3_parity_repaired') is not True
                or metadata.get('he3_repair_policy') != HE3_PARITY_POLICY):
            raise ValueError('cold-framework-invalid-ai-metadata')
    elif 'he3_parity_repaired' in metadata or 'he3_repair_policy' in metadata:
        raise ValueError('cold-framework-invalid-ai-metadata')
    if 'panel_context' in metadata and not isinstance(metadata['panel_context'], dict):
        raise ValueError('cold-framework-invalid-ai-metadata')
    if 'tail_hold3' in metadata:
        hold = metadata['tail_hold3']
        if (not isinstance(hold, dict) or hold.get('policy') not in
                ('pair-hold3-until-miss',) or hold.get('hold_max') != 3
                or type(hold.get('reused_previous')) is not bool
                or type(hold.get('previous_streak')) is not int
                or not 0 <= hold['previous_streak'] <= 288
                or not isinstance(hold.get('note'), str) or len(hold['note']) > 200):
            raise ValueError('cold-framework-invalid-ai-metadata')
        previous = hold.get('previous_tails')
        if not (previous is None or (isinstance(previous, dict) and len(previous) == 2
                and set(previous).issubset(TAIL_INDEXES)
                and all(type(value) is int and 0 <= value <= 9 for value in previous.values()))):
            raise ValueError('cold-framework-invalid-ai-metadata')
        hits = hold.get('previous_hits')
        if not (hits is None or (isinstance(hits, dict)
                and all(type(value) is bool for value in hits.values()))):
            raise ValueError('cold-framework-invalid-ai-metadata')
        model_tails = hold.get('model_tails')
        if not (model_tails is None or (isinstance(model_tails, dict)
                and all(type(value) is int and 0 <= value <= 9 for value in model_tails.values()))):
            raise ValueError('cold-framework-invalid-ai-metadata')
        sealed = hold.get('sealed_tails')
        if hold['reused_previous']:
            if (not isinstance(sealed, dict) or len(sealed) != _tail_count(REVISION)
                    or not set(sealed).issubset(TAIL_INDEXES)
                    or any(type(value) is not int or not 0 <= value <= 9
                           for value in sealed.values())):
                raise ValueError('cold-framework-invalid-ai-metadata')
        elif sealed is not None:
            raise ValueError('cold-framework-invalid-ai-metadata')
    return deepcopy(metadata)


def build_prediction(snapshot, now, selection, metadata=None, *, revision=REVISION):
    """Bind AI's selected physical keys to their exact first cold codes.

    The pure helper can omit metadata for analysis; publication requires verified
    provider metadata.  It never chooses replacement keys or digits itself.
    """
    now = _aware(now)
    context = candidate_context(snapshot, now, revision=revision)
    selected = _selection(selection, revision)
    body = {key: value for key, value in context.items()
            if key not in ('position_candidates', 'tail_candidates')}
    selection_metadata = _metadata(metadata, context, now)
    selected_tails = {key: context['tail_candidates'][key]
                      for key in selected['selected_tails']}
    hold = selection_metadata.get('tail_hold3') if isinstance(selection_metadata, dict) else None
    if isinstance(hold, dict) and hold.get('reused_previous') is True:
        sealed = hold.get('sealed_tails')
        if (isinstance(sealed, dict) and len(sealed) == _tail_count(revision)
                and set(sealed).issubset(TAIL_INDEXES)
                and all(type(value) is int and 0 <= value <= 9 for value in sealed.values())):
            selected_tails = {key: int(value) for key, value in sealed.items()}
    body.update({
        'sealed_at': now.isoformat(),
        'selected_positions': {key: context['position_candidates'][key]
                               for key in selected['selected_positions']},
        'selected_tails': selected_tails,
        'selection_metadata': selection_metadata,
    })
    if revision == REVISION:
        body.update({field: selected[field] for field in KEEP_KEYS})
    return dict(body, seal_sha256=_hash(body), publication_state='prepared',
                published_at=None, panel_evidence=None)


def evaluate(response, digits, *, revision=None):
    """Evaluate the explicit sealed rule; an overall win is all(values())."""
    if not isinstance(response, dict):
        raise ValueError('cold-framework-invalid-response')
    revision = response.get('revision', REVISION) if revision is None else revision
    if 'revision' in response and response['revision'] != revision:
        raise ValueError('cold-framework-invalid-response')
    tail_count = _tail_count(revision)
    result = {}
    if (not isinstance(digits, (list, tuple)) or len(digits) not in (4, 5)
            or any(type(value) is not int or not 0 <= value <= 9 for value in digits)):
        raise ValueError('cold-framework-invalid-digits')
    for field, keys, count in (('selected_positions', POSITION_KEYS, 3),
                              ('selected_tails', tuple(TAIL_INDEXES), tail_count)):
        selected = response.get(field)
        if (not isinstance(selected, dict) or len(selected) != count
                or not set(selected).issubset(keys)
                or any(type(value) is not int or not 0 <= value <= 9 for value in selected.values())):
            raise ValueError('cold-framework-invalid-response')
    for key, excluded in response['selected_positions'].items():
        result['position_' + key] = digits[POSITION_KEYS.index(key)] != excluded
    for key, excluded in response['selected_tails'].items():
        result['tail_' + key] = sum(digits[index] for index in TAIL_INDEXES[key]) % 10 != excluded
    if revision == REVISION:
        for field, size in (('he2_keep', 2), ('he3_keep', 3)):
            kept = _keep(response.get(field), field)
            result[field] = any(str(sum(digits[index] for index in indexes) % 10) in kept
                                for indexes in combinations(range(4), size))
    elif any(field in response for field in KEEP_KEYS):
        raise ValueError('cold-framework-invalid-response')
    return result


def _body(seal):
    return {key: value for key, value in seal.items()
            if key not in ('seal_sha256', 'publication_state', 'published_at', 'panel_evidence',
                           'publication_error')}


def _validate_seal(seal):
    if (not isinstance(seal, dict) or type(seal.get('version')) is not int
            or seal['version'] != VERSION or seal.get('revision') not in REVISIONS):
        raise ValueError('cold-framework-invalid-seal')
    if seal.get('seal_sha256') != _hash(_body(seal)):
        raise ValueError('cold-framework-seal-checksum')
    snapshot = deepcopy(seal.get('input_snapshot'))
    if not isinstance(snapshot, dict) or _hash(snapshot) != seal.get('input_sha256'):
        raise ValueError('cold-framework-input-checksum')
    snapshot['now'] = _aware(snapshot.get('now'))
    selection = {'selected_positions': list(seal.get('selected_positions', {})),
                 'selected_tails': list(seal.get('selected_tails', {}))}
    if seal['revision'] == REVISION:
        selection.update({field: seal.get(field) for field in KEEP_KEYS})
    expected = build_prediction(snapshot, _aware(seal.get('sealed_at')), selection,
                                seal.get('selection_metadata'), revision=seal['revision'])
    if _body(expected) != _body(seal):
        raise ValueError('cold-framework-seal-replay-mismatch')
    if seal.get('publication_state') not in ('prepared', 'published', 'late'):
        raise ValueError('cold-framework-invalid-publication')
    if seal['publication_state'] == 'published':
        if seal.get('selection_metadata') is None:
            raise ValueError('cold-framework-missing-ai-metadata')
        published = _aware(seal.get('published_at'))
        evidence = seal.get('panel_evidence')
        if not isinstance(evidence, dict):
            raise ValueError('cold-framework-no-panel-evidence')
        delivered = _aware(evidence.get('delivered_at'))
        if not _aware(seal['sealed_at']) <= published <= delivered < _aware(seal['deadline']):
            raise ValueError('cold-framework-invalid-publication-time')
        if evidence.get('prediction_sha256') != _hash(_public(seal)):
            raise ValueError('cold-framework-panel-evidence-checksum')
    elif seal.get('published_at') is not None or seal.get('panel_evidence') is not None:
        raise ValueError('cold-framework-invalid-unpublished')


def _public(seal):
    if seal is None:
        return None
    return {key: deepcopy(value) for key, value in seal.items()
            if key not in ('input_snapshot', 'panel_evidence', 'publication_error', 'version', 'revision')}


@contextmanager
def _lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        try:
            import fcntl
        except ImportError:
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b'':
                handle.write(b'0')
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _write(path, value):
    document = deepcopy(value)
    document.pop('checksum', None)
    document['checksum'] = _hash(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(document, handle, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        if os.name != 'nt':
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _read(path):
    if not path.exists():
        return None
    with path.open(encoding='utf-8') as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise ValueError('cold-framework-invalid-ledger')
    checksum = document.pop('checksum', None)
    if checksum != _hash(document):
        raise ValueError('cold-framework-ledger-checksum')
    if (type(document.get('version')) is not int or document['version'] != VERSION
            or document.get('revision') not in REVISIONS):
        raise ValueError('cold-framework-ledger-version')
    return document


def _day_path(data, day):
    if datetime.strptime(day, '%Y-%m-%d').strftime('%Y-%m-%d') != day:
        raise ValueError('cold-framework-invalid-day')
    return data / ('cold_framework_' + day + '.json')


def _new_day(day, now):
    return {'version': VERSION, 'revision': REVISION, 'biz_date': day,
            'created_at': now.isoformat(), 'updated_at': now.isoformat(),
            'predictions': {}, 'actuals': {}, 'conflicts': []}


def _validate_day(document, day, now):
    if (document.get('biz_date') != day or not isinstance(document.get('predictions'), dict)
            or not isinstance(document.get('actuals'), dict)):
        raise ValueError('cold-framework-invalid-day-ledger')
    created, updated = _aware(document.get('created_at')), _aware(document.get('updated_at'))
    if not created <= updated <= now:
        raise ValueError('cold-framework-invalid-ledger-time')
    conflicts = validate_conflicting_periods(document.get('conflicts'))
    for period, seal in document['predictions'].items():
        if _date(period_slot(_period(period))) != day or seal.get('target_period') != period:
            raise ValueError('cold-framework-invalid-day-target')
        _validate_seal(seal)
    for period, actual in document['actuals'].items():
        if (not isinstance(actual, dict) or actual.get('period') != period
                or _date(period_slot(_period(period))) != day
                or valid_draw_record(period, actual.get('numbers'), actual.get('opentime'), now) != actual):
            raise ValueError('cold-framework-invalid-actual')
    if any(_date(period_slot(period)) != day for period in conflicts):
        raise ValueError('cold-framework-invalid-day-conflicts')


def _empty_stat():
    return {'settled_count': 0, 'win_count': 0, 'loss_count': 0, 'hit_rate': None,
            'max_win_streak': 0, 'max_loss_streak': 0, 'current_win_streak': 0,
            'current_loss_streak': 0, 'four_loss_cycles': 0, 'streak_broken': False}


def _break(stat):
    stat['current_win_streak'] = 0
    stat['current_loss_streak'] = 0
    stat['streak_broken'] = True


def _add(stat, hit):
    stat['settled_count'] += 1
    stat['win_count' if hit else 'loss_count'] += 1
    stat['streak_broken'] = False
    if hit:
        stat['current_win_streak'] += 1
        stat['current_loss_streak'] = 0
    else:
        stat['current_loss_streak'] += 1
        stat['current_win_streak'] = 0
        if stat['current_loss_streak'] % 4 == 0:
            stat['four_loss_cycles'] += 1
    stat['max_win_streak'] = max(stat['max_win_streak'], stat['current_win_streak'])
    stat['max_loss_streak'] = max(stat['max_loss_streak'], stat['current_loss_streak'])
    stat['hit_rate'] = round(100 * stat['win_count'] / stat['settled_count'], 2)


def _previous_period(period):
    return expected_reference(period_slot(_period(period)) - timedelta(minutes=5))


def _pending_count(document, revision=None):
    return sum(1 for period, seal in document['predictions'].items()
               if (revision is None or seal['revision'] == revision)
               and seal['publication_state'] == 'published'
               and period not in document['actuals'] and period not in document['conflicts'])


def _new_index(now):
    start = next_period(expected_reference(now))
    return {'version': VERSION, 'revision': REVISION, 'created_at': now.isoformat(),
            'tracking_start_target': start, 'days': [], 'pending_counts': {},
            'rule_tracking': {REVISION: {'start_target': start, 'end_target': None}},
            'rule_pending_counts': {REVISION: {}}}


def _rule_migrations(index, ordered):
    """Read old v2's one transition or the explicit multi-transition history."""
    raw = index.get('rule_migrations')
    if len(ordered) == 1:
        if raw is not None or 'rule_migrated_at' in index:
            raise ValueError('cold-framework-invalid-rule-tracking')
        return {}
    if raw is None:
        if len(ordered) != 2 or index['revision'] != FIVE_REVISION:
            raise ValueError('cold-framework-invalid-rule-tracking')
        return {ordered[-1]: index.get('rule_migrated_at')}
    if not isinstance(raw, dict) or set(raw) != set(ordered[1:]):
        raise ValueError('cold-framework-invalid-rule-tracking')
    return deepcopy(raw)


def _validate_rule_tracking(index, now):
    created = _aware(index.get('created_at'))
    if (created > now
            or index.get('tracking_start_target') != next_period(expected_reference(created))):
        raise ValueError('cold-framework-invalid-tracking-start')
    rules = index.get('rule_tracking')
    if (not isinstance(rules, dict) or not rules or not set(rules).issubset(REVISIONS)):
        raise ValueError('cold-framework-invalid-rule-tracking')
    ordered = [revision for revision in REVISIONS if revision in rules]
    if index.get('revision') != ordered[-1]:
        raise ValueError('cold-framework-invalid-rule-tracking')
    for revision, span in rules.items():
        if not isinstance(span, dict) or set(span) != {'start_target', 'end_target'}:
            raise ValueError('cold-framework-invalid-rule-tracking')
        beginning = period_slot(_period(span['start_target']))
        ending = period_slot(_period(span['end_target'])) if span['end_target'] is not None else None
        if ending is not None and ending < beginning - timedelta(minutes=5):
            raise ValueError('cold-framework-invalid-rule-tracking')
    if rules[ordered[0]]['start_target'] != index['tracking_start_target']:
        raise ValueError('cold-framework-invalid-rule-tracking')
    if rules[ordered[-1]]['end_target'] is not None:
        raise ValueError('cold-framework-invalid-rule-tracking')
    migrations = _rule_migrations(index, ordered)
    previous_at = created
    for previous, revision in zip(ordered, ordered[1:]):
        beginning = rules[revision]['start_target']
        migrated = _aware(migrations[revision])
        if (not previous_at <= migrated <= now
                or period_slot(beginning) < period_slot(next_period(expected_reference(migrated)))
                or period_slot(beginning) < period_slot(rules[previous]['start_target'])
                or rules[previous]['end_target'] != _previous_period(beginning)):
            raise ValueError('cold-framework-invalid-rule-tracking')
        previous_at = migrated
    if migrations and _aware(index.get('rule_migrated_at')) != previous_at:
        raise ValueError('cold-framework-invalid-rule-tracking')
    pending = index.get('rule_pending_counts')
    if (not isinstance(pending, dict) or set(pending) != set(rules)
            or any(not isinstance(counts, dict) or set(counts) != set(index['days'])
                   or any(type(count) is not int or not 0 <= count <= 288 for count in counts.values())
                   for counts in pending.values())
            or any(sum(pending[revision][day] for revision in rules) != index['pending_counts'][day]
                   for day in index['days'])):
        raise ValueError('cold-framework-invalid-rule-pending-counts')


def _migrate_rules(index, documents, now):
    """Append the seven-item rule without changing prior seals or closed ranges."""
    previous = index.get('revision')
    if previous not in (LEGACY_REVISION, FIVE_REVISION):
        raise ValueError('cold-framework-invalid-rule-tracking')
    if 'rule_tracking' in index:
        _validate_rule_tracking(index, now)
        rules = deepcopy(index['rule_tracking'])
        ordered = [revision for revision in REVISIONS if revision in rules]
        migrations = _rule_migrations(index, ordered)
    else:
        if (previous != LEGACY_REVISION or 'rule_migrated_at' in index
                or 'rule_migrations' in index or 'rule_pending_counts' in index):
            raise ValueError('cold-framework-invalid-rule-tracking')
        rules = {previous: {'start_target': index['tracking_start_target'], 'end_target': None}}
        migrations = {}
    before = dict(index, rule_tracking=rules)
    for document in documents.values():
        _validate_rule_seals(document, before)
    sealed = [seal['target_period'] for document in documents.values()
              for seal in document['predictions'].values()]
    start = next_period(expected_reference(now))
    if sealed:
        start = max((start, next_period(max(sealed, key=period_slot))), key=period_slot)
    rules[previous]['end_target'] = _previous_period(start)
    rules[REVISION] = {'start_target': start, 'end_target': None}
    migrations[REVISION] = now.isoformat()
    index.update(revision=REVISION, rule_migrated_at=now.isoformat(),
                 rule_tracking=rules, rule_migrations=migrations,
                 rule_pending_counts={revision: {day: _pending_count(documents[day], revision)
                                                 for day in index['days']}
                                      for revision in rules})
    _validate_rule_tracking(index, now)


def _validate_rule_seals(document, index):
    for period, seal in document['predictions'].items():
        span = index['rule_tracking'].get(seal['revision'])
        if (span is None or period_slot(period) < period_slot(span['start_target'])
                or (span['end_target'] is not None
                    and period_slot(period) > period_slot(span['end_target']))):
            raise ValueError('cold-framework-seal-outside-rule-range')


def _summarize(document, index, now, prediction=None, source_error=None, *, revision=REVISION):
    _tail_count(revision)
    start = business_start(now)
    overall, metrics = _empty_stat(), {key: _empty_stat() for key in metric_keys(revision)}
    pending = missing = conflicts = rejected = 0
    latest = None
    span = index.get('rule_tracking', {}).get(revision)
    span = span or {'start_target': index['tracking_start_target'], 'end_target': None}
    first = max(start, period_slot(span['start_target']))
    last = period_slot(expected_reference(now))
    ending = period_slot(span['end_target']) if span['end_target'] is not None else None
    if ending is not None:
        last = min(last, ending)
    actuals = document['actuals']
    seals = {period: seal for period, seal in document['predictions'].items()
             if seal['revision'] == revision}
    slots = []
    cursor = first
    while cursor <= last:
        # 05:00 belongs to period 060, midnight to the previous date's 288.
        period = expected_reference(cursor)
        slots.append(period)
        cursor += timedelta(minutes=5)
    for period in slots:
        seal, actual = seals.get(period), actuals.get(period)
        if period in document['conflicts']:
            conflicts += 1
            for stat in (overall, *metrics.values()):
                _break(stat)
        elif seal is not None and seal['publication_state'] == 'published' and actual is not None:
            results = evaluate(seal, actual['numbers'])
            _add(overall, all(results.values()))
            latest = period
            for key, stat in metrics.items():
                _add(stat, results[key]) if key in results else _break(stat)
        elif actual is not None:
            missing += 1
            if seal is not None:
                rejected += 1
            for stat in (overall, *metrics.values()):
                _break(stat)
        else:
            pending += 1
            # A trailing unverified target does not change the last settled run.
            # A real gap followed by a later draw interrupts continuity.
            if any(period_slot(value) > period_slot(period) for value in actuals):
                for stat in (overall, *metrics.values()):
                    _break(stat)
    for period, seal in seals.items():
        if (period_slot(period) > last and period_slot(period) >= first
                and (ending is None or period_slot(period) <= ending)
                and seal['publication_state'] == 'published'):
            pending += 1
    if prediction is not None and prediction.get('revision') != revision:
        prediction = None
    if prediction is None and seals:
        prediction = seals[max(seals, key=period_slot)]
    return {
        'version': VERSION, 'revision': revision, 'stats_error': False, 'error_code': None,
        'biz_date': start.strftime('%Y-%m-%d'), 'business_start': start.isoformat(),
        'updated_at': now.isoformat(), 'prediction': _public(prediction),
        'prediction_revision': revision if prediction is not None else None,
        'overall': overall, 'metrics': metrics, 'pending_count': pending,
        'missing_count': missing, 'conflict_count': conflicts, 'rejected_count': rejected,
        'latest_settled_period': latest, 'source_error': source_error,
        'api_enabled': False,
        'overdue_pending_count': sum(count for day, count in index.get('rule_pending_counts', {}).get(revision, {}).items()
                                     if day < start.strftime('%Y-%m-%d')),
    }


def _error(now, code):
    result = _summarize(_new_day(_date(now), now), _new_index(now), now)
    result.update(stats_error=True, error_code=code, source_error=code)
    result['history_summaries'] = []
    return result


def refresh(snapshot, *, root=ROOT, clock=beijing_now, writer=None):
    """Only settle and render existing evidence; never selects or requests AI."""
    return _refresh(snapshot, root=root, clock=clock, writer=writer)


def prepare_and_publish(snapshot, selection, metadata, *, root=ROOT, clock=beijing_now, writer=None):
    """Publish one independently requested AI selection, without any fallback."""
    return _refresh(snapshot, selection=selection, metadata=metadata,
                    root=root, clock=clock, writer=writer)


def _refresh(snapshot, *, selection=None, metadata=None, root=ROOT, clock=beijing_now, writer=None):
    """Prepare, deliver and settle only this framework's own immutable evidence.

    ``writer(summary)`` must synchronously perform the real panel write and raise
    on failure.  Returning normally (including None) confirms that write.  A
    failed final write never creates publication evidence.
    """
    now = _aware(clock())
    root = Path(root)
    data = root / 'data'
    if writer is None:
        from cold_framework_view import publish
        writer = lambda summary: publish(summary, root=root)
    with _lock(data / 'cold_framework.lock'):
        def make_summary(document, index, stamp, prediction=None, source_error=None):
            result = _summarize(document, index, stamp, prediction, source_error)
            result['api_enabled'] = _api_enabled(root)
            result['history_summaries'] = []
            for revision in REVISIONS[:-1]:
                if revision not in index['rule_tracking']:
                    continue
                known = [seal for ledger in documents.values()
                         for seal in ledger['predictions'].values()
                         if seal['revision'] == revision]
                previous_prediction = max(known, key=lambda seal: period_slot(seal['target_period'])) if known else None
                previous_summary = _summarize(document, index, stamp, previous_prediction,
                                               source_error, revision=revision)
                previous_summary['api_enabled'] = result['api_enabled']
                result['history_summaries'].append(previous_summary)
                if revision == LEGACY_REVISION:
                    result['legacy_summary'] = previous_summary
            return result
        try:
            index_path = data / INDEX_NAME
            index = _read(index_path)
            if index is None:
                index = _new_index(now)
            if (not isinstance(index.get('days'), list) or any(not isinstance(day, str) for day in index['days'])
                    or sorted(set(index['days'])) != index['days']
                    or not isinstance(index.get('pending_counts'), dict)
                    or set(index['pending_counts']) != set(index['days'])
                    or any(type(count) is not int or not 0 <= count <= 288
                           for count in index['pending_counts'].values())):
                raise ValueError('cold-framework-invalid-index')
            created = _aware(index.get('created_at'))
            _period(index.get('tracking_start_target'))
            if (created > now
                    or index['tracking_start_target'] != next_period(expected_reference(created))):
                raise ValueError('cold-framework-invalid-tracking-start')
            for day in index['days']:
                if not _day_path(data, day).is_file():
                    raise ValueError('cold-framework-missing-day-ledger')
            if 'rule_tracking' in index:
                _validate_rule_tracking(index, now)
            elif index['revision'] != LEGACY_REVISION:
                raise ValueError('cold-framework-invalid-rule-tracking')
            source_error = None
            try:
                records, source_conflicts = _canonical_source(snapshot, now)
            except ValueError as exc:
                records, source_conflicts, source_error = {}, set(), str(exc)
            active_days = {_date(now), _date(now - timedelta(days=1)),
                           _date(period_slot(next_period(expected_reference(now))))}
            active_days.update(_date(period_slot(period)) for period in (*records, *source_conflicts))
            documents = {}
            for day in sorted(active_days):
                path = _day_path(data, day)
                document = _read(path)
                if document is None:
                    document = _new_day(day, now)
                _validate_day(document, day, now)
                documents[day] = document
            if index['revision'] != REVISION:
                if selection is not None:
                    raise ValueError('cold-framework-rule-migration-required')
                # Validate every indexed prior-rule day before the prospective
                # transition. Historical days are read, not rewritten.
                legacy_documents = dict(documents)
                for day in index['days']:
                    if day not in legacy_documents:
                        document = _read(_day_path(data, day))
                        _validate_day(document, day, now)
                        legacy_documents[day] = document
                _migrate_rules(index, legacy_documents, now)
                for document in legacy_documents.values():
                    _validate_rule_seals(document, index)
            else:
                for document in documents.values():
                    _validate_rule_seals(document, index)
            for period, record in records.items():
                day = _date(period_slot(period))
                document = documents[day]
                old = document['actuals'].get(period)
                if old and old['numbers'] != record['numbers']:
                    document['conflicts'] = sorted(set(document['conflicts']) | {period})
                elif old is None and period_slot(period) >= period_slot(index['tracking_start_target']):
                    document['actuals'][period] = record
            for period in source_conflicts:
                document = documents[_date(period_slot(period))]
                if period_slot(period) >= period_slot(index['tracking_start_target']):
                    document['conflicts'] = sorted(set(document['conflicts']) | {period})
            proposal = None
            if source_error is None and selection is not None:
                try:
                    _require_enabled(root, clock)
                    validate_new_he3_keep(selection.get('he3_keep') if isinstance(selection, dict) else None)
                    context = candidate_context(snapshot, now)
                    _metadata(metadata, context, now, required=True)
                    if period_slot(context['target_period']) < period_slot(index['rule_tracking'][REVISION]['start_target']):
                        raise ValueError('cold-framework-rule-not-started')
                    proposal = build_prediction(snapshot, now, selection, metadata)
                except ValueError as exc:
                    source_error = str(exc)
            if proposal is not None:
                day = _date(period_slot(proposal['target_period']))
                document = documents[day]
                existing = document['predictions'].get(proposal['target_period'])
                if existing is None:
                    document['predictions'][proposal['target_period']] = proposal
                elif (_body(existing)['history_sha256'] != proposal['history_sha256']
                      or existing['revision'] != proposal['revision']
                      or existing['selected_positions'] != proposal['selected_positions']
                      or existing['selected_tails'] != proposal['selected_tails']
                      or any(existing.get(field) != proposal.get(field) for field in KEEP_KEYS)):
                    # A source correction cannot silently change an already prepared card.
                    source_error = 'cold-framework-input-changed-after-seal'
                    proposal = None
                else:
                    proposal = existing
            for day, document in documents.items():
                if not document['predictions'] and not document['actuals'] and not document['conflicts']:
                    continue
                document['updated_at'] = now.isoformat()
                _write(_day_path(data, day), document)
                if day not in index['days']:
                    index['days'].append(day)
                index['pending_counts'][day] = _pending_count(document)
                for revision, counts in index['rule_pending_counts'].items():
                    counts[day] = _pending_count(document, revision)
            index['days'].sort()
            _write(index_path, index)
            current = documents[_date(now)]
            # Keep stale sealed digits visible for checking, but never republish them.
            display = proposal
            if display is None:
                known = [seal for document in documents.values() for seal in document['predictions'].values()
                         if seal['revision'] == REVISION]
                display = max(known, key=lambda seal: period_slot(seal['target_period'])) if known else None
            summary = make_summary(current, index, now, display, source_error)
            if proposal is not None and proposal['publication_state'] == 'prepared' and source_error is None:
                # First show the immutable prepared card.  It is deliberately not usable.
                writer(deepcopy(summary))
                published = _aware(clock())
                if (published >= _aware(proposal['deadline'])
                        or expected_reference(published) != proposal['reference_period']):
                    source_error = 'cold-framework-publication-too-late'
                else:
                    candidate = deepcopy(proposal)
                    candidate['publication_state'] = 'published'
                    candidate['published_at'] = published.isoformat()
                    _require_enabled(root, clock)
                    # Project a successful delivery into today's displayed
                    # statistics without committing a published seal before the
                    # writer and delivery deadline checks have succeeded.
                    publication_document = current
                    if _date(period_slot(candidate['target_period'])) == current['biz_date']:
                        publication_document = deepcopy(current)
                        publication_document['predictions'][candidate['target_period']] = candidate
                    final = make_summary(publication_document, index, published, candidate)
                    writer(deepcopy(final))
                    delivered = _aware(clock())
                    _require_enabled(root, clock)
                    if (published <= delivered < _aware(candidate['deadline'])
                            and expected_reference(delivered) == candidate['reference_period']):
                        candidate['panel_evidence'] = {'delivered_at': delivered.isoformat(),
                                                       'prediction_sha256': _hash(_public(candidate))}
                        document = documents[_date(period_slot(candidate['target_period']))]
                        document['predictions'][candidate['target_period']] = candidate
                        document['updated_at'] = delivered.isoformat()
                        _write(_day_path(data, document['biz_date']), document)
                        index['pending_counts'][document['biz_date']] = _pending_count(document)
                        for revision, counts in index['rule_pending_counts'].items():
                            counts[document['biz_date']] = _pending_count(document, revision)
                        _write(index_path, index)
                        return make_summary(current, index, delivered, candidate)
                    source_error = 'cold-framework-publication-too-late'
                proposal['publication_state'] = 'late'
                proposal['publication_error'] = source_error
                document = documents[_date(period_slot(proposal['target_period']))]
                _write(_day_path(data, document['biz_date']), document)
                summary = make_summary(current, index, _aware(clock()), proposal, source_error)
            writer(deepcopy(summary))
            return summary
        except Exception as exc:
            # Do not overwrite evidence or silently reset a damaged ledger.
            if (str(exc).startswith('ai-api-') and 'current' in locals()
                    and 'index' in locals() and isinstance(index, dict)):
                summary = make_summary(current, index, _aware(clock()), source_error=str(exc))
                try:
                    writer(deepcopy(summary))
                except Exception:
                    pass
                return summary
            code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('cold-') else 'cold-framework-refresh-failed'
            summary = _error(_aware(clock()), code)
            summary['api_enabled'] = _api_enabled(root)
            try:
                writer(deepcopy(summary))
            except Exception:
                pass
            return summary
