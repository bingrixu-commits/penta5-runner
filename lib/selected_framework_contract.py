"""Versioned six/seven-item contract and immutable 50-draw request input.

This module calculates descriptive features and verified outcomes only. It never
chooses an exclusion, invokes a provider, reads files, or repairs missing digits.
"""
from copy import deepcopy
from datetime import datetime
from itertools import combinations
import json

from ai_verified_stats import next_period
from position_input_features import build_position_features
from stat50_compare import validate_history
from strategy_features import (build_strategy, SELECTED_STRATEGY,
                               ISOLATED_SELECTED_STRATEGY, LEGACY_SELECTED_STRATEGY)


LEGACY_REVISION = 'selected-six-v1'
FIVE_REVISION = 'selected-six-v2-he3-five'
SEVEN_REVISION = 'selected-seven-v3-flex-extra'
JOINT_REVISION = 'selected-seven-v4-joint-overlap'
ISOLATED_REVISION = 'selected-seven-v5-isolated-joint'
REVISION = 'selected-seven-v6-three-positions'
FLEX_EXTRA_REVISIONS = frozenset((SEVEN_REVISION, JOINT_REVISION, ISOLATED_REVISION))
SEVEN_REVISIONS = FLEX_EXTRA_REVISIONS | {REVISION}
SUPPORTED_REVISIONS = frozenset((LEGACY_REVISION, FIVE_REVISION, SEVEN_REVISION,
                                 JOINT_REVISION, ISOLATED_REVISION, REVISION))
# The append-only ledger is a stable container. Individual immutable records
# carry the rule revision; changing a rule must never rewrite its old container.
LEDGER_REVISION = LEGACY_REVISION
MODEL = 'deepseek-v4-pro'
MARKER = 'SELECTED_FRAMEWORK'
POSITION_KEYS = ('q', 'b', 's', 'g')
POSITION_LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
TAIL_POSITIONS = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
                  'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}
TAIL_LABELS = {'all': '千百十个合尾', 'qbs': '千百十合尾', 'qbg': '千百个合尾',
               'qsg': '千十个合尾', 'bsg': '百十个合尾'}
BASE_METRIC_KEYS = (tuple('position_' + key for key in POSITION_KEYS)
               + tuple('tail_' + key for key in TAIL_POSITIONS)
               + ('he2_keep', 'he3_keep'))
EXTRA_METRIC_KEYS = tuple('extra_' + key for key in BASE_METRIC_KEYS
                          if key.startswith(('position_', 'tail_')))
METRIC_KEYS = BASE_METRIC_KEYS + EXTRA_METRIC_KEYS
RESPONSE_KEYS = frozenset(('selected_positions', 'selected_tails', 'he2_keep', 'he3_keep', 'reason'))


def _selected(values, allowed, label, count=2):
    if (not isinstance(values, dict) or len(values) != count
            or any(key not in allowed for key in values)
            or any(type(digit) is not int or not 0 <= digit <= 9 for digit in values.values())):
        raise ValueError('invalid-selected-' + label)
    return {key: values[key] for key in allowed if key in values}


def _keep(value, count, allowed):
    if (not isinstance(value, str) or len(value) != count or len(set(value)) != count
            or any(digit not in allowed for digit in value)):
        raise ValueError('invalid-selected-framework-keep-set')
    return ''.join(sorted(value))


def he3_count(revision=REVISION):
    """Return the exact retained count for a known, immutable rule revision."""
    if not isinstance(revision, str) or revision not in SUPPORTED_REVISIONS:
        raise ValueError('unknown-selected-framework-revision')
    return 6 if revision == LEGACY_REVISION else 5


def item_count(revision=REVISION):
    """Return the formal AND-group size without reinterpreting older records."""
    he3_count(revision)
    return 7 if revision in SEVEN_REVISIONS else 6


def _extra(value, positions, tails):
    if (not isinstance(value, dict) or set(value) != {'kind', 'key', 'digit'}
            or not isinstance(value['kind'], str) or not isinstance(value['key'], str)
            or type(value['digit']) is not int or not 0 <= value['digit'] <= 9):
        raise ValueError('invalid-selected-framework-extra-exclusion')
    kind, key = value['kind'], value['key']
    allowed, selected = ((POSITION_KEYS, positions) if kind == 'position' else
                         (TAIL_POSITIONS, tails) if kind == 'tail' else ((), {}))
    if key not in allowed or key in selected:
        raise ValueError('invalid-selected-framework-extra-exclusion')
    return {'kind': kind, 'key': key, 'digit': value['digit']}


