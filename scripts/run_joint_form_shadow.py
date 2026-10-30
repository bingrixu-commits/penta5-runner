#!/usr/bin/env python3
"""A0 shadow: AI kills fixed, choose exclusion patterns by exact joint optimization.

The official AI seals are never modified. For each published reference round
this job records the AI patterns and the pattern pair that maximizes the exact
joint-hit probability over all 10,000 four-digit outcomes, then evaluates both
against the real draw when it settles. Runs from server cron only.
"""
import argparse
import itertools
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from stats_history import beijing_now, load_snapshot
from ai_verified_stats import next_period

REVISION = 'joint-form-shadow-v1'
TAIL_DEFS = {'all': (0, 1, 2, 3), 'qbs': (0, 1, 2), 'bsg': (1, 2, 3)}
PAIR_IDX = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
TRIPLE_IDX = [(0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3)]
HE2_KEEPS = [''.join(c) for c in itertools.combinations('02468', 4)]
HE3_KEEPS = [''.join(c) for c in itertools.combinations('0123456789', 6)]
PARITY_PATTERNS = [''.join(p) for p in itertools.product('单双', repeat=4)]
SIZE_PATTERNS = [''.join(p) for p in itertools.product('大小', repeat=4)]
ALL_PATTERNS = PARITY_PATTERNS + SIZE_PATTERNS
FULL = (1 << 10000) - 1

_MASKS = None


def _build_masks():
    pos = [[bytearray(1250) for _ in range(10)] for _ in range(4)]
    tail = {name: [bytearray(1250) for _ in range(10)] for name in TAIL_DEFS}
    parity = [bytearray(1250) for _ in range(16)]
    size = [bytearray(1250) for _ in range(16)]
    he2 = {keep: bytearray(1250) for keep in HE2_KEEPS}
    he3 = {keep: bytearray(1250) for keep in HE3_KEEPS}
    he2_bits = {keep: sum(1 << int(c) for c in keep) for keep in HE2_KEEPS}
    he3_bits = {keep: sum(1 << int(c) for c in keep) for keep in HE3_KEEPS}
    for i in range(10000):
        q, b, s, g = i // 1000, (i // 100) % 10, (i // 10) % 10, i % 10
        d = (q, b, s, g)
        byte, bit = i >> 3, 1 << (i & 7)
        for p in range(4):
            pos[p][d[p]][byte] |= bit
        for name, indexes in TAIL_DEFS.items():
            tail[name][sum(d[k] for k in indexes) % 10][byte] |= bit
        pcode = 0
        scode = 0
        for p, value in enumerate(d):
            pcode |= (value & 1) << (3 - p)
            scode |= (1 if value >= 5 else 0) << (3 - p)
        parity[pcode][byte] |= bit
        size[scode][byte] |= bit
        pair_bits = 0
        for a, c in PAIR_IDX:
            pair_bits |= 1 << ((d[a] + d[c]) % 10)
        for keep in HE2_KEEPS:
            if not pair_bits & he2_bits[keep]:
                he2[keep][byte] |= bit
        triple_bits = 0
        for a, c, e in TRIPLE_IDX:
            triple_bits |= 1 << ((d[a] + d[c] + d[e]) % 10)
        for keep in HE3_KEEPS:
            if not triple_bits & he3_bits[keep]:
                he3[keep][byte] |= bit
    def hit(bytearray_value):
        return FULL ^ int.from_bytes(bytearray_value, 'little')
    return {
        'pos': [[hit(pos[p][digit]) for digit in range(10)] for p in range(4)],
        'tail': {name: [hit(tail[name][digit]) for digit in range(10)] for name in TAIL_DEFS},
        'parity': {pat: hit(parity[sum((ch == '单') << (3 - p) for p, ch in enumerate(pat))])
                   for pat in PARITY_PATTERNS},
        'size': {pat: hit(size[sum((ch == '大') << (3 - p) for p, ch in enumerate(pat))])
                 for pat in SIZE_PATTERNS},
        'he2': {keep: hit(he2[keep]) for keep in HE2_KEEPS},
        'he3': {keep: hit(he3[keep]) for keep in HE3_KEEPS},
    }


def masks():
    global _MASKS
    if _MASKS is None:
        _MASKS = _build_masks()
    return _MASKS


def _and(values):
    result = FULL
    for value in values:
        result &= value
    return result


def _killer_digit(value):
    return int(value[0] if isinstance(value, (list, tuple)) else value)


