"""Independent six-item contract; exact saved 50-draw evidence, no seventh item.

This contract never reads the seven-item prediction or any other model output.
Features describe the saved window; they make no claim about future probability.
"""
from copy import deepcopy
from datetime import datetime
from itertools import combinations
import json

from ai_verified_stats import next_period
from position_input_features import build_position_features
from stat50_compare import validate_history
from strategy_features import (build_strategy, SIX_STRATEGY, LEGACY_SIX_STRATEGY,
                              SINGLE_TAIL_SIX_STRATEGY)

LEGACY_REVISION = 'selected-six-independent-v1'
V2_REVISION = 'selected-six-independent-v2-minimax'
V3_REVISION = 'selected-six-independent-v3-minimax-reverse'
V4_REVISION = 'selected-six-independent-v4-minimax-forms'
V6_REVISION = 'selected-six-independent-v6-panel-context'
V7_REVISION = 'selected-six-independent-v7-single-tail'
PREVIOUS_REVISION = V6_REVISION
REVISION = V7_REVISION
LEDGER_REVISION = LEGACY_REVISION
SUPPORTED_REVISIONS = frozenset((LEGACY_REVISION, V2_REVISION, V3_REVISION, V4_REVISION,
                                 V6_REVISION, V7_REVISION))
FORM_REVISIONS = frozenset((V4_REVISION, V6_REVISION, V7_REVISION))
def _resolve_model():
    for name in ('PENTA5_MODEL_SIX', 'PENTA5_DEEPSEEK_MODEL'):
        try:
            from app_secrets import get_secret
            value = get_secret(name)
        except RuntimeError:
            continue
        if value:
            return value
    return 'deepseek-v4-pro'


MODEL = _resolve_model()
MARKER = 'SELECTED_SIX_INDEPENDENT'
POSITION_KEYS = ('q', 'b', 's', 'g')
POSITION_LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
TAIL_POSITIONS = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
                  'qsg': (0, 2, 3), 'bsg': (1, 2, 3)}
TAIL_LABELS = {'all': '千百十个合尾', 'qbs': '千百十合尾', 'qbg': '千百个合尾',
               'qsg': '千十个合尾', 'bsg': '百十个合尾'}
BASE_METRIC_KEYS = (tuple('position_' + key for key in POSITION_KEYS)
                   + tuple('tail_' + key for key in TAIL_POSITIONS)
                   + ('he2_keep', 'he3_keep'))
REVERSE_METRIC_KEYS = ('reverse_parity', 'reverse_size')
REVERSE_LABELS = {'reverse_parity': '单双反向形态', 'reverse_size': '大小反向形态'}
METRIC_KEYS = BASE_METRIC_KEYS + REVERSE_METRIC_KEYS
POSITION_SHORT_LABELS = {'q': '千', 'b': '百', 's': '十', 'g': '个'}
RESPONSE_KEYS = frozenset(('selected_positions', 'selected_tails', 'he2_keep', 'he3_keep', 'reason'))
FORM_RESPONSE_KEYS = RESPONSE_KEYS | frozenset(('reverse_parity', 'reverse_size'))


def single_tail(revision=REVISION):
    if not isinstance(revision, str) or revision not in SUPPORTED_REVISIONS:
        raise ValueError('unknown-selected-six-revision')
    return revision == V7_REVISION


def tail_count(revision=REVISION):
    return 1 if single_tail(revision) else 2


def he3_count(revision=REVISION):
    if not isinstance(revision, str) or revision not in SUPPORTED_REVISIONS:
        raise ValueError('unknown-selected-six-revision')
    return 5


def item_count(revision=REVISION):
    he3_count(revision)
    if revision == V7_REVISION:
        return 7
    return 8 if revision in (V3_REVISION, V4_REVISION, V6_REVISION) else 6


def _selected(values, allowed, label, count=2):
    if (not isinstance(values, dict) or len(values) != count
            or any(key not in allowed for key in values)
            or any(type(digit) is not int or not 0 <= digit <= 9 for digit in values.values())):
        raise ValueError('invalid-selected-' + label)
    return {key: values[key] for key in allowed if key in values}


def _keep(value, count, allowed):
    if (not isinstance(value, str) or len(value) != count or len(set(value)) != count
            or any(digit not in allowed for digit in value)):
        raise ValueError('invalid-selected-six-keep-set')
    return ''.join(sorted(value))