def validate_response(payload, *, revision=REVISION):
    """Validate a complete provider object, normalizing order but never filling."""
    count = he3_count(revision)
    fields = RESPONSE_KEYS | {'extra_exclusion'} if revision in FLEX_EXTRA_REVISIONS else RESPONSE_KEYS
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError('invalid-selected-framework-response-fields')
    reason = payload['reason']
    if not isinstance(reason, str) or len(reason) > 200:
        raise ValueError('invalid-selected-framework-reason')
    try:
        reason.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('invalid-selected-framework-reason') from None
    response = {'selected_positions': _selected(payload['selected_positions'], POSITION_KEYS, 'positions',
                                                3 if revision == REVISION else 2),
            'selected_tails': _selected(payload['selected_tails'], TAIL_POSITIONS, 'tails'),
            'he2_keep': _keep(payload['he2_keep'], 4, '02468'),
            'he3_keep': _keep(payload['he3_keep'], count, '0123456789'),
            'reason': reason}
    if revision in FLEX_EXTRA_REVISIONS:
        response['extra_exclusion'] = _extra(payload['extra_exclusion'],
            response['selected_positions'], response['selected_tails'])
    return response


def extra_metric_key(response, *, revision=ISOLATED_REVISION):
    """Identify a historical flexible extra; the default preserves old callers."""
    if revision not in FLEX_EXTRA_REVISIONS:
        raise ValueError('selected-framework-revision-has-no-extra-exclusion')
    extra = validate_response(response, revision=revision)['extra_exclusion']
    return 'extra_' + extra['kind'] + '_' + extra['key']


def merged_exclusions(response, *, revision=REVISION):
    """Return fresh physical maps for display/attack, including only chosen items."""
    response = validate_response(response, revision=revision)
    positions, tails = dict(response['selected_positions']), dict(response['selected_tails'])
    if revision in FLEX_EXTRA_REVISIONS:
        extra = response['extra_exclusion']
        (positions if extra['kind'] == 'position' else tails)[extra['key']] = extra['digit']
    return positions, tails


def outcomes(response, digits, *, revision=REVISION):
    """Return six/seven booleans against the target's actual first four balls.

    Physical identities are metric keys, so selecting a different position on a
    later target cannot mix its rate with an earlier position's rate. Each keep
    item passes once if *any* matching two-/three-position tail is retained.
    """
    response = validate_response(response, revision=revision)
    if (not isinstance(digits, (list, tuple)) or len(digits) != 4
            or any(type(digit) is not int or not 0 <= digit <= 9 for digit in digits)):
        raise ValueError('invalid-selected-framework-actual-first-four')
    result = {'position_' + key: digits[POSITION_KEYS.index(key)] != kill
              for key, kill in response['selected_positions'].items()}
    result.update({'tail_' + key: sum(digits[index] for index in TAIL_POSITIONS[key]) % 10 != kill
                   for key, kill in response['selected_tails'].items()})
    for count, field in ((2, 'he2_keep'), (3, 'he3_keep')):
        actual = {sum(digits[index] for index in indexes) % 10
                  for indexes in combinations(range(4), count)}
        result[field] = bool(actual.intersection(int(digit) for digit in response[field]))
    if revision in FLEX_EXTRA_REVISIONS:
        extra = response['extra_exclusion']
        actual = (digits[POSITION_KEYS.index(extra['key'])] if extra['kind'] == 'position' else
                  sum(digits[index] for index in TAIL_POSITIONS[extra['key']]) % 10)
        result[extra_metric_key(response, revision=revision)] = actual != extra['digit']
    return result


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _tail_features(history):
    tails = {}
    for key, positions in TAIL_POSITIONS.items():
        values = [sum(row['numbers'][index] for index in positions) % 10 for row in history]
        counts, omissions, censored = [0] * 10, [50] * 10, [True] * 10
        for value in values:
            counts[value] += 1
        for offset, value in enumerate(reversed(values)):
            if censored[value]:
                omissions[value], censored[value] = offset, False
        tails[key] = {'positions_1based': [index + 1 for index in positions], 'sample_size': 50,
                      'counts': counts, 'omissions': omissions, 'omission_censored': censored}
    return {'version': 1, 'history_size': 50, 'first_period': history[0]['period'],
            'last_period': history[-1]['period'], 'tails': tails}


