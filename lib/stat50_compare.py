"""Prospective, fixed 50-draw statistical controls. No model API is called.

Raw seals are append-only evidence; current-day comparisons are disposable
derived files. A comparison accepts only independently published same-input AI
predictions, not their existing aggregate rates.
"""
from copy import deepcopy
from datetime import datetime, timedelta
from fractions import Fraction
from hashlib import sha256
from html import escape
from pathlib import Path
import json
import re
import time

from ai_model_context import MAX_INPUT_BYTES, VERSION as INPUT_VERSION, canonical
from ai_prediction_guard import (PublicationRejected, find_seal, publication_rejection,
                                 publish_saved, read_stats, store_prepared,
                                 validate_killer_response)
from ai_verified_stats import atomic_json, next_period, publication_evidence_error, sync_parent_directory
from framework_hit_stats import _publication
from pro_model_compare import wait_for_shared_input
from stats_history import (beijing_now, business_start, load_snapshot, parse_period,
                           parse_time, period_slot, valid_draw_record,
                           validate_conflicting_periods)

ROOT = Path('/root/penta5-runner')
VERSION = 1
EXPERIMENT_ID = 'stat50-v1'
MARKER = 'STAT50_PREDICTION'
STRATEGY = {'experiment_id': EXPERIMENT_ID, 'history_size': 50,
            'frequency': 'minimum-count-then-smallest-digit',
            'markov_order': 1, 'transition_minimum': 5, 'prior_strength': 10,
            'prior': '(position_count+1)/60', 'tie_break': 'smallest-digit',
            'positions': 'first-four-only', 'mode': 'shadow-only'}
HASH = re.compile(r'[0-9a-f]{64}')
GROUPS = {'framework': ('q_kill1', 'b_kill1'),
          'killer': ('k1', 'k2', 'k3', 'k4')}
AI_LEDGER_FILES = {'framework': 'r1_framework_seals.json', 'killer': 'r1_stats.json'}


def _digest(value):
    return sha256(canonical(value).encode('utf-8')).hexdigest()