def reverse_attributes(digit):
    """Return the two reverse-form labels of one excluded digit.

    Parity and size are mirrored: an odd kill implies an even reverse form and a
    big kill implies a small reverse form.  Ten digits split 5/5 on both axes, so
    the mirrored class is exactly the majority of the digits left after the kill.
    """
    if type(digit) is not int or not 0 <= digit <= 9:
        raise ValueError('invalid-selected-six-reverse-digit')
    return ('双' if digit % 2 == 1 else '单'), ('小' if digit >= 5 else '大')


def reverse_form(response, *, revision=REVISION):
    """Return display labels for the two derived reverse forms of a response."""
    if revision != V3_REVISION:
        raise ValueError('selected-six-reverse-form-requires-previous-revision')
    response = validate_response(response, revision=revision)
    parity, size = [], []
    for key in POSITION_KEYS:
        if key in response['selected_positions']:
            parity_label, size_label = reverse_attributes(response['selected_positions'][key])
            parity.append(POSITION_SHORT_LABELS[key] + parity_label)
            size.append(POSITION_SHORT_LABELS[key] + size_label)
    return {'reverse_parity': '·'.join(parity), 'reverse_size': '·'.join(size)}


def reverse_keep_digits(digit):
    """Return the digit string satisfying both reverse forms for one position."""
    parity_label, size_label = reverse_attributes(digit)
    parity = '02468' if parity_label == '双' else '13579'
    size = '01234' if size_label == '小' else '56789'
    return ''.join(value for value in '1234567890' if value in parity and value in size)


def pattern_of(digits, kind):
    """Return the four-position form string of actual first-four balls."""
    if (not isinstance(digits, (list, tuple)) or len(digits) != 4
            or any(type(digit) is not int or not 0 <= digit <= 9 for digit in digits)):
        raise ValueError('invalid-selected-six-actual-first-four')
    if kind == 'parity':
        return ''.join('双' if digit % 2 == 0 else '单' for digit in digits)
    if kind == 'size':
        return ''.join('大' if digit >= 5 else '小' for digit in digits)
    raise ValueError('invalid-selected-six-pattern-kind')


def _pattern(value, kind):
    palette = '单双' if kind == 'parity' else '大小'
    if (not isinstance(value, str) or len(value) != 4
            or any(char not in palette for char in value)):
        raise ValueError('invalid-selected-six-reverse-form')
    return value


def reverse_form_legal(response):
    """True when both chosen forms mirror the selected positions' kill digits."""
    for index, key in enumerate(POSITION_KEYS):
        if key in response['selected_positions']:
            parity_label, size_label = reverse_attributes(response['selected_positions'][key])
            if response['reverse_parity'][index] != parity_label:
                return False
            if response['reverse_size'][index] != size_label:
                return False
    return True


def validate_response(payload, *, revision=REVISION):
    he3_count(revision)
    fields = FORM_RESPONSE_KEYS if revision in FORM_REVISIONS else RESPONSE_KEYS
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError('invalid-selected-six-response-fields')
    reason = payload['reason']
    if not isinstance(reason, str) or len(reason) > 200:
        raise ValueError('invalid-selected-six-reason')
    try:
        reason.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('invalid-selected-six-reason') from None
    he3_keep = _keep(payload['he3_keep'], 5, '0123456789')
    if revision in (V3_REVISION, V4_REVISION, V6_REVISION, V7_REVISION) and not 2 <= sum(int(digit) % 2 for digit in he3_keep) <= 3:
        # The current rules forbid all-odd/all-even and 4:1 counts; the balanced
        # candidate domain is compared by the same five-block minimax method.
        raise ValueError('invalid-selected-six-he3-parity')
    response = {'selected_positions': _selected(payload['selected_positions'], POSITION_KEYS, 'positions'),
                'selected_tails': _selected(payload['selected_tails'], TAIL_POSITIONS, 'tails',
                                            tail_count(revision)),
                'he2_keep': _keep(payload['he2_keep'], 4, '02468'),
                'he3_keep': he3_keep, 'reason': reason}
    if revision in FORM_REVISIONS:
        response['reverse_parity'] = _pattern(payload['reverse_parity'], 'parity')
        response['reverse_size'] = _pattern(payload['reverse_size'], 'size')
        if not reverse_form_legal(response):
            raise ValueError('invalid-selected-six-reverse-constraint')
    return response


