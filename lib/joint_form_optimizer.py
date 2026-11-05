"""Exact joint-form optimizer used after the A0 promotion gate unlocks.

The AI response itself is never re-generated here: only the two exclusion
patterns are replaced by the pair that maximizes the exact joint-hit
probability over all 10,000 four-digit outcomes, with the AI's position kills,
sum-tail kills and keep sets left untouched.
"""
import itertools
import json
from pathlib import Path

ROOT = Path('/root/penta5-runner')
TAIL_DEFS = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'bsg': (1, 2, 3)}
PAIR_IDX = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
TRIPLE_IDX = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))
PARITY_PATTERNS = [''.join(p) for p in itertools.product('单双', repeat=4)]
SIZE_PATTERNS = [''.join(p) for p in itertools.product('大小', repeat=4)]
ALL_PATTERNS = PARITY_PATTERNS + SIZE_PATTERNS
HE2_KEEPS = [''.join(c) for c in itertools.combinations('02468', 4)]
HE3_KEEPS = [''.join(c) for c in itertools.combinations('0123456789', 6)]
FULL = (1 << 10000) - 1
PARITY_OPPOSITE = {'单': '双', '双': '单'}
SIZE_OPPOSITE = {'大': '小', '小': '大'}


def digit_parity(value):
    return '单' if int(value) % 2 else '双'


def digit_size(value):
    return '大' if int(value) >= 5 else '小'


def reverse_lock(response):
    """千位(q_kill1)/百位(b_kill1)被杀：单双与大小两种形态在 0/1 位都取杀码形态反面
    （排除双写单、排除大写小；其余两位自由）。返回值可直接 JSON 序列化。"""
    q, b = str(response['q_kill1']), str(response['b_kill1'])
    return {'pattern_ds': [PARITY_OPPOSITE[digit_parity(q)], PARITY_OPPOSITE[digit_parity(b)]],
            'pattern_dx': [SIZE_OPPOSITE[digit_size(q)], SIZE_OPPOSITE[digit_size(b)]]}


def apply_reverse_form(response):
    """不依赖掩码的确定性兜底：千/百位单双/大小形态均取杀码形态反面。"""
    locked = reverse_lock(response)
    ds = list(str(response.get('pattern_ds') or '双双双双'))
    dx = list(str(response.get('pattern_dx') or '小小小小'))
    if len(ds) != 4 or set(ds) - set('单双'):
        ds = ['双'] * 4
    if len(dx) != 4 or set(dx) - set('大小'):
        dx = ['小'] * 4
    before = (''.join(ds), ''.join(dx))
    ds[0], ds[1] = locked['pattern_ds']
    dx[0], dx[1] = locked['pattern_dx']
    updated = dict(response, pattern_ds=''.join(ds), pattern_dx=''.join(dx))
    return updated, before, locked


_BASE = None
_KEEPS = None


def _hit(value):
    return FULL ^ int.from_bytes(value, 'little')


def _build_base():
    pos = [[bytearray(1250) for _ in range(10)] for _ in range(4)]
    tail = {name: [bytearray(1250) for _ in range(10)] for name in TAIL_DEFS}
    parity = [bytearray(1250) for _ in range(16)]
    size = [bytearray(1250) for _ in range(16)]
    for i in range(10000):
        q, b, s, g = i // 1000, (i // 100) % 10, (i // 10) % 10, i % 10
        digits = (q, b, s, g)
        byte, bit = i >> 3, 1 << (i & 7)
        for index in range(4):
            pos[index][digits[index]][byte] |= bit
        for name, indexes in TAIL_DEFS.items():
            tail[name][sum(digits[k] for k in indexes) % 10][byte] |= bit
        pcode = 0
        scode = 0
        for index, value in enumerate(digits):
            pcode |= (value & 1) << (3 - index)
            scode |= (1 if value >= 5 else 0) << (3 - index)
        parity[pcode][byte] |= bit
        size[scode][byte] |= bit
    return {
        'pos': [[_hit(pos[index][digit]) for digit in range(10)] for index in range(4)],
        'tail': {name: [_hit(tail[name][digit]) for digit in range(10)] for name in TAIL_DEFS},
        'parity': {pattern: _hit(parity[sum((ch == '单') << (3 - index) for index, ch in enumerate(pattern))])
                   for pattern in PARITY_PATTERNS},
        'size': {pattern: _hit(size[sum((ch == '大') << (3 - index) for index, ch in enumerate(pattern))])
                 for pattern in SIZE_PATTERNS},
    }


