"""Compact independent six-item minimax advice; AI alone returns the answer.

Only this model's frozen first-four 50-draw input is read. All comparisons use
five fixed blocks and exact rational variance, never seven-item failure union.
The candidate list is advice, not a response whitelist or local fallback.
"""
from copy import deepcopy
from hashlib import sha256
from itertools import combinations, product
import json
import random

from ai_verified_stats import next_period
from selected_six_contract import REVISION, build_input_doc, validate_response
from selected_strategy_validation import (POSITION_KEYS, TAIL_POSITIONS, SIX_RETRY_ADDITION,
    _failure_maps, _score, selected_validator, validation_report)

VERSION = 1
METHOD = 'selected-six-minimax-request-options-v1'
SCOPE = 'all-legal-single-item-minimax-including-physical-selection'
INSTRUCTION = (
    '独立六项仅按固定五个10期区块比较每个单项：(最大单段失败次数,方差分子/25,总失败)词典序最小。'
    '不使用七项失败并集、近期权重、相邻转移或任何其他模型结果。以下候选供AI自主比较；'
    '可输出其中任意方案，也可自主输出任何通过同一原始六项校验的其他完整JSON，程序不补号。'
    '单项样本内稳定性不是未来命中率保证。' + SIX_RETRY_ADDITION)
WIRE_PREFIX = (
    '你是独立六项Pro。只预测context.target_period，参考context.reference_period。\n'
    'history_codes为旧到新连续50期的千百十个，前导0有效，第五球不参与。'
    '严格固定分为五段各10期；score=[最大单段失败,方差分子,总失败]，方差=分子/25。'
    '按score词典序选每项，必须考虑未选物理项；position_pairs/tail_pairs为合法物理对，'
    '各key的digits为本key同分最小排除码。保留集候选也按相同五段失败规则比较。\n'
    'options.scores按选中定位、选中合尾、二数合、三数合顺序列同一score三元组。\n'
    '输出恰好五字段严格JSON：selected_positions从q/b/s/g选2个不同键、各整数排1码；'
    'selected_tails从all/qbs/qbg/qsg/bsg选2个不同键、各整数排1码。'
    'all是四位和%10，qbs/qbg/qsg/bsg分别是对应三位和%10；不等于排除码才成功。'
    'he2_keep从02468选4个不同数字字符串，任意两位六组合的任一合尾落入即命中；'
    'he3_keep从0123456789选5个不同数字字符串，任意三位四组合的任一合尾落入即命中，'
    '尽量2奇3偶或3奇2偶，仅为偏好。整组六项必须全部成功。reason用60字内简短中文，'
    '只输出完整JSON，不输出思考过程。候选不是强制答案，无默认补位、不强行为异号换码。\n'
    '【独立六项冻结输入与minimax候选context】\n')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _hash(value):
    return sha256(_canonical(value).encode('utf-8')).hexdigest()


def _identity(response):
    return _canonical({key: value for key, value in response.items() if key != 'reason'})


def _report_scores(report):
    return [[row['worst_block_failures'], row['variance_numerator'], row['total_failures']]
            for row in report['selected_scores']]


def _tables(frozen, masks):
    candidates, pairs, domains = {}, {}, {}
    for field, keys in (('selected_positions', POSITION_KEYS), ('selected_tails', tuple(TAIL_POSITIONS))):
        rows = []
        best = {}
        for key in keys:
            scores = {digit: _score(mask) for digit, mask in masks[(field, key)].items()}
            minimum = min(scores.values())
            best[key] = minimum
            rows.append({'key': key, 'digits': [digit for digit, score in scores.items() if score == minimum],
                         'score': list(minimum)})
        legal_pairs = [list(pair) for pair in combinations(keys, 2)
                       if max(best[key] for key in pair) <= min(best[key] for key in keys if key not in pair)]
        candidates[field], pairs[field] = rows, legal_pairs
        digits = {row['key']: row['digits'] for row in rows}
        domains[field] = [{a: da, b: db} for a, b in legal_pairs
                          for da, db in product(digits[a], digits[b])]
    for field in ('he2_keep', 'he3_keep'):
        scores = {keep: _score(mask) for keep, mask in masks[(field, None)].items()}
        minimum = min(scores.values())
        candidates[field] = [{'keep': keep, 'score': list(score)}
                             for keep, score in scores.items() if score == minimum]
        domains[field] = [row['keep'] for row in candidates[field]]
    return candidates, pairs, domains