def merged_exclusions(response, *, revision=REVISION):
    response = validate_response(response, revision=revision)
    return dict(response['selected_positions']), dict(response['selected_tails'])


def outcomes(response, digits, *, revision=REVISION):
    response = validate_response(response, revision=revision)
    if (not isinstance(digits, (list, tuple)) or len(digits) != 4
            or any(type(digit) is not int or not 0 <= digit <= 9 for digit in digits)):
        raise ValueError('invalid-selected-six-actual-first-four')
    result = {'position_' + key: digits[POSITION_KEYS.index(key)] != kill
              for key, kill in response['selected_positions'].items()}
    result.update({'tail_' + key: sum(digits[index] for index in TAIL_POSITIONS[key]) % 10 != kill
                   for key, kill in response['selected_tails'].items()})
    for count, field in ((2, 'he2_keep'), (3, 'he3_keep')):
        actual = {sum(digits[index] for index in indexes) % 10
                  for indexes in combinations(range(4), count)}
        result[field] = bool(actual.intersection(int(digit) for digit in response[field]))
    if revision == V3_REVISION:
        parity, size = True, True
        for key, kill in response['selected_positions'].items():
            digit = digits[POSITION_KEYS.index(key)]
            parity = parity and (digit % 2 == 0) == (kill % 2 == 1)
            size = size and (digit >= 5) == (kill < 5)
        result['reverse_parity'] = parity
        result['reverse_size'] = size
    elif revision in FORM_REVISIONS:
        # Exclusion semantics: the sealed four-position form is expected NOT to appear.
        result['reverse_parity'] = pattern_of(digits, 'parity') != response['reverse_parity']
        result['reverse_size'] = pattern_of(digits, 'size') != response['reverse_size']
    return result


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _isolated_input_doc(reference, history, moment):
    strategy = build_strategy(history, reference, SIX_STRATEGY, now=moment)
    history = validate_history(history, reference, moment)
    target = next_period(reference)
    codes = ', '.join(''.join(map(str, row['numbers'])) for row in history)
    prompt = (
        f'独立六项分段最差值框架；最新已开奖参考期{reference}，仅预测下一目标期{target}。\n'
        + codes + '\n'
        '以上恰好50期连续真实开奖，按旧到新排列，最后一条为参考期；每条仅含前四球，'
        '依次为千位q、百位b、十位s、个位g，0及前导0有效，第五球不参与。'
        '只使用此窗口及本任务分段特征，不读取其他模型候选。'
        '物理位置、合尾类型、排除数字与保留集均由你自主决定；允许独立得出相同数字，禁止为异号强行换码。\n'
        '【唯一决策方法：固定五段minimax】\n'
        '把50期按旧到新固定划为五个连续区块，每块10期，不改边界或放大最新段。'
        '所有定位、合尾排除候选和所有合法保留集均有按正式核验定义计算的区块失败统计。'
        '严格按固定优先级比较候选：先最小化五段中最大单段失败次数；最差段相同时，'
        '再最小化五段失败次数方差；方差仍相同时，再最小化五段失败总量。'
        '按这个最差段、方差、总量的词典序自主选择六项；不能用全窗总量优先覆盖最差段目标，'
        '不能按近期加权、相邻转移或跨项目重叠替换本方法。'
        '程序只描述完整候选的五段表现，不指定物理选择或最终号码。'
        '样本内稳定性不能证明下一期有概率优势，也不能编造战绩或保证未来命中。\n'
        '【六项任务及正式核验】\n'
        '1、2：selected_positions从q/b/s/g中自主选恰好两个不同位置，各排除一个0至9整数。'
        '目标期该位置实际数字不等于排除码时该项成功。两个位置可排相同数字，未选位置不补位。\n'
        '3、4：selected_tails从all/qbs/qbg/qsg/bsg中自主选恰好两种不同合尾，各排除一个0至9整数。'
        'all=(千+百+十+个)%10，qbs=(千+百+十)%10，qbg=(千+百+个)%10，'
        'qsg=(千+十+个)%10，bsg=(百+十+个)%10；目标期实际对应合尾不等于排除码时该项成功。\n'
        '5：he2_keep从02468中恰好保留4个不同ASCII数字。计算目标前四球任意两个位置的六种组合，'
        '分别求和后取%10；任意一种合尾落入保留集即算此项命中，不要求全部落入。\n'
        '6：he3_keep从0123456789中恰好保留5个不同ASCII数字。计算目标前四球任意三个位置的四种组合，'
        '分别求和后取%10；任意一种合尾落入保留集即算此项命中，不要求全部落入。'
        '尽量采用2奇3偶或3奇2偶；这只是偏好，不是硬性约束，不能为配比伪造数据或强行替换本次判断。'
        '整组六项必须上述六个布尔结果全部通过才算整组命中。\n'
        '【本任务完整独立分段策略】\n' + _canonical(strategy) + '\n'
        '最终仅输出完整严格JSON对象，恰好selected_positions、selected_tails、he2_keep、he3_keep、reason五个字段。'
        '两个selected字典均恰好两个键，值必须为0至9整数，不能为布尔值、小数、数组或字符串。'
        '保留集为规定长度的不重复ASCII数字字符串，保留前导0；不得默认补位或新增字段。'
        'reason用60字以内简短中文说明本次最差段、方差、总量依据，不输出分析过程。')
    return {'version': 1, 'revision': V2_REVISION, 'reference_period': reference,
            'target_period': target, 'history': deepcopy(history), 'strategy': strategy, 'prompt': prompt}


