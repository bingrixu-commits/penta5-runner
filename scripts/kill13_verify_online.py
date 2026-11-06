#!/usr/bin/env python3
"""kill13（合尾双冷）线上落地自检（只读）。

部署落地/事后复核用：一条命令取证，不触发预测、不写任何文件。
路径按仓库根推导（任意 cwd 可运行）；只在 penta5-runner 服务器上运行。

检查项：
1. 模块 ROOT 与脚本推导的仓库根一致；
2. 封存/结算账本可读，最新封存 rule_version / 2 项 / 期望 0.8082 / 枚举依据；
3. 最新封存 tails 与 cold_ranking 前 2 一致，tail_policy=cold-hold-top2；
4. 最新封存对当前开奖新鲜（ref == state latest，或发布时间 <= 360 秒）；
5. 面板 KILL_THIRTEEN 标记各 1、data-as-of 不落后、两版本串命中、攻击语与 build_attack 逐字一致；
6. cron 日志最新 published 行与最新封存 ref/target 一致；
7. 基线枚举文件 rate_rounded4=0.8082、draws/configs/常量命中数正确；
8. 结算记录最后一条对应上一期封存，且 tails 带 hit/actual 字段。

输出 PASS/FAIL 行 + RESULT JSON；任一 FAIL 退出码 1。
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

import kill13_tail  # noqa: E402

RATE = 0.8082
RULE = 'kill13-tail-v1-two-cold-hewei'
PANEL = ROOT / 'panel.html'
SEALS = ROOT / 'data' / 'kill13_tail' / 'seals.json'
SETTLEMENTS = ROOT / 'data' / 'kill13_tail' / 'settlements.json'
BASELINE = ROOT / 'data' / 'kill13_tail' / 'baseline_enumeration.json'
CRON_LOG = ROOT / 'data' / 'kill13_tail_cron.log'
PANEL_VERSIONS = ('quiet-panel-v32-add-kill13-tail', 'all-attack-editor-v26-kill13-tail')
BJT = timezone(timedelta(hours=8))

checks = []


def check(name, ok, detail=''):
    checks.append((name, bool(ok), str(detail)))
    print('PASS' if ok else 'FAIL', name, detail)


def load_doc(path, key):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        return None, str(exc)
    records = value.get(key) if isinstance(value, dict) else None
    if not isinstance(records, list):
        return None, 'records-not-list'
    return [item for item in records if isinstance(item, dict)], ''


def state_latest():
    try:
        value = json.loads((ROOT / 'data' / 'state.json').read_text(encoding='utf-8'))
        return str((value.get('latestRecord') or {}).get('period') or '')
    except (OSError, ValueError):
        return ''


def main():
    check('module_root', str(kill13_tail.ROOT) == str(ROOT), f'module={kill13_tail.ROOT} repo={ROOT}')

    seals, err = load_doc(SEALS, 'records')
    check('seals_readable', seals is not None, err or f'count={len(seals or [])}')
    settlements, err = load_doc(SETTLEMENTS, 'records')
    check('settlements_readable', settlements is not None, err or f'count={len(settlements or [])}')

    latest = seals[-1] if seals else {}
    latest_rule = str(latest.get('rule_version') or '')
    check('rule_version', latest_rule == RULE, latest_rule)
    tails = latest.get('tails') or []
    check('tail_count', len(tails) == kill13_tail.TOP_N, f'count={len(tails)} expected={kill13_tail.TOP_N}')
    check('expected_items', latest.get('expected_items') == kill13_tail.EXPECTED_ITEMS,
          f"{latest.get('expected_items')!r} expected={kill13_tail.EXPECTED_ITEMS}")
    check('expected_rate', latest.get('expected_group_rate') == kill13_tail.EXPECTED_GROUP_RATE,
          f"{latest.get('expected_group_rate')!r} expected={kill13_tail.EXPECTED_GROUP_RATE}")
    check('expected_basis', str(latest.get('expected_basis') or '').startswith('enumerated-10^4'),
          str(latest.get('expected_basis') or ''))
    policy = latest.get('tail_policy') or {}
    check('hold_policy', policy.get('policy') == kill13_tail.HOLD_POLICY, str(policy.get('policy') or ''))
    ranking = latest.get('cold_ranking') or []
    top2 = [(item.get('key'), str(item.get('kill'))) for item in ranking[:kill13_tail.TOP_N]]
    sealed = [(item.get('key'), str(item.get('kill'))) for item in tails]
    check('tails_match_cold_ranking', top2 == sealed, f'ranking={top2} sealed={sealed}')
    ranks = sorted(item.get('rank') for item in tails if isinstance(item.get('rank'), int))
    check('tail_ranks', ranks == list(range(1, kill13_tail.TOP_N + 1)), str(ranks))

    current = state_latest()
    ref = str(latest.get('ref_period') or '')
    published = str(latest.get('published_at') or '')
    fresh = ref == current
    age = None
    if published:
        try:
            age = (datetime.now(BJT) - datetime.fromisoformat(published)).total_seconds()
            fresh = fresh or age <= 360
        except ValueError:
            pass
    check('seal_freshness', fresh, f'ref={ref} state_latest={current} age={None if age is None else round(age)}s')

    html = ''
    try:
        html = PANEL.read_text(encoding='utf-8')
    except OSError as exc:
        check('panel_readable', False, str(exc))
    if html:
        check('panel_start_marker', html.count('<!-- KILL_THIRTEEN_START -->') == 1,
              f"count={html.count('<!-- KILL_THIRTEEN_START -->')}")
        check('panel_end_marker', html.count('<!-- KILL_THIRTEEN_END -->') == 1,
              f"count={html.count('<!-- KILL_THIRTEEN_END -->')}")
        match = re.search(r'<!-- KILL_THIRTEEN_START -->(.*?)<!-- KILL_THIRTEEN_END -->', html, re.S)
        block = match.group(1) if match else ''
        allowed = {ref, current}
        if len(seals) >= 2:
            allowed.add(str(seals[-2].get('ref_period') or ''))
        as_of = re.findall(r'data-as-of="([0-9]+)"', block)
        check('panel_as_of', bool(as_of) and as_of[0] in allowed, f'as_of={as_of[:1]} allowed={sorted(allowed)}')
        for version in PANEL_VERSIONS:
            check('panel_version_' + version.split('-')[1], version in html, version)
        attack = ''
        try:
            attack = kill13_tail.build_attack(latest)
        except (ValueError, TypeError) as exc:
            check('attack_build', False, str(exc))
        if attack:
            check('panel_attack', attack in block, f'len={len(attack)}')

    lines = []
    try:
        lines = CRON_LOG.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError as exc:
        check('cron_log_readable', False, str(exc))
    published_rows = [line for line in lines if 'kill13 published:' in line]
    last = published_rows[-1] if published_rows else ''
    ref_match = re.search(r'kill13 published: ref=([0-9]+) target=([0-9]+)', last)
    check('cron_published_line', bool(ref_match), last[-160:])
    if ref_match:
        check('cron_matches_latest_seal',
              ref_match.group(1) == ref and ref_match.group(2) == str(latest.get('target_period') or ''),
              f"log={ref_match.group(1)}->{ref_match.group(2)} seal={ref}->{latest.get('target_period')}")

    baseline = {}
    try:
        baseline = json.loads(BASELINE.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        check('baseline_readable', False, str(exc))
    if baseline:
        check('baseline_rate', baseline.get('rate_rounded4') == RATE, f"{baseline.get('rate_rounded4')!r}")
        check('baseline_configs', baseline.get('draws') == 10000 and baseline.get('configs') == 1225
              and baseline.get('both_hit_pairs') == 9900000,
              f"draws={baseline.get('draws')} configs={baseline.get('configs')} both_hit={baseline.get('both_hit_pairs')}")
        check('baseline_constant_hits', baseline.get('hit_count_is_constant_45') is True
              and baseline.get('formula_matches') is True,
              f"hit45={baseline.get('hit_count_is_constant_45')} formula={baseline.get('formula_matches')}")

    if settlements and len(seals) >= 2:
        last_settle = settlements[-1]
        check('settlement_matches_previous_seal',
              str(last_settle.get('target_period') or '') == str(seals[-2].get('target_period') or ''),
              f"settle={last_settle.get('target_period')} prev_seal_target={seals[-2].get('target_period')}")
        settle_tails = last_settle.get('tails') or []
        check('settlement_tail_fields',
              len(settle_tails) == kill13_tail.TOP_N
              and all('hit' in item and 'actual' in item for item in settle_tails),
              f'tails={len(settle_tails)}')

    passed = sum(1 for _, ok, _ in checks if ok)
    failed = sum(1 for _, ok, _ in checks if not ok)
    print('RESULT', json.dumps({'pass': passed, 'fail': failed, 'seals': len(seals or []),
                                'settlements': len(settlements or []), 'state_latest': current,
                                'latest_ref': ref, 'latest_target': latest.get('target_period')},
                               ensure_ascii=False))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
