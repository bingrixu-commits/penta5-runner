"""固定八码框架合尾 pair-hold3 稳定化（只读、确定性）。

未启用新 revision：作为封存前的本地确定性后处理，元数据 tail_hold3 留档；
命中口径不变（每项仍是杀 1 码 90%）。
"""
FRAMEWORK_TAIL_FIELDS = (('he_q3_kill', 'qbs'), ('he_h3_kill', 'bsg'))
TAIL_HOLD_MAX = 3
TAIL_HOLD_POLICY = 'pair-hold3-until-miss'


def _framework_tail_value(numbers, key):
    if key == 'qbs':
        return (numbers[0] + numbers[1] + numbers[2]) % 10
    return (numbers[1] + numbers[2] + numbers[3]) % 10


def apply_framework_tail_hold(response, seals, period, numbers):
    """Deterministic pair-hold for the two fixed framework tails (read-only)."""
    seals_map = seals.get('prediction_seals') if isinstance(seals, dict) else {}
    if not isinstance(seals_map, dict) or not isinstance(response, dict):
        return None
    if (not isinstance(numbers, (list, tuple)) or len(numbers) < 4
            or any(type(value) is not int or not 0 <= value <= 9 for value in numbers[:4])):
        return None
    refs = sorted(ref for ref in seals_map
                  if isinstance(ref, str) and len(ref) == 11 and ref.isdigit() and ref < period)
    if not refs:
        return None
    previous_ref = refs[-1]
    previous = seals_map.get(previous_ref)
    previous_response = previous.get('response') if isinstance(previous, dict) else None
    if not isinstance(previous_response, dict):
        return None
    try:
        previous_tails = {field: int(previous_response[field]) % 10
                          for field, _key in FRAMEWORK_TAIL_FIELDS}
    except (KeyError, TypeError, ValueError):
        return None
    streak = 1
    for ref in reversed(refs[:-1]):
        candidate = seals_map.get(ref)
        candidate_response = candidate.get('response') if isinstance(candidate, dict) else None
        if not isinstance(candidate_response, dict):
            break
        try:
            tails = {field: int(candidate_response[field]) % 10
                     for field, _key in FRAMEWORK_TAIL_FIELDS}
        except (KeyError, TypeError, ValueError):
            break
        if tails == previous_tails:
            streak += 1
        else:
            break
    hits = {field: _framework_tail_value(numbers, key) != previous_tails[field]
            for field, key in FRAMEWORK_TAIL_FIELDS}
    both_hit = all(hits.values())
    reused = bool(both_hit and streak < TAIL_HOLD_MAX)
    model_tails = {}
    for field, _key in FRAMEWORK_TAIL_FIELDS:
        try:
            model_tails[field] = int(response.get(field)) % 10
        except (TypeError, ValueError):
            model_tails[field] = None
    if reused:
        for field, _key in FRAMEWORK_TAIL_FIELDS:
            response[field] = previous_tails[field]
        note = '沿用上期尾码（两项均命中，第%s期）' % streak
        original = str(response.get('reason') or '')
        response['reason'] = ('尾码按稳定化沿用上期（AI 候选 %s/%s）。模型原话：%s'
                              % (model_tails.get('he_q3_kill'), model_tails.get('he_h3_kill'),
                                 original))[:200]
    else:
        note = ('换码（自动沿用已满%s期，改由模型重选）' % TAIL_HOLD_MAX
                if both_hit else '换码（上期至少一项未命中）')
        if model_tails == previous_tails:
            note += '（模型本次仍选同码）'
    return {'policy': TAIL_HOLD_POLICY, 'hold_max': TAIL_HOLD_MAX,
            'reused_previous': reused, 'note': note,
            'previous_ref_period': previous_ref, 'previous_tails': previous_tails,
            'previous_hits': hits, 'previous_streak': streak,
            'model_tails': model_tails}
