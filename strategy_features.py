"""Pure, versioned descriptive features for five independent AI strategies.

One exact, oldest-first, real 50-draw first-four input is validated before any
calculation. No model state, other predictions, files, or network are read.
Historical counts, transitions and overlaps are never next-draw probabilities.
"""
from datetime import datetime
from hashlib import sha256
from itertools import combinations, product
import json

from position_input_features import _validate_history
from stats_history import parse_period


LEGACY_KILLER_STRATEGY = 'conditional-transition-v1'
LEGACY_FRAMEWORK_STRATEGY = 'marginal-frequency-v1'
LEGACY_PRO4_STRATEGY = 'recent-weighted-v1'
LEGACY_SELECTED_STRATEGY = 'joint-overlap-v1'
LEGACY_SIX_STRATEGY = 'block-stability-v1'
KILLER_STRATEGY = 'conditional-transition-v2'
FRAMEWORK_STRATEGY = 'marginal-frequency-v2'
PRO4_STRATEGY = 'recent-weighted-v2'
ISOLATED_SELECTED_STRATEGY = 'joint-overlap-v2'
SELECTED_STRATEGY = 'joint-overlap-v3-three-positions'
SIX_STRATEGY = 'block-stability-v2'
STRATEGY_LABELS = {
    LEGACY_KILLER_STRATEGY: '条件转移',
    LEGACY_FRAMEWORK_STRATEGY: '总体边际频次',
    LEGACY_PRO4_STRATEGY: '近期指数加权',
    LEGACY_SELECTED_STRATEGY: '整组失败重叠',
    LEGACY_SIX_STRATEGY: '分段稳定性',
    KILLER_STRATEGY: '条件转移（固定均匀先验）',
    FRAMEWORK_STRATEGY: '总体边际频次（排除最少计数）',
    PRO4_STRATEGY: '近期指数加权（纯加权目标）',
    ISOLATED_SELECTED_STRATEGY: '整组失败并集（完整候选）',
    SELECTED_STRATEGY: '整组失败并集（三定位固定七项）',
    SIX_STRATEGY: '分段稳定性（最差分段优先）',
}
TAIL_POSITIONS = {
    'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'qbg': (0, 1, 3),
    'qsg': (0, 2, 3), 'bsg': (1, 2, 3),
}
POSITION_KEYS = ('q', 'b', 's', 'g')


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _series(draws, *, shapes=True):
    series = {key: [digits[i] for digits in draws]
              for i, key in enumerate(POSITION_KEYS)}
    series.update({key: [sum(digits[i] for i in indices) % 10 for digits in draws]
                   for key, indices in TAIL_POSITIONS.items()})
    if shapes:
        series['parity'] = [''.join('单' if d % 2 else '双' for d in row) for row in draws]
        series['size'] = [''.join('大' if d >= 5 else '小' for d in row) for row in draws]
    return series


def _states(key):
    if key == 'parity':
        return [''.join(items) for items in product('单双', repeat=4)]
    if key == 'size':
        return [''.join(items) for items in product('大小', repeat=4)]
    return list(range(10))


def _counts(sequence, states):
    return [sequence.count(state) for state in states]


def _marginal(draws):
    features = {}
    for key, sequence in _series(draws).items():
        states = _states(key)
        counts = _counts(sequence, states)
        features[key] = {'states': states, 'n': 50, 'counts': counts,
                         'laplace_descriptive_share': [round((count + 1) / (50 + len(states)), 6)
                                                       for count in counts]}
    return {'laplace_alpha': 1, 'window': 50}, features


def _transition(draws):
    features = {}
    for key, sequence in _series(draws).items():
        states, current = _states(key), sequence[-1]
        successors = [sequence[i + 1] for i in range(49) if sequence[i] == current]
        counts, marginal = _counts(successors, states), _counts(sequence, states)
        n, prior = len(successors), [(count + 1) / (50 + len(states)) for count in marginal]
        features[key] = {
            'states': states, 'current_state': current, 'transition_opportunities': 49,
            'conditional_n': n, 'successor_counts': counts, 'marginal_counts': marginal,
            'smoothed_descriptive_share': [round((count + 10 * p) / (n + 10), 6)
                                           for count, p in zip(counts, prior)],
            'sparse': n < 5,
            'fallback': 'same-window-marginal-prior' if n < 5 else 'conditional-with-prior',
        }
    return {'prior_strength': 10, 'prior_laplace_alpha': 1,
            'minimum_conditional_n': 5, 'maximum_transitions': 49}, features


