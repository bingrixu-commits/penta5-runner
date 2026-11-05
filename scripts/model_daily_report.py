"""多模型每日排除命中率对比。只做统计,不改任何模型。

把各账本里语义经过反验的"排除单个号码/尾数"指标统一到一个量表上
(成功 = 实际开奖不等于被排除的值;公平随机基线 = 90%),按业务日聚合,
用 Wilson 95% 置信下限排序,避免小样本噪声冒充优势。

产物:data/model_daily_report.json 与 data/model_daily_history.jsonl
"""
from pathlib import Path
import json
import math
import os
import sys
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats_history import TZ, business_start, parse_time  # noqa: E402

ROOT = Path('/root/penta5-runner')
DATA = ROOT / 'data'
OUT_PATH = DATA / 'model_daily_report.json'
HISTORY_PATH = DATA / 'model_daily_history.jsonl'
BASELINE = 0.9

# 每个模型:账本文件 + 指标提取规则(语义均已与账本 outcomes 反验一致)
MODELS = (
    {
        'model': 'r1_framework',
        'ledger': 'r1_framework_hit_stats.json',
        # 2026-10-30: framework 已按 PENTA5_MODEL_FRAMEWORK 可在 flash/pro 间切换;
        # 从封存记录里取实际 requested_model, 统计要按模型拆分(pro 试运行评估用)。
        'seal_ledger': 'r1_framework_seals.json',
        'metrics': {
            'q_kill1': (None, 'q_kill1', 0),
            'b_kill1': (None, 'b_kill1', 1),
        },
    },
)


def wilson_lower(hits, total, z=1.96):
    if total <= 0:
        return 0.0
    p = hits / total
    d = 1 + z * z / total
    c = p + z * z / (2 * total)
    s = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (c - s) / d


def business_day(entry):
    biz = entry.get('biz_date')
    if biz:
        return str(biz)
    try:
        opened = parse_time((entry.get('actual') or {}).get('opentime'))
        return business_start(opened).strftime('%Y-%m-%d')
    except Exception:
        return None


def _seal_models(spec):
    """reference_period -> requested_model from the model's own seal ledger."""
    name = spec.get('seal_ledger')
    if not name:
        return {}
    path = DATA / name
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    result = {}
    for reference, seal in (document.get('prediction_seals') or {}).items():
        metadata = (seal or {}).get('model_metadata') if isinstance(seal, dict) else None
        if isinstance(metadata, dict):
            result[str(reference)] = metadata.get('requested_model') or '(unknown)'
    return result


