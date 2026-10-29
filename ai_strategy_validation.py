"""Prospective caller-specific strategy checks; never repairs provider numbers.

Only current independently archived request policies opt in. Historical seal
validators retain their original behavior, and shared inference clients keep
their schema-only default for other models and intentionally shared A/B input.
"""
from copy import deepcopy
import math
import re

from ai_prediction_guard import validate_framework_response, validate_killer_response

VERSION = 'strategy-response-v1'
REQUEST_POLICY = 'independent-strategy-v2'
KILLER_METHOD = 'conditional-transition-v2'
FRAMEWORK_METHOD = 'marginal-frequency-v2'
PRO4_METHOD = 'recent-weighted-v2'
PRO4_REVISION = 'pro-four-v4-isolated-weighted'


class StrategyValidationError(ValueError):
    """Machine code plus bounded, locally computed retry advice.

    Callers must generate feedback from validated frozen features, never from
    provider prose. The exception string deliberately contains only the code.
    """
    def __init__(self, code, feedback):
        if not isinstance(code, str) or not re.fullmatch(r'strategy-response-[a-z0-9-]{1,110}', code):
            raise ValueError('invalid-strategy-validation-code')
        if not isinstance(feedback, str) or not feedback or len(feedback) > 500:
            raise ValueError('invalid-strategy-validation-feedback')
        self.code = code
        self.feedback = feedback
        super().__init__(code)


def strategy_retry_feedback(error):
    """Never echo arbitrary exceptions or provider response text into retries."""
    if not isinstance(error, StrategyValidationError):
        return ''
    return error.feedback[:500]

KILLER_RETRY_INSTRUCTION = (
    '上次响应格式或独立策略目标未通过校验。必须只按本次冻结strategy：各定位/合尾选择'
    'smoothed_descriptive_share最小状态，同分任意；形态在所选类别内按排除数量选择最小的状态，'
    '同类别选2种时必须是该类别分数最小的2个不同形态（允许边界同分）。'
    '不得参考其他模型、改用总体冷码或自行补默认号。'
    '重新输出完整JSON：k1/k2/k3/k4各一个整数数组，he_all/he_q3/he_h3各一个整数，'
    'exclude_patterns为2个不同四位单双或大小形态，reason为简短说明。')
FRAMEWORK_RETRY_INSTRUCTION = (
    '上次响应格式或独立策略目标未通过校验。只按本次冻结strategy：定位、合尾、单双和大小排除'
    '取对应counts最小状态；pos_kills各位置取counts最小的2个不同数字（允许边界同分）；'
    'he2_keep/he3_keep取各自keep_coverage历史覆盖期数最大的合法集合，同分任意。'
    '不得改用别的模型或策略，不得补位或默认凑数。重新给出全部原字段的完整JSON；'
    'he2_keep恰好保留02468中的4码，he3_keep恰好保留0至9中的6码，保留前导0。')
PRO4_RETRY_ADDITION = (
    '必须遵守本次冻结近期指数加权策略：k1/k2/k3/k4分别选对应q/b/s/g的weighted_counts最小数字；'
    '只允许合法同分数字，不以近10期/前40期计数或其他模型破同分。不得自动补号或为了异号换号。')


def _fail(code, feedback='冻结策略证据未通过校验；不能补号或改用其他策略，请依据原冻结输入重新核查。'):
    raise StrategyValidationError('strategy-response-' + code, feedback)


def _state_text(value):
    if type(value) is int and 0 <= value <= 9:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r'[单双]{4}|[大小]{4}', value):
        return value
    _fail('invalid-frozen-state')


def _topk(feature, field, selected, *, states=None, code):
    if not isinstance(feature, dict):
        _fail('invalid-frozen-features')
    values = feature.get(field)
    states = feature.get('states') if states is None else states
    if (not isinstance(values, list) or not isinstance(states, list) or len(values) != len(states)
            or not states or any(type(v) is not int and not isinstance(v, str) for v in states)
            or len(set(states)) != len(states)
            or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in values)
            or not isinstance(selected, list) or not selected or len(selected) > len(states)
            or len(set(selected)) != len(selected) or any(value not in states for value in selected)):
        _fail('invalid-frozen-features')
    scores = [values[states.index(value)] for value in selected]
    # Comparing ordered score vectors also requires every strictly better state
    # to be included; a threshold-only check would wrongly allow omitting it.
    if any(not math.isclose(actual, expected, rel_tol=0.0, abs_tol=1e-9)
           for actual, expected in zip(sorted(scores), sorted(values)[:len(scores)])):
        cutoff = sorted(values)[len(scores) - 1]
        required = [_state_text(state) for state, score in zip(states, values)
                    if score < cutoff and not math.isclose(score, cutoff, rel_tol=0.0, abs_tol=1e-9)]
        tied = [_state_text(state) for state, score in zip(states, values)
                if math.isclose(score, cutoff, rel_tol=0.0, abs_tol=1e-9)]
        feedback = (f'字段{code}违反冻结{field}最小化目标：需选{len(scores)}项；'
                    f'严格低于边界{cutoff:g}的必选集合为[{",".join(required)}]；'
                    f'边界同分允许集合为[{",".join(tied)}]，从中选满原数量。'
                    '请自行按冻结策略重选，不得补默认号或改变其他规则。')
        _fail(code + '-not-optimal', feedback)


