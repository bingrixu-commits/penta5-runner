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
from selected_six_contract import (REVISION, V3_REVISION, V4_REVISION, FORM_REVISIONS, SUPPORTED_REVISIONS,
                                   build_input_doc, reverse_attributes, validate_response)
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
INSTRUCTION_REVERSE = (
    '独立八项仅按固定五个10期区块比较每个单项：(最大单段失败次数,方差分子/25,总失败)词典序最小。'
    '不使用七项失败并集、近期权重、相邻转移或任何其他模型结果。以下候选供AI自主比较；'
    '可输出其中任意方案，也可自主输出任何通过同一原始八项校验的其他完整JSON，程序不补号。'
    'he3_keep必须2奇3偶或3奇2偶（5奇0偶、4奇1偶、1奇4偶、0奇5偶均不合格）。'
    '六项自选之外，程序按两个已选定位的排除码自动推导单双反向、大小反向两项；'
    '单项样本内稳定性不是未来命中率保证。' + SIX_RETRY_ADDITION)
WIRE_PREFIX_REVERSE = (
    '你是独立八项Pro（六项自选+两项自动反向形态）。只预测context.target_period，参考context.reference_period。\n'
    'history_codes为旧到新连续50期的千百十个，前导0有效，第五球不参与。'
    '严格固定分为五段各10期；score=[最大单段失败,方差分子,总失败]，方差=分子/25。'
    '按score词典序选每项，必须考虑未选物理项；position_pairs/tail_pairs为合法物理对，'
    '各key的digits为本key同分最小排除码。保留集候选也按相同五段失败规则比较。\n'
    'options.scores按选中定位、选中合尾、二数合、三数合顺序列同一score三元组。\n'
    '输出恰好五字段严格JSON：selected_positions从q/b/s/g选2个不同键、各整数排1码；'
    'selected_tails从all/qbs/qbg/qsg/bsg选2个不同键、各整数排1码。'
    'all是四位和%10，qbs/qbg/qsg/bsg分别是对应三位和%10；不等于排除码才成功。'
    'he2_keep从02468选4个不同数字字符串，任意两位六组合的任一合尾落入即命中；'
    'he3_keep从0123456789选5个不同数字字符串，必须2奇3偶或3奇2偶'
    '（5奇0偶、4奇1偶、1奇4偶、0奇5偶均不合格），任意三位四组合的任一合尾落入即命中。'
    '程序会按两个已选定位的排除码自动推导单双反向形态与大小反向形态两项（取排除码形态的反面），'
    '连同六项自选共八项，整组八项全部成功才命中；你只需输出六项自选字段，不得为凑反向形态改动自选项。'
    'reason用60字内简短中文，只输出完整JSON，不输出思考过程。候选不是强制答案，无默认补位、不强行为异号换码。\n'
    '【独立八项冻结输入与minimax候选context】\n')

INSTRUCTION_FORMS = (
    '独立八项按固定五个10期区块比较每个单项：(最大单段失败次数,方差分子/25,总失败)词典序最小。'
    '不使用七项失败并集、近期权重、相邻转移或任何其他模型结果。以下候选供AI自主比较；'
    '可输出其中任意方案，也可自主输出任何通过同一原始八项校验的其他完整JSON，程序不补号。'
    'he3_keep必须2奇3偶或3奇2偶。第7、8项为四位单双/大小形态，已在被选定位上与排除码反面一致，'
    '其余位置在给定形态分数中按同一词典序取最小；形态为“反向排除”项，目标期实际形态不等于它才成功。'
    '单项样本内稳定性不是未来命中率保证。' + SIX_RETRY_ADDITION)
