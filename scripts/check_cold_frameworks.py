#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读线上自检：cold30 / cold15 定位冷码框架（不写任何文件）。

检查项：模块口径常量、精确基线、封存 input_sha256 可重算、发布早于目标开奖、
结算逐项可复算、面板区块/数据时效、cron/logrotate 挂接、派生统计注册行。
用法：python3 scripts/check_cold_frameworks.py [--root /root/penta5-runner]
退出码：0 全部 PASS；1 存在 FAIL。
"""
import argparse
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

BASE_CHECKS = []


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='/root/penta5-runner')
    args = parser.parse_args()
    root = Path(args.root)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / 'scripts'))
    sys.path.insert(0, str(root / 'lib'))

    fails = []

    def check(ok, label, detail=''):
        tag = 'PASS' if ok else 'FAIL'
        print('%s %-46s %s' % (tag, label, detail))
        if not ok:
            fails.append(label)

    import cold30_tail
    import cold15_tail
    from stats_history import load_snapshot, parse_time, period_slot
    import hitrate_baseline
    import framework_hitrate_cumulative as cum

    snapshot = load_snapshot(persist=False)
    records = snapshot.get('records') or []
    period_to_index = {str(row.get('period')): index for index, row in enumerate(records)}
    panel_path = root / 'panel.html'
    panel = panel_path.read_text(encoding='utf-8', errors='replace') if panel_path.exists() else ''
    crontab_txt = (root / 'ops' / 'crontab.txt').read_text(encoding='utf-8', errors='replace')
    try:
        live_cron = subprocess.run(['crontab', '-l'], capture_output=True, text=True,
                                   timeout=15).stdout
    except Exception:
        live_cron = ''

    for tag, mod, section, start, end in (
            ('cold30', cold30_tail, 'cold-thirty-ai', '<!-- COLD_THIRTY_START -->', '<!-- COLD_THIRTY_END -->'),
            ('cold15', cold15_tail, 'cold-fifteen-ai', '<!-- COLD_FIFTEEN_START -->', '<!-- COLD_FIFTEEN_END -->')):
        print('\n===== %s =====' % mod.RULE_VERSION)
        expected_window = 30 if tag == 'cold30' else 15
        check(mod.WINDOW == expected_window, tag + '.window', str(mod.WINDOW))
        check(mod.EXPECTED_ITEMS == 4, tag + '.expected_items', str(mod.EXPECTED_ITEMS))
        check(abs(mod.EXPECTED_GROUP_RATE - 0.6561) < 1e-12, tag + '.expected_group_rate',
              '%.4f' % mod.EXPECTED_GROUP_RATE)
        check(abs(9 ** 4 / float(10 ** 4) - mod.EXPECTED_GROUP_RATE) < 1e-12,
              tag + '.closure_9_4_over_10_4', '6561/10000')
        check(mod.EXPECTED_BASIS == 'enumerated-10^4-draws-x-10^4-configs',
              tag + '.expected_basis', mod.EXPECTED_BASIS)
        key_name = 'cold30_tail' if tag == 'cold30' else 'cold15_tail'
        reg_key = (key_name, mod.RULE_VERSION)
        check(reg_key in hitrate_baseline.RULE_BASELINE, tag + '.baseline_registry',
              str(hitrate_baseline.RULE_BASELINE.get(reg_key)))
        check(reg_key in cum.RULE_BASELINE, tag + '.cumulative_registry')
        check(key_name in cum.WINDOWS, tag + '.cumulative_window')

        seals = mod.load_seals()
        check(len(seals) >= 1, tag + '.seals_present', 'n=%d' % len(seals))
        if seals:
            seal = seals[-1]
            check(seal.get('rule_version') == mod.RULE_VERSION, tag + '.seal_rule',
                  str(seal.get('ref_period')))
            keys = [str(item.get('key')) for item in (seal.get('positions') or [])]
            check(keys == ['q', 'b', 's', 'g'], tag + '.seal_positions', str(keys))
            check(seal.get('publication_state') == 'published' and bool(seal.get('published_at')),
                  tag + '.seal_published', str(seal.get('published_at')))
            target_slot = period_slot(seal.get('target_period'))
            published = parse_time(seal.get('published_at'))
            check(target_slot is not None and published is not None and published < target_slot,
                  tag + '.seal_before_target_open',
                  'target_slot=%s' % (target_slot.isoformat() if target_slot else None))
            ref = str(seal.get('ref_period'))
            index = period_to_index.get(ref)
            window_rows = [list(row.get('numbers') or [])[:4] for row in records[:index + 1]][-mod.WINDOW:]
            payload = json.dumps([[int(value) for value in row] for row in window_rows],
                                 ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            check(sha256(payload.encode('utf-8')).hexdigest() == seal.get('input_sha256'),
                  tag + '.seal_input_sha256_recomputed', 'rows=%d' % len(window_rows))

        settlements = mod.load_settlements()
        check(len(settlements) >= 1, tag + '.settlements_present', 'n=%d' % len(settlements))
        seal_by_target = {str(item.get('target_period')): item for item in seals}
        for row in settlements:
            target = str(row.get('target_period'))
            actual = [str(value) for value in (row.get('actual') or [])]
            seal = seal_by_target.get(target) or {}
            kills = {str(item.get('key')): str(item.get('kill'))
                     for item in (seal.get('positions') or []) if isinstance(item, dict)}
            positions = row.get('positions') or []
            recomputed_ok = len(positions) == 4
            for i, item in enumerate(positions):
                hit = i < len(actual) and actual[i] != str(item.get('kill'))
                recomputed_ok = recomputed_ok and item.get('hit') == hit
                recomputed_ok = recomputed_ok and str(item.get('actual')) == (actual[i] if i < len(actual) else '')
            check(recomputed_ok, tag + '.settle_hits_recomputed', target)
            check(row.get('group_hit') == all(item.get('hit') is True for item in (row.get('positions') or [])),
                  tag + '.settle_group_hit', '%s=%s' % (target, row.get('group_hit')))
            check(row.get('counted') == ['q', 'b', 's', 'g'], tag + '.settle_counted', str(row.get('counted')))
            check(row.get('verified') is True, tag + '.settle_verified', target)
            record = records[period_to_index.get(target, 0)] if target in period_to_index else {}
            numbers = [str(value) for value in (record.get('numbers') or [])]
            check(actual == numbers[:4], tag + '.settle_actual_matches_draw',
                  '%s vs %s' % (''.join(actual), ''.join(numbers[:4])))
            check(row.get('rule_version') == mod.RULE_VERSION, tag + '.settle_rule', target)
            check(row.get('expected_items') == mod.EXPECTED_ITEMS and
                  abs(float(row.get('expected_group_rate') or 0) - mod.EXPECTED_GROUP_RATE) < 1e-12,
                  tag + '.settle_expected_fields', target)

        check(section in panel and start in panel and end in panel,
              tag + '.panel_block', section)
        latest_ref = ''
        if seals:
            latest_ref = str(seals[-1].get('ref_period'))
        check('data-as-of="' + latest_ref + '"' in panel, tag + '.panel_data_as_of', latest_ref)
        check('65.61%' in panel, tag + '.panel_baseline_text')
        check(('cold30-attack-text' if tag == 'cold30' else 'cold15-attack-text') in panel,
              tag + '.panel_attack_editor')

        runner = 'scripts/run_%s_tail.py' % tag
        lock = 'penta5-%st.lock' % tag
        check(runner in crontab_txt and lock in crontab_txt, tag + '.crontab_baseline')
        check(runner in live_cron and lock in live_cron, tag + '.crontab_live')
        log_path = root / 'data' / ('%s_tail_cron.log' % tag)
        log_tail = log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-40:] if log_path.exists() else []
        check(any(line.startswith('%s published:' % tag) for line in log_tail),
              tag + '.cron_publish_log')
        logrotate = Path('/etc/logrotate.d/penta5')
        ro_text = logrotate.read_text(encoding='utf-8', errors='replace') if logrotate.exists() else ''
        check(('%s_tail_cron.log' % tag) in ro_text, tag + '.logrotate_entry')

        baseline_json = load_json(root / 'data' / 'hitrate_baseline.json', {}) or {}
        baseline_rows = [row for row in baseline_json.get('rows') or []
                         if row.get('key') == ('%s_tail' % tag)]
        check(bool(baseline_rows) and abs(float(baseline_rows[0].get('expected') or 0) - 0.6561) < 1e-12,
              tag + '.baseline_json_row', str(len(baseline_rows)))
        cum_json = load_json(root / 'data' / 'framework_hitrate_cumulative.json', {}) or {}
        cum_rows = [row for row in cum_json.get('rows') or [] if row.get('key') == ('%s_tail' % tag)]
        check(bool(cum_rows) and abs(float(cum_rows[0].get('expected') or 0) - 0.6561) < 1e-12,
              tag + '.cumulative_json_row', str(len(cum_rows)))
        stability = load_json(root / 'data' / 'hitrate_stability.json', {}) or {}
        stable_rows = [row for row in stability.get('rows') or []
                       if row.get('key') == ('%s_tail' % tag)]
        check(bool(stable_rows), tag + '.stability_json_row', str(len(stable_rows)))

        import importlib
        view_name = 'cold_thirty_view' if tag == 'cold30' else 'cold_fifteen_view'
        view = importlib.import_module(view_name)
        rendered = view.render_block()
        check(section in rendered and '65.61%' in rendered, tag + '.view_render')

    print('\nSUMMARY: fails=%d' % len(fails))
    if fails:
        print('FAILED: ' + '; '.join(fails))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())