def _weighted(draws):
    weights = [2 ** (-(49 - i) / 10) for i in range(50)]
    total = sum(weights)
    features = {}
    for key, sequence in _series(draws, shapes=False).items():
        counts = [sum(weight for state, weight in zip(sequence, weights) if state == digit)
                  for digit in range(10)]
        features[key] = {
            'weighted_counts': [round(value, 6) for value in counts],
            'weighted_descriptive_share': [round(value / total, 6) for value in counts],
            'recent_10_counts': _counts(sequence[-10:], list(range(10))),
            'preceding_40_counts': _counts(sequence[:40], list(range(10))),
        }
    return {'half_life_draws': 10, 'latest_draw_weight': 1,
            'oldest_draw_weight': round(weights[0], 6), 'total_weight': round(total, 6)}, features


def _joint(draws):
    slots, masks = {}, {}
    for key, sequence in _series(draws, shapes=False).items():
        rows = []
        for digit in range(10):
            mask = sum(1 << i for i, state in enumerate(sequence) if state == digit)
            masks[(key, digit)] = mask
            rows.append([digit, mask.bit_count(), f'{mask:013x}'])
        slots[key] = rows
    # Small, fixed-size screen for overlap review, not an automatic selection.
    screen = [[key, row[0], row[1]] for key, rows in slots.items()
              for row in sorted(rows, key=lambda row: (row[1], row[0]))[:3]]
    overlaps = []
    for i, (slot_a, digit_a, _) in enumerate(screen):
        for j in range(i + 1, len(screen)):
            slot_b, digit_b, _ = screen[j]
            if slot_a != slot_b:
                overlaps.append([i, j, (masks[(slot_a, digit_a)] & masks[(slot_b, digit_b)]).bit_count()])
    return {'window': 50, 'screen_per_slot': 3,
            'screen_order': 'lowest-failure-count-then-digit',
            'bit_zero': 'oldest-draw', 'failure_definition': 'actual-slot-equals-excluded-digit'}, {
                'slot_definitions': {**{key: [i + 1] for i, key in enumerate(POSITION_KEYS)},
                                     **{key: [i + 1 for i in indices]
                                        for key, indices in TAIL_POSITIONS.items()}},
                'slot_row_columns': ['excluded_digit', 'historical_failure_count', 'failure_mask_hex'],
                'slot_failures': slots,
                'screen_columns': ['slot', 'digit', 'historical_failure_count'],
                'screen_candidates': screen,
                'overlap_columns': ['screen_index_a', 'screen_index_b', 'both_failed_count'],
                'pair_overlaps': overlaps,
                'interpretation': ('同50期内联合失败数=count_a+count_b-both_failed；同样单项失败数时，'
                                   '失败重叠较多对应较少的失败并集。只是样本内描述，不能证明下期有效。'),
            }


def _block_stability(draws):
    features = {}
    for key, sequence in _series(draws, shapes=False).items():
        blocks = [_counts(sequence[start:start + 10], list(range(10)))
                  for start in range(0, 50, 10)]
        totals = [sum(block[digit] for block in blocks) for digit in range(10)]
        means = [total / 5 for total in totals]
        features[key] = {
            'block_counts_oldest_first': blocks,
            'total_counts': totals,
            'maximum_single_block_failure_count': [max(block[digit] for block in blocks) for digit in range(10)],
            'minimum_single_block_failure_count': [min(block[digit] for block in blocks) for digit in range(10)],
            'across_block_count_range': [max(block[digit] for block in blocks) - min(block[digit] for block in blocks)
                                         for digit in range(10)],
            'across_block_count_variance': [round(sum((block[digit] - means[digit]) ** 2 for block in blocks) / 5, 6)
                                            for digit in range(10)],
            'zero_count_blocks': [sum(block[digit] == 0 for block in blocks) for digit in range(10)],
        }
    return {'window': 50, 'block_size': 10, 'block_count': 5,
            'block_order': 'oldest-first', 'variance_divisor': 5,
            'failure_definition': 'actual-slot-equals-excluded-digit'}, features


def _transition_v2(draws):
    """Conditional successors with a fixed uniform prior, never a marginal prior."""
    features = {}
    for key, sequence in _series(draws).items():
        states, current = _states(key), sequence[-1]
        successors = [sequence[i + 1] for i in range(49) if sequence[i] == current]
        counts, n = _counts(successors, states), len(successors)
        features[key] = {
            'states': states, 'current_state': current, 'transition_opportunities': 49,
            'conditional_n': n, 'successor_counts': counts,
            'uniform_prior_share': round(1 / len(states), 6),
            'smoothed_descriptive_share': [round((count + 10 / len(states)) / (n + 10), 6)
                                           for count in counts],
            'sparse': n < 5,
            'evidence_status': ('no-conditional-evidence' if n == 0 else
                                'sparse-conditional-evidence' if n < 5 else
                                'conditional-evidence-with-fixed-prior'),
        }
    return {'prior_strength': 10, 'prior_distribution': 'uniform-over-states',
            'minimum_conditional_n': 5, 'maximum_transitions': 49,
            'exclusion_objective': 'minimize-smoothed-conditional-share'}, features


