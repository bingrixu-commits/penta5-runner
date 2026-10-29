"""Several verified local options, never a replacement for an AI response.

This prospective helper reads only the current framework's frozen input. It
does not call a provider, choose the final answer, write a seal, or alter the
historical v6 builder/validator. Every descent move strictly reduces the same
complete seven-condition failure union on the same real first-four 50 window.
"""
from copy import deepcopy
from hashlib import sha256
from itertools import combinations, product
import json
import random

from ai_verified_stats import next_period
from selected_framework_contract import (REVISION, build_input_doc,
                                          validate_response)
from selected_strategy_validation import (
    POSITION_KEYS, TAIL_POSITIONS, SEVEN_RETRY_ADDITION, _failure_maps,
    _items, _mask, _options, selected_validator, validation_report)

VERSION = 1
METHOD = 'selected-seven-request-options-v1'
SCOPE = 'single-item-local-optimum-not-global-optimum'
_REASON = '同一冻结50期的完整七项历史失败并集候选'
INSTRUCTION = (
    '以下是同一冻结50期生成的多个可核验完整方案，各方案仅通过单项局部必要条件，不宣称全局最优。'
    '由你自主比较并选取完整七项，也可自主输出任何通过相同原校验的其他完整合法JSON；'
    '备选不是强制答案，程序不会替你修补返回号码。历史并集分数不是未来命中率或保证。'
    + SEVEN_RETRY_ADDITION)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _identity(response):
    return _canonical({key: value for key, value in response.items() if key != 'reason'})


def _physical(response):
    return tuple(response['selected_positions']), tuple(response['selected_tails'])


def _parity_penalty(keep):
    return 0 if sum(int(value) % 2 for value in keep) in (2, 3) else 1


def _union(response, masks):
    value = 0
    for item in _items(response):
        value |= _mask(masks, item)
    return value


def _apply(response, item, option):
    updated = deepcopy(response)
    field, key, _ = item
    if key is None:
        updated[field] = option[2]
    else:
        del updated[field][key]
        updated[field][option[1]] = option[2]
    return validate_response(updated, revision=REVISION)


def _descend(response, masks, rng):
    """Return a legal terminal plus its strictly decreasing integer trace."""
    current = response
    trace = [_union(current, masks).bit_count()]
    while True:
        items = _items(current)
        chosen = [_mask(masks, item) for item in items]
        best_score, best_parity, choices = trace[-1], 2, []
        for index, item in enumerate(items):
            others = 0
            for other, mask in enumerate(chosen):
                if index != other:
                    others |= mask
            for option in _options(item, current, masks):
                score = (others | _mask(masks, option)).bit_count()
                if score >= trace[-1]:
                    continue
                parity = _parity_penalty(option[2] if option[0] == 'he3_keep'
                                         else current['he3_keep'])
                key = score, parity
                if key < (best_score, best_parity):
                    best_score, best_parity, choices = score, parity, [(item, option)]
                elif key == (best_score, best_parity):
                    choices.append((item, option))
        if not choices:
            return current, trace[-1], tuple(trace)
        current = _apply(current, *rng.choice(choices))
        score = _union(current, masks).bit_count()
        if score != best_score or not 0 <= score < trace[-1]:
            raise ValueError('selected-request-options-invalid-descent')
        trace.append(score)
        if len(trace) > 51:
            raise ValueError('selected-request-options-invalid-descent')


def _initial(positions, tails, masks, rng, exploratory):
    def pick(field, key):
        options = list(masks[(field, key)])
        if exploratory:
            return rng.choice(options)
        best = min(masks[(field, key)][value].bit_count() for value in options)
        options = [value for value in options if masks[(field, key)][value].bit_count() == best]
        if field == 'he3_keep':
            parity = min(_parity_penalty(value) for value in options)
            options = [value for value in options if _parity_penalty(value) == parity]
        return rng.choice(options)
    return validate_response({
        'selected_positions': {key: pick('selected_positions', key) for key in positions},
        'selected_tails': {key: pick('selected_tails', key) for key in tails},
        'he2_keep': pick('he2_keep', None), 'he3_keep': pick('he3_keep', None),
        'reason': _REASON}, revision=REVISION)


