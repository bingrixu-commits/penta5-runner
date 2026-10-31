#!/usr/bin/env python3
"""joint_form 转正后的版本化基线校准（只读，审计/复现用）。

背景：2026-10-31 16:44 起 framework/killer 的 2 个反向形态从「AI 原形态」改为
「本地精确联合优化形态」（主判断不变）。两段口径的整组随机期望不同：

- 分界前：AI 形态，随机期望 = shadow 的 expected_ai（逐期 10^4 精确枚举）；
- 分界后：联合优化形态，随机期望 = shadow 的 expected_opt。

本脚本把两条基线写入 data/joint_form_baseline.json，供累计报告/guard/面板引用与审计；
不调用模型、不改封存、不写预测。运行：python3 scripts/joint_form_baseline.py
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

import framework_hitrate_cumulative as cumulative


def main():
    promoted_at, promoted_moment = cumulative._joint_form_boundary()
    if promoted_moment is None:
        print('joint_form 未转正，无需版本化基线')
        return 0
    mapping, summary = cumulative._joint_expected(promoted_moment)
    models = {}
    checks = []
    for name, round_baseline in (('killer', 0.4162), ('framework', 0.5559)):
        item = summary.get(name) or {}
        before = item.get('before')
        after = item.get('after')
        if isinstance(before, (int, float)):
            delta = abs(before - round_baseline)
            checks.append({'model': name, 'round_baseline': round_baseline,
                           'shadow_expected_ai_avg': before,
                           'abs_delta': round(delta, 6), 'pass': bool(delta < 0.001)})
        models[name] = {'before': before, 'after': after, 'n': item.get('n'),
                        'expected_targets': len(mapping.get(name) or {})}
    report = {
        'version': 1,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'promoted_at': promoted_at,
        'source': 'data/joint_form_shadow.json#summary',
        'basis': ('分界前 expected_ai / 分界后 expected_opt；每期用 10^4 开奖组合精确枚举整组命中概率，'
                  '按记录加权即为该口径的随机期望'),
        'models': models,
        'rounding_checks': checks,
        'note': ('累计报告/guard/面板用本文件作为 killer/framework 的版本化随机基线；'
                 '换 joint_form 口径必须重跑本脚本并升版本号。'),
    }
    out = ROOT / 'data' / 'joint_form_baseline.json'
    cumulative._write_json(out, report)
    for name in ('killer', 'framework'):
        item = models[name]
        print('%s: before=%s after=%s n=%s targets=%d' % (
            name, item['before'], item['after'], item['n'], item['expected_targets']))
    for check in checks:
        print('check %s round_baseline=%.4f shadow_before=%.4f pass=%s' % (
            check['model'], check['round_baseline'],
            check['shadow_expected_ai_avg'], check['pass']))
    print('joint_form_baseline written: %s' % out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