def _reverse_input_doc(reference, history, moment):
    """Build the v3 fixed-minimax request; the two reverse forms are derived."""
    strategy = build_strategy(history, reference, SIX_STRATEGY, now=moment)
    history = validate_history(history, reference, moment)
    target = next_period(reference)
    codes = ', '.join(''.join(map(str, row['numbers'])) for row in history)
    prompt = (
        f'独立八项分段最差值框架；最新已开奖参考期{reference}，仅预测下一目标期{target}。\n'
        + codes + '\n'
        '以上恰好50期连续真实开奖，按旧到新排列，最后一条为参考期；每条仅含前四球，'
        '依次为千位q、百位b、十位s、个位g，0及前导0有效，第五球不参与。'
        '只使用此窗口及本任务分段特征，不读取其他模型候选。'
        '物理位置、合尾类型、排除数字与保留集均由你自主决定；允许独立得出相同数字，禁止为异号强行换码。\n'
        '【唯一决策方法：固定五段minimax】\n'
        '把50期按旧到新固定划为五个连续区块，每块10期，不改边界或放大最新段。'
        '所有定位、合尾排除候选和所有合法保留集均有按正式核验定义计算的区块失败统计。'
        '严格按固定优先级比较候选：先最小化五段中最大单段失败次数；最差段相同时，'
        '再最小化五段失败次数方差；方差仍相同时，再最小化五段失败总量。'
        '按这个最差段、方差、总量的词典序自主选择六项；不能用全窗总量优先覆盖最差段目标，'
        '不能按近期加权、相邻转移或跨项目重叠替换本方法。'
        '程序只描述完整候选的五段表现，不指定物理选择或最终号码。'
        '样本内稳定性不能证明下一期有概率优势，也不能编造战绩或保证未来命中。\n'
        '【六项自选任务及正式核验】\n'
        '1、2：selected_positions从q/b/s/g中自主选恰好两个不同位置，各排除一个0至9整数。'
        '目标期该位置实际数字不等于排除码时该项成功。两个位置可排相同数字，未选位置不补位。\n'
        '3、4：selected_tails从all/qbs/qbg/qsg/bsg中自主选恰好两种不同合尾，各排除一个0至9整数。'
        'all=(千+百+十+个)%10，qbs=(千+百+十)%10，qbg=(千+百+个)%10，'
        'qsg=(千+十+个)%10，bsg=(百+十+个)%10；目标期实际对应合尾不等于排除码时该项成功。\n'
        '5：he2_keep从02468中恰好保留4个不同ASCII数字。计算目标前四球任意两个位置的六种组合，'
        '分别求和后取%10；任意一种合尾落入保留集即算此项命中，不要求全部落入。\n'
        '6：he3_keep从0123456789中恰好保留5个不同ASCII数字，且必须2奇3偶或3奇2偶'
        '（5奇0偶、4奇1偶、1奇4偶、0奇5偶均不合格）。计算目标前四球任意三个位置的四种组合，'
        '分别求和后取%10；任意一种合尾落入保留集即算此项命中，不要求全部落入。\n'
        '【两项自动反向形态】\n'
        '程序按两个已选定位的排除码自动推导两项：单双反向形态取排除码单双的反面，'
        '大小反向形态取排除码大小的反面；两项均按目标期对应位置实际形态核验。'
        '不要求你输出额外字段，也不得为凑反向形态改动六项自选。整组八项全部通过才算整组命中。\n'
        '【本任务完整独立分段策略】\n' + _canonical(strategy) + '\n'
        '最终仅输出完整严格JSON对象，恰好selected_positions、selected_tails、he2_keep、he3_keep、reason五个字段。'
        '两个selected字典均恰好两个键，值必须为0至9整数，不能为布尔值、小数、数组或字符串。'
        '保留集为规定长度的不重复ASCII数字字符串，保留前导0；不得默认补位或新增字段。'
        'reason用60字以内简短中文说明本次最差段、方差、总量依据，不输出分析过程。')
    return {'version': 1, 'revision': V3_REVISION, 'reference_period': reference,
            'target_period': target, 'history': deepcopy(history), 'strategy': strategy, 'prompt': prompt}