def _old_v5_isolated_input_doc(reference, history, moment):
    # The strategy validator rejects ambiguous inputs before normalization.
    # No marginal/omission features or example answer enters this request.
    strategy = build_strategy(history, reference, ISOLATED_SELECTED_STRATEGY, now=moment)
    history = validate_history(history, reference, moment)
    target = next_period(reference)
    codes = ', '.join(''.join(map(str, row['numbers'])) for row in history)
    prompt = (
        f'独立七项整组失败重叠框架；最新已开奖参考期{reference}，仅预测下一目标期{target}。\n'
        + codes + '\n'
        '以上恰好50期连续真实开奖，按旧到新排列，最后一条为参考期；每条仅含前四球，'
        '依次为千位q、百位b、十位s、个位g，0及前导0有效，第五球不参与。'
        '只使用此窗口和本任务的完整失败位图，自主选择本期物理位置、合尾、排除数字与保留集。'
        '不读取其他模型当前结果；允许独立选择相同数字，不得为了异号强行换码。\n'
        '【唯一决策方法：整组失败并集】\n'
        '下方策略提供所有定位、合尾排除数字，以及所有合法二数合与三数合保留集的历史失败位图。'
        '位图均对应同一50期，最低位为最旧一期；某条件失败时对应位为1。'
        '自主比较完整七项条件，优先减少七个位图按位或所得的失败并集大小；'
        '任一项目失败即整组失败，不能将单项失败次数相加当成失败并集，也不能只看两两交集。'
        '在相同单项失败数下，失败集中在相同期会缩小并集。'
        '保留集的失败位图已按任一组合落入即成功的正式定义计算，也必须纳入整组并集。'
        '程序仅提供完整候选的可核查描述，由你自主选取七项，不能把程序统计当成指定答案。'
        '样本内并集描述不能证明下一期有优势，也不能编造历史战绩或保证未来命中。\n'
        '【七项任务与正式核验定义】\n'
        '1、2：selected_positions从q/b/s/g中恰好选两个不同位置，各排除一个0至9整数。'
        '目标期该位置实际数字不等于排除码时该项成功。两个位置可排除相同数字；未选位置不能补位。\n'
        '3、4：selected_tails从all/qbs/qbg/qsg/bsg中恰好选两种不同合尾，各排除一个0至9整数。'
        'all=(千+百+十+个)%10，qbs=(千+百+十)%10，qbg=(千+百+个)%10，'
        'qsg=(千+十+个)%10，bsg=(百+十+个)%10；目标期对应合尾不等于排除码时该项成功。\n'
        '5：he2_keep从02468中恰好保留4个不同ASCII数字。计算目标前四球任意两个位置的六种组合'
        '(千百、千十、千个、百十、百个、十个)，分别求和后取%10，任意一个合尾在保留集内即成功，不要求全部落入。\n'
        '6：he3_keep从0123456789中恰好保留5个不同ASCII数字。计算目标前四球任意三个位置的四种组合'
        '(千百十、千百个、千十个、百十个)，分别求和后取%10，任意一个合尾在保留集内即成功，不要求全部落入。'
        '尽量采用2奇3偶或3奇2偶；这只是偏好，不是硬性约束，不能为配比改写数据或强行替换本次判断。\n'
        '7：自主增加一个定位排除或合尾排除，extra_exclusion恰好包含kind、key、digit。'
        'kind为position时，key必须是未出现在selected_positions中的q/b/s/g位置；'
        'kind为tail时，key必须是未出现在selected_tails中的all/qbs/qbg/qsg/bsg合尾。'
        'digit为新增项目排除的一个0至9整数。新增项目不能重复已选物理位置或合尾类型，'
        '可与其他项目排除相同数字；实际对应位置数字或合尾不等于digit时成功。'
        '上述七个布尔结果全部成功才算整组命中。\n'
        '【本任务完整独立策略】\n' + _canonical(strategy) + '\n'
        '最终仅输出完整严格JSON对象，恰好selected_positions、selected_tails、he2_keep、he3_keep、reason、extra_exclusion六个字段。'
        'selected_positions与selected_tails均为恰好两个键的字典，值为0至9整数，不能为布尔值、小数、数组或字符串。'
        '两个保留集为上述规定长度的不重复ASCII数字字符串，保留前导0；不得默认补位或新增字段。'
        'reason用60字以内简短中文说明本次失败并集依据，不输出分析过程。')
    return {'version': 1, 'revision': ISOLATED_REVISION, 'reference_period': reference,
            'target_period': target, 'history': deepcopy(history), 'strategy': strategy, 'prompt': prompt}