def _keep_failure_candidates(draws, *, he3_size=5):
    """All legal keep sets; a draw fails iff none of its combination tails hit.

    The two-number item has six position combinations and the three-number item
    has four. Repeated tails still cover one draw only, exactly as the outcome
    contracts specify. Bit zero represents the oldest validated draw.
    """
    result = {}
    for field, width, allowed, retained in (
            ('he2_keep', 2, '02468', 4),
            ('he3_keep', 3, '0123456789', he3_size)):
        observed = [{sum(row[i] for i in indices) % 10
                     for indices in combinations(range(4), width)} for row in draws]
        candidates = []
        for digits in combinations(allowed, retained):
            keep = ''.join(digits)
            keep_digits = {int(digit) for digit in digits}
            mask = sum(1 << i for i, tails in enumerate(observed) if not tails & keep_digits)
            candidates.append((keep, mask))
        result[field] = candidates
    return result


def _marginal_v2(draws):
    parameters, features = _marginal(draws)
    parameters = dict(parameters,
                      exclusion_objective='minimize-total-count-per-excluded-state',
                      keep_objective='maximize-covered-draw-count-any-combination')
    features['keep_row_columns'] = ['keep_digits', 'historical_covered_draw_count']
    features['keep_coverage'] = {
        field: [[keep, 50 - mask.bit_count()] for keep, mask in candidates]
        for field, candidates in _keep_failure_candidates(draws, he3_size=6).items()}
    return parameters, features


def _weighted_v2(draws):
    parameters, features = _weighted(draws)
    return dict(parameters, exclusion_objective='minimize-exponential-weighted-count',
                diagnostic_counts_role='description-only-never-selection-or-tiebreak'), features


def _joint_v2(draws):
    slots = {}
    for key, sequence in _series(draws, shapes=False).items():
        slots[key] = []
        for digit in range(10):
            mask = sum(1 << i for i, state in enumerate(sequence) if state == digit)
            slots[key].append([digit, mask.bit_count(), f'{mask:013x}'])
    return {'window': 50, 'bit_zero': 'oldest-draw',
            'selection_objective': 'minimize-complete-group-failure-union-count',
            'candidate_screen': 'none-all-legal-candidates-included',
            'failure_definition': 'actual-slot-equals-excluded-digit',
            'keep_failure_definition': 'no-combination-tail-in-keep-set'}, {
        'slot_definitions': {**{key: [i + 1] for i, key in enumerate(POSITION_KEYS)},
                             **{key: [i + 1 for i in indices]
                                for key, indices in TAIL_POSITIONS.items()}},
        'slot_row_columns': ['excluded_digit', 'historical_failure_count', 'failure_mask_hex'],
        'slot_failures': slots,
        'keep_row_columns': ['keep_digits', 'historical_failure_count', 'failure_mask_hex'],
        'keep_failures': {field: [[keep, mask.bit_count(), f'{mask:013x}']
                                 for keep, mask in candidates]
                          for field, candidates in _keep_failure_candidates(draws).items()},
        'interpretation': ('每项失败掩码为50位十六进制，最低位对应最早一期；整组失败掩码为所有选中项掩码按位或，'
                           '其置位数才是整组历史失败数。二数合逐期计算六个位置组合合尾，三数合计算四个；'
                           '任一组合合尾落入该保留集即该项通过，全部未落入才失败。'
                           '完整七项包括两定位、两合尾、两保留集和额外排除项，必须共同核查；'
                           '不得先按单项最低频次截断候选，不把最低交集当目标，不按单项失败数之和代替失败并集。'),
    }


def _block_stability_v2(draws):
    parameters, features = _block_stability(draws)
    parameters = dict(parameters,
                      selection_objective='lexicographic-minimize-worst-block-then-variance-then-total',
                      objective_order=['maximum_single_block_failure_count',
                                       'across_block_count_variance', 'total_counts'])
    features['keep_row_columns'] = [
        'keep_digits', 'maximum_block_failure_count', 'across_block_failure_variance',
        'total_failure_count', 'block_failure_counts_oldest_first']
    features['keep_failures'] = {}
    for field, candidates in _keep_failure_candidates(draws).items():
        rows = []
        for keep, mask in candidates:
            blocks = [((mask >> start) & 1023).bit_count() for start in range(0, 50, 10)]
            total = sum(blocks)
            variance = round(sum((count - total / 5) ** 2 for count in blocks) / 5, 6)
            rows.append([keep, max(blocks), variance, total, blocks])
        features['keep_failures'][field] = rows
    return parameters, features