def _aware(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parse_time(value) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def validate_history(history, reference, now, *, conflicting_periods=()):
    """Exactly 50 consecutive real opens, oldest first, ending at reference."""
    if not isinstance(history, list) or len(history) != 50:
        raise ValueError('history-must-have-exactly-50-draws')
    conflicts = validate_conflicting_periods(conflicting_periods)
    result = []
    for raw in history:
        if not isinstance(raw, dict):
            raise ValueError('invalid-history-record')
        numbers, period = raw.get('numbers'), raw.get('period')
        # History seals already contain precisely the first four balls. Never
        # silently reinterpret a stored five-ball seal or coerce its digits.
        if (not isinstance(numbers, list) or len(numbers) != 4
                or any(type(d) is not int or not 0 <= d <= 9 for d in numbers)
                or not isinstance(period, str) or not parse_period(period)
                or period != parse_period(period)[0]):
            raise ValueError('invalid-history-first-four')
        record = valid_draw_record(period, numbers, raw.get('opentime'), now)
        if record is None or period in conflicts:
            raise ValueError('invalid-or-conflicting-history-draw')
        if result and next_period(result[-1]['period']) != period:
            raise ValueError('nonconsecutive-history')
        result.append(record)
    if result[-1]['period'] != reference:
        raise ValueError('history-reference-mismatch')
    return result


def history_from_snapshot(snapshot, reference, now):
    recent = snapshot.get('recent')
    if not isinstance(recent, (list, tuple)) or len(recent) != 50:
        raise ValueError('history-must-have-exactly-50-draws')
    records, conflicts = {}, validate_conflicting_periods(snapshot.get('conflicting_periods', ()))
    for raw in snapshot.get('records', []):
        if not isinstance(raw, dict):
            continue
        record = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if record:
            prior = records.get(record['period'])
            if prior and prior != record:
                conflicts.add(record['period'])
            records[record['period']] = record
    chosen = []
    for item in recent:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise ValueError('invalid-recent-history')
        period, digits = item
        if (not isinstance(digits, (list, tuple)) or len(digits) != 4
                or any(type(d) is not int for d in digits)
                or period not in records or records[period]['numbers'] != list(digits)):
            raise ValueError('recent-history-not-real-record')
        chosen.append(records[period])
    result = validate_history(chosen, reference, now, conflicting_periods=conflicts)
    latest = snapshot.get('latest') or {}
    actual = valid_draw_record(latest.get('period'), latest.get('numbers'), latest.get('opentime'), now)
    if actual != result[-1]:
        raise ValueError('latest-reference-history-mismatch')
    return result


def calculate_candidates(history):
    """Exact rational scores, with deterministic numeric tie breaking."""
    if (not isinstance(history, list) or len(history) != 50
            or any(not isinstance(r, dict) or not isinstance(r.get('numbers'), list)
                   or len(r['numbers']) != 4
                   or any(type(d) is not int or not 0 <= d <= 9 for d in r['numbers'])
                   for r in history)):
        raise ValueError('invalid-candidate-history')
    frequency, markov, fallback, transition_counts = [], [], [], []
    for position in range(4):
        values = [r['numbers'][position] for r in history]
        counts = [values.count(d) for d in range(10)]
        cold = min(range(10), key=lambda d: (counts[d], d))
        state = values[-1]
        following = [b for a, b in zip(values, values[1:]) if a == state]
        n = len(following)
        frequency.append(cold)
        fallback.append(n < 5)
        transition_counts.append(n)
        markov.append(cold if n < 5 else min(range(10), key=lambda d:
            (Fraction(following.count(d), 1) + 10 * Fraction(counts[d] + 1, 60), d)))
        # The common denominator (n+10) does not change the minimizing digit.
    return {'frequency': frequency, 'markov': markov,
            'markov_fallback': fallback, 'transition_counts': transition_counts}


def _archive(root, kind, reference, audit, history, generated, now):
    """Check byte provenance and full original prompt, without rebuilding it."""
    relative = f'ai_model_inputs/{kind}-{reference}.json'
    if not isinstance(audit, dict) or audit.get('input_archive') != relative:
        raise ValueError('missing-ai-input-archive')
    for key in ('input_archive_sha256', 'initial_request_prompt_sha256'):
        if not isinstance(audit.get(key), str) or not HASH.fullmatch(audit[key]):
            raise ValueError('invalid-ai-input-hash')
    with (Path(root) / 'data' / relative).open('rb') as handle:
        encoded = handle.read(MAX_INPUT_BYTES + 1)
    if len(encoded) > MAX_INPUT_BYTES or sha256(encoded).hexdigest() != audit['input_archive_sha256']:
        raise ValueError('ai-input-archive-hash-mismatch')
    payload = json.loads(encoded)
    if not isinstance(payload, dict):
        raise ValueError('invalid-ai-input-object')
    prompt, inner = payload.get('initial_prompt'), payload.get('audit')
    observed = _aware(payload.get('observed_at'))
    if (payload.get('version') != INPUT_VERSION or payload.get('model_kind') != kind
            or payload.get('reference_period') != reference
            or payload.get('target_period') != next_period(reference)
            or not isinstance(prompt, str) or not isinstance(inner, dict)
            or inner.get('version') != INPUT_VERSION
            or type(inner.get('context_applied')) is not bool
            or observed is None or not parse_time(history[-1]['opentime']) <= observed <= generated <= now
            or observed >= period_slot(next_period(reference))
            or inner.get('initial_request_prompt_sha256') != sha256(prompt.encode('utf-8')).hexdigest()
            or inner.get('context_sha256') != _digest(inner.get('body'))):
        raise ValueError('invalid-ai-original-input-evidence')
    expected_audit = dict(inner, input_archive=relative,
                          input_archive_sha256=sha256(encoded).hexdigest())
    for extra_key in ('panel_context', 'panel_context_sha256', 'sent_prompt_sha256'):
        if extra_key in audit:
            expected_audit[extra_key] = audit[extra_key]
    if audit != expected_audit:
        raise ValueError('invalid-ai-original-input-evidence')
    lines = prompt.splitlines()
    expected = ', '.join(''.join(map(str, item['numbers'])) for item in history)
    if len(lines) < 2 or reference not in lines[0] or lines[1] != expected:
        raise ValueError('ai-input-not-same-50-history')
    policy = inner.get('request_policy')
    body = inner.get('body')
    saved_strategy = body.get('strategy') if isinstance(body, dict) else None
    from ai_model_context import POLICY_STRATEGIES
    if (isinstance(saved_strategy, dict)
            and saved_strategy.get('strategy_id') in POLICY_STRATEGIES.values() and policy is None):
        raise ValueError('missing-independent-ai-request-policy')
    if policy is not None:
        from ai_model_context import (REQUEST_POLICY, REQUEST_DEFINITION_VERSION,
                                      render_independent_request)
        if (policy != REQUEST_POLICY
                or type(inner.get('request_definition_version')) is not int
                or inner.get('request_definition_version') != REQUEST_DEFINITION_VERSION
                or inner.get('context_applied') is not True
                or not isinstance(body, dict) or body.get('as_of') != payload.get('observed_at')
                or prompt != render_independent_request(inner.get('body'), history, kind)):
            raise ValueError('invalid-independent-ai-request')
        # base_prompt_sha256 identifies the unchanged legacy caller text;
        # that text is intentionally absent from the isolated request. The
        # saved actual request above is reconstructed and hash-verified.
        if not isinstance(inner.get('base_prompt_sha256'), str) or not HASH.fullmatch(inner['base_prompt_sha256']):
            raise ValueError('invalid-ai-base-prompt-hash')
        return sha256(encoded).hexdigest()
    marker = '\n\n【后台结构化参考与本模型真实已封存战绩】\n'
    if inner['context_applied'] is True and marker not in prompt:
        raise ValueError('missing-ai-context-boundary')
    base = prompt.split(marker, 1)[0] if inner['context_applied'] is True else prompt
    if inner.get('base_prompt_sha256') != sha256(base.encode('utf-8')).hexdigest():
        raise ValueError('invalid-ai-base-prompt-hash')
    return sha256(encoded).hexdigest()


def _seal_body(seal):
    return {key: deepcopy(seal.get(key)) for key in
            ('experiment_id', 'comparison_version', 'strategy', 'ref_period', 'history',
             'history_sha256', 'model_context_audit', 'response')}


def _validate_saved(seal, reference, now, *, published=True):
    if (not isinstance(seal, dict) or seal.get('experiment_id') != EXPERIMENT_ID
            or type(seal.get('comparison_version')) is not int or seal['comparison_version'] != VERSION
            or _digest(seal.get('strategy')) != _digest(STRATEGY) or seal.get('ref_period') != reference
            or seal.get('target_period') != next_period(reference)
            or seal.get('seal_sha256') != _digest(_seal_body(seal))):
        raise ValueError('invalid-stat50-seal')
    history = validate_history(seal.get('history'), reference, now)
    if (seal.get('history_sha256') != _digest(history)
            or _digest(seal.get('response')) != _digest(calculate_candidates(history))):
        raise ValueError('stat50-sealed-input-or-candidate-changed')
    if seal.get('card_html') != _card(reference, seal['response']):
        raise ValueError('stat50-sealed-card-changed')
    opened, generated = _aware(seal.get('reference_open_time')), _aware(seal.get('generated_at'))
    cutoff = period_slot(next_period(reference))
    if (type(seal.get('evidence_version')) is not int or seal['evidence_version'] != 2
            or opened != parse_time(history[-1]['opentime']) or generated is None
            or not opened <= generated < cutoff or generated > now):
        raise ValueError('invalid-stat50-generation-evidence')
    if published or seal.get('publication_state') == 'published':
        publication = _aware(seal.get('published_at'))
        if (seal.get('publication_state') != 'published' or publication is None
                or not generated <= publication < cutoff or publication > now):
            raise ValueError('invalid-stat50-publication-evidence')
    return history


def _killer_proof(seal, now):
    if not isinstance(seal, dict):
        raise ValueError('invalid-killer-seal')
    reference, target = seal.get('ref_period'), seal.get('target_period')
    if not isinstance(reference, str) or not parse_period(reference) or target != next_period(reference):
        raise ValueError('invalid-killer-target')
    opened, generated, published = map(lambda k: _aware(seal.get(k)),
                                     ('reference_open_time', 'generated_at', 'published_at'))
    if (type(seal.get('evidence_version')) is not int or seal['evidence_version'] != 2
            or seal.get('publication_state') != 'published' or not opened or not generated or not published
            or not period_slot(reference) <= opened < period_slot(reference) + timedelta(minutes=1)
            or not opened <= generated <= published < period_slot(target) or published > now):
        raise ValueError('invalid-killer-publication')
    kills = seal.get('kills')
    if not isinstance(kills, dict):
        raise ValueError('invalid-killer-position-map')
    response = dict(kills, he_all=seal.get('he_all'), he_q3=seal.get('he_q3'),
                    he_h3=seal.get('he_h3'), exclude_patterns=[seal.get('p1'), seal.get('p2')])
    validated = validate_killer_response(response)
    proof = {key: deepcopy(seal.get(key)) for key in ('ref_period', 'target_period', 'evidence_version',
             'publication_state', 'reference_open_time', 'generated_at', 'published_at')}
    proof['response'] = validated
    return proof


def _new_group(kind):
    models = ['frequency', 'markov', kind]
    return {'models': models, 'matched_count': 0, 'matched_targets': [], 'pending_count': 0,
            'unpaired_count': 0, 'rejected_count': 0, 'latest_settled_period': None,
            'metrics': {key: {'total': 0, 'hits': {m: 0 for m in models},
                              'percent': {m: None for m in models}} for key in GROUPS[kind]}}


def _stat_proof(seal):
    return {key: deepcopy(seal.get(key)) for key in ('ref_period', 'target_period', 'evidence_version',
            'publication_state', 'reference_open_time', 'generated_at', 'published_at', 'response',
            'history_sha256', 'seal_sha256')}


def summarize_stat50(stat_seals, ai_ledgers, records, now, *, root=ROOT, conflicting_periods=(),
                     verified_out=None, strategy_ids=None):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('stat50-clock-must-be-aware')
    start = business_start(now)
    if not isinstance(stat_seals, dict) or not isinstance(ai_ledgers, dict):
        raise ValueError('invalid-stat50-ledgers')
    publications, attempts = stat_seals.get('prediction_seals', {}), stat_seals.get('attempts', {})
    if not isinstance(publications, dict) or not isinstance(attempts, dict):
        raise ValueError('invalid-stat50-index')
    cohort = {ref for ref in set(publications) | set(attempts)
              if isinstance(ref, str) and parse_period(ref) and ref == parse_period(ref)[0]
              and business_start(period_slot(next_period(ref))) == start}
    if not isinstance(records, (list, tuple)):
        raise ValueError('invalid-stat50-actual-records')
    actuals, conflicts = {}, validate_conflicting_periods(conflicting_periods)
    for raw in records:
        if not isinstance(raw, dict):
            continue
        actual = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), now)
        if actual:
            previous = actuals.get(actual['period'])
            if previous and previous != actual:
                conflicts.add(actual['period'])
            actuals[actual['period']] = actual
    summary = {'version': VERSION, 'experiment_id': EXPERIMENT_ID,
               'business_start': start.isoformat(), 'biz_date': start.strftime('%Y-%m-%d'),
               'updated_at': now.isoformat(), 'groups': {kind: _new_group(kind) for kind in GROUPS}}
    for kind, keys in GROUPS.items():
        group, rejected = summary['groups'][kind], set()
        if strategy_ids is not None:
            group.update(strategy_id=strategy_ids[kind], historical_policy_count=0)
        ledger = ai_ledgers.get(kind, {})
        if not isinstance(ledger, dict) or not isinstance(ledger.get('prediction_seals', {}), dict):
            raise ValueError('invalid-ai-comparison-ledger')
        seals = ledger.get('prediction_seals', {})
        for reference in sorted(cohort):
            target, stat, ai = next_period(reference), publications.get(reference), seals.get(reference)
            try:
                if stat is None or ai is None:
                    group['unpaired_count'] += 1
                    continue
                history = _validate_saved(stat, reference, now)
                _archive(root, 'framework', reference, stat.get('model_context_audit'), history,
                         parse_time(stat['generated_at']), now)
                for item in history:
                    observed = actuals.get(item['period'])
                    if item['period'] in conflicts or (observed and observed != item):
                        raise ValueError('conflicting-sealed-source-history')
                proof = _publication(ai, now) if kind == 'framework' else _killer_proof(ai, now)
                if proof['ref_period'] != reference or proof['target_period'] != target:
                    raise ValueError('ai-comparison-target-mismatch')
                _archive(root, kind, reference, ai.get('model_context_audit'), history,
                         parse_time(proof['generated_at']), now)
                if strategy_ids is not None:
                    method = ai['model_context_audit']['body'].get('strategy')
                    if not isinstance(method, dict) or method.get('strategy_id') != strategy_ids[kind]:
                        group['historical_policy_count'] += 1
                        continue
                actual = actuals.get(target)
                if target in conflicts:
                    raise ValueError('conflicting-target-draw')
                if actual is None:
                    group['pending_count'] += 1
                    continue
                if not start <= parse_time(actual['opentime']) <= now:
                    continue
                if publication_evidence_error(stat, actual) or publication_evidence_error(proof, actual):
                    raise ValueError('invalid-target-publication')
                if verified_out is not None:
                    verified_out.setdefault(kind, {})[target] = {
                        'ref_period': reference, 'target_period': target,
                        'actual': deepcopy(actual), 'stat_publication': _stat_proof(stat),
                        'ai_publication': deepcopy(proof), 'history_sha256': stat['history_sha256'],
                        'source_input_sha256': ai['model_context_audit']['input_archive_sha256']}
                for i, key in enumerate(keys):
                    metric = group['metrics'][key]
                    metric['total'] += 1
                    exclusions = {'frequency': stat['response']['frequency'][i],
                                  'markov': stat['response']['markov'][i],
                                  kind: proof['response'][key] if kind == 'framework' else proof['response'][key][0]}
                    for model, excluded in exclusions.items():
                        metric['hits'][model] += int(actual['numbers'][i] != excluded)
                group['matched_targets'].append(target)
            except (ValueError, TypeError, KeyError, OverflowError, OSError):
                rejected.add(target)
                group['unpaired_count'] += 1
        group['matched_count'] = len(group['matched_targets'])
        group['latest_settled_period'] = group['matched_targets'][-1] if group['matched_targets'] else None
        group['rejected_count'] = len(rejected)
        for metric in group['metrics'].values():
            if metric['total']:
                metric['percent'] = {m: round(100 * h / metric['total'], 2) for m, h in metric['hits'].items()}
    return summary