def _isolated_input_doc(reference, history, moment):
    """Build the current fixed seven-item request without historical examples."""
    strategy = build_strategy(history, reference, SELECTED_STRATEGY, now=moment)
    history = validate_history(history, reference, moment)
    target = next_period(reference)
    codes = ', '.join(''.join(map(str, row['numbers'])) for row in history)
    prompt = (
        f'独立七项整组失败并集框架；最新已开奖参考期{reference}，仅预测下一目标期{target}。\n'
        + codes + '\n'
        '以上恰好50期连续真实开奖，按旧到新排列，最后一条为参考期；每条仅含前四球，'
        '依次为千位q、百位b、十位s、个位g，0及前导0有效，第五球不参与。'
        '只使用此窗口和本任务的完整失败位图，自主选择本期三个不同物理位置、两种不同合尾、排除数字与保留集。'
        '不读取其他模型当前结果；允许独立选择相同数字，不得为了异号强行换码。\n'
        '【唯一决策方法：整组失败并集】\n'
        '下方策略提供所有定位、合尾排除数字，以及所有合法二数合与三数合保留集的历史失败位图。'
        '位图均对应同一50期，最低位为最旧一期；某条件失败时对应位为1。'
        '自主比较完整七项条件，以七个位图按位或所得的失败并集置位数最少为目标；'
        '任一项目失败即整组失败，不能将单项失败次数相加当成失败并集，也不能只看两两交集。'
        '在相同单项失败数下，失败集中在相同期会缩小并集。'
        '保留集的失败位图已按任一组合落入即成功的正式定义计算，也必须纳入整组并集。'
        '程序仅提供完整候选的可核查描述，由你自主选取七项，不能把程序统计当成指定答案。'
        '样本内并集描述不能证明下一期有优势，也不能编造历史战绩或保证未来命中。\n'
        '【固定七项任务与正式核验定义】\n'
        '1、2、3：selected_positions从q/b/s/g中恰好选三个不同位置，各排除一个0至9整数。'
        '目标期该位置实际数字不等于排除码时该项成功。三个位置可排除相同数字；未选位置不能补位。\n'
        '4、5：selected_tails从all/qbs/qbg/qsg/bsg中恰好选两种不同合尾，各排除一个0至9整数。'
        'all=(千+百+十+个)%10，qbs=(千+百+十)%10，qbg=(千+百+个)%10，'
        'qsg=(千+十+个)%10，bsg=(百+十+个)%10；目标期对应合尾不等于排除码时该项成功。\n'
        '6：he2_keep从02468中恰好保留4个不同ASCII数字。计算目标前四球任意两个位置的六种组合'
        '(千百、千十、千个、百十、百个、十个)，分别求和后取%10，任意一个合尾在保留集内即成功，不要求全部落入。\n'
        '7：he3_keep从0123456789中恰好保留5个不同ASCII数字。计算目标前四球任意三个位置的四种组合'
        '(千百十、千百个、千十个、百十个)，分别求和后取%10，任意一个合尾在保留集内即成功，不要求全部落入。'
        '尽量采用2奇3偶或3奇2偶；这只是偏好，不是硬性约束，不能为配比改写数据或强行替换本次判断。'
        '上述七个布尔结果全部成功才算整组命中。\n'
        '【本任务完整独立策略】\n' + _canonical(strategy) + '\n'
        '最终仅输出完整严格JSON对象，恰好selected_positions、selected_tails、he2_keep、he3_keep、reason五个字段。'
        'selected_positions为恰好三个键的字典，selected_tails为恰好两个键的字典，值均为0至9整数，'
        '不能为布尔值、小数、数组或字符串。两个保留集为上述规定长度的不重复ASCII数字字符串，保留前导0；'
        '不得默认补位或新增字段。reason用60字以内简短中文说明本次失败并集依据，不输出分析过程。')
    return {'version': 1, 'revision': REVISION, 'reference_period': reference,
            'target_period': target, 'history': deepcopy(history), 'strategy': strategy, 'prompt': prompt}