WIRE_PREFIX_FORMS_V1 = (
    '你是独立八项Pro（六项自选+两项四位形态排除）。只预测context.target_period，参考context.reference_period。\n'
    'history_codes为旧到新连续50期的千百十个，前导0有效，第五球不参与。'
    '严格固定分为五段各10期；score=[最大单段失败,方差分子,总失败]，方差=分子/25。'
    '按score词典序选每项，必须考虑未选物理项；position_pairs/tail_pairs为合法物理对，'
    '各key的digits为本key同分最小排除码。保留集候选与形态候选也按相同五段失败规则比较。\n'
    'options.scores按选中定位、选中合尾、二数合、三数合、单双形态、大小形态顺序列同一score三元组。\n'
    '输出恰好七字段严格JSON：selected_positions从q/b/s/g选2个不同键、各整数排1码；'
    'selected_tails从all/qbs/qbg/qsg/bsg选2个不同键、各整数排1码。'
    'all是四位和%10，qbs/qbg/qsg/bsg分别是对应三位和%10；不等于排除码才成功。'
    'he2_keep从02468选4个不同数字字符串，任意两位六组合的任一合尾落入即命中；'
    'he3_keep从0123456789选5个不同数字字符串，必须2奇3偶或3奇2偶，任意三位四组合的任一合尾落入即命中。'
    'reverse_parity为按千百十个顺序的四位单双形态，reverse_size为四位大小形态；'
    '两个已选定位的对应字符必须等于排除码形态的反面（百排9→百位=双/小；个排6→个位=单/小），'
    '其余位置在 candidates.reverse_parity / candidates.reverse_size 的分数表中按同一词典序取最小。'
    '这两项是反向排除项：目标期实际四位形态不等于该形态才算命中，攻击语会以「除」加入该形态。'
    '整组八项必须全部成功。reason用60字内简短中文，只输出完整JSON，不输出思考过程。'
    '候选不是强制答案，无默认补位、不强行为异号换码。\n'
    '【独立八项冻结输入与minimax候选context】\n')
WIRE_PREFIX_FORMS = (
    '你是独立八项Pro（六项自选+两项四位形态排除）。只预测context.target_period，参考context.reference_period。\n'
    'history_codes为旧到新连续50期的千百十个，前导0有效，第五球不参与。\n'
    '严格固定分为五段各10期；score=[最大单段失败,方差分子,总失败]，方差=分子/25。\n'
    '按score词典序选每项，必须考虑未选物理项；position_pairs/tail_pairs为合法物理对，'
    '各key的digits为本key同分最小排除码。保留集候选与形态候选也按相同五段失败规则比较。\n'
    'options.scores按选中定位、选中合尾、二数合、三数合、单双形态、大小形态顺序列同一score三元组。\n'
    '【首答硬性要求】options中每一项都已独立通过同一原始八项校验；请直接从中任选一项，'
    '原样输出其response的七个字段（selected_positions、selected_tails、he2_keep、he3_keep、'
    'reverse_parity、reverse_size、reason，reason可改写）。若选择自选，必须同时满足：'
    '两个定位键互异、两个合尾键互异、he3_keep恰好5个数字且2奇3偶或3奇2偶、'
    'reverse_parity/reverse_size各4字符且在被选定位上分别等于排除码的单双/大小反面、'
    '其余位取对应形态分数表中的最小形态。任一项不满足会被本地校验拒绝并重答。\n'
    '输出恰好七字段严格JSON：selected_positions从q/b/s/g选2个不同键、各整数排1码；'
    'selected_tails从all/qbs/qbg/qsg/bsg选2个不同键、各整数排1码。'
    'all是四位和%10，qbs/qbg/qsg/bsg分别是对应三位和%10；不等于排除码才成功。\n'
    'he2_keep从02468选4个不同数字字符串，任意两位六组合的任一合尾落入即命中；'
    'he3_keep从0123456789选5个不同数字字符串，必须2奇3偶或3奇2偶，任意三位四组合的任一合尾落入即命中。\n'
    'reverse_parity为按千百十个顺序的四位单双形态，reverse_size为四位大小形态；'
    '两个已选定位的对应字符必须等于排除码形态的反面（百排9→百位=双/小；个排6→个位=单/小），'
    '其余位置在candidates.reverse_parity/candidates.reverse_size的分数表中按同一词典序取最小。'
    '这两项是反向排除项：目标期实际四位形态不等于该形态才算命中，攻击语会以「除」加入该形态。\n'
    '整组八项必须全部成功。reason用60字内简短中文，只输出完整JSON，不输出思考过程。'
    '禁止补位、默认凑数或为凑形态改选码；首答优先照抄一个option的response。\n'
    '【独立八项冻结输入与minimax候选context】\n')

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
        allowed = list(masks[(field, None)])
        if field == 'he3_keep' and frozen['revision'] in (V3_REVISION, V4_REVISION, REVISION):
            # Current rules: only 2:3 balanced keep sets are legal candidates.
            allowed = [keep for keep in allowed if 2 <= sum(int(digit) % 2 for digit in keep) <= 3]
        scores = {keep: _score(masks[(field, None)][keep]) for keep in allowed}
        minimum = min(scores.values())
        candidates[field] = [{'keep': keep, 'score': list(score)}
                             for keep, score in scores.items() if score == minimum]
        domains[field] = [row['keep'] for row in candidates[field]]
    if frozen['revision'] in FORM_REVISIONS:
        # Four-position forms are constrained by the selected positions, so the
        # complete score table (not just points) is offered for AI comparison.
        for field in ('reverse_parity', 'reverse_size'):
            table = [{'form': form, 'score': list(_score(mask))}
                     for form, mask in masks[(field, None)].items()]
            table.sort(key=lambda row: (row['score'], row['form']))
            candidates[field] = table
            domains[field] = [row['form'] for row in table]
    return candidates, pairs, domains