def _frozen(input_doc, *, rebuild):
    frozen = deepcopy(input_doc)
    if not isinstance(frozen, dict) or frozen.get('revision') != REVISION:
        raise ValueError('selected-six-request-options-invalid-input')
    if rebuild:
        original = build_input_doc(frozen['reference_period'], frozen['history'], revision=REVISION)
        if _canonical(frozen) != _canonical(original):
            raise ValueError('selected-six-request-options-invalid-input')
    validator = selected_validator(frozen, validate_response, kind='six', revision=REVISION)
    return frozen, validator, _failure_maps(frozen['history'])


def _base_context(frozen, candidates, pairs):
    return {'version': VERSION, 'method': METHOD, 'strategy_id': 'block-stability-v2',
            'reference_period': frozen['reference_period'], 'target_period': frozen['target_period'],
            'history_sha256': frozen['strategy']['history_sha256'], 'input_sha256': _hash(frozen),
            'history_codes': [''.join(map(str, row['numbers'])) for row in frozen['history']],
            'block_size': 10, 'block_count': 5, 'variance_denominator': 25,
            'validation_scope': SCOPE, 'instruction': INSTRUCTION,
            'candidates': candidates, 'position_pairs': pairs['selected_positions'],
            'tail_pairs': pairs['selected_tails']}


def build_request_options(input_doc, *, max_options=8):
    """Return 1..8 distinct independently valid minimax advice options.

    A genuinely unique legal domain stays unique; physical diversity is never
    manufactured. Constructing these options never selects the provider result.
    """
    if type(max_options) is not int or not 1 <= max_options <= 8:
        raise ValueError('selected-six-request-options-invalid-limit')
    try:
        frozen, validator, masks = _frozen(input_doc, rebuild=True)
        candidates, pairs, domains = _tables(frozen, masks)
        context = _base_context(frozen, candidates, pairs)
        fields = ('selected_positions', 'selected_tails', 'he2_keep', 'he3_keep')
        # Balanced parity is a tie preference only. All legal ties remain in
        # context and any original-valid provider answer remains acceptable.
        preferred = [keep for keep in domains['he3_keep'] if sum(int(d) % 2 for d in keep) in (2, 3)]
        if preferred:
            domains['he3_keep'] = preferred + [keep for keep in domains['he3_keep'] if keep not in preferred]
        sizes = [len(domains[field]) for field in fields]
        total = 1
        for size in sizes:
            total *= size
        if total < 1:
            raise ValueError('empty-domain')
        rng = random.Random(int(context['input_sha256'], 16))
        indexes = rng.sample(range(total), min(max_options, total))
        options = []
        for ordinal in indexes:
            response = {}
            for field, size in reversed(list(zip(fields, sizes))):
                response[field] = deepcopy(domains[field][ordinal % size])
                ordinal //= size
            response['reason'] = '同一冻结50期五段minimax同分合法候选'
            response = validator(response)
            report = validation_report(validator)
            if not report or report.get('checked') is not True or report.get('validation_scope') != SCOPE:
                raise ValueError('unverified-option')
            options.append({'id': 'option-' + str(len(options) + 1), 'response': response,
                            'scores': _report_scores(report)})
        context['options'] = options
        return context
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, UnicodeError):
        raise ValueError('selected-six-request-options-invalid-input') from None


def validate_request_context(input_doc, context):
    """Recheck fixed features and offered scores without sampling/search again."""
    try:
        frozen, validator, masks = _frozen(input_doc, rebuild=False)
        saved = deepcopy(context)
        candidates, pairs, _ = _tables(frozen, masks)
        base = _base_context(frozen, candidates, pairs)
        if (not isinstance(saved, dict) or set(saved) != set(base) | {'options'}
                or _canonical({key: value for key, value in saved.items() if key != 'options'}) != _canonical(base)
                or not isinstance(saved['options'], list) or not 1 <= len(saved['options']) <= 8
                or saved['target_period'] != next_period(saved['reference_period'])):
            raise ValueError('invalid-context')
        identities = set()
        for index, option in enumerate(saved['options']):
            if (not isinstance(option, dict) or set(option) != {'id', 'response', 'scores'}
                    or option['id'] != 'option-' + str(index + 1)):
                raise ValueError('invalid-option')
            response = validator(option['response'])
            report = validation_report(validator)
            if (not report or report.get('checked') is not True
                    or _canonical(response) != _canonical(option['response'])
                    or _canonical(option['scores']) != _canonical(_report_scores(report))):
                raise ValueError('invalid-option-score')
            identity = _identity(response)
            if identity in identities:
                raise ValueError('duplicate-option')
            identities.add(identity)
        return saved
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, OverflowError, UnicodeError):
        raise ValueError('selected-six-request-options-invalid-context') from None


def wire_prompt(context):
    return WIRE_PREFIX + _canonical(context)
