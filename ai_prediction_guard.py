"""Shared strict AI schemas and recoverable, deadline-checked panel publication."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import json
import os
import re

from ai_verified_stats import (atomic_json, digit, next_period, pending_items,
                               sync_parent_directory)
from stats_history import beijing_now, parse_time, period_slot


class PublicationRejected(ValueError):
    pass


def read_stats(path):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open('rb') as handle:
        raw = handle.read()
        # Retry a preceding replace whose directory fsync failed. Never treat
        # a surviving prepared/published response as durable before this commit.
        os.fsync(handle.fileno())
    sync_parent_directory(path)
    stats = json.loads(raw.decode('utf-8'))
    if not isinstance(stats, dict):
        raise ValueError('Prediction statistics must be an object; refusing to overwrite it')
    return stats


def _single(value):
    number = digit(value)
    if number is None:
        raise ValueError('Prediction digit must be an integer 0..9 or one ASCII digit')
    return number


def _reason(value):
    text = str(value)
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        # JSON permits escaped lone surrogates, but our HTML/JSON files are
        # UTF-8. Reject these inside validation so clients can safely retry.
        raise ValueError('AI reason must contain valid UTF-8 text') from None
    return text[:200]


def validate_killer_response(response):
    if not isinstance(response, dict):
        raise ValueError('AI response must be an object')
    result = {}
    for key in ('k1', 'k2', 'k3', 'k4'):
        values = response.get(key)
        if not isinstance(values, list) or len(values) != 1:
            raise ValueError('Each position must contain exactly one valid kill digit')
        result[key] = [_single(values[0])]
    for key in ('he_all', 'he_q3', 'he_h3'):
        result[key] = _single(response.get(key))
    patterns = response.get('exclude_patterns')
    if (not isinstance(patterns, list) or len(patterns) != 2
            or any(not isinstance(value, str) or not re.fullmatch(r'(?:[单双]{4}|[奇偶]{4}|[大小]{4})', value) for value in patterns)):
        raise ValueError('Exactly two distinct, valid four-position patterns are required')
    normalized = [value.replace('奇', '单').replace('偶', '双') for value in patterns]
    if normalized[0] == normalized[1]:
        raise ValueError('Exactly two semantically distinct four-position patterns are required')
    result['exclude_patterns'] = normalized
    result['reason'] = _reason(response.get('reason', ''))
    return result


def normalize_framework_keep(value, count, allowed):
    """Normalize representations only; never pad, truncate, select, or invent digits."""
    if isinstance(value, bool):
        raise ValueError('Invalid framework keep set; boolean is not a digit encoding')
    if isinstance(value, list):
        text = ''.join(str(_single(item)) for item in value)
    elif isinstance(value, int) and value >= 0:
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ValueError('Invalid framework keep set type')
    if len(text) != count or len(set(text)) != count or any(d not in allowed for d in text):
        raise ValueError('Invalid framework keep set; count, uniqueness and range are mandatory')
    return ''.join(sorted(text))


def validate_framework_response(response):
    if not isinstance(response, dict):
        raise ValueError('AI response must be an object')
    result = {key: _single(response.get(key)) for key in ('q_kill1', 'b_kill1', 'he_q3_kill', 'he_h3_kill')}
    for key, pattern in [('pattern_ds', r'[单双]{4}'), ('pattern_dx', r'[大小]{4}')]:
        value = response.get(key)
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError('Invalid framework pattern')
        result[key] = value
    for key, count, allowed in [('he2_keep', 4, '02468'), ('he3_keep', 6, '0123456789')]:
        value = response.get(key)
        result[key] = normalize_framework_keep(value, count, allowed)
    positions = response.get('pos_kills')
    if not isinstance(positions, dict):
        raise ValueError('Missing framework position kills')
    result['pos_kills'] = {}
    for key in ('q', 'b', 's', 'g'):
        values = positions.get(key)
        if not isinstance(values, list) or len(values) != 2:
            raise ValueError('Two distinct kills per position are required')
        normalized = [_single(value) for value in values]
        if len(set(normalized)) != 2:
            raise ValueError('Duplicate position kill digits')
        result['pos_kills'][key] = normalized
    result['reason'] = _reason(response.get('reason', ''))
    return result


def publication_rejection(reference, snapshot, now=None):
    """The minute boundary is conservative: never wait for a lagging source."""
    now = now or beijing_now()
    latest = snapshot.get('latest') or {}
    target = next_period(reference)
    deadline = period_slot(target)
    if not deadline:
        return 'invalid-reference-period'
    if str(latest.get('period') or '') != str(reference):
        return 'reference-period-advanced'
    if any(str(record.get('period')) == target for record in snapshot.get('records', [])):
        return 'target-already-opened'
    # Leave a short I/O margin; actual post-write completion is checked again.
    if now + timedelta(seconds=5) >= deadline:
        return 'publication-deadline-passed'
    opened = parse_time(latest.get('opentime'))
    if opened is None or now < opened:
        return 'invalid-reference-open-time'
    return None


def find_seal(stats, reference):
    seals = stats.get('prediction_seals', {})
    if not isinstance(seals, dict):
        raise ValueError('prediction_seals must be an object')
    item = seals.get(str(reference))
    if isinstance(item, dict):
        return item
    return next((item for item in pending_items(stats) if str(item.get('ref_period')) == str(reference)), None)


def _sync_pending(stats, seal):
    queue = pending_items(stats)
    target = seal['target_period']
    stats['pending_evals'] = [seal if str(item.get('target_period') or next_period(item.get('ref_period'))) == target else item for item in queue]
    pointer = stats.get('pending_eval')
    if isinstance(pointer, dict) and str(pointer.get('target_period') or next_period(pointer.get('ref_period'))) == target:
        stats['pending_eval'] = seal


def store_prepared(stats, path, snapshot, prediction, card, generated_at=None, enqueue=True):
    """First commit the immutable prediction + render snapshot; no publication claim."""
    reference = str(prediction.get('ref_period') or '')
    existing = find_seal(stats, reference)
    if existing is not None:
        raise PublicationRejected('An existing sealed prediction cannot be replaced')
    generated = generated_at or beijing_now()
    error = publication_rejection(reference, snapshot, generated)
    if error:
        raise PublicationRejected(error)
    seal = deepcopy(prediction)
    seal.update(target_period=next_period(reference), evidence_version=2,
                generated_at=generated.isoformat(),
                reference_open_time=snapshot['latest']['opentime'],
                publication_state='prepared', card_html=card)
    stats.setdefault('prediction_seals', {})[reference] = seal
    if enqueue:
        stats['pending_evals'] = pending_items(stats) + [seal]
        stats['pending_eval'] = seal
    atomic_json(path, stats)
    return seal


def publish_saved(stats, path, reference, marker, load_snapshot, writer=None, clock=beijing_now,
                  *, operation_window=None):
    """Retry only the saved card. A failed write never triggers another AI call."""
    seal = find_seal(stats, reference)
    if not seal or not isinstance(seal.get('card_html'), str) or seal.get('evidence_version') != 2:
        return False  # Legacy sealed predictions are preserved as legacy evidence.
    if seal.get('publication_state') not in ('prepared', 'published'):
        return False
    if operation_window is not None:
        if operation_window.reference != reference:
            raise PublicationRejected('operation-window-invalid-reference')
        if seal.get('publication_state') == 'published' and clock() >= operation_window.close:
            return True  # Preserve acknowledged evidence; no late card rewrite.
    if writer is None:
        from panel_io import update_panel
        writer = update_panel

    def transform(content):
        start, end = '<!-- ' + marker + '_START -->', '<!-- ' + marker + '_END -->'
        counts = content.count(start), content.count(end)
        if counts not in ((0, 0), (1, 1)) or (counts == (1, 1) and content.index(start) >= content.index(end)):
            # Structural damage is repairable; do not mark the saved prediction expired.
            raise ValueError('Missing, duplicated or reversed prediction card markers; panel preserved')
        error = publication_rejection(reference, load_snapshot(persist=False), clock())
        if error:
            raise PublicationRejected(error)
        if operation_window is not None:
            operation_window.require_open(reserve=5)
        pattern = r'<!-- ' + re.escape(marker) + r'_START -->.*?<!-- ' + re.escape(marker) + r'_END -->'
        if re.search(pattern, content, re.S):
            return re.sub(pattern, lambda _: seal['card_html'], content, count=1, flags=re.S)
        if '</body>' not in content:
            raise ValueError('Panel body not found; prediction was not published')
        return content.replace('</body>', seal['card_html'] + '\n</body>', 1)

    try:
        writer(transform)
    except PublicationRejected as error:
        if operation_window is not None and str(error).startswith('operation-window-'):
            return False  # Keep an unissued saved card and its exact original evidence.
        if seal.get('publication_state') == 'prepared':
            if str(error) == 'invalid-reference-open-time':
                # A backward wall-clock step is not proof that this immutable
                # response expired. Keep it prepared for a same-card retry.
                return False
            seal['publication_state'] = 'expired-unpublished'
            seal['publication_error'] = str(error)
            _sync_pending(stats, seal)
            atomic_json(path, stats)
        return False
    completed = clock()
    if seal.get('publication_state') == 'prepared':
        reference_time = parse_time(seal.get('reference_open_time'))
        generated = parse_time(seal.get('generated_at'))
        deadline = period_slot(seal.get('target_period'))
        if (reference_time is None or generated is None or deadline is None
                or not reference_time <= generated <= completed):
            # Never manufacture a timestamp with max()/clamping. The card may
            # already be on disk, but there is no valid publication proof yet.
            return False
        if (completed >= deadline or operation_window is not None
                and completed >= operation_window.close):
            seal['publication_state'] = 'late-unverified'
            if operation_window is not None and completed >= operation_window.close:
                seal['publication_error'] = 'operation-window-closed'
        else:
            seal['published_at'] = completed.isoformat()
            seal['publication_state'] = 'published'
        _sync_pending(stats, seal)
        atomic_json(path, stats)
    return seal.get('publication_state') == 'published'
