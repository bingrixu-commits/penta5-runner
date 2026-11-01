"""Compact panel-wide context for the AI kill3 / kill1 decision (read-only, no provider calls).

Collects the data the operator sees on the panel — other frameworks' latest
published decisions, today's hit rates, version-locked cumulative rates,
stat50/cold/he3 summaries, model tiers, 三数共现避开1/2/3组参考 and the kill3 own recent record — into a
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
MAX_JSON_CHARS_RICH = 12000


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
                'PENTA5_MODEL_KILL9', 'PENTA5_MODEL_COLD'):
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


def _kill1_self(reference, kill1_stats, kill1_previous):
    previous = None
    if isinstance(kill1_previous, dict):
        previous = _pick(kill1_previous, ('ref_period', 'target_period', 'rule_version',
                                          'positions', 'counted', 'tail', 'tail_policy',
                                          'form_type', 'form',
                                          'generated_at', 'published_at'))
    stats = None
    if isinstance(kill1_stats, dict):
        stats = _pick(kill1_stats, ('total', 'group_hits', 'group_rate',
                                    'positions', 'tail', 'form',
                                    'current_win_streak', 'current_loss_streak',
                                    'max_loss_streak', 'four_loss_cycles',
                                    'pending', 'latest_period'))
    if previous is None and stats is None:
        return None
    return {'previous': previous, 'today': stats, 'reference': reference}


def _kill9_self(reference, kill9_stats, kill9_previous):
    previous = None
    if isinstance(kill9_previous, dict):
        previous = _pick(kill9_previous, ('ref_period', 'target_period', 'rule_version',
                                          'parity_form', 'size_form', 'prime_form', 'middle_form',
                                          'he2_keep', 'he3_keep',
                                          'generated_at', 'published_at'))
    stats = None
    if isinstance(kill9_stats, dict):
        stats = _pick(kill9_stats, ('total', 'group_hits', 'group_rate',
                                    'parity', 'size', 'prime', 'middle',
                                    'he2_keep', 'he3_keep',
                                    'current_win_streak', 'current_loss_streak',
                                    'max_loss_streak', 'four_loss_cycles',
                                    'pending', 'latest_period'))
    if previous is None and stats is None:
        return None
    return {'previous': previous, 'today': stats, 'reference': reference}


def _cold_stats_frame():
    """Compact snapshot of the restored cold-code statistics blocks (read-only)."""
    observation = _read(DATA / 'cold_observation.json', {}) or {}
    positions = {}
    for key in ('q', 'b', 's', 'g'):
        block = (observation.get('positions') or {}).get(key)
        if not isinstance(block, dict):
            continue
        items = [item for item in (block.get('items') or []) if isinstance(item, dict)][:1]
        positions[key] = {'cold': block.get('cold'),
                          'top': [{'v': item.get('value'), 'n': item.get('count'),
                                   'omit': item.get('current_omission')} for item in items]}
    tails = {}
    for key, block in (observation.get('tails') or {}).items():
        if not isinstance(block, dict):
            continue
        items = [item for item in (block.get('items') or []) if isinstance(item, dict)][:1]
        tails[str(key)] = {'cold': block.get('cold'),
                           'top': [{'v': item.get('value'), 'n': item.get('count'),
                                    'omit': item.get('current_omission')} for item in items]}
    shapes = {}
    for kind, block in (observation.get('shapes') or {}).items():
        if isinstance(block, dict):
            cold = block.get('cold') or []
            shapes[str(kind)] = cold[0] if cold else None
    report = _read(DATA / 'position_cold_report.json', {}) or {}
    position_windows = [entry for entry in (_pick(entry, ('window', 'rank', 'line'))
                                            for entry in (report.get('windows') or [])
                                            if isinstance(entry, dict)) if entry]
    hit_stats = _read(DATA / 'cold_hit_stats.json', {}) or {}
    hit_windows = []
    for entry in (hit_stats.get('windows') or []):
        if not isinstance(entry, dict):
            continue
        tops = {}
        for rank, block in (entry.get('tops') or {}).items():
            if isinstance(block, dict):
                tops[str(rank)] = _pick(block, ('rate', 'baseline',
                                                'current_win_streak', 'current_loss_streak'))
        hit_windows.append({'window': entry.get('window'), 'samples': entry.get('samples'),
                            'tops': tops})
    if not observation and not report and not hit_stats:
        return None
    return {'reference_period': observation.get('reference_period'),
            'sample_count': observation.get('sample_count'),
            'window_size': observation.get('window_size'),
            'positions': positions, 'tails': tails, 'shapes': shapes,
            'position_cold': {'as_of_period': report.get('as_of_period'),
                              'windows': position_windows},
            'cold_hit_stats': {'as_of_period': hit_stats.get('as_of_period'),
                               'windows': hit_windows},
            'note': '冷码为历史样本排序统计，不是下期概率保证；仅作参考'}


def _ai_self_frame(kind):
    """Latest published decision + last-10 settled form of another AI framework (read-only)."""
    document = _read(DATA / kind / 'seals.json', {}) or {}
    records = [item for item in (document.get('records') or [])
               if isinstance(item, dict) and item.get('publication_state') == 'published']
    fields = ['ref_period', 'target_period', 'rule_version', 'positions', 'tail', 'published_at']
    if kind == 'kill3_tail':
        fields += ['display_only', 'counted', 'reverse_parity', 'reverse_size']
    elif kind == 'kill1_tail':
        fields += ['counted', 'form_type', 'form']
    elif kind == 'kill9_tail':
        fields += ['parity_form', 'size_form', 'prime_form', 'middle_form', 'he2_keep', 'he3_keep']
    latest = _pick(records[-1], fields) if records else None
    settlements = _read(DATA / kind / 'settlements.json', {}) or {}
    verified = [item for item in (settlements.get('records') or [])
                if isinstance(item, dict) and item.get('verified')]
    recent = verified[-10:]
    hits = sum(1 for item in recent if item.get('group_hit') is True)
    return {'latest': latest,
            'last10': {'n': len(recent), 'hits': hits,
                       'rate': round(hits / len(recent), 4) if recent else None,
                       'latest_target': recent[-1].get('target_period') if recent else None}}


def _ai_self_frames():
    return {'kill3': _ai_self_frame('kill3_tail'), 'kill1': _ai_self_frame('kill1_tail'),
            'kill9': _ai_self_frame('kill9_tail')}


def _three_avoid2():
    """三数共现避开 1/2/3 组参考（与面板核验块同源 data/three_avoid2_daily.json，只读）。"""
    doc = _read(DATA / 'three_avoid2_daily.json', None) or {}
    if not isinstance(doc, dict) or not doc.get('business_day'):
        return None
    groups = {}
    latest_groups = ((doc.get('latest_groups') or {}).get('groups')) or {}
    pending_groups = ((doc.get('pending_groups') or {}).get('groups')) or {}
    for key, label in (('threeDigitAvoid', '避开1组'), ('threeDigitHit', '避开2组'),
                       ('threeDigitAvoid2', '避开3组')):
        latest = latest_groups.get(key) or {}
        pending = pending_groups.get(key) or {}
        item = {'value': pending.get('value') or latest.get('value')}
        if isinstance(latest.get('success'), bool):
            item['latest_success'] = latest['success']
        groups[label] = item
    current = doc.get('current') or {}
    previous = doc.get('previous') or {}
    return {
        'as_of_period': doc.get('as_of_period'),
        'groups': groups,
        'latest_period': (doc.get('latest_groups') or {}).get('period'),
        'latest_merged_success': (doc.get('latest_groups') or {}).get('merged_success'),
        'pending_period': (doc.get('pending_groups') or {}).get('period'),
        'today_merged': _pick(current, ('hit', 'total', 'rate', 'baseline')),
        'prev_day_merged': _pick(previous, ('hit', 'total', 'rate', 'baseline', 'sealed')),
    }


def build(reference, target, *, kill3_stats=None, kill3_previous=None,
          kill1_stats=None, kill1_previous=None,
          kill9_stats=None, kill9_previous=None,
          extra_panel_input=False, now=None):
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
        'three_avoid2': _three_avoid2(),
        'kill3_self': _kill3_self(reference, kill3_stats, kill3_previous),
    }
    if kill1_stats is not None or kill1_previous is not None:
        snapshot['kill1_self'] = _kill1_self(reference, kill1_stats, kill1_previous)
    if kill9_stats is not None or kill9_previous is not None:
        snapshot['kill9_self'] = _kill9_self(reference, kill9_stats, kill9_previous)
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
    he3 = _he3_frame()
    if he3:
        snapshot['frameworks']['he3_refine'] = he3
    stat50 = _stat50_frame()
    if stat50:
        snapshot['frameworks']['stat50'] = stat50
    if extra_panel_input:
        snapshot['cold_stats'] = _cold_stats_frame()
        snapshot['other_ai_self'] = _ai_self_frames()
    # Hard bound: drop the largest optional sections until the JSON is small enough.
    # extra_panel_input 多喂冷码统计与其它 AI 框架实值，使用独立上限，避免挤压 kill3/kill1。
    limit = MAX_JSON_CHARS_RICH if extra_panel_input else MAX_JSON_CHARS
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    if len(payload) > limit:
        for key in ('panel_cumulative', 'panel_hitrate_10p', 'kill3_self', 'kill1_self',
                    'kill9_self', 'cold_stats', 'other_ai_self', 'three_avoid2'):
            snapshot.pop(key, None)
            payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if len(payload) <= limit:
                break
    return snapshot


def prompt_text(snapshot):
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return payload, sha256(payload.encode('utf-8')).hexdigest()