def main():
    samples = []
    for spec in MODELS:
        path = DATA / spec['ledger']
        if not path.exists():
            continue
        document = json.loads(path.read_text(encoding='utf-8'))
        seal_models = _seal_models(spec)
        for entry in (document.get('verified') or {}).values():
            actual = entry.get('actual') or {}
            numbers = actual.get('numbers')
            if not isinstance(numbers, list) or len(numbers) < 4:
                continue
            response = (entry.get('publication') or {}).get('response') or {}
            biz = business_day(entry)
            if not biz:
                continue
            revision = (entry.get('publication') or {}).get('revision') or '(未标注)'
            for name, (container, field, index) in spec['metrics'].items():
                source = response.get(container) if container else response
                value = (source or {}).get(field) if isinstance(source, dict) else None
                if not isinstance(value, int):
                    continue
                variant = seal_models.get(str(entry.get('ref_period'))) if seal_models else None
                samples.append({'model': spec['model'], 'revision': revision, 'metric': name,
                                'biz': biz, 'ok': numbers[index] != value, 'variant': variant})

    if not samples:
        print('no scorable samples', flush=True)
        return 0

    days = sorted({s['biz'] for s in samples})
    today = days[-1]

    def aggregate(scope_filter):
        bucket = defaultdict(lambda: [0, 0])
        for s in samples:
            if not scope_filter(s):
                continue
            key = (s['model'], s['revision'])
            bucket[key][0] += 1
            bucket[key][1] += 1 if s['ok'] else 0
        return bucket

    today_bucket = aggregate(lambda s: s['biz'] == today)
    last3_bucket = aggregate(lambda s: s['biz'] in days[-3:])
    all_bucket = aggregate(lambda s: True)

    rows = []
    for key, (n, hits) in today_bucket.items():
        model, revision = key
        n3, h3 = last3_bucket.get(key, [0, 0])
        na, ha = all_bucket.get(key, [0, 0])
        rows.append({
            'model': model, 'revision': revision,
            'today': {'n': n, 'hits': hits, 'rate': round(hits / n, 6),
                      'wilson_lower': round(wilson_lower(hits, n), 6)},
            'last3': {'n': n3, 'hits': h3, 'rate': round(h3 / n3, 6) if n3 else None},
            'all': {'n': na, 'hits': ha, 'rate': round(ha / na, 6) if na else None},
            'delta_vs_baseline': round(hits / n - BASELINE, 6),
        })
    rows.sort(key=lambda r: r['today']['wilson_lower'], reverse=True)

    # 2026-10-30: r1_framework 按实际请求模型(flash / v4-pro)拆分, 只统计框架
    # 的两个固定排除位; 与上面 models 行使用同一批已验证样本。
    def variant_bucket(scope_filter):
        bucket = defaultdict(lambda: [0, 0])
        for s in samples:
            if s['model'] != 'r1_framework' or not s.get('variant') or not scope_filter(s):
                continue
            bucket[s['variant']][0] += 1
            bucket[s['variant']][1] += 1 if s['ok'] else 0
        return bucket

    v_today = variant_bucket(lambda s: s['biz'] == today)
    v_last3 = variant_bucket(lambda s: s['biz'] in days[-3:])
    v_all = variant_bucket(lambda s: True)
    framework_by_model = []
    for variant, (n, hits) in v_today.items():
        n3, h3 = v_last3.get(variant, [0, 0])
        na, ha = v_all.get(variant, [0, 0])
        framework_by_model.append({
            'requested_model': variant,
            'today': {'n': n, 'hits': hits, 'rate': round(hits / n, 6),
                      'wilson_lower': round(wilson_lower(hits, n), 6)},
            'last3': {'n': n3, 'hits': h3, 'rate': round(h3 / n3, 6) if n3 else None},
            'all': {'n': na, 'hits': ha, 'rate': round(ha / na, 6) if na else None},
            'delta_vs_baseline': round(hits / n - BASELINE, 6),
        })
    framework_by_model.sort(key=lambda r: r['all']['n'], reverse=True)

    report = {
        'generated_at': __import__('datetime').datetime.now(TZ).isoformat(),
        'revision': 'model-daily-report-v1',
        'today': today,
        'baseline': BASELINE,
        'samples_total': len(samples),
        'day_range': {'first': days[0], 'last': days[-1]},
        'models': rows,
        'framework_by_model': framework_by_model,
        'note': ('命中 = 实际开奖不等于被排除的值;公平随机基线 90%。'
                 'wilson_lower 低于基线说明今日领先无法排除随机。'
                 'he3_refine 未纳入:其 he3_drop 需三合尾定义,语义未反验。'),
    }

    temp = OUT_PATH.with_suffix('.json.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, OUT_PATH)

    line = {'generated_at': report['generated_at'], 'today': today,
            'models': {f"{r['model']}/{r['revision']}": r['today']['rate'] for r in rows}}
    with HISTORY_PATH.open('a', encoding='utf-8') as handle:
        handle.write(json.dumps(line, ensure_ascii=False) + '\n')

    print(f"业务日 {today}  样本 {len(samples)}  基线 {BASELINE:.0%}", flush=True)
    for row in rows:
        print(f"  {row['model'] + ' / ' + row['revision']:<48} "
              f"今日 {row['today']['n']:>4} 次  {row['today']['rate']:.1%}  "
              f"Wilson下限 {row['today']['wilson_lower']:.1%}  "
              f"近3日 {row['last3']['rate']:.1%}  全期 {row['all']['rate']:.1%}", flush=True)
    if framework_by_model:
        print('  -- framework 按实际请求模型拆分 --', flush=True)
        for row in framework_by_model:
            print(f"  {row['requested_model']:<24} 今日 {row['today']['n']:>4} 次  "
                  f"{row['today']['rate']:.1%}  Wilson下限 {row['today']['wilson_lower']:.1%}  "
                  f"近3日 {(row['last3']['rate'] if row['last3']['rate'] is not None else 0):.1%}  "
                  f"全期 {(row['all']['rate'] if row['all']['rate'] is not None else 0):.1%}", flush=True)
    print(f"report -> {OUT_PATH}", flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
