#!/usr/bin/env python3
"""kill3 v16 预登记回滚门禁（只读计算 + 报告产物）。

预登记（2026-11-06，ops/kill3-stability-plan.md §9）：
- 观察起点：RULE_VERSION = kill3-tail-v16-...-cold-drop 的首个已结算 target_period；
- 触发回滚需同时满足：已结算样本 n >= N_TRIGGER(150)，且满足以下任一：
    * 相对整组精确基线 EXPECTED(0.5863) 的正态 z < Z_TRIGGER(-2.5)；
    * 本版整组命中率 < RATE_FLOOR(0.50)；
- 未达样本量只观察；达到样本量且未触发 -> passed（维持 v16，门禁关闭）；
- 现行口径已不是 v16（回滚或再升版）-> inactive（门禁停用，历史 v16 样本仅记录）。

产物 data/kill3_v16_guard.json 由 scripts/ops_health_check.py 的 check_kill3_v16_gate
每 10 分钟刷新一次；面板 lib/kill_three_view.py 只读该 JSON 展示门禁进度。
只读 data/kill3_tail/settlements.json，不写账本、不触发预测、不改口径。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get('PENTA5_ROOT') or '/root/penta5-runner')
V16_RULE = ('kill3-tail-v16-hot-cold-repeat-neighbor-hold-shadow-two-counted-three-avoid'
            '-cold-drop')
EXPECTED = 0.5863          # 与 lib/hitrate_baseline.py RULE_BASELINE[('kill3_tail', v16)] 一致
N_TRIGGER = 150
Z_TRIGGER = -2.5
RATE_FLOOR = 0.50
OUT_REL = Path('data') / 'kill3_v16_guard.json'
RULE_RE = re.compile(r"^RULE_VERSION\s*=\s*['\"]([^'\"]+)['\"]", re.MULTILINE)
PLAN = 'ops/kill3-stability-plan.md §9'
ROLLBACK_COMMAND = 'python3 scripts/kill3_v16_rollback.py --execute'


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def current_rule_version(root=None):
    base = Path(root or DEFAULT_ROOT)
    try:
        text = (base / 'lib' / 'kill3_tail.py').read_text(encoding='utf-8')
    except OSError:
        return None
    match = RULE_RE.search(text)
    return match.group(1) if match else None


def _wilson(hits, n, z=1.96):
    if not n:
        return None
    p = hits / float(n)
    denom = 1.0 + z * z / n
    center = (p + z * z / (2.0 * n)) / denom
    margin = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return [round(max(0.0, center - margin), 6), round(min(1.0, center + margin), 6)]


def _binom_lower_tail(k, n, p):
    """P(X <= k), X ~ Binomial(n, p) 的精确下尾。"""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    total = 0.0
    q = 1.0 - p
    for i in range(k + 1):
        total += math.comb(n, i) * (p ** i) * (q ** (n - i))
    return min(1.0, total)


def _v16_records(root):
    document = _read_json(Path(root) / 'data' / 'kill3_tail' / 'settlements.json') or {}
    records = document.get('records') if isinstance(document, dict) else None
    picked = []
    for record in records or []:
        if not isinstance(record, dict):
            continue
        if str(record.get('rule_version') or '') != V16_RULE:
            continue
        if record.get('verified') is not True:
            continue
        hit = record.get('group_hit')
        if not isinstance(hit, bool):
            continue
        picked.append((str(record.get('target_period') or ''), hit))
    return picked


def evaluate(root=None):
    base = Path(root or DEFAULT_ROOT)
    rule = current_rule_version(base)
    picked = _v16_records(base)
    n = len(picked)
    hits = sum(1 for _, hit in picked if hit)
    rate = round(hits / n, 6) if n else None
    z = p_two = p_lower = None
    ci95 = _wilson(hits, n)
    if n:
        se = math.sqrt(EXPECTED * (1.0 - EXPECTED) / n)
        if se:
            z = round((rate - EXPECTED) / se, 4)
        p_lower = _binom_lower_tail(hits, n, EXPECTED)
        p_upper = 1.0 - _binom_lower_tail(hits - 1, n, EXPECTED)
        p_two = round(min(1.0, 2.0 * min(p_lower, p_upper)), 6)
        p_lower = round(p_lower, 6)
    reasons = []
    if rule is None:
        status = 'inactive'
        verdict = '现行 lib/kill3_tail.py 未找到 RULE_VERSION，无法判定（门禁停用）'
    elif rule != V16_RULE:
        status = 'inactive'
        verdict = '现行口径非 v16（已回滚或再升版），门禁停用；历史 v16 样本仅记录'
    elif n >= N_TRIGGER and ((z is not None and z < Z_TRIGGER)
                             or (rate is not None and rate < RATE_FLOOR)):
        status = 'triggered'
        if z is not None and z < Z_TRIGGER:
            reasons.append('z=%.2f < %.1f' % (z, Z_TRIGGER))
        if rate is not None and rate < RATE_FLOOR:
            reasons.append('命中率 %.2f%% < %.0f%%' % (rate * 100.0, RATE_FLOOR * 100.0))
        verdict = '触发预登记回滚（' + '，'.join(reasons) + '）'
    elif n >= N_TRIGGER:
        status = 'passed'
        verdict = '已达预登记样本 n=%d，未触发回滚条件（维持 v16，门禁关闭）' % n
    else:
        status = 'observe'
        verdict = '观察中（n=%d < %d 预登记样本；未触发判据）' % (n, N_TRIGGER)
    return {
        'version': 1,
        'generated_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'rule_version': rule,
        'expected_rule_version': V16_RULE,
        'basis': '本版整组命中率 vs 10^4 精确枚举随机基线 %.4f（%d 项）' % (EXPECTED, 8),
        'n': n,
        'hits': hits,
        'rate': rate,
        'ci95': ci95,
        'expected': EXPECTED,
        'delta': round(rate - EXPECTED, 6) if rate is not None else None,
        'z': z,
        'p_lower': p_lower,
        'p_two': p_two,
        'thresholds': {'n_trigger': N_TRIGGER, 'z_trigger': Z_TRIGGER,
                       'rate_floor': RATE_FLOOR},
        'progress': {'n': n, 'n_trigger': N_TRIGGER,
                     'ratio': round(min(1.0, n / float(N_TRIGGER)), 4)},
        'first_period': picked[0][0] if picked else None,
        'last_period': picked[-1][0] if picked else None,
        'status': status,
        'verdict': verdict,
        'trigger_reasons': reasons,
        'rollback_command': ROLLBACK_COMMAND,
        'plan': PLAN,
    }


def _atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=str(path.parent),
                                         delete=False, suffix='.tmp')
    try:
        json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    finally:
        handle.close()
    os.replace(handle.name, str(path))
    try:
        os.chmod(str(path), 0o644)
    except OSError:
        pass


def write_report(root=None):
    payload = evaluate(root)
    base = Path(root or DEFAULT_ROOT)
    _atomic_write_json(base / OUT_REL, payload)
    return payload


def main():
    parser = argparse.ArgumentParser(description='kill3 v16 预登记回滚门禁（只读）')
    parser.add_argument('--root', default=None, help='项目根目录（默认 /root/penta5-runner）')
    parser.add_argument('--json', action='store_true', help='输出 JSON')
    parser.add_argument('--write', action='store_true', help='原子写 data/kill3_v16_guard.json')
    args = parser.parse_args()
    payload = write_report(args.root) if args.write else evaluate(args.root)
    if args.json or args.write:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print('口径: %s' % (payload.get('rule_version') or '-'))
        print('进度: %d/%d 期' % (payload['n'], N_TRIGGER))
        print('命中: %d/%d = %s' % (payload['hits'], payload['n'],
                                   ('%.2f%%' % (payload['rate'] * 100.0))
                                   if payload['rate'] is not None else '-'))
        print('基线: %.2f%%  z=%s  p_two=%s' % (EXPECTED * 100.0, payload['z'], payload['p_two']))
        print('判定: %s %s' % (payload['status'], payload['verdict']))
        if payload['status'] == 'triggered':
            print('回滚: %s' % ROLLBACK_COMMAND)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
