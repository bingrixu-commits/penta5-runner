"""Exact first-four coverage for the two current formal framework rules.

This pure module enumerates the 10,000 unique four-digit outcomes. It has no
provider, persistence, historical sample, stake, or payout dependencies. A mask
byte at index q*1000+b*100+s*10+g is one iff every formal condition passes.
"""
from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256

import selected_framework_contract as seven_contract
import selected_six_contract as six_contract


_CONTRACTS = {'selected': seven_contract, 'six': six_contract}
_POSITIONS = ('q', 'b', 's', 'g')
_TAILS = ('all', 'qbs', 'qbg', 'qsg', 'bsg')
_CURRENT_REVISIONS = {
    'selected': 'selected-seven-v6-three-positions',
    'six': 'selected-six-independent-v2-minimax',
}


def _actual_index(digits):
    if (not isinstance(digits, (list, tuple)) or len(digits) != 4
            or any(type(digit) is not int or not 0 <= digit <= 9 for digit in digits)):
        raise ValueError('invalid-framework-coverage-actual-first-four')
    return digits[0] * 1000 + digits[1] * 100 + digits[2] * 10 + digits[3]


@dataclass(frozen=True)
class Coverage:
    """Immutable unique-code membership; leading-zero codes retain their index."""

    number_count: int
    coverage_sha256: str
    mask: bytes

    def covers_actual(self, digits):
        return bool(self.mask[_actual_index(digits)])


def _universe():
    # Precompute sums once, independently of any prediction. Pair/triple masks
    # implement ANY retained tail, never a sum of matching combinations.
    values = []
    for q in range(10):
        for b in range(10):
            for s in range(10):
                for g in range(10):
                    total = q + b + s + g
                    pair_mask = (1 << ((q + b) % 10) | 1 << ((q + s) % 10)
                                 | 1 << ((q + g) % 10) | 1 << ((b + s) % 10)
                                 | 1 << ((b + g) % 10) | 1 << ((s + g) % 10))
                    triple_mask = (1 << ((total - q) % 10) | 1 << ((total - b) % 10)
                                   | 1 << ((total - s) % 10) | 1 << ((total - g) % 10))
                    tails = (total % 10, (total - g) % 10, (total - s) % 10,
                             (total - b) % 10, (total - q) % 10)
                    values.append(((q, b, s, g), tails, pair_mask, triple_mask))
    return tuple(values)


_UNIVERSE = _universe()


@lru_cache(maxsize=128)
def _build_cached(model, revision, positions, tails, he2_keep, he3_keep):
    # Parameters are already validated and normalized once by the exact formal
    # contract. Keeping reason outside the key makes an editorial change inert.
    he2_mask = sum(1 << int(value) for value in he2_keep)
    he3_mask = sum(1 << int(value) for value in he3_keep)
    mask = bytearray(10000)
    for code, (digits, tail_values, pair_mask, triple_mask) in enumerate(_UNIVERSE):
        if any(digits[index] == kill for index, kill in positions):
            continue
        if any(tail_values[index] == kill for index, kill in tails):
            continue
        if pair_mask & he2_mask and triple_mask & he3_mask:
            mask[code] = 1
    mask = bytes(mask)
    return Coverage(number_count=sum(mask), coverage_sha256=sha256(mask).hexdigest(), mask=mask)


def build_coverage(model, response, revision):
    """Validate one current response and return bounded-cached exact coverage.

    ``model`` is ``selected`` (three-position seven-item framework) or ``six``
    (independent six-item framework). Legacy revisions are deliberately rejected
    so simulated accounting cannot silently reinterpret an older rule cohort.
    """
    if (not isinstance(model, str) or model not in _CONTRACTS
            or not isinstance(revision, str) or revision != _CURRENT_REVISIONS[model]):
        raise ValueError('unsupported-framework-coverage-model-or-revision')
    normalized = _CONTRACTS[model].validate_response(response, revision=revision)
    positions = tuple((_POSITIONS.index(key), kill)
                      for key, kill in normalized['selected_positions'].items())
    tails = tuple((_TAILS.index(key), kill)
                  for key, kill in normalized['selected_tails'].items())
    return _build_cached(model, revision, positions, tails,
                         normalized['he2_keep'], normalized['he3_keep'])


def analyze_coverage(model, response, revision):
    coverage = build_coverage(model, response, revision)
    return {'number_count': coverage.number_count, 'coverage_sha256': coverage.coverage_sha256}


def covers_actual(model, response, revision, digits):
    return build_coverage(model, response, revision).covers_actual(digits)