def _forms_input_doc(reference, history, moment, *, revision=V4_REVISION, panel_snapshot=None):
    """Build the v4 fixed-minimax request; the two forms are now AI choices."""
    strategy = build_strategy(history, reference, SIX_STRATEGY, now=moment)
    history = validate_history(history, reference, moment)
    target = next_period(reference)
    codes = ', '.join(''.join(map(str, row['numbers'])) for row in history)
    prompt = (
        f'独立八项形态排除框架；最新已开奖参考期{reference}，仅预测下一目标期{target}。\n'
        + codes + '\n'
        '以上恰好50期连续真实开奖，按旧到新排列，最后一条为参考期；每条仅含前四球，'
        '依次为千位q、百位b、十位s、个位g，0及前导0有效，第五球不参与。'
        '只使用此窗口及本任务分段特征，不读取其他模型候选。'
        '物理位置、合尾类型、排除数字、保留集与两个形态均由你自主决定；允许独立得出相同数字，禁止为异号强行换码。\n'
        '【唯一决策方法：固定五段minimax】\n'
        '把50期按旧到新固定划为五个连续区块，每块10期，不改边界或放大最新段。'
        '所有定位、合尾排除候选、所有合法保留集和所有合法四位形态均有按正式核验定义计算的区块失败统计。'
        '严格按固定优先级比较候选：先最小化五段中最大单段失败次数；最差段相同时，'
        '再最小化五段失败次数方差；方差仍相同时，再最小化五段失败总量。'
        '按这个最差段、方差、总量的词典序自主选择八项；不能用全窗总量优先覆盖最差段目标，'
        '不能按近期加权、相邻转移或跨项目重叠替换本方法。'
        '程序只描述完整候选的五段表现，不指定物理选择或最终号码。'
        '样本内稳定性不能证明下一期有概率优势，也不能编造战绩或保证未来命中。\n'
        '【八项任务及正式核验】\n'
        '1、2：selected_positions从q/b/s/g中自主选恰好两个不同位置，各排除一个0至9整数。'
        '目标期该位置实际数字不等于排除码时该项成功。两个位置可排相同数字，未选位置不补位。\n'
        '3、4：selected_tails从all/qbs/qbg/qsg/bsg中自主选恰好两种不同合尾，各排除一个0至9整数。'
        'all=(千+百+十+个)%10，qbs=(千+百+十)%10，qbg=(千+百+个)%10，'
        'qsg=(千+十+个)%10，bsg=(百+十+个)%10；目标期实际对应合尾不等于排除码时该项成功。\n'
        '5：he2_keep从02468中恰好保留4个不同ASCII数字。计算目标前四球任意两个位置的六种组合，'
        '分别求和后取%10；任意一种合尾落入保留集即算此项命中，不要求全部落入。\n'
        '6：he3_keep从0123456789中恰好保留5个不同ASCII数字，且必须2奇3偶或3奇2偶'
        '（5奇0偶、4奇1偶、1奇4偶、0奇5偶均不合格）。计算目标前四球任意三个位置的四种组合，'
        '分别求和后取%10；任意一种合尾落入保留集即算此项命中，不要求全部落入。\n'
        '7：reverse_parity为按千百十个顺序的四位单双形态，字符只能取单或双。'
        '两个已选定位对应的字符必须等于该定位排除码的单双反面（例：百排除9为单，百位字符必须是双；'
        '个排除6为双，个位字符必须是单）；其余位置自主选择。'
        '目标期实际四位单双形态不等于该形态时该项成功，即反向排除该形态。\n'
        '8：reverse_size为按千百十个顺序的四位大小形态，字符只能取大或小。'
        '两个已选定位对应的字符必须等于该定位排除码的大小反面（例：百排除9为大，百位字符必须是小）；'
        '其余位置自主选择。目标期实际四位大小形态不等于该形态时该项成功，即反向排除该形态。\n'
        '第7、8项同样按五段minimax比较：候选形态的失败次数等于该完整形态在对应10期区块中出现的次数；'
        '先最大单段失败、再方差、最后总量，在满足定位字符约束的合法形态中取词典序最小。'
        '整组八项必须同时通过才算整组命中。\n'
        '【本任务完整独立分段策略】\n' + _canonical(strategy) + '\n'
        '最终仅输出完整严格JSON对象，恰好selected_positions、selected_tails、he2_keep、he3_keep、'
        'reverse_parity、reverse_size、reason七个字段。'
        'reverse_parity与reverse_size各为恰好4个字符的字符串；两个selected字典均恰好两个键，'
        '值必须为0至9整数，不能为布尔值、小数、数组或字符串。'
        '保留集为规定长度的不重复ASCII数字字符串，保留前导0；不得默认补位或新增字段。'
        'reason用60字以内简短中文说明本次最差段、方差、总量依据，不输出分析过程。')
    panel_context = None
    if revision == V6_REVISION:
        if panel_snapshot is None:
            from panel_context import build as _build_panel
            panel_snapshot = _build_panel(reference, target)
        from panel_context import prompt_text as _panel_prompt_text
        panel_json, panel_sha = _panel_prompt_text(panel_snapshot)
        prompt += ('\n\n【面板全量上下文（同一面板其他框架最近发布/战绩；只含已发布数据，各条自带 reference/target；仅供自主决策参考，不是未来概率保证）】\n' + panel_json + '\n')
        panel_context = panel_snapshot
    document = {'version': 1, 'revision': revision, 'reference_period': reference,
            'target_period': target, 'history': deepcopy(history), 'strategy': strategy, 'prompt': prompt}
    if panel_context is not None:
        document['panel_context'] = panel_context
    return document


