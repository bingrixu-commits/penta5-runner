"""七项独立 v7 合尾单码 hold3 稳定化（只读、确定性后处理）。

不升 revision：作为封存前本地确定性后处理，元数据 tail_policy 留档；命中口径不变
（单尾杀 1 码仍是 90%）。旧封存无 tail_policy 字段仍可原样校验。
"""
TAIL_INDEXES = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
                'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}
TAIL_LABELS = {'all': '千百十个', 'qbs': '千百十', 'qbg': '千百个',
               'qsg': '千十个', 'bsg': '百十个'}
TAIL_HOLD_MAX = 3
TAIL_HOLD_POLICY = 'hold3-until-miss'


def tail_value(numbers, key):
    return sum(numbers[index] for index in TAIL_INDEXES[key]) % 10


def _single_tail(seal):
    response = seal.get('response') if isinstance(seal, dict) else None
    tails = response.get('selected_tails') if isinstance(response, dict) else None
    if not isinstance(tails, dict) or len(tails) != 1:
        return None
    key, kill = next(iter(tails.items()))
    if key not in TAIL_INDEXES:
        return None
    try:
        return {'key': key, 'label': TAIL_LABELS[key], 'kill': int(kill) % 10}
    except (TypeError, ValueError):
        return None


def apply_six_tail_hold(response, seals, reference, numbers):
    """Apply deterministic single-tail hold; mutates response in place."""
    if (not isinstance(response, dict) or not isinstance(seals, dict)
            or not isinstance(numbers, (list, tuple)) or len(numbers) < 4
            or any(type(value) is not int or not 0 <= value <= 9 for value in numbers[:4])):
        return None
    refs = sorted(ref for ref in seals
                  if isinstance(ref, str) and len(ref) == 11 and ref.isdigit() and ref < reference)
    model_tail = None
    tails = response.get('selected_tails')
    if isinstance(tails, dict) and len(tails) == 1:
        key, kill = next(iter(tails.items()))
        if key in TAIL_INDEXES:
            try:
                model_tail = {'key': key, 'label': TAIL_LABELS[key], 'kill': int(kill) % 10}
            except (TypeError, ValueError):
                model_tail = None
    if not refs:
        return {'policy': TAIL_HOLD_POLICY, 'hold_max': TAIL_HOLD_MAX,
                'reused_previous': False, 'note': '新选（无上一期尾码）',
                'model_tail': model_tail, 'previous_tail': None,
                'previous_hit': None, 'previous_streak': 0,
                'previous_ref_period': None}
    previous_ref = refs[-1]
    previous_tail = _single_tail(seals.get(previous_ref))
    if previous_tail is None:
        return {'policy': TAIL_HOLD_POLICY, 'hold_max': TAIL_HOLD_MAX,
                'reused_previous': False, 'note': '新选（上一期非单尾口径，无法沿用）',
                'model_tail': model_tail, 'previous_tail': None,
                'previous_hit': None, 'previous_streak': 0,
                'previous_ref_period': previous_ref}
    streak = 1
    for ref in reversed(refs[:-1]):
        candidate = _single_tail(seals.get(ref))
        if candidate is None or candidate != previous_tail:
            break
        streak += 1
    previous_hit = tail_value(numbers, previous_tail['key']) != previous_tail['kill']
    reused = bool(previous_hit and streak < TAIL_HOLD_MAX)
    if reused:
        response['selected_tails'] = {previous_tail['key']: previous_tail['kill']}
        note = '沿用上期尾码（%s 杀%s，第%s期，上期命中）' % (
            previous_tail['label'], previous_tail['kill'], streak)
        original = str(response.get('reason') or '')
        candidate_text = ('%s 杀%s' % (model_tail['label'], model_tail['kill'])
                          if model_tail else '-')
        response['reason'] = ('尾码按稳定化沿用上期（AI 候选 %s）。模型原话：%s'
                              % (candidate_text, original))[:200]
    else:
        if not previous_hit:
            note = '换码（上期未命中）'
        else:
            note = '换码（自动沿用已满%s期，改由模型重选）' % TAIL_HOLD_MAX
        if model_tail == previous_tail:
            note += '（模型本次仍选同码）'
    return {'policy': TAIL_HOLD_POLICY, 'hold_max': TAIL_HOLD_MAX,
            'reused_previous': reused, 'note': note, 'model_tail': model_tail,
            'previous_tail': previous_tail, 'previous_hit': previous_hit,
            'previous_streak': streak, 'previous_ref_period': previous_ref}
