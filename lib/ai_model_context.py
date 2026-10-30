"""Independent, bounded AI requests with immutable same-reference input archives.

New requests contain the exact real 50-draw window, the assigned method, and
task/output definitions. The caller's legacy prompt identifies an archive but
is never included in a new request. Existing archives keep their original bytes.
"""
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import tempfile

from ai_prediction_guard import validate_framework_response
from ai_verified_stats import (next_period, prediction_outcome,
                               publication_evidence_error, validate_killer_ledger)
from framework_hit_stats import _outcomes as framework_outcomes
from position_input_features import build_position_features, history_from_snapshot
from strategy_features import (build_strategy, KILLER_STRATEGY, FRAMEWORK_STRATEGY,
                               LEGACY_KILLER_STRATEGY, LEGACY_FRAMEWORK_STRATEGY)
from stats_history import parse_period, parse_time, period_slot

VERSION = 'ai-model-context-v1'
REQUEST_POLICY = 'independent-strategy-v2'
REQUEST_DEFINITION_VERSION = 2
POLICY_STRATEGIES = {'killer': 'conditional-transition-v2',
                     'framework': 'marginal-frequency-v2'}
MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_CONTEXT_BYTES = 16000
MAX_INPUT_BYTES = 96000
POSITIONS = {'wan': 1, 'qian': 2, 'bai': 3, 'shi': 4}
SUM_POSITIONS = {'wanQianBai': [1, 2, 3], 'qianBaiShi': [2, 3, 4]}
SOURCE_FILES = {'claude_ensemble': 'algorithm-projects/claude-ensemble/state.json',
                'claude_stable': 'algorithm-projects/claude-stable/state.json',
                'adaptive_exclusion': 'adaptive_exclusion_sidecar.json'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _sync_directory(path):
    """Commit directory entries before an inference may consume this evidence.

    A file fsync alone does not persist a later link or a newly created parent.
    Unsupported filesystems/platforms fail closed rather than claim durability.
    """
    if os.name != 'posix':
        raise OSError('model-input-directory-sync-unsupported')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_archive_directory(directory):
    missing, current = [], directory
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            raise OSError('model-input-archive-root-unavailable')
        current = current.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Include the immediate parent even on retries: the previous mkdir may have
    # succeeded before its parent fsync failed. Never skip that failed commit.
    parents = [created.parent for created in reversed(missing)] + [directory.parent]
    for parent in dict.fromkeys(parents):
        _sync_directory(parent)


def _aware_time(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parse_time(value) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _load(path):
    # A partial concurrent write, missing/oversized file, or unsupported JSON is
    # simply unavailable advice. Never wait, invoke a model, or create a file.
    with path.open('rb') as handle:
        raw = handle.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError('oversized-source')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('source-not-object')
    return value, sha256(raw).hexdigest()


def _digits(value):
    if (not isinstance(value, list) or not 1 <= len(value) <= 2
            or any(type(d) is not int or not 0 <= d <= 9 for d in value)
            or len(set(value)) != len(value)):
        raise ValueError('invalid-digits')
    return sorted(value)


def _claude_item(item):
    if not isinstance(item, dict) or item.get('mode', 'avoid') != 'avoid':
        return None
    kind, key, code = item.get('kind'), item.get('key'), item.get('code')
    if not isinstance(code, str):
        return None
    if kind == 'position' and key in POSITIONS and re.fullmatch('[0-9]', code):
        return {'kind': 'position', 'positions_1based': [POSITIONS[key]], 'exclude': [int(code)]}
    if kind == 'hewei' and key in SUM_POSITIONS and re.fullmatch('[0-9]', code):
        return {'kind': 'sum_tail', 'positions_1based': SUM_POSITIONS[key], 'exclude': [int(code)]}
    chars = {'reverseParity': '[单双]{3,4}', 'reverseSize': '[大小]{3,4}'}
    if kind == 'derivedReverseShape' and key in chars and re.fullmatch(chars[key], code):
        return {'kind': 'parity_shape' if key == 'reverseParity' else 'size_shape',
                'positions_1based': list(range(1, len(code) + 1)), 'exclude': code}
    return None  # Different coverage tasks, arbitrary labels and rates stay out.


def _source(source_id, raw, reference, target, opened, now):
    if source_id == 'adaptive_exclusion':
        if (raw.get('ok') is not True or raw.get('readOnly') is not True
                or raw.get('appliesToTip') is not False or raw.get('sidecar') != 'adaptive-exclusion'):
            raise ValueError('not-readonly-advice')
        entry = raw
    else:
        if raw.get('mode') != 'sidecar-shadow' or not isinstance(raw.get('pending'), list):
            raise ValueError('not-shadow-mode')
        entries = [e for e in raw['pending'] if isinstance(e, dict)
                   and e.get('basePeriod') == reference and e.get('targetPeriod') == target]
        if len(entries) != 1:
            raise ValueError('no-exact-pending')
        entry = entries[0]
    generated = _aware_time(entry.get('generatedAt'))
    if (entry.get('basePeriod') != reference or entry.get('targetPeriod') != target
            or generated is None or not opened <= generated <= now < period_slot(target)):
        raise ValueError('stale-or-unproven-advice')
    if source_id == 'adaptive_exclusion':
        decision = raw.get('decision')
        if not isinstance(decision, dict) or decision.get('period_hint') != target:
            raise ValueError('invalid-decision-target')
        action = decision.get('action')
        if action not in ('EXCLUDE', 'SKIP', 'HALT'):
            raise ValueError('invalid-action')
        advice = []
        if action == 'EXCLUDE':
            excluded = decision.get('excluded')
            if not isinstance(excluded, dict) or set(excluded) != {'1', '2', '3', '4'}:
                raise ValueError('invalid-position-map')
            advice = [{'kind': 'position', 'positions_1based': [i], 'exclude': _digits(excluded[str(i)])}
                      for i in range(1, 5)]
    else:
        snapshot = entry.get('snapshot')
        if not isinstance(snapshot, dict):
            raise ValueError('missing-snapshot')
        snapshot_time = _aware_time(snapshot.get('generatedAt'))
        if (snapshot_time is None or not opened <= snapshot_time <= generated
                or snapshot.get('basePeriod') != reference):
            raise ValueError('invalid-snapshot-time')
        items = snapshot.get('items')
        if not isinstance(items, list) or not 1 <= len(items) <= 32:
            raise ValueError('invalid-items')
        advice = [clean for item in items if (clean := _claude_item(item)) is not None]
        if not advice:
            raise ValueError('no-compatible-advice')
        # Repeated or conflicting advice for a slot is ambiguous, so reject it.
        slots = [(e['kind'], tuple(e['positions_1based'])) for e in advice]
        if len(set(slots)) != len(slots):
            raise ValueError('duplicate-advice-slot')
        action = 'EXCLUDE'
    return {'source_id': source_id, 'status': 'ready', 'source_type': 'shadow_advice',
            'generated_at': generated.isoformat(), 'action': action,
            'performance_claim': False, 'advice': advice}


def _feedback(snapshot, kind, seals, now, *, history=None):
    if kind not in ('killer', 'framework') or not isinstance(seals, dict):
        raise ValueError('invalid-ai-ledger')
    if kind == 'killer':
        validate_killer_ledger(seals, now)
    # Use the exact same 50 draw identities as the raw request. Missing published
    # predictions reduce sample size; they never pull older targets into input.
    history = history if history is not None else history_from_snapshot(snapshot, now=now)
    available = {row['period']: row for row in history}
    published = seals.get('prediction_seals')
    if not isinstance(published, dict):
        raise ValueError('missing-publication-index')
    candidates, seen = [], set()
    for reference, seal in published.items():
        if not isinstance(seal, dict) or seal.get('ref_period') != reference:
            continue
        target = seal.get('target_period')
        actual = available.get(target)
        if not actual:
            continue
        if publication_evidence_error(seal, actual):
            continue
        if target in seen:
            raise ValueError('duplicate-exact-target')
        seen.add(target)
        if kind == 'killer':
            outcome = prediction_outcome(seal, actual['numbers'])
        else:
            response = validate_framework_response(seal.get('response'))
            outcome = framework_outcomes(response, actual['numbers'])
        if not outcome:
            raise ValueError('invalid-published-prediction')
        candidates.append((target, outcome))
    candidates.sort()
    metrics = {}
    for _, outcomes in candidates:
        for name, hit in outcomes.items():
            if type(hit) is not bool:
                raise ValueError('invalid-outcome')
            metric = metrics.setdefault(name, {'hits': 0, 'total': 0})
            metric['hits'] += int(hit)
            metric['total'] += 1
    for name, metric in metrics.items():
        metric['historical_percent'] = round(metric['hits'] / metric['total'] * 100, 2)
        metric['uniform_independent_baseline_percent'] = (
            98.08 if name == 'he2_keep' else 97.44 if name == 'he3_keep'
            else 93.75 if name in ('pat1', 'pat2', 'pattern_ds', 'pattern_dx') else 90.0)
    return {'status': 'ready', 'window': 'same-50-real-history-targets',
            'revision': 'position-feedback50-v1', 'raw_history_count': 50,
            'first_history_target': history[0]['period'], 'last_history_target': history[-1]['period'],
            'history_sha256': sha256(canonical(history).encode('utf-8')).hexdigest(),
            'unpublished_or_ineligible_targets': 50 - len(candidates), 'samples': len(candidates),
            'first_target': candidates[0][0] if candidates else None,
            'last_target': candidates[-1][0] if candidates else None,
            'metrics': metrics, 'next_draw_probability_claim': False}


def build_model_context(snapshot, kind, seals, *, data_dir=Path('/root/penta5-runner/data'),
                        now=None, strategy_id=None):
    now = now or snapshot['now']
    latest = snapshot['latest']
    reference = latest['period']
    target = next_period(reference)
    opened = parse_time(latest['opentime'])
    if (not parse_period(reference) or opened is None or now.tzinfo is None
            or not opened <= now < period_slot(target)):
        raise ValueError('invalid-current-target')
    history = history_from_snapshot(snapshot, now=now)
    if kind not in ('killer', 'framework'):
        raise ValueError('invalid-model-context-kind')
    supported = ((KILLER_STRATEGY, LEGACY_KILLER_STRATEGY) if kind == 'killer'
                 else (FRAMEWORK_STRATEGY, LEGACY_FRAMEWORK_STRATEGY))
    strategy_id = strategy_id or supported[0]
    if strategy_id not in supported:
        raise ValueError('invalid-model-context-strategy')
    strategy = build_strategy(history, reference, strategy_id, now)
    original_features = build_position_features(history, reference, now)
    # Preserve the established positional identity field, but do not mix an
    # omission heuristic, other methods or other models' current predictions
    # into these independent strategies.
    features = {key: value for key, value in original_features.items() if key != 'positions'}
    features['positions'] = {
        key: {'position_1based': item['position_1based'], 'sample_size': item['sample_size']}
        for key, item in original_features['positions'].items()}
    body = {'version': VERSION, 'reference_period': reference, 'target_period': target,
            'as_of': now.isoformat(), 'advisory_only': True, 'sources': [],
            'position_features': features, 'strategy': strategy,
            'cross_model_advice': 'disabled', 'own_verified_feedback': {'status': 'omitted-for-independent-strategy'}}
    if len(canonical(body).encode('utf-8')) > MAX_CONTEXT_BYTES:
        raise ValueError('oversized-context')
    return body


COMMON_TASK_DEFINITION = (
    '历史按开奖时间从旧到新排列，最后一条是参考期已开奖数据；只对下一目标期提出候选。'
    '每条历史只含第1至4球，依次记为千位q、百位b、十位s、个位g；0及前导0有效，第五球不参与。\n'
    '合尾均为指定位置求和后对10取余；all/qbs/qbg/qsg/bsg依次代表千百十个、千百十、千百个、千十个、百十个。'
    '排除成功是目标实际值不等于排除码；形态排除成功是完整四位形态不同。'
    '单双形态按数字奇偶定义，0为双；大小形态中0至4为小、5至9为大。\n'
    '只能依据下方strategy指定方法及固定参数和同一真实50期自主判断；'
    '允许独立得出与其他模型相同数字，禁止为异号强行换码。'
    '所有计数和份额描述历史，样本内拟合不代表事前战绩或未来概率，不得承诺命中率。'
)

KILLER_TASK_DEFINITION = (
    'k1/k2/k3/k4依次对千/百/十/个各排除一码，每项独立核验。'
    'he_all/he_q3/he_h3依次对all/qbs/bsg各排除一个合尾。'
    'exclude_patterns排除两个不同的完整四位形态；每个形态只能全用单双字符或全用大小字符，'
    '可自主选择两个形态的类别，不得把不同类别字符混在同一形态。'
)

KILLER_OUTPUT_DEFINITION = (
    '只输出严格JSON对象，必填字段为k1、k2、k3、k4、he_all、he_q3、he_h3、exclude_patterns、reason。\n'
    'k1/k2/k3/k4各为数组，数组恰好一个0至9整数。'
    'he_all/he_q3/he_h3各为一个0至9整数；所有数字均不得为布尔值或小数。\n'
    'exclude_patterns为恰好两个不同字符串的数组，每个字符串恰好四个单双字符或恰好四个大小字符。'
    'reason为40字以内的历史依据和不确定性说明。最终回答不输出分析过程。'
)

FRAMEWORK_TASK_DEFINITION = (
    'q_kill1/b_kill1分别对千位/百位各排除一码；he_q3_kill/he_h3_kill分别对qbs/bsg各排除一个合尾。'
    'pattern_ds排除按千百十个顺序的完整四位单双形态，pattern_dx排除完整四位大小形态。\n'
    'he2_keep是保留集：计算前四位任意2数的6个位置组合（千百、千十、千个、百十、百个、十个）合尾，'
    '目标期任意一个合尾落入保留集即算该项命中。'
    'he3_keep是保留集：计算前四位任意3数的4个位置组合（千百十、千百个、千十个、百十个）合尾，'
    '目标期任意一个合尾落入保留集即算该项命中。\n'
    'pos_kills的q/b/s/g分别是四位各排两码的单独参考，'
    '不与q_kill1/b_kill1合并，不加入正式8项统计或本条攻击语。'
)

FRAMEWORK_OUTPUT_DEFINITION = (
    '只输出严格JSON对象，必填字段为q_kill1、b_kill1、he_q3_kill、he_h3_kill、'
    'pattern_ds、pattern_dx、he2_keep、he3_keep、pos_kills、reason。\n'
    'q_kill1/b_kill1/he_q3_kill/he_h3_kill各为一个0至9整数，不得为布尔值或小数。'
    'pattern_ds恰好四个单双字符，pattern_dx恰好四个大小字符。\n'
    'he2_keep恰好保留4个不重复ASCII数字且只能从02468中选；'
    'he3_keep恰好保留6个不重复ASCII数字且只能从0123456789中选。'
    '这两个字段输出字符串并保留前导0，也允许包含完全相同数字的JSON数组；禁止补位、重复或默认凑数。\n'
    'pos_kills为对象，q/b/s/g四个数组全部必填，每个数组恰好2个不同的0至9整数。'
    'reason为40字以内的历史依据和不确定性说明。最终回答不输出分析过程。'
)


def render_independent_request(body, history, kind):
    """Render the one known request policy, verifying its entire method body.

    This pure helper also lets archive auditors verify a saved request without
    reconstructing or trusting the caller's legacy heuristic prompt.
    """
    if (kind not in POLICY_STRATEGIES or not isinstance(body, dict)
            or not isinstance(history, list) or len(history) != 50
            or not isinstance(body.get('strategy'), dict)
            or body['strategy'].get('strategy_id') != POLICY_STRATEGIES[kind]):
        raise ValueError('invalid-independent-request-body')
    observed = _aware_time(body.get('as_of'))
    if observed is None:
        raise ValueError('invalid-independent-request-time')
    # Preserve the original timezone spelling used by datetime.isoformat().
    observed = datetime.fromisoformat(body['as_of'].replace('Z', '+00:00'))
    snapshot = {'now': observed, 'latest': history[-1], 'records': history,
                'recent': [(row.get('period'), row.get('numbers')) for row in history],
                'conflicting_periods': []}
    expected = build_model_context(snapshot, kind, {}, now=observed,
                                   strategy_id=POLICY_STRATEGIES[kind])
    if canonical(body) != canonical(expected):
        raise ValueError('independent-request-body-mismatch')
    reference, target = body['reference_period'], body['target_period']
    task, output = ((KILLER_TASK_DEFINITION, KILLER_OUTPUT_DEFINITION) if kind == 'killer'
                    else (FRAMEWORK_TASK_DEFINITION, FRAMEWORK_OUTPUT_DEFINITION))
    return (f'以下是真实的最近50期开奖前四位（最新已开奖参考期号:{reference}，预测目标期号:{target}）：\n'
            + ', '.join(''.join(map(str, row['numbers'])) for row in history)
            + '\n\n【任务定义与正式核验口径】\n' + COMMON_TASK_DEFINITION + '\n' + task
            + '\n\n【输出字段定义】\n' + output
            + '\n\n【同一50期的独立方法数据】\n' + canonical(body))


def append_model_context(prompt, snapshot, kind, seals, *, data_dir=Path('/root/penta5-runner/data'),
                         strategy_id=None):
    """Build a clean independent request; explicit v1 keeps old pure rendering.

    The base hash retains caller identity for legacy archive/retry checks. New
    request content is built solely from validated history and definitions.
    """
    base_hash = sha256(prompt.encode('utf-8')).hexdigest()
    body = build_model_context(snapshot, kind, seals, data_dir=data_dir, strategy_id=strategy_id)
    active = True
    rendered = canonical(body)
    if body['strategy']['strategy_id'] in (LEGACY_KILLER_STRATEGY, LEGACY_FRAMEWORK_STRATEGY):
        prompt += ('\n\n【后台结构化参考与本模型真实已封存战绩】\n'
                   '本次开始采用JSON中strategy这一独立策略；此前通用冷热、遗漏回补、温弱码候选仅为旧格式兼容文字，'
                   '不作为本次决策依据。必须按strategy.instruction及其固定参数独立判断，不混用其他模型方法或当前排除码。'
                   'q/b/s/g对应千/百/十/个；all/qbs/qbg/qsg/bsg为四位全合/千百十/千百个/千十个/百十个取和尾。'
                   '条件转移只有当前状态的历史后继，样本稀疏须注明；平滑份额、加权份额及失败重叠均只描述历史。'
                   'sources为空表示不读取后台模型建议，不代表请求停止。允许不同模型独立选到同一数字，不得强制异号。'
                   '保持原输出字段与排除数量，禁止补造战绩或声称保证提高命中率。\n' + rendered)
        audit = {}
    else:
        prompt = render_independent_request(body, history_from_snapshot(snapshot), kind)
        audit = {'request_policy': REQUEST_POLICY,
                 'request_definition_version': REQUEST_DEFINITION_VERSION}
    audit.update(version=VERSION, context_applied=active, body=body,
                 context_sha256=sha256(rendered.encode('utf-8')).hexdigest(),
                 base_prompt_sha256=base_hash,
                 initial_request_prompt_sha256=sha256(prompt.encode('utf-8')).hexdigest())
    return prompt, audit


def prepare_model_request(prompt, snapshot, kind, seals, *, data_dir=Path('/root/penta5-runner/data')):
    """Persist the first input before requesting inference; same-ref retries reuse it.

    Corrupt or unwritable input evidence fails the request instead of silently
    selecting new advice. No API keys, headers or arbitrary source text are saved.
    """
    reference = snapshot['latest']['period']
    target = next_period(reference)
    if kind not in ('killer', 'framework') or not parse_period(reference):
        raise ValueError('invalid-model-input-identity')
    directory = Path(data_dir) / 'ai_model_inputs'
    _ensure_archive_directory(directory)
    path = directory / f'{kind}-{reference}.json'
    if not path.exists():
        enhanced, audit = append_model_context(prompt, snapshot, kind, seals, data_dir=data_dir)
        payload = {'version': VERSION, 'model_kind': kind, 'reference_period': reference,
                   'target_period': target, 'observed_at': snapshot['now'].isoformat(),
                   'initial_prompt': enhanced, 'audit': audit}
        encoded = canonical(payload).encode('utf-8')
        if len(encoded) > MAX_INPUT_BYTES:
            raise ValueError('oversized-model-input')
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as handle:
                temporary = handle.name
                os.chmod(temporary, 0o600)
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, path)  # Atomic create, never replace an existing input.
            except FileExistsError:
                pass
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
    with path.open('rb') as handle:
        encoded = handle.read(MAX_INPUT_BYTES + 1)
        # Also commit an existing winner. A previous attempt may have linked it
        # successfully and then failed its directory fsync before calling API.
        os.fsync(handle.fileno())
    _sync_directory(directory)
    if len(encoded) > MAX_INPUT_BYTES:
        raise ValueError('oversized-saved-model-input')
    payload = json.loads(encoded)
    if not isinstance(payload, dict):
        raise ValueError('invalid-saved-model-input')
    audit = payload.get('audit')
    initial = payload.get('initial_prompt')
    observed = _aware_time(payload.get('observed_at'))
    opened = parse_time(snapshot['latest']['opentime'])
    if (payload.get('version') != VERSION or payload.get('model_kind') != kind
            or payload.get('reference_period') != reference or payload.get('target_period') != target
            or not isinstance(audit, dict) or not isinstance(initial, str)
            or observed is None or not opened <= observed <= snapshot['now'] < period_slot(target)
            or audit.get('version') != VERSION
            or audit.get('base_prompt_sha256') != sha256(prompt.encode('utf-8')).hexdigest()
            or audit.get('initial_request_prompt_sha256') != sha256(initial.encode('utf-8')).hexdigest()
            or audit.get('context_sha256') != sha256(canonical(audit.get('body')).encode('utf-8')).hexdigest()):
        raise ValueError('invalid-saved-model-input')
    if 'request_policy' in audit or 'request_definition_version' in audit:
        if (audit.get('request_policy') != REQUEST_POLICY
                or type(audit.get('request_definition_version')) is not int
                or audit['request_definition_version'] != REQUEST_DEFINITION_VERSION
                or audit.get('context_applied') is not True
                or not isinstance(audit.get('body'), dict)
                or audit['body'].get('as_of') != payload['observed_at']
                or initial != render_independent_request(
                    audit['body'], history_from_snapshot(snapshot, now=observed), kind)):
            raise ValueError('invalid-saved-independent-model-input')
    elif (isinstance(audit.get('body'), dict)
          and isinstance(audit['body'].get('strategy'), dict)
          and audit['body']['strategy'].get('strategy_id') in POLICY_STRATEGIES.values()):
        # A v2 body cannot be relabelled as a legacy archive by deleting policy
        # metadata while keeping its self-consistent content hashes.
        raise ValueError('missing-saved-independent-model-input-policy')
    audit = dict(audit, input_archive=f'ai_model_inputs/{path.name}',
                 input_archive_sha256=sha256(encoded).hexdigest())
    return initial, audit