def _single_tail_input_doc(reference, history, moment, *, panel_snapshot=None):
    """Build the v7 fixed-minimax request: one self-chosen tail, seven items total."""
    strategy = build_strategy(history, reference, SINGLE_TAIL_SIX_STRATEGY, now=moment)
    history = validate_history(history, reference, moment)
    target = next_period(reference)
    codes = ', '.join(''.join(map(str, row['numbers'])) for row in history)
    prompt = (
        f'独立七项形态排除框架；最新已开奖参考期{reference}，仅预测下一目标期{target}。\n'
        + codes + '\n'
        '以上恰好50期连续真实开奖，按旧到新排列，最后一条为参考期；每条仅含前四球，'
        '依次为千位q、百位b、十位s、个位g，0及前导0有效，第五球不参与。'
        '只使用此窗口及本任务分段特征，不读取其他模型候选。'
        '物理位置、合尾类型、排除数字、保留集与两个形态均由你自主决定；允许独立得出相同数字，禁止为异号强行换码。\n'
        '【唯一决策方法：固定五段minimax】\n'
        '把50期按旧到新固定划为五个连续区块，每块10期，不改边界或放大最新段。'
        '所有定位、合尾排除候选、所有合法保留集和所有合法四位形态均有按正式核验定义计算的区块失败统计。'
        '严格按固定优先级比较候选：先最小化五段中最大单段失败次数；最差段相同时，'
        '再最小化五段失败次数方差；方差仍相同时，再最小化五段失败总量。'
        '按这个最差段、方差、总量的词典序自主选择七项；不能用全窗总量优先覆盖最差段目标，'
        '不能按近期加权、相邻转移或跨项目重叠替换本方法。'
        '程序只描述完整候选的五段表现，不指定物理选择或最终号码。'
        '样本内稳定性不能证明下一期有概率优势，也不能编造战绩或保证未来命中。\n'
        '【七项任务及正式核验】\n'
        '1、2：selected_positions从q/b/s/g中自主选恰好两个不同位置，各排除一个0至9整数。'
        '目标期该位置实际数字不等于排除码时该项成功。两个位置可排相同数字，未选位置不补位。\n'
        '3：selected_tails从all/qbs/qbg/qsg/bsg中自主选恰好一种合尾，排除一个0至9整数。'
        'all=(千+百+十+个)%10，qbs=(千+百+十)%10，qbg=(千+百+个)%10，'
        'qsg=(千+十+个)%10，bsg=(百+十+个)%10；目标期实际对应合尾不等于排除码时该项成功。\n'
        '4：he2_keep从02468中恰好保留4个不同ASCII数字。计算目标前四球任意两个位置的六种组合，'
        '分别求和后取%10；任意一种合尾落入保留集即算此项命中，不要求全部落入。\n'
        '5：he3_keep从0123456789中恰好保留5个不同ASCII数字，且必须2奇3偶或3奇2偶'
        '（5奇0偶、4奇1偶、1奇4偶、0奇5偶均不合格）。计算目标前四球任意三个位置的四种组合，'
        '分别求和后取%10；任意一种合尾落入保留集即算此项命中，不要求全部落入。\n'
        '6：reverse_parity为按千百十个顺序的四位单双形态，字符只能取单或双。'
        '两个已选定位对应的字符必须等于该定位排除码的单双反面（例：百排除9为单，百位字符必须是双；'
        '个排除6为双，个位字符必须是单）；其余位置自主选择。'
        '目标期实际四位单双形态不等于该形态时该项成功，即反向排除该形态。\n'
        '7：reverse_size为按千百十个顺序的四位大小形态，字符只能取大或小。'
        '两个已选定位对应的字符必须等于该定位排除码的大小反面（例：百排除9为大，百位字符必须是小；'
        '个排除6为小，个位字符必须是大）。其余位置自主选择。'
        '目标期实际四位大小形态不等于该形态时该项成功，即反向排除该形态。\n'
        '第6、7项同样按五段minimax比较：候选形态的失败次数等于该完整形态在对应10期区块中出现的次数；'
        '先最大单段失败、再方差、最后总量，在满足定位字符约束的合法形态中取词典序最小。'
        '整组七项必须同时通过才算整组命中。\n'
        '【本任务完整独立分段策略】\n' + _canonical(strategy) + '\n'
        '最终仅输出完整严格JSON对象，恰好selected_positions、selected_tails、he2_keep、he3_keep、'
        'reverse_parity、reverse_size、reason七个字段。'
        'reverse_parity与reverse_size各为恰好4个字符的字符串；selected_positions为恰好两个键的字典，'
        'selected_tails为恰好一个键的字典，值必须为0至9整数，不能为布尔值、小数、数组或字符串。'
        '保留集为规定长度的不重复ASCII数字字符串，保留前导0；不得默认补位或新增字段。'
        'reason用60字以内简短中文说明本次最差段、方差、总量依据，不输出分析过程。')
    if panel_snapshot is None:
        from panel_context import build as _build_panel
        panel_snapshot = _build_panel(reference, target)
    from panel_context import prompt_text as _panel_prompt_text
    panel_json, panel_sha = _panel_prompt_text(panel_snapshot)
    prompt += ('\n\n【面板全量上下文（同一面板其他框架最近发布/战绩；只含已发布数据，各条自带 reference/target；仅供自主决策参考，不是未来概率保证）】\n' + panel_json + '\n')
    return {'version': 1, 'revision': V7_REVISION, 'reference_period': reference,
            'target_period': target, 'history': deepcopy(history), 'strategy': strategy,
            'prompt': prompt, 'panel_context': panel_snapshot}


