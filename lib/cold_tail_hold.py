"""冷码七项框架合尾 pair-hold3 稳定化（只读、确定性后处理）。"""
from pathlib import Path
import copy

from cold_framework import INDEX_NAME, TAIL_INDEXES, _day_path, _read, period_slot

TAIL_HOLD_MAX = 3
TAIL_HOLD_POLICY = 'pair-hold3-until-miss'


def tail_value(numbers, key):
    return sum(numbers[index] for index in TAIL_INDEXES[key]) % 10


def _numbers_for(snapshot, target):
    for record in snapshot.get('records') or []:
        if isinstance(record, dict) and str(record.get('period')) == str(target):
            return record.get('numbers')
    latest = snapshot.get('latest') or {}
    if str(latest.get('period')) == str(target):
        return latest.get('numbers')
    return None


def apply_cold_tail_hold(data_dir, target_period, snapshot):
    data_dir = Path(data_dir)
    index = _read(data_dir / INDEX_NAME)
    days = (index or {}).get('days') if isinstance(index, dict) else None
    if not isinstance(days, list):
        return None
    predictions = []
    for day in reversed(days):
        if not isinstance(day, str):
            continue
        document = _read(_day_path(data_dir, day))
        if not isinstance(document, dict):
            continue
        for target, prediction in (document.get('predictions') or {}).items():
            if (isinstance(prediction, dict)
                    and prediction.get('publication_state') in ('published', 'prepared', 'late')
                    and str(target) < str(target_period)):
                predictions.append((str(target), prediction))
        if len(predictions) >= 6:
            break
    if not predictions:
        return {'policy': TAIL_HOLD_POLICY, 'hold_max': TAIL_HOLD_MAX,
                'reused_previous': False, 'note': '新选（无上一期尾码）',
                'previous_target_period': None, 'previous_ref_period': None,
                'previous_tails': None, 'previous_hits': None,
                'previous_streak': 0, 'sealed_tails': None}
    predictions.sort(key=lambda item: period_slot(item[0]), reverse=True)
    previous_target, previous = predictions[0]
    previous_tails = previous.get('selected_tails')
    if (not isinstance(previous_tails, dict) or len(previous_tails) != 2
            or not set(previous_tails).issubset(TAIL_INDEXES)
            or any(type(value) is not int or not 0 <= value <= 9
                   for value in previous_tails.values())):
        return None
    streak = 1
    for target, prediction in predictions[1:]:
        tails = prediction.get('selected_tails')
        if tails != previous_tails:
            break
        streak += 1
    numbers = _numbers_for(snapshot, previous_target)
    if (not isinstance(numbers, (list, tuple)) or len(numbers) < 4
            or any(type(value) is not int or not 0 <= value <= 9 for value in numbers[:4])):
        return None
    hits = {key: tail_value(numbers, key) != previous_tails[key] for key in previous_tails}
    both_hit = all(hits.values())
    reused = bool(both_hit and streak < TAIL_HOLD_MAX)
    if reused:
        note = '沿用上期两项尾码（第%s期，上期两项均命中）' % streak
        sealed_tails = {key: int(value) for key, value in previous_tails.items()}
    else:
        note = ('换码（自动沿用已满%s期，改由本期冷码重选）' % TAIL_HOLD_MAX
                if both_hit else '换码（上期至少一项未命中）')
        sealed_tails = None
    return {'policy': TAIL_HOLD_POLICY, 'hold_max': TAIL_HOLD_MAX,
            'reused_previous': reused, 'note': note,
            'previous_target_period': previous_target,
            'previous_ref_period': previous.get('reference_period'),
            'previous_tails': {key: int(value) for key, value in previous_tails.items()},
            'previous_hits': hits, 'previous_streak': streak,
            'sealed_tails': sealed_tails}
