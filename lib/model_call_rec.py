# -*- coding: utf-8 -*-
"""AI 模型调用推荐（只读派生）：按 AI 任务建议每期调用 flash 还是 pro。

数据源（全部只读）：
- data/framework_hitrate_cumulative.json：各任务版本锁定累计命中率 + model_split
  （按封存记录 requested_model 分档）；
- data/model_usage/summary.json：近 7 日每任务调用量/错误/延迟、账户余额与燃耗；
- data/ai_balance_guard.json：自动停启状态；
- .env：各任务现行模型（PENTA5_MODEL_*）。

判定口径（写死，不做样本外推断）：
- MIN_SAMPLE=200：现行档或替代档任一累计样本 <200 → 不下换档结论，只观察；
- 两档样本均 ≥200：双侧两样本 z 检验（pooled）比命中率；
  替代档显著高 ≥2pp 且 p<0.05 → 建议换档；现档显著高 → 维持现档；
  无显著差异 → 维持现档（省成本）；
- 整组期望基线（10^4 精确枚举）只作对照列，不参与换档判定；
- 本模块只生成 data/model_call_rec.json 与面板区块，绝不改 .env、不重启任务。
"""
import json
import math
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path('/root/penta5-runner')
CUMULATIVE_PATH = ROOT / 'data' / 'framework_hitrate_cumulative.json'
USAGE_PATH = ROOT / 'data' / 'model_usage' / 'summary.json'
GUARD_PATH = ROOT / 'data' / 'ai_balance_guard.json'
ENV_PATH = ROOT / '.env'
OUT_PATH = ROOT / 'data' / 'model_call_rec.json'

MIN_SAMPLE = 200
MIN_DIFF_PP = 2.0
P_THRESHOLD = 0.05

TASKS = (
    {'key': 'killer', 'usage_key': 'killer', 'label': '智能深排 killer（9 项）',
     'env_chain': ('PENTA5_MODEL_KILLER', 'PENTA5_DEEPSEEK_MODEL')},
    {'key': 'framework', 'usage_key': 'framework', 'label': '固定八码框架（8 项）',
     'env_chain': ('PENTA5_MODEL_FRAMEWORK', 'PENTA5_DEEPSEEK_MODEL')},
    {'key': 'selected_six', 'usage_key': 'six', 'label': '七项独立框架 selected_six（7 项）',
     'env_chain': ('PENTA5_MODEL_SIX', 'PENTA5_DEEPSEEK_MODEL')},
    {'key': 'he3_refine', 'usage_key': 'he3', 'label': '合尾 he3（剩余 5 码）',
     'env_chain': ('PENTA5_MODEL_HE3', 'PENTA5_DEEPSEEK_MODEL')},
    {'key': 'kill3_tail', 'usage_key': 'kill3', 'label': 'AI 三位置杀码',
     'env_chain': ('PENTA5_MODEL_KILL3', 'PENTA5_MODEL_KILLER', 'PENTA5_DEEPSEEK_MODEL')},
    {'key': 'kill1_tail', 'usage_key': 'kill1', 'label': 'AI 双位置杀码（3 项）',
     'env_chain': ('PENTA5_MODEL_KILL1', 'PENTA5_MODEL_KILLER', 'PENTA5_DEEPSEEK_MODEL')},
)

ALT_MODEL = {
    'deepseek-flash': 'deepseek-v4-pro',
    'deepseek-v4-pro': 'deepseek-flash',
}

DECISION_LABELS = {
    'maintain': '维持现行档',
    'maintain_strong': '维持现行档（现档显著优）',
    'switch': '建议换替代档',
    'observe': '样本不足 · 继续观察',
    'unknown': '未配置模型',
}


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _read_env():
    mapping = {}
    try:
        text = ENV_PATH.read_text(encoding='utf-8')
    except OSError:
        return mapping
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        mapping[key.strip()] = value.strip()
    return mapping


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=str(path.parent))
    try:
        with open(handle, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=1)
            stream.flush()
            import os
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            import os
            os.unlink(temporary)
        except OSError:
            pass