def build_input_doc(reference, history, *, revision=REVISION, panel_snapshot=None):
    he3_count(revision)
    if not isinstance(history, list) or len(history) != 50 or not isinstance(history[-1], dict):
        raise ValueError('selected-six-history-must-have-exactly-50-draws')
    opened = history[-1].get('opentime')
    try:
        moment = datetime.fromisoformat(opened) if isinstance(opened, str) else None
    except ValueError:
        moment = None
    if moment is None or moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('selected-six-opening-time-must-be-aware')
    if revision == V7_REVISION:
        return _single_tail_input_doc(reference, history, moment, panel_snapshot=panel_snapshot)
    if revision in FORM_REVISIONS:
        return _forms_input_doc(reference, history, moment, revision=revision,
                                panel_snapshot=panel_snapshot)
    if revision == V3_REVISION:
        return _reverse_input_doc(reference, history, moment)
    if revision == V2_REVISION:
        return _isolated_input_doc(reference, history, moment)
    # Reject booleans, stringified digits and other ambiguous first-four inputs
    # before the shared history normalizer may coerce their representation.
    build_position_features(history, reference, moment)
    history = validate_history(history, reference, moment)
    strategy = build_strategy(history, reference, LEGACY_SIX_STRATEGY, now=None)
    target = next_period(reference)
    codes = ', '.join(''.join(map(str, row['numbers'])) for row in history)
    prompt = (
        f'独立六项分段稳定性框架；最新已开奖参考期{reference}，仅预测下一目标期{target}。\n'
        + codes + '\n'
        '以上恰好50期连续真实开奖，按旧到新排列，每条只含前四球，依次为千位q、百位b、十位s、个位g。'
        '0及前导0有效，第五球不参与。只能使用此窗口，不读取七项模型或其他模型的候选结果，'
        '不能为已开奖期改写预测、编造战绩或为追求异号强行换码。\n'
        '【独立方法：五段稳定性】\n'
        '将50期固定分成五段，每段10期，以五段计数、最差段计数、段间跨度与方差、未出现区块数为判断依据。'
        '定位和合尾排除候选须检查各段的出现分布，避免只凭全50期总频次、最近热冷或另一模型的重叠结果作选择。'
        '这只是样本内稳定性描述，不能证明下期有概率优势，也不能保证未来命中。\n'
        '【六项任务及核验】\n'
        '1、2：selected_positions从q/b/s/g中自主选恰好两个不同位置，各排除一个0至9整数。'
        '每项仅在目标期该位置实际数字不等于排除码时成功。未选位置不补位，两个位置可以排相同数字。\n'
        '3、4：selected_tails从all/qbs/qbg/qsg/bsg五种合尾中自主选恰好两种，各排除一个0至9整数。'
        'all=(千+百+十+个)%10，qbs=(千+百+十)%10，qbg=(千+百+个)%10，'
        'qsg=(千+十+个)%10，bsg=(百+十+个)%10；实际对应合尾不等于排除码时该项成功。\n'
        '5：he2_keep从02468中恰好保留4个不同ASCII数字。对目标前四球任意两个位置的六种组合分别求和%10，'
        '任意一种合尾落入保留集即算此项命中。\n'
        '6：he3_keep从0123456789中恰好保留5个不同ASCII数字。对目标前四球任意三个位置的四种组合分别求和%10，'
        '任意一种合尾落入保留集即算此项命中；尽量采用2奇3偶或3奇2偶，此为偏好，不能为了配比伪造数据。\n'
        '整组六项必须上述六个布尔结果全部通过才算整组命中。没有第七项，不输出extra_exclusion。'
        '两类保留集也需参考五段样本内覆盖情况，不能把任一组合命中误作全部组合命中。\n'
        '【程序固定计算的同一窗口特征】\n' + _canonical(strategy) + '\n'
        '最终只输出完整严格JSON对象，恰好selected_positions、selected_tails、he2_keep、he3_keep、reason五字段。'
        'selected字典的值必须为0至9整数，不能为布尔、小数、数组或字符串；保留集为规定长度的不重复ASCII数字字符串，保留前导0。'
        'reason用60字内简短中文说明，不输出分析过程。以下仅为结构示例，不能照抄代替本次判断：\n'
        + _canonical({'selected_positions': {'q': 1, 'g': 2}, 'selected_tails': {'all': 3, 'qbs': 4},
                      'he2_keep': '0248', 'he3_keep': '01235', 'reason': '分段候选简述'}))
    return {'version': 1, 'revision': revision, 'reference_period': reference, 'target_period': target,
            'history': deepcopy(history), 'strategy': strategy, 'prompt': prompt}
