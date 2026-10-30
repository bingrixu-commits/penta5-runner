"""Prospective selected-strategy checks against one frozen real history.

The validator never chooses or changes returned numbers.  Seven-item checking
is a necessary single-item local condition, deliberately not a claim of global
optimality.  Six-item comparisons use exact rational five-block variance.
Historical contracts and publication reconstruction do not call this module.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from itertools import combinations, product
import json

from ai_strategy_validation import StrategyValidationError, validation_report
from selected_framework_contract import (REVISION as SEVEN_REVISION,
                                          SUPPORTED_REVISIONS as SEVEN_REVISIONS)
from selected_six_contract import (REVISION as SIX_REVISION,
                                    V2_REVISION as SIX_V2_REVISION,
                                    V3_REVISION as SIX_V3_REVISION,
                                    SUPPORTED_REVISIONS as SIX_REVISIONS,
                                    reverse_attributes)
from stat50_compare import validate_history

VERSION = 'strategy-response-v1'
POSITION_KEYS = ('q', 'b', 's', 'g')
TAIL_POSITIONS = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
                  'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}
METHODS = {'seven': 'joint-overlap-v3-three-positions', 'six': 'block-stability-v2'}
SEVEN_RETRY_ADDITION = (
    '还必须核对冻结策略的完整七项失败并集：固定其余六项时，任何一个合法单项目替换'
    '（包括改为未选物理位置或合尾、改排除数字、改任一完整保留集）都不能使完整并集更小。'
    '这是单项局部必要条件，不代表全局最优；须自主重选完整合法JSON，不得补号、抄其他模型或为异号换号。')
SIX_RETRY_ADDITION = (
    '还必须按固定五个10期区块的词典序比较全部合法单项目候选：先最大单段失败次数，'
    '再跨段失败次数方差，最后才总量；位置/合尾可替换为未选项目，不能只核对已选物理项内的数字。'
    '二数合与三数合比较全部合法保留集；同分允许任意。自主输出完整合法JSON，不得程序补号或为异号换号。')


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _failure_maps(history):
    draws = [record['numbers'] for record in history]
    result = {}
    for index, key in enumerate(POSITION_KEYS):
        result[('selected_positions', key)] = {
            digit: sum(1 << i for i, row in enumerate(draws) if row[index] == digit)
            for digit in range(10)}
    for key, indices in TAIL_POSITIONS.items():
        result[('selected_tails', key)] = {
            digit: sum(1 << i for i, row in enumerate(draws)
                       if sum(row[index] for index in indices) % 10 == digit)
            for digit in range(10)}
    for field, width, allowed, retained in (
            ('he2_keep', 2, '02468', 4), ('he3_keep', 3, '0123456789', 5)):
        actual = [{sum(row[index] for index in indices) % 10
                   for indices in combinations(range(4), width)} for row in draws]
        result[(field, None)] = {}
        for values in combinations(allowed, retained):
            keep, digits = ''.join(values), {int(value) for value in values}
            result[(field, None)][keep] = sum(1 << i for i, tails in enumerate(actual)
                                             if not tails.intersection(digits))
    for field, palette in (('reverse_parity', '单双'), ('reverse_size', '大小')):
        actual = []
        for row in draws:
            if field == 'reverse_parity':
                actual.append(''.join('双' if number % 2 == 0 else '单' for number in row))
            else:
                actual.append(''.join('大' if number >= 5 else '小' for number in row))
        result[(field, None)] = {
            ''.join(form): sum(1 << i for i, value in enumerate(actual) if value == ''.join(form))
            for form in product(palette, repeat=4)}
    return result


def _items(response):
    items = ([(field, key, digit) for field in ('selected_positions', 'selected_tails')
              for key, digit in response[field].items()]
             + [(field, None, response[field]) for field in ('he2_keep', 'he3_keep')])
    for field in ('reverse_parity', 'reverse_size'):
        if field in response:
            items.append((field, None, response[field]))
    return items


def _options(item, response, masks, revision=None):
    field, key, _ = item
    if key is None:
        values = list(masks[(field, None)])
        if field == 'he3_keep' and revision in (SIX_V3_REVISION, SIX_REVISION):
            # v3/v4 compare only balanced 2:3 keep sets; older revisions keep
            # the complete legal domain byte-for-byte.
            values = [value for value in values
                      if 2 <= sum(int(digit) % 2 for digit in value) <= 3]
        elif field in ('reverse_parity', 'reverse_size') and revision == SIX_REVISION:
            # v4 forms must mirror the selected positions' kill digits; only the
            # other positions are free, so compare inside this constrained set.
            column = 0 if field == 'reverse_parity' else 1
            for position, kill in response['selected_positions'].items():
                expected = reverse_attributes(kill)[column]
                index = POSITION_KEYS.index(position)
                values = [value for value in values if value[index] == expected]
        return [(field, None, value) for value in values]
    keys = POSITION_KEYS if field == 'selected_positions' else tuple(TAIL_POSITIONS)
    occupied = set(response[field]) - {key}
    return [(field, candidate, value) for candidate in keys if candidate not in occupied
            for value in masks[(field, candidate)]]


def _mask(masks, item):
    return masks[item[:2]][item[2]]


def _score(mask):
    blocks = [((mask >> start) & 1023).bit_count() for start in range(0, 50, 10)]
    total = sum(blocks)
    # Population variance = (5*sum(count^2)-total^2)/25.  The common
    # denominator is omitted only during comparison, so no float tie drifts.
    return max(blocks), 5 * sum(count * count for count in blocks) - total * total, total


def _score_report(value):
    return {'worst_block_failures': value[0], 'variance_numerator': value[1],
            'variance_denominator': 25, 'total_failures': value[2]}


def _describe(item):
    return item[0] + ('.' + item[1] if item[1] is not None else '') + '=' + str(item[2])


def _joint(response, masks):
    items = _items(response)
    chosen_masks = [_mask(masks, item) for item in items]
    union = 0
    for mask in chosen_masks:
        union |= mask
    achieved, comparisons = union.bit_count(), 0
    for index, item in enumerate(items):
        others = 0
        for other_index, mask in enumerate(chosen_masks):
            if other_index != index:
                others |= mask
        for option in _options(item, response, masks):
            comparisons += 1
            alternative = (others | _mask(masks, option)).bit_count()
            if alternative < achieved:
                raise StrategyValidationError(
                    'strategy-response-selected-seven-joint-not-locally-optimal',
                    '完整七项在冻结50期的失败并集为%d期；保持另外六项不变，将%s替换为合法候选%s，'
                    '完整并集可降到%d期。这是可核查反例，不是程序代选的最终答案。当前输出没有通过单项'
                    '局部必要条件；请依据冻结候选自主重新比较并返回完整七项，不能改历史、补号或为了异号换号。'
                    % (achieved, _describe(item), _describe(option), alternative))
    return {'validation_scope': 'single-item-local-optimum-not-global-optimum',
            'historical_union_failures': achieved, 'legal_single_item_comparisons': comparisons}


def _minimax(response, masks, revision=None):
    comparisons, scores = 0, []
    for item in _items(response):
        achieved = _score(_mask(masks, item))
        for option in _options(item, response, masks, revision=revision):
            comparisons += 1
            alternative = _score(_mask(masks, option))
            if alternative < achieved:
                raise StrategyValidationError(
                    'strategy-response-selected-six-minimax-not-optimal',
                    '%s的(最大单段失败,方差分子/25,总失败)为(%d,%d/25,%d)；合法单项目反例%s'
                    '达到(%d,%d/25,%d)，按先最大、再方差、最后总量的词典序严格更优。'
                    '须比较未选的物理项目与全部合法保留集，自主重选完整六项；不得只按全窗总量或补默认号。'
                    % ((_describe(item),) + achieved + (_describe(option),) + alternative))
        scores.append({'field': item[0], 'physical_key': item[1], **_score_report(achieved)})
    return {'validation_scope': 'all-legal-single-item-minimax-including-physical-selection',
            'legal_single_item_comparisons': comparisons, 'selected_scores': scores}



def _best_digit(masks, field, key):
    return min(range(10), key=lambda digit: (_score(_mask(masks, (field, key, digit))), digit))


def _best_keep(masks, field, allowed, width, revision):
    def legal(keep):
        if field == 'he3_keep' and revision in (SIX_V3_REVISION, SIX_REVISION):
            return 2 <= sum(int(digit) % 2 for digit in keep) <= 3
        return True
    values = [''.join(combo) for combo in combinations(allowed, width) if legal(''.join(combo))]
    return min(values, key=lambda keep: (_score(_mask(masks, (field, None, keep))), keep))


def _best_form(masks, field, palette, column, selected_positions):
    def legal(form):
        for position, kill in selected_positions.items():
            if form[POSITION_KEYS.index(position)] != reverse_attributes(kill)[column]:
                return False
        return True
    values = [''.join(form) for form in product(palette, repeat=4) if legal(''.join(form))]
    return min(values, key=lambda value: (_score(_mask(masks, (field, None, value))), value))


def optimal_six_response(input_doc, *, revision=SIX_REVISION):
    """Deterministic minimax optimum for the frozen 50-draw window.

    Used only when the provider response fails the frozen rule validation; it
    never rewrites a response that already passed.  It applies the same
    five-block lexicographic objective the validators check.
    """
    history = input_doc['history']
    moment = datetime.fromisoformat(history[-1]['opentime'])
    history = validate_history(history, input_doc['reference_period'], moment)
    masks = _failure_maps(history)
    position_rank = sorted(POSITION_KEYS, key=lambda key: (
        _score(_mask(masks, ('selected_positions', key, _best_digit(masks, 'selected_positions', key)))),
        POSITION_KEYS.index(key)))
    selected_positions = {key: _best_digit(masks, 'selected_positions', key) for key in position_rank[:2]}
    selected_positions = {key: selected_positions[key] for key in POSITION_KEYS if key in selected_positions}
    tail_rank = sorted(TAIL_POSITIONS, key=lambda key: (
        _score(_mask(masks, ('selected_tails', key, _best_digit(masks, 'selected_tails', key)))),
        tuple(TAIL_POSITIONS).index(key)))
    selected_tails = {key: _best_digit(masks, 'selected_tails', key) for key in tail_rank[:2]}
    selected_tails = {key: selected_tails[key] for key in TAIL_POSITIONS if key in selected_tails}
    response = {
        'selected_positions': selected_positions,
        'selected_tails': selected_tails,
        'he2_keep': _best_keep(masks, 'he2_keep', '02468', 4, revision),
        'he3_keep': _best_keep(masks, 'he3_keep', '0123456789', 5, revision),
        'reverse_parity': _best_form(masks, 'reverse_parity', '单双', 0, selected_positions),
        'reverse_size': _best_form(masks, 'reverse_size', '大小', 1, selected_positions),
        'reason': '本地分段稳定性最优回退：模型输出未通过冻结校验，按五段词典序计算'}
    if revision != SIX_REVISION:
        response.pop('reverse_parity', None); response.pop('reverse_size', None)
    return response

def selected_validator(input_doc, base_validator, *, kind, revision):
    """Return one response-only closure; old revisions retain their old contract."""
    if kind not in METHODS:
        raise ValueError('strategy-response-invalid-selected-kind')
    supported, active = ((SEVEN_REVISIONS, SEVEN_REVISION) if kind == 'seven'
                         else (SIX_REVISIONS, SIX_REVISION))
    if revision not in supported:
        raise ValueError('strategy-response-invalid-selected-revision')
    # Six-item v2/v3/v4 seals carry the fast-transport option context, so their
    # saved minimax context must be revalidated under its own rule version.
    # Seven-item behavior is unchanged.
    strict_six = kind == 'six' and revision in (SIX_V2_REVISION, SIX_V3_REVISION, SIX_REVISION)
    if revision != active and not strict_six:
        return base_validator
    frozen = deepcopy(input_doc)
    if (not isinstance(frozen, dict) or frozen.get('revision') != revision
            or not isinstance(frozen.get('strategy'), dict)
            or frozen['strategy'].get('strategy_id') != METHODS[kind]):
        raise ValueError('strategy-response-invalid-selected-input')
    history = frozen.get('history')
    try:
        moment = datetime.fromisoformat(history[-1]['opentime'])
        history = validate_history(history, frozen['reference_period'], moment)
    except (TypeError, ValueError, KeyError, IndexError):
        raise ValueError('strategy-response-invalid-selected-history') from None
    history_hash = _digest(history)
    if (frozen['history'] != history or frozen['strategy'].get('history_sha256') != history_hash
            or frozen['strategy'].get('reference_period') != frozen['reference_period']
            or frozen['strategy'].get('target_period') != frozen.get('target_period')):
        raise ValueError('strategy-response-invalid-selected-history')
    masks = _failure_maps(history)
    report = {'version': VERSION, 'method': METHODS[kind], 'checked': False,
              'history_sha256': history_hash}
    def validator(payload):
        report['checked'] = False
        result = (base_validator(payload, revision=revision) if kind == 'six'
                  else base_validator(payload))
        details = (_joint if kind == 'seven' else _minimax)(
            result, masks, revision=(revision if kind == 'six' else None))
        report.update(details, checked=True)
        return result
    validator.strategy_validation = report
    return validator