def _joint_v3(draws):
    """The same complete bitmaps with the new three-position group definition."""
    parameters, features = _joint_v2(draws)
    features['interpretation'] = features['interpretation'].replace(
        '完整七项包括两定位、两合尾、两保留集和额外排除项，必须共同核查；',
        '完整七项固定包括三定位、两合尾、二数合保留集和三数合保留集，必须共同核查；')
    return parameters, features


INSTRUCTIONS = {
    LEGACY_KILLER_STRATEGY: ('本模型独立采用条件转移思路，以各位置、合尾和形态的当前状态对应历史后继计数为主依据；'
                      '仅有49次相邻转移机会，条件样本不足5时明确承认稀疏并参考同窗边际先验平滑。'
                      '不得把重复、邻码或遗漏回补当主规则，不得把平滑份额声称为已校准概率。'),
    LEGACY_FRAMEWORK_STRATEGY: ('本模型独立采用总体边际频次思路，以同50期各位置、合尾和形态总体计数及Laplace平滑为主依据；'
                        '不按最近状态转移、不使用遗漏回补或温弱码保护作主规则。保持原输出项目数量。'),
    LEGACY_PRO4_STRATEGY: ('本模型独立采用近期指数加权思路，以半衰期固定10期、最新权重1的加权计数为主依据，'
                   '近10/前40计数仅用于描述差异。不得改半衰期追逐本次结果，不以其他模型或遗漏回补决策。'),
    LEGACY_SELECTED_STRATEGY: ('本模型独立采用整组失败重叠思路，在保持单项历史失败数受控的同时，核查多个排除条件的失败并集；'
                       '相同单项失败数时，失败交集越大，并集越小，不要误把低交集当作提高整组通过。'
                       'screen仅为固定筛选辅助，非强制候选；须自行选择原要求的定位、合尾、保留集和额外项目，'
                       '并核查七项共同约束，不能只看单项最冷码。二数合/三数合仍按原定义任一组合落入保留集。'),
    LEGACY_SIX_STRATEGY: ('本模型独立采用分段稳定性思路，固定把最新连续50期划为5个连续10期区块，'
                  '以每个位置和合尾各数字的区块计数、最大单段失败次数、跨段波动作为主依据；'
                  '避免仅凭总体次数低就忽略某一段集中出现，或把最新段机械放大。'
                  '自主选两个不同位置各排一码、两个不同合尾各排一码、二数合保留4码和三数合保留5码，恰好六项。'
                  '不读取七项或其他模型号码，不从七项结果删一项，不复制七项定位或保留集。'
                  '分段稳定只是样本内描述，不能声称下一期概率或保证命中。'),
    KILLER_STRATEGY: ('只采用条件转移方法：分别取各位置、合尾及完整形态的当前状态，'
                      '在49次相邻转移中统计该状态的历史后继；排除项以最小smoothed_descriptive_share为目标，'
                      '按原数量选择。固定均匀先验总强度10，每个状态先验份额1/状态数，不使用边际频次先验。'
                      'conditional_n是实际后继样本数；不足5标记稀疏，0时只有均匀先验、各状态无条件证据差别。'
                      '平滑不会增加实际样本量，稀疏时不得声称证据充分；同分允许任意合法同分项。'
                      '不使用总体冷码、遗漏回补、邻码、重复保护或其他方法作为选择与破同分依据。'),
    FRAMEWORK_STRATEGY: ('只采用同50期总体边际频次方法。对每个定位、合尾与完整形态排除项，'
                        '按原排除数量选择总体counts最少的合法状态；Laplace alpha固定1，不改变同槽计数排序。'
                        '二数合保留4码、三数合保留6码，以keep_coverage中的历史覆盖期数最大为目标；'
                        '覆盖按每期任一对应组合合尾落入保留集计算，每期最多计一次，不能把保留集当排除集。'
                        '同分允许任意合法同分项。不使用近期权重、条件转移、区块稳定、失败并集、遗漏回补或温弱码保护。'),
    PRO4_STRATEGY: ('只采用近期指数加权方法：固定半衰期10期、最新权重1、其前每期按2^(-间隔/10)衰减。'
                   '每个原定位与合尾排除项以weighted_counts最小为唯一选择目标；同分允许任意合法同分项。'
                   'recent_10_counts与preceding_40_counts仅供描述，不能用于选择或破同分。'
                   '不调整半衰期，不叠加总体冷码、遗漏回补、条件转移、分段保护或其他模型结果。'),
    ISOLATED_SELECTED_STRATEGY: ('只采用完整七项失败并集方法：全部90个定位/合尾排除候选及5个二数合保留4码候选、'
                       '252个三数合保留5码候选均已提供失败掩码，不做最低3码或任何边际频次预筛。'
                       '按原合法结构选择两定位、两合尾、两保留集及额外排除项，对全部七项失败掩码按位或，'
                       '以整组失败并集置位数最少为目标；允许合法同分组，不要求与其他模型异号。'
                       '保留项按任一组合合尾落入保留集即通过，须纳入完整组；不能只优化五个排除项。'
                       '不把低失败交集、单项最冷或单项失败数之和当整组目标；样本内最小并集不保证下期通过。'),
    SELECTED_STRATEGY: ('只采用完整七项失败并集方法：全部90个定位/合尾排除候选及5个二数合保留4码候选、'
                       '252个三数合保留5码候选均已提供失败掩码，不做最低3码或任何边际频次预筛。'
                       '固定选择三个不同定位各排除1码、两种不同合尾各排除1码、二数合保留4码与三数合保留5码，'
                       '共七项。对全部七项失败掩码按位或，以整组失败并集置位数最少为目标；'
                       '允许合法同分组，不要求与其他模型异号。'
                       '保留项按任一组合合尾落入保留集即通过，须纳入完整组；不能只优化五个排除项。'
                       '不把低失败交集、单项最冷或单项失败数之和当整组目标；样本内最小并集不保证下期通过。'),
    SIX_STRATEGY: ('只采用分段稳定方法：同50期固定分为5个连续10期区块，各区块等权。'
                  '每项合法排除或保留候选严格按字典序最小化：先比较最大单段失败次数，'
                  '相同再比较跨段失败次数方差，再相同才比较总失败次数；全相同允许任意合法同分项。'
                  '定位/合尾的三项值分别来自maximum_single_block_failure_count、across_block_count_variance、total_counts；'
                  '保留集取keep_failures对应列。保留项仍按每期任一组合合尾落入即通过，全部未落入才失败。'
                  '自主选两不同位置各排1码、两不同合尾各排1码、二数合保留4码、三数合保留5码，恰好六项。'
                  '不先按总体最冷筛选，不放大最新段，不叠加条件转移、近期指数权重或整组失败并集目标；'
                  '不读取或复用其他模型当前号码。分段统计不能证明下期概率。'),
}