def framework_fixed(masks, response):
    return [
        masks['pos'][0][int(response['q_kill1'])],
        masks['pos'][1][int(response['b_kill1'])],
        masks['tail']['qbs'][int(response['he_q3_kill'])],
        masks['tail']['bsg'][int(response['he_h3_kill'])],
        masks['he2'][response['he2_keep']],
        masks['he3'][response['he3_keep']],
    ]


def killer_fixed(masks, seal):
    return [
        masks['pos'][0][_killer_digit(seal['kills']['k1'])],
        masks['pos'][1][_killer_digit(seal['kills']['k2'])],
        masks['pos'][2][_killer_digit(seal['kills']['k3'])],
        masks['pos'][3][_killer_digit(seal['kills']['k4'])],
        masks['tail']['all'][int(seal['he_all'])],
        masks['tail']['qbs'][int(seal['he_q3'])],
        masks['tail']['bsg'][int(seal['he_h3'])],
    ]


def optimize_framework(masks, fixed, ai_patterns):
    base = _and(fixed)
    best = (-1, None)
    for ds in PARITY_PATTERNS:
        part = base & masks['parity'][ds]
        for dx in SIZE_PATTERNS:
            hits = (part & masks['size'][dx]).bit_count()
            if hits > best[0]:
                best = (hits, (ds, dx))
    chosen = best[1]
    expected_ai = (base & masks['parity'][ai_patterns[0]] & masks['size'][ai_patterns[1]]).bit_count() / 10000
    return {'ai_patterns': list(ai_patterns), 'opt_patterns': list(chosen),
            'expected_ai': round(expected_ai, 6), 'expected_opt': round(best[0] / 10000, 6)}, base


def optimize_killer(masks, fixed, ai_patterns):
    base = _and(fixed)
    best = (-1, None)
    for p1 in ALL_PATTERNS:
        part = base & masks['parity'].get(p1, masks['size'].get(p1)) if p1 in PARITY_PATTERNS else base & masks['size'][p1]
        for p2 in ALL_PATTERNS:
            if p2 == p1:
                continue
            mask2 = masks['parity'].get(p2, masks['size'].get(p2)) if p2 in PARITY_PATTERNS else masks['size'][p2]
            hits = (part & mask2).bit_count()
            if hits > best[0]:
                best = (hits, (p1, p2))
    chosen = best[1]
    def pmask(pat):
        return masks['parity'][pat] if pat in PARITY_PATTERNS else masks['size'][pat]
    expected_ai = (base & pmask(ai_patterns[0]) & pmask(ai_patterns[1])).bit_count() / 10000
    return {'ai_patterns': list(ai_patterns), 'opt_patterns': list(chosen),
            'expected_ai': round(expected_ai, 6), 'expected_opt': round(best[0] / 10000, 6)}, base


def _pattern_mask(masks, pattern):
    return masks['parity'][pattern] if pattern in PARITY_PATTERNS else masks['size'][pattern]


def _evaluate(masks, model, fixed, record, actual):
    index = (actual[0] * 1000 + actual[1] * 100 + actual[2] * 10 + actual[3])
    base = _and(fixed)
    if model == 'framework':
        pm = [masks['parity'][record['ai_patterns'][0]] if record['ai_patterns'][0] in PARITY_PATTERNS else masks['size'][record['ai_patterns'][0]],
              masks['size'][record['ai_patterns'][1]]]
        po = [masks['parity'][record['opt_patterns'][0]], masks['size'][record['opt_patterns'][1]]]
    else:
        pm = [_pattern_mask(masks, record['ai_patterns'][0]), _pattern_mask(masks, record['ai_patterns'][1])]
        po = [_pattern_mask(masks, record['opt_patterns'][0]), _pattern_mask(masks, record['opt_patterns'][1])]
    return {'actual': list(actual),
            'ai_joint': bool((base >> index) & 1 and all((mask >> index) & 1 for mask in pm)),
            'opt_joint': bool((base >> index) & 1 and all((mask >> index) & 1 for mask in po)),
            'ai_pattern_hits': [bool((mask >> index) & 1) for mask in pm],
            'opt_pattern_hits': [bool((mask >> index) & 1) for mask in po]}