def _keep(feature, selected, code):
    if (not isinstance(feature, list) or not feature
            or any(not isinstance(row, list) or len(row) != 2 or not isinstance(row[0], str)
                   or not re.fullmatch(r'[0-9]{4}|[0-9]{6}', row[0])
                   or type(row[1]) is not int or not 0 <= row[1] <= 50 for row in feature)
            or len({row[0] for row in feature}) != len(feature)):
        _fail('invalid-frozen-coverage')
    scores = dict(feature)
    if selected not in scores or scores[selected] != max(scores.values()):
        best = max(scores.values())
        ties = [value for value, score in feature if score == best]
        # Coverage can have hundreds of ties. Keep feedback small and refer to
        # the complete frozen feature for all remaining equally valid choices.
        feedback = (f'字段{code}违反冻结keep_coverage最大覆盖目标：最大覆盖{best}期，'
                    f'允许同分集合共{len(ties)}个，示例[{",".join(ties[:12])}]；'
                    '完整允许集合以原冻结keep_coverage中该最大值为准。'
                    '请自行重选原数量保留码，不得把保留集当排除集或补默认号。')
        _fail(code + '-not-optimal', feedback)


def _closure(method, base_validator, strategy, check):
    frozen = deepcopy(strategy)
    if not isinstance(frozen, dict) or frozen.get('strategy_id') != method or not isinstance(frozen.get('features'), dict):
        _fail('invalid-frozen-method')
    report = {'version': VERSION, 'method': method, 'checked': False}
    def validator(payload):
        report['checked'] = False
        result = base_validator(payload)
        check(result, frozen['features'])
        report['checked'] = True
        return result
    validator.strategy_validation = report
    return validator


def validation_report(validator):
    report = getattr(validator, 'strategy_validation', None)
    return deepcopy(report) if isinstance(report, dict) and report.get('checked') is True else None


def _killer(result, features):
    for slot, field in zip(('q', 'b', 's', 'g'), ('k1', 'k2', 'k3', 'k4')):
        _topk(features.get(slot), 'smoothed_descriptive_share', result[field], code='killer-' + slot)
    for slot, field in (('all', 'he_all'), ('qbs', 'he_q3'), ('bsg', 'he_h3')):
        _topk(features.get(slot), 'smoothed_descriptive_share', [result[field]], code='killer-' + slot)
    for family in ('parity', 'size'):
        chosen = [value for value in result['exclude_patterns']
                  if (set(value) <= set('单双') if family == 'parity' else set(value) <= set('大小'))]
        if chosen:
            _topk(features.get(family), 'smoothed_descriptive_share', chosen, code='killer-' + family)


def _framework(result, features):
    for slot, field in (('q', 'q_kill1'), ('b', 'b_kill1'), ('qbs', 'he_q3_kill'),
                        ('bsg', 'he_h3_kill'), ('parity', 'pattern_ds'), ('size', 'pattern_dx')):
        _topk(features.get(slot), 'counts', [result[field]], code='framework-' + slot)
    for slot in ('q', 'b', 's', 'g'):
        _topk(features.get(slot), 'counts', result['pos_kills'][slot], code='framework-pos2-' + slot)
    coverage = features.get('keep_coverage')
    if not isinstance(coverage, dict):
        _fail('invalid-frozen-coverage')
    for field in ('he2_keep', 'he3_keep'):
        _keep(coverage.get(field), result[field], 'framework-' + field)


def _audit_strategy(audit, method):
    if not isinstance(audit, dict) or audit.get('request_policy') != REQUEST_POLICY:
        return None
    body = audit.get('body')
    strategy = body.get('strategy') if isinstance(body, dict) else None
    if not isinstance(strategy, dict) or strategy.get('strategy_id') != method:
        _fail('invalid-frozen-method')
    return strategy


def classic_killer_validator(audit):
    strategy = _audit_strategy(audit, KILLER_METHOD)
    return validate_killer_response if strategy is None else _closure(KILLER_METHOD, validate_killer_response, strategy, _killer)


def classic_framework_validator(audit):
    strategy = _audit_strategy(audit, FRAMEWORK_METHOD)
    return validate_framework_response if strategy is None else _closure(FRAMEWORK_METHOD, validate_framework_response, strategy, _framework)


def pro4_validator(strategy, base_validator, *, revision):
    if revision != PRO4_REVISION:
        return base_validator
    def check(result, features):
        for slot, field in zip(('q', 'b', 's', 'g'), ('k1', 'k2', 'k3', 'k4')):
            _topk(features.get(slot), 'weighted_counts', result[field], states=list(range(10)), code='pro4-' + slot)
    return _closure(PRO4_METHOD, base_validator, strategy, check)