def build_request_options(input_doc, *, max_options=8):
    """Return 2..8 distinct, prospective, independently revalidated options.

    Input must equal the canonical current v6 builder byte-for-byte under its
    canonical JSON encoding. No external history or other model is consulted.
    The caller must bind the exact added context and actual transport prompt
    separately; input_sha256 here always identifies the unchanged original doc.
    """
    if type(max_options) is not int or not 2 <= max_options <= 8:
        raise ValueError('selected-request-options-invalid-limit')
    try:
        frozen = deepcopy(input_doc)
        if not isinstance(frozen, dict) or frozen.get('revision') != REVISION:
            raise ValueError('invalid-input')
        rebuilt = build_input_doc(frozen['reference_period'], frozen['history'], revision=REVISION)
        canonical = _canonical(frozen)
        if canonical != _canonical(rebuilt):
            raise ValueError('input-changed')
        validator = selected_validator(frozen, validate_response, kind='seven', revision=REVISION)
        masks = _failure_maps(frozen['history'])
    except (ValueError, TypeError, KeyError, IndexError, OverflowError, UnicodeError):
        raise ValueError('selected-request-options-invalid-input') from None
    input_hash = sha256(canonical.encode('utf8')).hexdigest()
    seed = int(input_hash, 16)
    physical = list(product(combinations(POSITION_KEYS, 3), combinations(tuple(TAIL_POSITIONS), 2)))
    pool = {}
    # Each of the 40 physical combinations starts once from minimum-count
    # candidates and once from a deterministic exploratory full-domain seed.
    for exploratory in (False, True):
        for index, (positions, tails) in enumerate(physical):
            rng = random.Random(seed + index + (len(physical) if exploratory else 0))
            start = _initial(positions, tails, masks, rng, exploratory)
            response, score, _trace = _descend(start, masks, rng)
            identity = _identity(response)
            if identity in pool:
                continue
            accepted = validator(response)
            report = validation_report(validator)
            if (accepted != response or not report or report.get('checked') is not True
                    or report.get('validation_scope') != SCOPE
                    or report.get('historical_union_failures') != score):
                raise ValueError('selected-request-options-invalid-terminal')
            pool[identity] = (score, response)
    ranked = sorted(pool.values(), key=lambda row: (row[0], _parity_penalty(row[1]['he3_keep']), _identity(row[1])))
    if len(ranked) < 2 or len({_physical(row[1]) for row in ranked}) < 2:
        raise ValueError('selected-request-options-insufficient-diversity')
    chosen, used = [], set()
    # Show distinct physical choices before filling any remaining slots with
    # further complete digit alternatives; all remain independently legal.
    for row in ranked:
        if _physical(row[1]) not in used:
            chosen.append(row)
            used.add(_physical(row[1]))
            if len(chosen) == max_options:
                break
    identities = {_identity(row[1]) for row in chosen}
    if len(chosen) < max_options:
        chosen.extend(row for row in ranked if _identity(row[1]) not in identities)
        chosen = chosen[:max_options]
    chosen.sort(key=lambda row: (row[0], _parity_penalty(row[1]['he3_keep']), _identity(row[1])))
    return {'version': VERSION, 'method': METHOD,
            'reference_period': frozen['reference_period'], 'target_period': frozen['target_period'],
            'history_sha256': frozen['strategy']['history_sha256'], 'input_sha256': input_hash,
            'validation_scope': SCOPE, 'instruction': INSTRUCTION,
            'options': [{'id': 'option-' + str(index + 1), 'response': deepcopy(response),
                         'historical_union_failures': score}
                        for index, (score, response) in enumerate(chosen)]}


def validate_request_context(input_doc, context):
    """Verify saved advice using one shared validator, never rerun the search.

    Publication validation separately reconstructs the original v6 input doc.
    This additional proof check binds that exact doc and validates every offered
    option against its same real history, without rebuilding the input twice.
    """
    try:
        frozen, saved = deepcopy(input_doc), deepcopy(context)
        if (not isinstance(frozen, dict) or type(frozen.get('version')) is not int
                or frozen['version'] != 1 or frozen.get('revision') != REVISION
                or not isinstance(saved, dict) or set(saved) != {
                    'version', 'method', 'reference_period', 'target_period', 'history_sha256',
                    'input_sha256', 'validation_scope', 'instruction', 'options'}
                or type(saved['version']) is not int or saved['version'] != VERSION
                or saved['method'] != METHOD or saved['validation_scope'] != SCOPE
                or saved['instruction'] != INSTRUCTION
                or saved['reference_period'] != frozen.get('reference_period')
                or saved['target_period'] != frozen.get('target_period')
                or saved['target_period'] != next_period(frozen.get('reference_period'))
                or saved['history_sha256'] != frozen.get('strategy', {}).get('history_sha256')
                or saved['input_sha256'] != sha256(_canonical(frozen).encode('utf8')).hexdigest()
                or not isinstance(saved['options'], list) or not 2 <= len(saved['options']) <= 8):
            raise ValueError('invalid-context')
        validator = selected_validator(frozen, validate_response, kind='seven', revision=REVISION)
        identities, physical = set(), set()
        for index, option in enumerate(saved['options']):
            if (not isinstance(option, dict) or set(option) != {'id', 'response', 'historical_union_failures'}
                    or option['id'] != 'option-' + str(index + 1)
                    or type(option['historical_union_failures']) is not int
                    or not 0 <= option['historical_union_failures'] <= 50):
                raise ValueError('invalid-option')
            response = validator(option['response'])
            report = validation_report(validator)
            if (_canonical(response) != _canonical(option['response']) or not report
                    or report.get('checked') is not True or report.get('validation_scope') != SCOPE
                    or report.get('historical_union_failures') != option['historical_union_failures']):
                raise ValueError('invalid-option-proof')
            identity = _identity(response)
            if identity in identities:
                raise ValueError('duplicate-option')
            identities.add(identity)
            physical.add(_physical(response))
        if len(physical) < 2:
            raise ValueError('insufficient-physical-diversity')
        return saved
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, UnicodeError):
        raise ValueError('selected-request-options-invalid-context') from None