def _frozen(input_doc, *, rebuild):
    frozen = deepcopy(input_doc)
    if not isinstance(frozen, dict) or frozen.get('revision') not in SUPPORTED_REVISIONS:
        raise ValueError('selected-six-request-options-invalid-input')
    revision = frozen['revision']
    if rebuild:
        original = build_input_doc(frozen['reference_period'], frozen['history'], revision=revision, panel_snapshot=frozen.get('panel_context'))
        if _canonical(frozen) != _canonical(original):
            raise ValueError('selected-six-request-options-invalid-input')
    validator = selected_validator(frozen, validate_response, kind='six', revision=revision)
    return frozen, revision, validator, _failure_maps(frozen['history'])


def _base_context(frozen, candidates, pairs):
    return {'version': VERSION, 'method': METHOD, 'strategy_id': 'block-stability-v2',
            'reference_period': frozen['reference_period'], 'target_period': frozen['target_period'],
            'history_sha256': frozen['strategy']['history_sha256'], 'input_sha256': _hash(frozen),
            'history_codes': [''.join(map(str, row['numbers'])) for row in frozen['history']],
            'block_size': 10, 'block_count': 5, 'variance_denominator': 25,
            'validation_scope': SCOPE,
            'instruction': (INSTRUCTION_FORMS if frozen['revision'] in FORM_REVISIONS else
                            INSTRUCTION_REVERSE if frozen['revision'] == V3_REVISION else INSTRUCTION),
            **({'panel_context': frozen['panel_context']} if frozen.get('panel_context') is not None else {}),
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
        frozen, revision, validator, masks = _frozen(input_doc, rebuild=True)
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
            if revision in FORM_REVISIONS:
                for field in ('reverse_parity', 'reverse_size'):
                    column = 0 if field == 'reverse_parity' else 1
                    legal = list(masks[(field, None)])
                    for position, kill in response['selected_positions'].items():
                        expected = reverse_attributes(kill)[column]
                        index = POSITION_KEYS.index(position)
                        legal = [value for value in legal if value[index] == expected]
                    response[field] = min(legal, key=lambda form: (_score(masks[(field, None)][form]), form))
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
        frozen, revision, validator, masks = _frozen(input_doc, rebuild=False)
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


def wire_prompt(context, *, revision=None):
    if revision in FORM_REVISIONS:
        return WIRE_PREFIX_FORMS + _canonical(context)
    if revision == V3_REVISION:
        return WIRE_PREFIX_REVERSE + _canonical(context)
    return WIRE_PREFIX + _canonical(context)


def wire_prompt_legacy(context, *, revision=None):
    """Validation-only prefix for pre-2026-10-31 v1 transport evidence."""
    if revision in FORM_REVISIONS:
        return WIRE_PREFIX_FORMS_V1 + _canonical(context)
    return wire_prompt(context, revision=revision)