def _actual_map(root):
    result = {}
    for source in ('data/state.json', 'data/stats_draw_history.json'):
        try:
            document = json.loads((root / source).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        rows = document.get('records') if isinstance(document, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and row.get('period') and isinstance(row.get('numbers'), list) and len(row['numbers']) >= 4:
                    result[str(row['period'])] = [int(x) for x in row['numbers'][:4]]
        if isinstance(document, dict) and isinstance(document.get('latestRecord'), dict):
            row = document['latestRecord']
            if row.get('period') and isinstance(row.get('numbers'), list) and len(row['numbers']) >= 4:
                result[str(row['period'])] = [int(x) for x in row['numbers'][:4]]
    return result


def _load(root, relative):
    try:
        return json.loads((root / relative).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _summary(records):
    evaluated = [record for record in records.values() if isinstance(record.get('evaluation'), dict)]
    n = len(evaluated)
    if not n:
        return {'n': 0}
    ai = sum(1 for record in evaluated if record['evaluation']['ai_joint'])
    opt = sum(1 for record in evaluated if record['evaluation']['opt_joint'])
    live = [record for record in evaluated if record.get('source') == 'live']
    live_n = len(live)
    live_ai = sum(1 for record in live if record['evaluation']['ai_joint'])
    live_opt = sum(1 for record in live if record['evaluation']['opt_joint'])
    return {'n': n, 'ai_joint_hits': ai, 'opt_joint_hits': opt,
            'ai_rate': round(ai / n, 6), 'opt_rate': round(opt / n, 6),
            'delta_pp': round((opt - ai) / n * 100, 3),
            'expected_ai_avg': round(sum(record['expected_ai'] for record in evaluated) / n, 6),
            'expected_opt_avg': round(sum(record['expected_opt'] for record in evaluated) / n, 6),
            'live_n': live_n, 'live_ai_hits': live_ai, 'live_opt_hits': live_opt}


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', dir=str(path.parent), delete=False, encoding='utf-8')
    temporary = handle.name
    try:
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)


def backfill(root, shadow, masks_data, actuals_fw, actuals_killer):
    framework_seals = (_load(root, 'data/r1_framework_seals.json').get('prediction_seals') or {})
    killer_stats = _load(root, 'data/r1_stats.json')
    killer_seals = killer_stats.get('prediction_seals') or {}
    created = 0
    for ref, seal in framework_seals.items():
        if not isinstance(seal, dict) or seal.get('publication_state') != 'published':
            continue
        response = seal.get('response')
        target = str(seal.get('target_period'))
        if not isinstance(response, dict) or target not in actuals_fw:
            continue
        records = shadow['models']['framework']['records']
        if ref in records:
            continue
        try:
            fixed = framework_fixed(masks_data, response)
            item, base = optimize_framework(masks_data, fixed, (response['pattern_ds'], response['pattern_dx']))
            record = {'ref_period': ref, 'target_period': target, 'source': 'backfill',
                      'created_at': beijing_now().isoformat(), **item,
                      'ai_response': response}
            record['evaluation'] = _evaluate(masks_data, 'framework', fixed, record, actuals_fw[target])
            records[ref] = record
            created += 1
        except (KeyError, TypeError, ValueError):
            continue
    for ref, seal in killer_seals.items():
        if not isinstance(seal, dict) or seal.get('publication_state') != 'published':
            continue
        target = str(seal.get('target_period'))
        if target not in actuals_killer:
            continue
        records = shadow['models']['killer']['records']
        if ref in records:
            continue
        try:
            fixed = killer_fixed(masks_data, seal)
            item, base = optimize_killer(masks_data, fixed, (seal['p1'], seal['p2']))
            record = {'ref_period': ref, 'target_period': target, 'source': 'backfill',
                      'created_at': beijing_now().isoformat(), **item,
                      'ai_response': {key: seal.get(key) for key in ('kills', 'he_all', 'he_q3', 'he_h3', 'p1', 'p2')}}
            record['evaluation'] = _evaluate(masks_data, 'killer', fixed, record, actuals_killer[target])
            records[ref] = record
            created += 1
        except (KeyError, TypeError, ValueError):
            continue
    return created


def live(root, shadow, masks_data):
    state = _load(root, 'data/state.json')
    latest = state.get('latestRecord') if isinstance(state, dict) else None
    if not isinstance(latest, dict) or not latest.get('period') or not isinstance(latest.get('numbers'), list):
        return 0
    reference = str(latest['period'])
    target = next_period(reference)
    draw_map = _actual_map(root)
    added = 0
    framework_seals = (_load(root, 'data/r1_framework_seals.json').get('prediction_seals') or {})
    killer_seals = (_load(root, 'data/r1_stats.json').get('prediction_seals') or {})
    if reference in framework_seals:
        records = shadow['models']['framework']['records']
        seal = framework_seals[reference]
        if reference not in records and isinstance(seal, dict) and seal.get('publication_state') == 'published' and str(seal.get('target_period')) == target:
            response = seal.get('response')
            if isinstance(response, dict):
                try:
                    fixed = framework_fixed(masks_data, response)
                    item, base = optimize_framework(masks_data, fixed, (response['pattern_ds'], response['pattern_dx']))
                    record = {'ref_period': reference, 'target_period': target, 'source': 'live',
                              'created_at': beijing_now().isoformat(), **item, 'ai_response': response}
                    if target in draw_map:
                        record['evaluation'] = _evaluate(masks_data, 'framework', fixed, record, draw_map[target])
                    records[reference] = record
                    added += 1
                except (KeyError, TypeError, ValueError):
                    pass
    if reference in killer_seals:
        records = shadow['models']['killer']['records']
        seal = killer_seals[reference]
        if reference not in records and isinstance(seal, dict) and seal.get('publication_state') == 'published' and str(seal.get('target_period')) == target:
            try:
                fixed = killer_fixed(masks_data, seal)
                item, base = optimize_killer(masks_data, fixed, (seal['p1'], seal['p2']))
                record = {'ref_period': reference, 'target_period': target, 'source': 'live',
                          'created_at': beijing_now().isoformat(), **item,
                          'ai_response': {key: seal.get(key) for key in ('kills', 'he_all', 'he_q3', 'he_h3', 'p1', 'p2')}}
                if target in draw_map:
                    record['evaluation'] = _evaluate(masks_data, 'killer', fixed, record, draw_map[target])
                records[reference] = record
                added += 1
            except (KeyError, TypeError, ValueError):
                pass
    for model in ('framework', 'killer'):
        records = shadow['models'][model]['records']
        for ref, record in records.items():
            if isinstance(record, dict) and not record.get('evaluation'):
                target = record.get('target_period')
                if target in draw_map:
                    try:
                        if model == 'framework':
                            fixed = framework_fixed(masks_data, record['ai_response'])
                        else:
                            fixed = killer_fixed(masks_data, record['ai_response'])
                        record['evaluation'] = _evaluate(masks_data, model, fixed, record, draw_map[target])
                    except (KeyError, TypeError, ValueError):
                        pass
    return added


def main(argv=None):
    parser = argparse.ArgumentParser(description='A0 joint-form shadow ledger')
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--backfill', action='store_true')
    args = parser.parse_args(argv)
    root = Path(args.root)
    masks_data = masks()
    existing = _load(root, 'data/joint_form_shadow.json')
    if (isinstance(existing, dict) and existing.get('revision') == REVISION
            and isinstance(existing.get('models'), dict)):
        shadow = existing
        for model in ('framework', 'killer'):
            shadow['models'].setdefault(model, {}).setdefault('records', {})
    else:
        shadow = {'version': 1, 'revision': REVISION, 'updated_at': beijing_now().isoformat(),
                  'models': {'framework': {'records': {}}, 'killer': {'records': {}}}}
    created = 0
    if args.backfill:
        framework_stats = _load(root, 'data/r1_framework_hit_stats.json').get('verified') or {}
        actuals_fw = {}
        for target, record in framework_stats.items():
            actual = record.get('actual') if isinstance(record, dict) else None
            if isinstance(actual, dict) and isinstance(actual.get('numbers'), list) and len(actual['numbers']) >= 4:
                actuals_fw[str(target)] = [int(x) for x in actual['numbers'][:4]]
        killer_stats = _load(root, 'data/r1_stats.json')
        actuals_killer = {}
        for record in killer_stats.get('verified_evaluations') or []:
            if isinstance(record, dict) and isinstance(record.get('actual'), list) and len(record['actual']) == 4:
                actuals_killer[str(record.get('target_period'))] = [int(x) for x in record['actual']]
        created = backfill(root, shadow, masks_data, actuals_fw, actuals_killer)
    created += live(root, shadow, masks_data)
    shadow['updated_at'] = beijing_now().isoformat()
    for model in ('framework', 'killer'):
        shadow['models'][model]['summary'] = _summary(shadow['models'][model]['records'])
    _atomic_json(root / 'data/joint_form_shadow.json', shadow)
    print(json.dumps({'created': created, 'framework': shadow['models']['framework']['summary'],
                      'killer': shadow['models']['killer']['summary'], 'backfill': args.backfill}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