def _build_keeps():
    he2 = {keep: bytearray(1250) for keep in HE2_KEEPS}
    he3 = {keep: bytearray(1250) for keep in HE3_KEEPS}
    he2_bits = {keep: sum(1 << int(c) for c in keep) for keep in HE2_KEEPS}
    he3_bits = {keep: sum(1 << int(c) for c in keep) for keep in HE3_KEEPS}
    for i in range(10000):
        q, b, s, g = i // 1000, (i // 100) % 10, (i // 10) % 10, i % 10
        digits = (q, b, s, g)
        byte, bit = i >> 3, 1 << (i & 7)
        pair_bits = 0
        for a, c in PAIR_IDX:
            pair_bits |= 1 << ((digits[a] + digits[c]) % 10)
        for keep in HE2_KEEPS:
            if not pair_bits & he2_bits[keep]:
                he2[keep][byte] |= bit
        triple_bits = 0
        for a, c, e in TRIPLE_IDX:
            triple_bits |= 1 << ((digits[a] + digits[c] + digits[e]) % 10)
        for keep in HE3_KEEPS:
            if not triple_bits & he3_bits[keep]:
                he3[keep][byte] |= bit
    return {'he2': {keep: _hit(he2[keep]) for keep in HE2_KEEPS},
            'he3': {keep: _hit(he3[keep]) for keep in HE3_KEEPS}}


def _base_masks():
    global _BASE
    if _BASE is None:
        _BASE = _build_base()
    return _BASE


def _keep_masks():
    global _KEEPS
    if _KEEPS is None:
        _KEEPS = _build_keeps()
    return _KEEPS


def auto_promoted(root=ROOT):
    try:
        value = json.loads((Path(root) / 'data' / 'joint_form_auto_promote.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    return isinstance(value, dict) and value.get('enabled') is True and value.get('manual_disable') is not True


def _and(masks):
    result = FULL
    for mask in masks:
        result &= mask
    return result


def _pattern_mask(masks, pattern):
    return masks['parity'][pattern] if pattern in PARITY_PATTERNS else masks['size'][pattern]


def _best_framework(base, masks, ai_patterns, lock=None):
    ds_candidates = PARITY_PATTERNS
    dx_candidates = SIZE_PATTERNS
    if lock is not None:
        ds_candidates = [p for p in ds_candidates
                         if p[0] == lock['pattern_ds'][0] and p[1] == lock['pattern_ds'][1]]
        dx_candidates = [p for p in dx_candidates
                         if p[0] == lock['pattern_dx'][0] and p[1] == lock['pattern_dx'][1]]
    best = (-1, None)
    for ds in ds_candidates:
        part = base & masks['parity'][ds]
        for dx in dx_candidates:
            count = (part & masks['size'][dx]).bit_count()
            if count > best[0]:
                best = (count, (ds, dx))
    chosen = best[1]
    expected_ai = (base & masks['parity'][ai_patterns[0]] & masks['size'][ai_patterns[1]]).bit_count() / 10000
    return chosen, expected_ai, best[0] / 10000


def _best_killer(base, masks, ai_patterns):
    best = (-1, None)
    for first in ALL_PATTERNS:
        part = base & _pattern_mask(masks, first)
        for second in ALL_PATTERNS:
            if second == first:
                continue
            count = (part & _pattern_mask(masks, second)).bit_count()
            if count > best[0]:
                best = (count, (first, second))
    chosen = best[1]
    expected_ai = (base & _pattern_mask(masks, ai_patterns[0]) & _pattern_mask(masks, ai_patterns[1])).bit_count() / 10000
    return chosen, expected_ai, best[0] / 10000


def optimize_framework_response(response, root=ROOT, reverse_form=True):
    if not auto_promoted(root):
        return None
    base_masks = _base_masks()
    keeps = _keep_masks()
    fixed = [
        base_masks['pos'][0][int(response['q_kill1'])],
        base_masks['pos'][1][int(response['b_kill1'])],
        base_masks['tail']['qbs'][int(response['he_q3_kill'])],
        base_masks['tail']['bsg'][int(response['he_h3_kill'])],
        keeps['he2'][response['he2_keep']],
        keeps['he3'][response['he3_keep']],
    ]
    base = _and(fixed)
    lock = reverse_lock(response) if reverse_form else None
    chosen, expected_ai, expected_opt = _best_framework(
        base, base_masks, (response['pattern_ds'], response['pattern_dx']), lock=lock)
    updated = dict(response, pattern_ds=chosen[0], pattern_dx=chosen[1])
    return {'response': updated,
            'ai_patterns': [response['pattern_ds'], response['pattern_dx']],
            'opt_patterns': list(chosen),
            'reverse_lock': lock,
            'expected_ai': round(expected_ai, 6), 'expected_opt': round(expected_opt, 6)}


def optimize_killer_response(response, root=ROOT):
    if not auto_promoted(root):
        return None
    masks = _base_masks()
    fixed = [
        masks['pos'][0][int(response['k1'][0])],
        masks['pos'][1][int(response['k2'][0])],
        masks['pos'][2][int(response['k3'][0])],
        masks['pos'][3][int(response['k4'][0])],
        masks['tail']['all'][int(response['he_all'])],
        masks['tail']['qbs'][int(response['he_q3'])],
        masks['tail']['bsg'][int(response['he_h3'])],
    ]
    base = _and(fixed)
    ai_patterns = list(response['exclude_patterns'])
    chosen, expected_ai, expected_opt = _best_killer(base, masks, ai_patterns)
    updated = dict(response, exclude_patterns=[chosen[0], chosen[1]])
    return {'response': updated, 'ai_patterns': ai_patterns, 'opt_patterns': list(chosen),
            'expected_ai': round(expected_ai, 6), 'expected_opt': round(expected_opt, 6)}