def build_input_doc(reference, history, *, revision=REVISION):
    """Build a deterministic, self-contained input from an exact real 50 window.

    Freshness against the live wall clock belongs to the caller's publication
    guard. Reconstruction uses the saved last opening time, never today's clock.
    """
    count = he3_count(revision)
    if not isinstance(history, list) or len(history) != 50 or not isinstance(history[-1], dict):
        raise ValueError('selected-framework-history-must-have-exactly-50-draws')
    opened = history[-1].get('opentime')
    try:
        moment = datetime.fromisoformat(opened) if isinstance(opened, str) else None
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('selected-framework-opening-time-must-be-aware')
    if revision == ISOLATED_REVISION:
        return _old_v5_isolated_input_doc(reference, history, moment)
    if revision == REVISION:
        return _isolated_input_doc(reference, history, moment)
    # The shared feature validator rejects ambiguous timestamps and non-integer
    # first-four inputs before validate_history may normalize their representation.
    features = build_position_features(history, reference, moment)
    history = validate_history(history, reference, moment)
    tail_features = _tail_features(history)
    target = next_period(reference)
    codes = ', '.join(''.join(map(str, row['numbers'])) for row in history)
    prompt = (
        f'独立六项自选排除框架；最新已开奖参考期{reference}，仅预测下一目标期{target}。\n'
        + codes + '\n'
        '以上恰好50期连续真实开奖，按旧到新排列，最后一条为参考期；每条只含前四球，'
        '依次为千位q、百位b、十位s、个位g，0及前导0有效，第五球不参与。'
        '只能使用这同一50期作本次候选；不得为已开奖期改写预测或编造历史战绩。\n'
        '【六项任务与正式核验定义】\n'
        '1、2：从q/b/s/g四个不同位置中恰好选两个位置，各排除一个0至9整数。'
        'selected_positions恰好两个键；每项仅在目标期该位置实际数字不等于排除码时成功。'
        '两个位置可以排除相同数字，但不能重复位置、排除多个数字或替未选位置补位。\n'
        '3、4：从五种合尾all/qbs/qbg/qsg/bsg中恰好选两种，各排除一个0至9整数。'
        'all=(千+百+十+个)%10，qbs=(千+百+十)%10，qbg=(千+百+个)%10，'
        'qsg=(千+十+个)%10，bsg=(百+十+个)%10。selected_tails恰好两个键；'
        '每项仅在目标期对应合尾不等于该排除码时成功，不能重复合尾类型。\n'
        '5：he2_keep从02468中恰好保留4个不同ASCII数字。计算目标前四球任意2个位置的六种组合'
        '(千百、千十、千个、百十、百个、十个)，各组合求和后取%10；'
        '这六个合尾任意一个在保留集内即算该项命中，不要求全部落入。\n'
        f'6：he3_keep从0123456789中恰好保留{count}个不同ASCII数字。计算目标前四球任意3个位置的四种组合'
        '(千百十、千百个、千十个、百十个)，各组合求和后取%10；'
        '这四个合尾任意一个在保留集内即算该项命中，不要求全部落入。\n'
        '整组六项全部通过必须上述六个布尔结果同时成功；个别位置的成绩只能统计其实际被选中并事前发布的期数。'
        '冷热、遗漏和相邻模式都只是历史描述，不能据此声称下期概率优势、稳定命中或保证收益。\n'
        '【程序统一计算的同一50期统计】\n'
        'position_features的q/b/s/g为千百十个，tail_features为上述五种合尾。counts与omissions的数组下标'
        '0至9就是数字0至9；sample_size为50，遗漏0表示参考期出现；omission_censored=true时遗漏50'
        '只能确认至少50期未见，不得当成精确遗漏或推断回补概率。\n'
        + _canonical({'position_features': features, 'tail_features': tail_features})
        + '\n最终回答只输出严格JSON对象，恰好selected_positions、selected_tails、he2_keep、he3_keep、reason五个字段。'
        '两个selected字典中的值必须是0至9整数，不能是布尔值、小数、数组或字符串。'
        '两个保留集必须输出上述长度的不重复ASCII数字字符串并保留前导0；不得默认补位或新增字段。'
        'reason用60字以内简短中文理由，不输出分析过程。以下仅为结构示例，不能照抄替代本次判断：\n'
        + _canonical({'selected_positions': {'q': 1, 'g': 2}, 'selected_tails': {'all': 3, 'qbs': 4},
                      'he2_keep': '0248', 'he3_keep': '012359' if count == 6 else '01235',
                      'reason': '简短候选理由'}))
    if revision in FLEX_EXTRA_REVISIONS:
        # Keep both historical prompts byte-identical for immutable proof
        # reconstruction. Only the new version adds the independent seventh item.
        prompt = prompt.replace('独立六项自选排除框架', '独立七项自选排除框架', 1)
        prompt = prompt.replace('【六项任务与正式核验定义】', '【七项任务与正式核验定义】', 1)
        prompt = prompt.replace(
            '整组六项全部通过必须上述六个布尔结果同时成功；',
            '7：由你自行决定增加一个定位排除或合尾排除，extra_exclusion恰好包含kind、key、digit。'
            'kind为position时，key从q/b/s/g中选择未出现在selected_positions的一个位置；'
            'kind为tail时，key从all/qbs/qbg/qsg/bsg中选择未出现在selected_tails的一种合尾。'
            'digit为该新增项目排除的一个0至9整数。新增项目不能重复本期已选物理位置或合尾类型；'
            '可以与其他项目排除相同数字，不能默认补位或改动前六项。'
            '新增定位在该位置实际数字不等于digit时成功；新增合尾在对应求和%10不等于digit时成功。\n'
            '整组七项全部通过必须上述七个布尔结果同时成功；', 1)
        prompt = prompt.replace(
            '恰好selected_positions、selected_tails、he2_keep、he3_keep、reason五个字段。',
            '恰好selected_positions、selected_tails、he2_keep、he3_keep、reason、extra_exclusion六个字段。', 1)
        old_example = _canonical({'selected_positions': {'q': 1, 'g': 2},
            'selected_tails': {'all': 3, 'qbs': 4}, 'he2_keep': '0248',
            'he3_keep': '01235', 'reason': '简短候选理由'})
        new_example = _canonical({'selected_positions': {'q': 1, 'g': 2},
            'selected_tails': {'all': 3, 'qbs': 4}, 'he2_keep': '0248',
            'he3_keep': '01235', 'reason': '简短候选理由',
            'extra_exclusion': {'kind': 'position', 'key': 'b', 'digit': 5}})
        prompt = prompt.replace(old_example, new_example, 1)
    document = {'version': 1, 'revision': revision, 'reference_period': reference, 'target_period': target,
            'history': deepcopy(history), 'position_features': features, 'tail_features': tail_features,
            'prompt': prompt}
    if revision == JOINT_REVISION:
        # Only the new revision adds this explicit decision method. Reconstruct
        # older sealed requests above byte-for-byte with their original prompt.
        strategy = build_strategy(history, reference, LEGACY_SELECTED_STRATEGY, now=None)
        document['strategy'] = strategy
        document['prompt'] = (prompt + '\n【本框架独立方法：整组失败重叠策略】\n'
            '对每个定位与合尾排除候选，使用同一50期的失败次数、失败位图及跨项目失败重叠。'
            '请自主选择两项定位、两项合尾与一个未选类型额外排除项：兼顾单项失败次数，'
            '检查五项排除的失败是否集中在相同期，避免只看各自成功率却让整组失败分散。'
            '二数合、三数合也须按上述原核验定义纳入整组七项判断，不改输出结构。'
            '历史重叠只能解释样本覆盖，不构成未来优势；不读取其他模型输出，不为不同而强制换号。\n'
            + _canonical(strategy))
    return document