def _loader(root):
    return lambda **kw: load_snapshot(state_path=Path(root) / 'data' / 'state.json',
                                      cache_path=Path(root) / 'data' / 'stats_draw_history.json', **kw)


def refresh_stat50_stats(snapshot=None, root=ROOT):
    import fcntl
    root = Path(root)
    with (root / 'data' / 'stat50_compare_stats.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        snapshot = snapshot if snapshot is not None else _loader(root)(persist=False)
        stat = read_stats(root / 'data' / 'stat50_compare_seals.json')
        ai = {kind: read_stats(root / 'data' / AI_LEDGER_FILES[kind]) for kind in GROUPS}
        # Current-day source verification remains bounded to at most 288 targets.
        # Older daily files retain their original proofs and actuals untouched.
        day = business_start(snapshot['now']).strftime('%Y-%m-%d')
        directory = root / 'data' / 'stat50_compare_verified'
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        sync_parent_directory(directory)
        path = directory / f'{day}.json'
        previous = read_stats(path)
        if previous and (type(previous.get('version')) is not int or previous['version'] != VERSION
                         or previous.get('experiment_id') != EXPERIMENT_ID or previous.get('biz_date') != day
                         or not isinstance(previous.get('groups'), dict)
                         or set(previous['groups']) != set(GROUPS)):
            raise ValueError('invalid-stat50-verified-archive')
        retained = deepcopy(previous) if previous else {
            'version': VERSION, 'experiment_id': EXPERIMENT_ID, 'biz_date': day,
            'groups': {kind: {} for kind in GROUPS}}
        saved_actuals = {}
        for kind, entries in retained['groups'].items():
            if not isinstance(entries, dict):
                raise ValueError('invalid-stat50-verified-index')
            for target, row in entries.items():
                if not isinstance(row, dict) or row.get('target_period') != target:
                    raise ValueError('invalid-stat50-verified-entry')
                reference = row.get('ref_period')
                actual = row.get('actual')
                if (not isinstance(actual, dict) or actual.get('period') != target
                        or valid_draw_record(target, actual.get('numbers'), actual.get('opentime'), snapshot['now']) != actual
                        or target != next_period(reference)):
                    raise ValueError('invalid-stat50-retained-actual')
                if business_start(parse_time(actual['opentime'])).strftime('%Y-%m-%d') != day:
                    raise ValueError('incorrect-stat50-verified-business-day')
                stat_seal = stat.get('prediction_seals', {}).get(reference)
                ai_seal = ai[kind].get('prediction_seals', {}).get(reference)
                history = _validate_saved(stat_seal, reference, snapshot['now'])
                _archive(root, 'framework', reference, stat_seal.get('model_context_audit'), history,
                         parse_time(stat_seal['generated_at']), snapshot['now'])
                proof = _publication(ai_seal, snapshot['now']) if kind == 'framework' else _killer_proof(ai_seal, snapshot['now'])
                _archive(root, kind, reference, ai_seal.get('model_context_audit'), history,
                         parse_time(proof['generated_at']), snapshot['now'])
                expected = {'ref_period': reference, 'target_period': target,
                            'actual': actual, 'stat_publication': _stat_proof(stat_seal),
                            'ai_publication': proof, 'history_sha256': stat_seal['history_sha256'],
                            'source_input_sha256': ai_seal['model_context_audit']['input_archive_sha256']}
                if (row != expected or publication_evidence_error(stat_seal, actual)
                        or publication_evidence_error(proof, actual)):
                    raise ValueError('stat50-retained-publication-conflict')
                prior = saved_actuals.get(target)
                if prior is not None and prior != actual:
                    raise ValueError('stat50-retained-target-conflict')
                saved_actuals[target] = actual
        observed, conflicts = {}, validate_conflicting_periods(snapshot.get('conflicting_periods', ()))
        for raw in snapshot['records']:
            record = valid_draw_record(raw.get('period'), raw.get('numbers'), raw.get('opentime'), snapshot['now'])
            if record:
                old = observed.get(record['period'])
                if old and old != record:
                    conflicts.add(record['period'])
                observed[record['period']] = record
        for target, actual in saved_actuals.items():
            current = observed.get(target)
            if current and current != actual:
                conflicts.add(target)
        # Retained actuals keep older verified pairs independently auditable.
        # New source disagreements remain conflicts and never overwrite them.
        records = list(saved_actuals.values()) + list(snapshot['records'])
        additions = {}
        from ai_model_context import POLICY_STRATEGIES
        summary = summarize_stat50(stat, ai, records, snapshot['now'], root=root,
                                  conflicting_periods=conflicts, verified_out=additions,
                                  strategy_ids=POLICY_STRATEGIES)
        for kind, entries in additions.items():
            for target, row in entries.items():
                prior = retained['groups'][kind].get(target)
                if prior is not None and prior != row:
                    raise ValueError('stat50-verified-entry-cannot-be-replaced')
                retained['groups'][kind].setdefault(target, row)
        atomic_json(path, retained)
        atomic_json(root / 'data' / 'stat50_compare_stats.json', summary)
        return summary


def _status(root, status, reference, clock, error_code=None):
    atomic_json(Path(root) / 'data' / 'stat50_compare_status.json',
                {'version': VERSION, 'experiment_id': EXPERIMENT_ID, 'status': status,
                 'updated_at': clock().isoformat(), 'reference_period': reference,
                 'target_period': next_period(reference), 'error_code': error_code})


def _card(reference, response):
    digits = lambda values: ' / '.join(map(str, values))
    fallback = '、'.join('千百十个'[i] for i, yes in enumerate(response['markov_fallback']) if yes) or '无'
    return (f'<!-- {MARKER}_START -->\n'
            '<section style="margin:20px auto;padding:16px;background:#fff;border:1px solid #64748b;border-radius:8px;max-width:1200px">'
            '<b>近50期定位排除统计候选 · 固定实验 stat50-v1</b>'
            f'<div>参考期：{escape(reference)}；预测目标期：{escape(next_period(reference))}</div>'
            '<div>以下依次为千 / 百 / 十 / 个，每个位置排除1码。</div>'
            f'<div>固定频率候选：{digits(response["frequency"])}</div>'
            f'<div>一阶转移候选：{digits(response["markov"])}</div>'
            f'<div>转移样本不足5次、回退频率的位置：{fallback}</div>'
            '<div>严格使用同一连续50期真实开奖的前4球；固定参数，仅作对照实验，不替换正式AI。当前候选不构成命中提升证据。</div>'
            '</section>\n' + f'<!-- {MARKER}_END -->')


def run_once(*, root=ROOT, loader=None, clock=beijing_now, sleep=time.sleep,
             monotonic=time.monotonic, writer=None):
    """Caller owns independent flock. Same reference can never replace a seal."""
    root = Path(root)
    loader = loader or _loader(root)
    if writer is None:
        from panel_io import update_panel
        writer = lambda transform: update_panel(transform, path=root / 'panel.html')
    snapshot = loader(persist=False)
    reference = (snapshot.get('latest') or {}).get('period')
    if not isinstance(reference, str) or not parse_period(reference) or reference != parse_period(reference)[0]:
        raise PublicationRejected('no-current-stat50-reference')
    path = root / 'data' / 'stat50_compare_seals.json'
    ledger = read_stats(path)
    mapped = ledger.get('prediction_seals', {})
    if not isinstance(mapped, dict) or (reference in mapped and not isinstance(mapped[reference], dict)):
        raise ValueError('invalid-saved-stat50-evidence')
    existing = find_seal(ledger, reference)
    try:
        if existing is not None:
            history = _validate_saved(existing, reference, clock(), published=False)
            _archive(root, 'framework', reference, existing.get('model_context_audit'), history,
                     parse_time(existing['generated_at']), clock())
            if existing.get('card_html') != _card(reference, existing['response']):
                raise ValueError('saved-stat50-card-changed')
            published = publish_saved(ledger, path, reference, MARKER,
                                      _checked_loader(loader, reference, history, clock, root=root,
                                                      audit=existing['model_context_audit']),
                                      writer=writer, clock=clock)
            status = 'preserved' if published else 'rejected'
            _status(root, status, reference, clock, None if published else 'publication-rejected')
            return {'status': status, 'published': published, 'ref_period': reference}
        attempts = ledger.setdefault('attempts', {})
        if not isinstance(attempts, dict):
            raise ValueError('invalid-stat50-attempt-index')
        if reference in attempts:
            raise PublicationRejected('stat50-reference-already-attempted')
        attempts[reference] = {'started_at': clock().isoformat(), 'experiment_id': EXPERIMENT_ID}
        atomic_json(path, ledger)
        _status(root, 'waiting', reference, clock)
        (prompt, audit), snapshot = wait_for_shared_input(reference, root=root, loader=loader,
                                    clock=clock, sleep=sleep, monotonic=monotonic)
        now = clock()
        if publication_rejection(reference, snapshot, now):
            raise PublicationRejected('stat50-publication-deadline')
        history = history_from_snapshot(snapshot, reference, now)
        _archive(root, 'framework', reference, audit, history, now, now)
        response = calculate_candidates(history)
        candidate = {'experiment_id': EXPERIMENT_ID, 'comparison_version': VERSION,
                     'strategy': deepcopy(STRATEGY), 'ref_period': reference,
                     'history': history, 'history_sha256': _digest(history),
                     'model_context_audit': audit, 'response': response}
        candidate['seal_sha256'] = _digest(_seal_body(candidate))
        checked_loader = _checked_loader(loader, reference, history, clock, root=root, audit=audit)
        store_prepared(ledger, path, checked_loader(persist=False), candidate, _card(reference, response),
                       generated_at=now, enqueue=False)
        published = publish_saved(ledger, path, reference, MARKER, checked_loader, writer=writer, clock=clock)
        _status(root, 'published' if published else 'rejected', reference, clock,
                None if published else 'publication-rejected')
        return {'status': 'published' if published else 'rejected',
                'published': published, 'ref_period': reference}
    except Exception as error:
        _status(root, 'rejected' if isinstance(error, PublicationRejected) else 'failed', reference,
                clock, 'publication-rejected' if isinstance(error, PublicationRejected) else 'input-or-evidence-error')
        raise


def _checked_loader(loader, reference, history, clock, *, root, audit):
    """Recheck all source evidence under the shared panel writer's lock."""
    def checked(**kwargs):
        current = loader(**kwargs)
        try:
            observed = history_from_snapshot(current, reference, clock())
            conflicts = validate_conflicting_periods(current.get('conflicting_periods', ()))
            if observed != history or next_period(reference) in conflicts:
                raise ValueError('stat50-original-history-changed')
            _archive(root, 'framework', reference, audit, history, clock(), clock())
        except (ValueError, TypeError, KeyError, OverflowError, OSError) as error:
            raise PublicationRejected('stat50-source-evidence-changed') from error
        return current
    return checked


def main():
    import fcntl
    with (ROOT / 'data' / 'stat50_compare_job.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            result = run_once()
            code = 0 if result['published'] else 1
        except Exception as error:
            print('Stat50 candidate not published; error_type=' + type(error).__name__)
            code = 1
        try:
            from stat50_compare_view import refresh_stat50_panel
            refresh_stat50_panel(root=ROOT)
        except Exception as error:
            print('Stat50 comparison view deferred; error_type=' + type(error).__name__)
        return code


if __name__ == '__main__':
    raise SystemExit(main())
