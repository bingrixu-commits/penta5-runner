"""Compact panel-wide context for the AI kill3 decision (read-only, no provider calls).

Collects the data the operator sees on the panel — other frameworks' latest
published decisions, today's hit rates, version-locked cumulative rates,
stat50/cold/he3 summaries, model tiers and the kill3 own recent record — into a
bounded JSON snapshot. The snapshot is embedded verbatim into the kill3 prompt,
stored in the seal (panel_context / panel_context_sha256) and therefore fully
auditable/replayable. Missing or stale sources are reported as notes instead of
failing the prediction.
"""
import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
MAX_JSON_CHARS = 9000


def _read(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def _pick(mapping, keys):
    if not isinstance(mapping, dict):
        return None
    return {key: mapping[key] for key in keys if mapping.get(key) is not None}


def _percent(value):
    if not isinstance(value, (int, float)):
        return None
    return round(float(value), 2)


def _latest_daily(prefix):
    files = []
    for path in DATA.glob(prefix + '*.json'):
        suffix = path.name[len(prefix):]
        if len(suffix) == 15 and suffix[4] == '-' and suffix[7] == '-' and suffix.endswith('.json'):
            files.append(path)
    return sorted(files)[-1] if files else None


def _strategy_models():
    return _read(DATA / 'strategy_comparison_stats.json', {}) or {}


def _model_tiers():
    tiers = {}
    env = _read(ROOT / '.env', None)
    if not isinstance(env, dict):
        try:
            text = (ROOT / '.env').read_text(encoding='utf-8')
        except OSError:
            text = ''
        env = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            env[key.strip()] = value.strip()
    for key in ('PENTA5_DEEPSEEK_MODEL', 'PENTA5_MODEL_KILLER', 'PENTA5_MODEL_FRAMEWORK',
                'PENTA5_MODEL_HE3', 'PENTA5_MODEL_KILL3', 'PENTA5_MODEL_SIX',
                'PENTA5_MODEL_COLD'):
        if env.get(key):
            tiers[key.replace('PENTA5_MODEL_', '').replace('PENTA5_DEEPSEEK_MODEL', 'global').lower()] = env[key]
    return tiers


def _framework_from_strategy(key, label, models):
    model = models.get(key) if isinstance(models, dict) else None
    if not isinstance(model, dict):
        return None
    latest = model.get('latest') if isinstance(model.get('latest'), dict) else None
    overall = model.get('overall') if isinstance(model.get('overall'), dict) else {}
    return {
        'rule': model.get('strategy_id'),
        'label': model.get('label') or label,
        'latest': _pick(latest, ('reference', 'target', 'model', 'published_at',
                                 'elapsed_seconds')) if latest else None,
        'latest_positions': _pick(latest, ('positions',)) if latest else None,
        'today': _pick(overall, ('hits', 'total', 'percent',
                                 'current_win_streak', 'current_loss_streak')),
        'pending_count': model.get('pending_count'),
        'rejected_count': model.get('rejected_count'),
    }


def _cold_frame(key, prefix):
    path = _latest_daily(prefix)
    if path is None:
        return None
    document = _read(path, {}) or {}
    predictions = document.get('predictions') if isinstance(document.get('predictions'), dict) else {}
    entries = sorted((str(target), pred) for target, pred in predictions.items()
                     if isinstance(pred, dict))
    if not entries:
        return None
    target, prediction = entries[-1]
    return {
        'revision': document.get('revision'),
        'target': target,
        'prediction': _pick(prediction, ('selected_positions', 'selected_tails',
                                         'he2_keep', 'he3_keep',
                                         'publication_state', 'published_at')),
    }


def _he3_frame():
    stats = _read(DATA / 'he3_refine_stats.json', {}) or {}
    comparison = stats.get('comparison') if isinstance(stats.get('comparison'), dict) else {}
    if not stats:
        return None
    return {
        'rule': comparison.get('revision'),
        'today': _pick(stats, ('hits', 'total', 'percent', 'pending_count',
                               'latest_settled_period')),
        'rolling50': _pick(comparison.get('rolling50') or {},
                           ('total', 'hits5', 'percent5', 'hits6', 'percent6', 'published_count')),
        'rolling200': _pick(comparison.get('rolling200') or {},
                            ('total', 'hits5', 'percent5', 'hits6', 'percent6', 'published_count')),
    }


def _stat50_frame():
    compare = _read(DATA / 'stat50_compare_stats.json', {}) or {}
    position = _read(DATA / 'stat50_position_stats.json', {}) or {}
    groups = {}
    for group, block in (compare.get('groups') or {}).items():
        if not isinstance(block, dict):
            continue
        compact = {}
        for metric, data in (block.get('metrics') or {}).items():
            if not isinstance(data, dict):
                continue
            compact[metric] = {'total': data.get('total'),
                               'percent': data.get('percent'),
                               'hits': data.get('hits')}
        groups[group] = compact
    models = {}
    for name, block in (position.get('models') or {}).items():
        metrics = (block or {}).get('metrics') or {}
        models[name] = {metric: _pick(value, ('hits', 'total', 'percent'))
                        for metric, value in metrics.items()}
    if not groups and not models:
        return None
    return {'latest_settled_period': compare.get('latest_settled_period')
            or position.get('latest_settled_period'),
            'compare': groups, 'position_models': models}


def _hitrate_panel():
    document = _read(DATA / 'hitrate_baseline.json', {}) or {}
    rows = []
    for row in document.get('rows') or []:
        if not isinstance(row, dict):
            continue
        rows.append(_pick(row, ('key', 'label', 'rule', 'n', 'rate', 'expected',
                                'delta', 'p', 'verdict')))
    return rows


def _cumulative_panel():
    document = _read(DATA / 'framework_hitrate_cumulative.json', {}) or {}
    rows = []
    for row in document.get('rows') or []:
        if not isinstance(row, dict):
            continue
        rows.append(_pick(row, ('key', 'rule', 'n', 'rate', 'expected', 'coverage',
                                'planned_hit_rate', 'verdict')))
    return rows


def _kill3_self(reference, kill3_stats, kill3_previous):
    previous = None
    if isinstance(kill3_previous, dict):
        previous = _pick(kill3_previous, ('ref_period', 'target_period', 'rule_version',
                                          'positions', 'display_only', 'dropped', 'tail',
                                          'reverse_parity', 'reverse_size',
                                          'generated_at', 'published_at'))
    stats = None
    if isinstance(kill3_stats, dict):
        stats = _pick(kill3_stats, ('total', 'group_hits', 'group_rate',
                                    'positions', 'tail', 'patterns',
                                    'current_win_streak', 'current_loss_streak',
                                    'max_loss_streak', 'four_loss_cycles',
                                    'pending', 'latest_period'))
    if previous is None and stats is None:
        return None
    return {'previous': previous, 'today': stats, 'reference': reference}


def build(reference, target, *, kill3_stats=None, kill3_previous=None, now=None):
    moment = now or datetime.now().astimezone()
    snapshot = {
        'as_of_reference': str(reference),
        'target_period': str(target),
        'read_at': moment.isoformat(timespec='seconds'),
        'note': '同一面板其他框架的最近发布与战绩；只含已发布数据，各条自带 reference/target；'
                '仅供自主决策参考，不是未来概率保证。',
        'frameworks': {},
        'panel_hitrate_10p': _hitrate_panel(),
        'panel_cumulative': _cumulative_panel(),
        'model_tiers': _model_tiers(),
        'kill3_self': _kill3_self(reference, kill3_stats, kill3_previous),
    }
    models = _strategy_models().get('models') or {}
    for key, label in (('killer', '智能深排 killer'), ('framework', '固定八码框架'),
                       ('six', '七项独立框架')):
        frame = _framework_from_strategy(key, label, models)
        if frame:
            snapshot['frameworks'][key] = frame
    six_stats = _read(DATA / 'selected_six_stats.json', {}) or {}
    if six_stats:
        snapshot['frameworks']['selected_six'] = {
            'rule': six_stats.get('rule_revision'),
            'today': _pick(six_stats.get('overall') or {},
                           ('hits', 'total', 'percent', 'current_win_streak', 'current_loss_streak')),
            'cumulative': _pick(six_stats.get('cumulative') or {},
                                ('bets', 'hits', 'rate', 'days')),
            'latest_settled_period': six_stats.get('latest_settled_period'),
            'pending_count': six_stats.get('pending_count'),
        }
    cold = _cold_frame('cold_framework', 'cold_framework_')
    if cold:
        snapshot['frameworks']['cold_framework'] = cold
    he3 = _he3_frame()
    if he3:
        snapshot['frameworks']['he3_refine'] = he3
    stat50 = _stat50_frame()
    if stat50:
        snapshot['frameworks']['stat50'] = stat50
    # Hard bound: drop the largest optional sections until the JSON is small enough.
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if len(payload) > MAX_JSON_CHARS:
        for key in ('panel_cumulative', 'panel_hitrate_10p', 'kill3_self'):
            snapshot.pop(key, None)
            payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if len(payload) <= MAX_JSON_CHARS:
                break
    return snapshot


def prompt_text(snapshot):
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return payload, sha256(payload.encode('utf-8')).hexdigest()
