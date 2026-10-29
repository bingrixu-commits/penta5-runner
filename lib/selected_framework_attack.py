"""Render the versioned sealed selections in the compact attack grammar.

This is a deterministic representation of a validated response. It never
selects a number, changes a prediction, invokes a model, or adds constraints.
"""
from selected_framework_contract import (POSITION_KEYS, TAIL_POSITIONS, REVISION,
                                         merged_exclusions, validate_response)


DIGIT_ORDER = '1234567890'
POSITION_ATTACK_LABELS = dict(zip(POSITION_KEYS, ('千', '百', '十', '个')))
TAIL_ATTACK_LABELS = {
    'all': '千百十个合', 'qbs': '千百十合', 'qbg': '千百个合',
    'qsg': '千十个合', 'bsg': '百十个合',
}


def _remaining(kill):
    return ''.join(digit for digit in DIGIT_ORDER if digit != str(kill))


def build_attack(response, *, revision=REVISION):
    """Return attack text for exactly the response's six/seven conditions.

    Unselected positions retain all ten digits. Only chosen sum tails, including
    a seventh-item tail when present, are emitted. The two keep sets stay their
    validated keep sets, including leading zero. No other constraint is added.
    """
    response = validate_response(response, revision=revision)
    positions, tails = merged_exclusions(response, revision=revision)
    parts = [POSITION_ATTACK_LABELS[key] + (_remaining(positions[key]) if key in positions else DIGIT_ORDER)
             for key in POSITION_KEYS]
    parts.append('四定')
    parts.extend(TAIL_ATTACK_LABELS[key] + _remaining(tails[key])
                 for key in TAIL_POSITIONS if key in tails)
    parts.extend(('二数合', response['he2_keep'], '三数合', response['he3_keep'], '各'))
    return ''.join(parts)