def _norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _proportion_z(n1, rate1, n2, rate2):
    """双侧两样本比例 z 检验（pooled），返回 (diff_pp, z, p)。"""
    p1 = rate1 if isinstance(rate1, (int, float)) else 0.0
    p2 = rate2 if isinstance(rate2, (int, float)) else 0.0
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2) if (n1 + n2) else 0.0
    se = math.sqrt(pooled * (1.0 - pooled) * (1.0 / n1 + 1.0 / n2)) if (n1 and n2) else 0.0
    if se <= 0:
        return (p2 - p1) * 100.0, 0.0, 1.0
    z = (p2 - p1) / se
    p = 2.0 * (1.0 - _norm_cdf(abs(z)))
    return (p2 - p1) * 100.0, z, p


def _decide(current_model, cur_n, cur_rate, alt_model, alt_n, alt_rate):
    if not current_model:
        return 'unknown', '未配置模型档位（.env 无对应 PENTA5_MODEL_*）'
    if alt_model is None:
        if cur_n < MIN_SAMPLE:
            return 'observe', '现行档 %s 样本 %d<%d，继续观察' % (current_model, cur_n, MIN_SAMPLE)
        return 'maintain', '无替代档实测样本（该任务仅 %s 运行），维持现行' % current_model
    if cur_n < MIN_SAMPLE:
        return 'observe', '现行档 %s 样本 %d<%d，继续观察（满 %d 再比档）' % (
            current_model, cur_n, MIN_SAMPLE, MIN_SAMPLE)
    if alt_n < MIN_SAMPLE:
        return 'maintain', '替代档 %s 样本 %d<%d，维持 %s' % (alt_model, alt_n, MIN_SAMPLE, current_model)
    diff, z, p = _proportion_z(cur_n, cur_rate, alt_n, alt_rate)
    if diff >= MIN_DIFF_PP and p < P_THRESHOLD:
        return 'switch', '替代档 %s 高 %+.2fpp（z=%+.2f, p=%.3f）：可换 %s' % (
            alt_model, diff, z, p, alt_model)
    if diff <= -MIN_DIFF_PP and p < P_THRESHOLD:
        return 'maintain_strong', '现档 %s 高 %+.2fpp（z=%+.2f, p=%.3f）：维持 %s' % (
            current_model, -diff, z, p, current_model)
    return 'maintain', '两档无显著差异（%+.2fpp, p=%.3f）：维持 %s 省成本' % (diff, p, current_model)


def _split_of(row, model):
    if not isinstance(row, dict):
        return None
    for entry in row.get('model_split') or ():
        if isinstance(entry, dict) and (entry.get('model') or '').strip() == model:
            return entry
    return None


