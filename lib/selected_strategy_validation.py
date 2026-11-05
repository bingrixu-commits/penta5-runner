"""Prospective selected-strategy checks against one frozen real history.

The validator never chooses or changes returned numbers.  Seven-item checking
is a necessary single-item local condition, deliberately not a claim of global
optimality.  Historical contracts and publication reconstruction do not call
this module.
"""
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
from itertools import combinations, product
import json

from ai_strategy_validation import StrategyValidationError, validation_report
from selected_framework_contract import (REVISION as SEVEN_REVISION,
                                          SUPPORTED_REVISIONS as SEVEN_REVISIONS)
from stat50_compare import validate_history

VERSION = 'strategy-response-v1'
POSITION_KEYS = ('q', 'b', 's', 'g')
TAIL_POSITIONS = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
                  'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}
METHODS = {'seven': 'joint-overlap-v3-three-positions'}
SEVEN_RETRY_ADDITION = (
    '还必须核对冻结策略的完整七项失败并集：固定其余六项时，任何一个合法单项目替换'
    '（包括改为未选物理位置或合尾、改排除数字、改任一完整保留集）都不能使完整并集更小。'
    '这是单项局部必要条件，不代表全局最优；须自主重选完整合法JSON，不得补号、抄其他模型或为异号换号。')


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


def _options(item, response, masks):
    field, key, _ = item
    if key is None:
        return [(field, None, value) for value in masks[(field, None)]]
    keys = POSITION_KEYS if field == 'selected_positions' else tuple(TAIL_POSITIONS)
    occupied = set(response[field]) - {key}
    return [(field, candidate, value) for candidate in keys if candidate not in occupied
            for value in masks[(field, candidate)]]


def _mask(masks, item):
    return masks[item[:2]][item[2]]


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


def selected_validator(input_doc, base_validator, *, kind, revision):
    """Return one response-only closure; old revisions retain their old contract."""
    if kind != 'seven':
        raise ValueError('strategy-response-invalid-selected-kind')
    if revision not in SEVEN_REVISIONS:
        raise ValueError('strategy-response-invalid-selected-revision')
    if revision != SEVEN_REVISION:
        return base_validator
    frozen = deepcopy(input_doc)
    if (not isinstance(frozen, dict) or frozen.get('revision') != revision
            or not isinstance(frozen.get('strategy'), dict)
            or frozen['strategy'].get('strategy_id') != METHODS['seven']):
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
    report = {'version': VERSION, 'method': METHODS['seven'], 'checked': False,
              'history_sha256': history_hash}
    def validator(payload):
        report['checked'] = False
        result = base_validator(payload)
        report.update(_joint(result, masks), checked=True)
        return result
    validator.strategy_validation = report
    return validator