def build_strategy(history, reference, strategy_id, now=None):
    """Build descriptive features; default time is the last archived real draw."""
    if strategy_id not in STRATEGY_LABELS:
        raise ValueError('unsupported-strategy-id')
    if now is None:
        try:
            now = datetime.fromisoformat(history[-1]['opentime'])
        except (TypeError, KeyError, IndexError, ValueError):
            raise ValueError('invalid-strategy-history-time') from None
    draws = _validate_history(history, reference, now)
    parsed = parse_period(reference)
    # Reuse canonical sequential period logic, including midnight rollover.
    from position_input_features import _next_period
    parameters, features = {
        LEGACY_KILLER_STRATEGY: _transition, LEGACY_FRAMEWORK_STRATEGY: _marginal,
        LEGACY_PRO4_STRATEGY: _weighted, LEGACY_SELECTED_STRATEGY: _joint,
        LEGACY_SIX_STRATEGY: _block_stability,
        KILLER_STRATEGY: _transition_v2, FRAMEWORK_STRATEGY: _marginal_v2,
        PRO4_STRATEGY: _weighted_v2, ISOLATED_SELECTED_STRATEGY: _joint_v2,
        SELECTED_STRATEGY: _joint_v3, SIX_STRATEGY: _block_stability_v2,
    }[strategy_id](draws)
    return {'strategy_id': strategy_id, 'label': STRATEGY_LABELS[strategy_id],
            'reference_period': reference, 'target_period': _next_period(parsed),
            'history_size': 50,
            'history_sha256': sha256(_canonical(history).encode('utf-8')).hexdigest(),
            'parameters': parameters, 'features': features,
            'instruction': INSTRUCTIONS[strategy_id] +
                           '只能依据本模型这一策略和同一真实50期作独立判断；不读取或复用其他模型当前号码。'
                           '允许独立得出相同数字，禁止为了异号强行换码。样本内拟合不代表事前战绩或未来概率。'}