def compute(write=True):
    now = datetime.now().astimezone()
    cumulative = _read_json(CUMULATIVE_PATH) or {}
    usage = _read_json(USAGE_PATH) or {}
    guard = _read_json(GUARD_PATH) or {}
    env = _read_env()
    rows_by_key = {}
    for row in cumulative.get('rows') or ():
        if isinstance(row, dict) and row.get('key'):
            rows_by_key[row['key']] = row

    tasks = []
    for spec in TASKS:
        key = spec['key']
        current_model = None
        for env_name in spec['env_chain']:
            candidate = (env.get(env_name) or '').strip()
            if candidate:
                current_model = candidate
                break
        alt_model = ALT_MODEL.get(current_model) if current_model else None
        row = rows_by_key.get(key) or {}
        split = row.get('model_split')
        split = split if isinstance(split, list) else []
        cur_entry = _split_of(row, current_model) if current_model else None
        alt_entry = _split_of(row, alt_model) if alt_model else None
        if not split and current_model:
            cur_entry = {'model': current_model, 'n': row.get('n'), 'hits': row.get('hits'),
                         'rate': row.get('rate')}
        cur_n = int(cur_entry.get('n') or 0) if cur_entry else 0
        cur_hits = int(cur_entry.get('hits') or 0) if cur_entry else 0
        cur_rate = cur_entry.get('rate') if cur_entry else None
        if cur_rate is None and cur_n:
            cur_rate = round(cur_hits / cur_n, 6)
        alt_n = int(alt_entry.get('n') or 0) if alt_entry else 0
        alt_hits = int(alt_entry.get('hits') or 0) if alt_entry else 0
        alt_rate = alt_entry.get('rate') if alt_entry else None
        if alt_rate is None and alt_n:
            alt_rate = round(alt_hits / alt_n, 6)
        decision, reason = _decide(current_model, cur_n, cur_rate, alt_model, alt_n, alt_rate)
        usage_key = spec.get('usage_key') or key
        usage7 = ((usage.get('last7d') or {}).get('by_task') or {}).get(usage_key) or {}
        usage_today = ((usage.get('today') or {}).get('by_task') or {}).get(usage_key) or {}
        tasks.append({
            'key': key,
            'label': spec['label'],
            'env_key': spec['env_chain'][0],
            'current_model': current_model,
            'alt_model': alt_model,
            'current': {'n': cur_n, 'hits': cur_hits, 'rate': cur_rate},
            'alt': {'n': alt_n, 'hits': alt_hits, 'rate': alt_rate} if alt_entry or (alt_model and split) else None,
            'expected_pct': round(row['expected'] * 100, 4) if isinstance(row.get('expected'), (int, float)) else None,
            'row_verdict': row.get('verdict'),
            'decision': decision,
            'decision_label': DECISION_LABELS.get(decision, decision),
            'reason': reason,
            'usage_7d': {
                'attempts': usage7.get('attempts'), 'errors': usage7.get('errors'),
                'p50': usage7.get('p50'), 'p95': usage7.get('p95'),
                'prompt': usage7.get('prompt'), 'completion': usage7.get('completion'),
            },
            'usage_today': {
                'attempts': usage_today.get('attempts'), 'errors': usage_today.get('errors'),
            },
        })

    balance = usage.get('balance') or {}
    errors_7d = (usage.get('last7d') or {}).get('errors') or {}
    report = {
        'version': 'model-call-rec-v1',
        'generated_at': now.strftime('%Y-%m-%dT%H:%M:%S+08:00'),
        'as_of_period': cumulative.get('as_of_period'),
        'cumulative_generated_at': cumulative.get('generated_at'),
        'usage_updated_at': usage.get('updated_at'),
        'min_sample': MIN_SAMPLE,
        'min_diff_pp': MIN_DIFF_PP,
        'p_threshold': P_THRESHOLD,
        'rule': ('换档条件：两档累计样本均≥%d 且替代档高≥%gpp、双侧z检验p<%g；'
                 '否则维持现行档或观察。整组期望基线仅对照。' % (MIN_SAMPLE, MIN_DIFF_PP, P_THRESHOLD)),
        'balance': {
            'available': balance.get('available'),
            'balance': balance.get('balance'),
            'at': balance.get('at'),
            'burn_1h_cny': balance.get('burn_1h_cny'),
            'recharged_recently': balance.get('recharged_recently'),
            'runway_hours': balance.get('runway_hours'),
        },
        'guard': {
            'auto_paused': guard.get('auto_paused'),
            'last_checked_at': guard.get('last_checked_at'),
        },
        'errors_7d': errors_7d,
        'tasks': tasks,
    }
    if write:
        _write_json(OUT_PATH, report)
    return report


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    dry = '--dry' in argv
    report = compute(write=not dry)
    print('== AI 模型调用推荐（数据截至 %s）==' % report['as_of_period'])
    print('规则: %s' % report['rule'])
    for task in report['tasks']:
        cur = task['current']
        alt = task['alt'] or {'n': 0}
        cur_text = ('%d期 %.2f%%' % (cur['n'], cur['rate'] * 100)) if cur['n'] and cur['rate'] is not None else '-'
        alt_text = ('%d期 %.2f%%' % (alt['n'], alt['rate'] * 100)) if alt.get('n') and alt.get('rate') is not None else '-'
        print('  %-28s 现档 %-15s %-14s 替代 %-15s %-14s -> %s | %s' % (
            task['label'], task['current_model'] or '-', cur_text,
            task['alt_model'] or '-', alt_text, task['decision_label'], task['reason']))
    balance = report['balance']
    print('余额: %.2f 元 · 近1h燃耗 %.3f 元/h · runway %.1f h · 守护 auto_paused=%s · 近7日错误 %s' % (
        balance.get('balance') or 0.0, balance.get('burn_1h_cny') or 0.0,
        balance.get('runway_hours') or 0.0, report['guard'].get('auto_paused'),
        json.dumps(report['errors_7d'], sort_keys=True)))
    if not dry:
        print('written: %s' % OUT_PATH)
    return 0


if __name__ == '__main__':
    sys.exit(main())
