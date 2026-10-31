"""智能深排 killer 三合尾 pair-hold3 稳定化（只读、确定性后处理）。

三项合尾（all/qbs/bsg）作为一组：上期三项均命中且自动沿用未满 3 期时沿用上期三项尾码；
失手或满 3 期改由模型本期重选。不升 revision，元数据 tail_hold3 留档；命中口径不变。
"""
TAIL_FIELDS = (('he_all', 'all'), ('he_q3', 'qbs'), ('he_h3', 'bsg'))
TAIL_INDEXES = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'bsg': (1, 2, 3)}
TAIL_HOLD_MAX = 3
TAIL_HOLD_POLICY = 'triple-hold3-until-miss'


def tail_value(numbers, key):
    return sum(numbers[index] for index in TAIL_INDEXES[key]) % 10


def apply_killer_tail_hold(response, seals, period, numbers):
    seals_map = seals.get('prediction_seals') if isinstance(seals, dict) else {}
    if (not isinstance(seals_map, dict) or not isinstance(response, dict)
            or not isinstance(numbers, (list, tuple)) or len(numbers) < 4
            or any(type(value) is not int or not 0 <= value <= 9 for value in numbers[:4])):
        return None
    refs = sorted(ref for ref in seals_map
                  if isinstance(ref, str) and len(ref) == 11 and ref.isdigit() and ref < period)
    if not refs:
        return {'policy': TAIL_HOLD_POLICY, 'hold_max': TAIL_HOLD_MAX,
                'reused_previous': False, 'note': '新选（无上一期尾码）',
                'previous_ref_period': None, 'previous_tails': None,
                'previous_hits': None, 'previous_streak': 0, 'model_tails': None}
    previous_ref = refs[-1]
    previous = seals_map.get(previous_ref) or {}
    if not isinstance(previous, dict):
        return None
    try:
        previous_tails = {field: int(previous[field]) % 10 for field, _key in TAIL_FIELDS}
    except (KeyError, TypeError, ValueError):
        return None
    streak = 1
    for ref in reversed(refs[:-1]):
        candidate = seals_map.get(ref) or {}
        if not isinstance(candidate, dict):
            break
        try:
            tails = {field: int(candidate[field]) % 10 for field, _key in TAIL_FIELDS}
        except (KeyError, TypeError, ValueError):
            break
        if tails == previous_tails:
            streak += 1
        else:
            break
    hits = {field: tail_value(numbers, key) != previous_tails[field]
            for field, key in TAIL_FIELDS}
    both_hit = all(hits.values())
    reused = bool(both_hit and streak < TAIL_HOLD_MAX)
    model_tails = {}
    for field, _key in TAIL_FIELDS:
        try:
            model_tails[field] = int(response.get(field)) % 10
        except (TypeError, ValueError):
            model_tails[field] = None
    if reused:
        for field, _key in TAIL_FIELDS:
            response[field] = previous_tails[field]
        note = '沿用上期三项尾码（第%s期，上期三项均命中）' % streak
        original = str(response.get('reason') or '')
        candidate_text = '/'.join(str(model_tails.get(field)) for field, _key in TAIL_FIELDS)
        response['reason'] = ('尾码按稳定化沿用上期（AI 候选 %s）。模型原话：%s'
                              % (candidate_text, original))[:200]
    else:
        note = ('换码（自动沿用已满%s期，改由模型重选）' % TAIL_HOLD_MAX
                if both_hit else '换码（上期至少一项未命中）')
        if model_tails == previous_tails:
            note += '（模型本次仍选同码）'
    return {'policy': TAIL_HOLD_POLICY, 'hold_max': TAIL_HOLD_MAX,
            'reused_previous': reused, 'note': note,
            'previous_ref_period': previous_ref,
            'previous_tails': previous_tails, 'previous_hits': hits,
            'previous_streak': streak, 'model_tails': model_tails}
