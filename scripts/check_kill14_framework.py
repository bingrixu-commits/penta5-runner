#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读线上自检：kill14 定位排除框架（不写任何文件）。

检查项：模块口径常量、精确基线、封存 input_sha256/cold_ranking/position_state/连败强制可独立复算、
发布早于目标开奖、结算逐项可复算、面板区块/数据时效、cron/logrotate/env 挂接、派生统计注册行、视图渲染。
用法：python3 scripts/check_kill14_framework.py [--root /root/penta5-runner]
退出码：0 全部 PASS；1 存在 FAIL。
"""
import argparse
import json
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

POSITIONS = (('q', '千', 0), ('b', '百', 1), ('s', '十', 2), ('g', '个', 3))
STATS_WINDOWS = (70, 50, 30, 15)
TOP = 3
WINDOW = 70
# v1 口径的封存仍按 v1 规则（70/50 两窗口都连败）独立复算，不回填。
LEGACY_RULES = ('kill14-tail-v1-four-position-cold-on-loss',)


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def normalize_rows(rows):
    out = []
    for row in rows or []:
        try:
            values = [int(value) for value in list(row)[:4]]
        except (TypeError, ValueError):
            continue
        if len(values) == 4 and all(0 <= value <= 9 for value in values):
            out.append(values)
    return out


def rankings(rows, window):
    window_rows = rows[-window:]
    size = len(window_rows)
    ranked = {}
    for key, label, index in POSITIONS:
        values = [row[index] for row in window_rows]
        counts = [0] * 10
        last_index = {}
        for position, value in enumerate(values):
            counts[value] += 1
            last_index[value] = position
        items = []
        for code in range(10):
            omission = (size - 1 - last_index[code]) if code in last_index else size
            items.append({'key': key, 'label': label, 'code': str(code),
                          'count': counts[code], 'omission': omission})
        items.sort(key=lambda item: (item['count'], -item['omission'], int(item['code'])))
        for rank, item in enumerate(items, 1):
            item['rank'] = rank
        ranked[key] = items
    return ranked


def streak(values):
    win = loss = max_win = max_loss = 0
    for hit in values:
        if hit:
            win += 1
            loss = 0
            max_win = max(max_win, win)
        else:
            loss += 1
            win = 0
            max_loss = max(max_loss, loss)
    return {'current_win_streak': win, 'current_loss_streak': loss,
            'max_win_streak': max_win, 'max_loss_streak': max_loss}


def position_state(rows, window, top=TOP):
    hits = {key: [] for key, _label, _index in POSITIONS}
    for index in range(window, len(rows)):
        trailing = rows[index - window:index]
        ranked = rankings(trailing, window)
        for key, _label, position in POSITIONS:
            order = tuple(item['code'] for item in ranked[key][:top])
            hits[key].append(str(rows[index][position]) not in order)
    states = {}
    for key, _label, _index in POSITIONS:
        state = streak(hits[key])
        state['samples'] = len(hits[key])
        states[key] = state
    return states


def build_context(rows, windows=STATS_WINDOWS):
    """独立复算冷榜 / 连败状态；同时给出「全部窗口连败」(v1 口径) 与「任一窗口连败」(v2 口径)。"""
    windows = tuple(int(value) for value in windows)
    cold = {}
    for window in windows:
        ranked = rankings(rows[-window:], window)
        cold[str(window)] = {
            'window': window,
            'cold_top3': {key: ranked[key][:TOP] for key, _label, _index in POSITIONS},
            'cold_first': {key: ranked[key][0]['code'] for key, _label, _index in POSITIONS},
        }
    states = {str(window): position_state(rows, window) for window in windows}
    enforced_any = {}
    enforced_all = {}
    for key, label, _index in POSITIONS:
        loss_streak = {str(window): states[str(window)][key]['current_loss_streak']
                       for window in windows}
        losing = [window for window in windows if loss_streak[str(window)] > 0]
        if losing:
            allowed = []
            for window in losing:
                code = cold[str(window)]['cold_first'][key]
                if code not in allowed:
                    allowed.append(code)
            enforced_any[key] = {
                'key': key, 'label': label, 'allowed': allowed,
                'loss_streak': loss_streak, 'losing_windows': losing,
            }
        if losing and len(losing) == len(windows):
            enforced_all[key] = {
                'key': key, 'label': label,
                'allowed': [cold[str(window)]['cold_first'][key] for window in windows],
                'loss_streak': loss_streak,
            }
    return {'windows': cold, 'states': states,
            'enforced': enforced_any, 'enforced_any': enforced_any,
            'enforced_all': enforced_all}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default=str(Path(__file__).resolve().parents[1]),
                        help='仓库根；默认按本文件位置推导，任意 cwd 可跑')
    args = parser.parse_args()
    root = Path(args.root)
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / 'scripts'))
    sys.path.insert(0, str(root / 'lib'))

    fails = []

    def check(ok, label, detail=''):
        tag = 'PASS' if ok else 'FAIL'
        print('%s %-52s %s' % (tag, label, detail))
        if not ok:
            fails.append(label)

    import kill14_tail
    import hitrate_baseline
    import framework_hitrate_cumulative as cum
    import ops_health_check
    import evidence_backup
    import panel_live_refresh
    import attack_editor_view
    import kill_fourteen_view
    from stats_history import load_snapshot, parse_time, period_slot

    print('===== %s =====' % kill14_tail.RULE_VERSION)
    check(kill14_tail.WINDOW == 70, 'module.window', str(kill14_tail.WINDOW))
    check(tuple(kill14_tail.STATS_WINDOWS) == (70, 50, 30, 15), 'module.stats_windows',
          str(kill14_tail.STATS_WINDOWS))
    check(kill14_tail.TOP == 3, 'module.top', str(kill14_tail.TOP))
    check(kill14_tail.EXPECTED_ITEMS == 4, 'module.expected_items',
          str(kill14_tail.EXPECTED_ITEMS))
    check(abs(kill14_tail.EXPECTED_GROUP_RATE - 0.6561) < 1e-12, 'module.expected_group_rate',
          '%.4f' % kill14_tail.EXPECTED_GROUP_RATE)
    check(abs(9 ** 4 / float(10 ** 4) - kill14_tail.EXPECTED_GROUP_RATE) < 1e-12,
          'module.closure_9_4_over_10_4', '6561/10000')
    check(kill14_tail.EXPECTED_BASIS == 'enumerated-10^4-draws-x-10^4-configs',
          'module.expected_basis', kill14_tail.EXPECTED_BASIS)
    check(kill14_tail.PROMPT_VERSION == 'kill14-tail-prompt-v2-four-position-cold-multi-window',
          'module.prompt_version', kill14_tail.PROMPT_VERSION)
    check(tuple(getattr(kill14_tail, 'LEGACY_RULES', ())) == LEGACY_RULES,
          'module.legacy_rules', str(getattr(kill14_tail, 'LEGACY_RULES', ())))

    key_name = 'kill14_tail'
    reg_key = (key_name, kill14_tail.RULE_VERSION)
    check(reg_key in hitrate_baseline.RULE_BASELINE, 'baseline.registry',
          str(hitrate_baseline.RULE_BASELINE.get(reg_key)))
    check(hitrate_baseline.RULE_BASELINE.get(reg_key) == (4, 0.6561), 'baseline.registry_value')
    check(key_name in hitrate_baseline.LABELS, 'baseline.label')
    check(hasattr(hitrate_baseline, '_kill14_rows'), 'baseline.rows_builder')
    check(reg_key in cum.RULE_BASELINE, 'cumulative.registry')
    check(key_name in cum.WINDOWS, 'cumulative.window_label')
    check(hasattr(cum, 'cum_kill14'), 'cumulative.function')
    checks = list(getattr(ops_health_check, 'CHECK_FUNCS', []) or [])
    check(any(getattr(fn, '__name__', '') == 'check_kill14' for fn in checks),
          'ops_health.check_registered')
    check(ops_health_check.FALLBACK_NAMES.get('check_kill14') == ('kill14_tail',),
          'ops_health.fallback_names')
    check('kill14_tail/seals.json' in ops_health_check.LEDGER_SIZE_FILES,
          'ops_health.ledger_size_files')
    check(ops_health_check.LEDGER_ALERT_FILES.get('kill14_tail/seals.json') == 'kill14 封存账本',
          'ops_health.ledger_alert_label')
    check('kill14_tail' in evidence_backup.JSON_DIRS, 'evidence_backup.json_dirs')
    check('KILL_FOURTEEN' in panel_live_refresh.REFRESH_SCRIPT and
          "var version='quiet-panel-v" in panel_live_refresh.REFRESH_SCRIPT,
          'panel_refresh.script')
    check('kill14-attack-text' in attack_editor_view.SCRIPT, 'attack_editor.definition')

    snapshot = load_snapshot(persist=False)
    records = snapshot.get('records') or []
    period_to_index = {str(row.get('period')): index for index, row in enumerate(records)}
    all_rows = [normalize_rows([row.get('numbers') or []])[0] if normalize_rows([row.get('numbers') or []]) else None
                for row in records]
    all_rows = [row for row in all_rows if row]
    # rows with period alignment
    rows_by_period = {}
    ordered_periods = []
    for row in records:
        norm = normalize_rows([row.get('numbers') or []])
        if norm:
            rows_by_period[str(row.get('period'))] = norm[0]
            ordered_periods.append(str(row.get('period')))
    panel_path = root / 'panel.html'
    panel = panel_path.read_text(encoding='utf-8', errors='replace') if panel_path.exists() else ''
    crontab_txt = (root / 'ops' / 'crontab.txt').read_text(encoding='utf-8', errors='replace')
    try:
        live_cron = subprocess.run(['crontab', '-l'], capture_output=True, text=True,
                                   timeout=15).stdout
    except Exception:
        live_cron = ''
    env_text = (root / '.env').read_text(encoding='utf-8', errors='replace') if (root / '.env').exists() else ''
    logrotate_text = ''
    if Path('/etc/logrotate.d/penta5').exists():
        logrotate_text = Path('/etc/logrotate.d/penta5').read_text(encoding='utf-8', errors='replace')

    seals = kill14_tail.load_seals()
    settlements = kill14_tail.load_settlements()
    check(len(seals) >= 1, 'seals.present', 'n=%d' % len(seals))
    check(len(settlements) >= 1, 'settlements.present', 'n=%d' % len(settlements))

    latest_ctx = None
    for seal in seals:
        ref = str(seal.get('ref_period'))
        target = str(seal.get('target_period'))
        label = ref + '->' + target
        seal_rule = str(seal.get('rule_version') or '')
        check(seal_rule in (kill14_tail.RULE_VERSION,) + LEGACY_RULES, 'seal.rule',
              label + ' ' + seal_rule)
        positions = [item for item in (seal.get('positions') or []) if isinstance(item, dict)]
        keys = [str(item.get('key')) for item in positions]
        check(keys == ['q', 'b', 's', 'g'], 'seal.positions', label + ' ' + str(keys))
        check(all(str(item.get('kill')).isdigit() and len(str(item.get('kill'))) == 1
                  for item in positions), 'seal.kill_digits', label)
        check(seal.get('publication_state') == 'published' and bool(seal.get('published_at')),
              'seal.published', label + ' ' + str(seal.get('published_at')))
        check(seal.get('window') == WINDOW, 'seal.window', label + ' ' + str(seal.get('window')))
        check(seal.get('expected_items') == 4 and
              abs(float(seal.get('expected_group_rate') or 0) - 0.6561) < 1e-12,
              'seal.expected_fields', label)
        check(len(str(seal.get('prompt_sha256') or '')) == 64, 'seal.prompt_sha256', label)
        check(bool(seal.get('panel_context')) and
              sha256(str(seal.get('panel_context')).encode('utf-8')).hexdigest() ==
              seal.get('panel_context_sha256'), 'seal.panel_context_sha256', label)

        index = period_to_index.get(ref)
        check(index is not None, 'seal.ref_in_history', label)
        if index is not None:
            rows = [rows_by_period[p] for p in ordered_periods[:index + 1] if p in rows_by_period]
            window_rows = rows[-WINDOW:]
            payload = json.dumps(window_rows, ensure_ascii=False, sort_keys=True,
                                 separators=(',', ':'))
            check(sha256(payload.encode('utf-8')).hexdigest() == seal.get('input_sha256'),
                  'seal.input_sha256_recomputed', label + ' rows=%d' % len(window_rows))
            # 保持封存里的窗口顺序（v1: 70/50；v2: 70/50/30/15），与运行时判定顺序一致。
            stored_windows = tuple(int(str(w)) for w in (seal.get('cold_ranking') or {}))
            expected_windows = ((70, 50) if seal_rule in LEGACY_RULES else STATS_WINDOWS)
            check(stored_windows == expected_windows, 'seal.windows_recorded',
                  label + ' ' + str(stored_windows))
            ctx = build_context(rows, stored_windows)
            latest_ctx = ctx
            recomputed_cold = {str(w): ctx['windows'][str(w)]['cold_top3']
                               for w in stored_windows}
            check(recomputed_cold == (seal.get('cold_ranking') or {}),
                  'seal.cold_ranking_recomputed', label)
            stored_state = seal.get('position_state') or {}
            recomputed_state = {str(w): ctx['states'][str(w)] for w in stored_windows}

            def _streaks_only(state):
                return {str(w): {key: {field: value for field, value in item.items()
                                      if field != 'samples'}
                                 for key, item in (state.get(str(w)) or {}).items()}
                        for w in stored_windows}

            check(_streaks_only(recomputed_state) == _streaks_only(stored_state),
                  'seal.position_state_recomputed', label)
            trimmed = len(records) - len(rows)
            drift_notes = []
            samples_ok = True
            for window in stored_windows:
                for key, _label, _index in POSITIONS:
                    stored = ((stored_state.get(str(window)) or {}).get(key) or {}).get('samples')
                    recomputed = recomputed_state[str(window)][key]['samples']
                    drift = None if stored is None else stored - recomputed
                    if drift is None or drift < 0 or drift > trimmed:
                        samples_ok = False
                    drift_notes.append('%s/%s:%s-%s=%s' % (window, key, stored,
                                                           recomputed, drift))
            check(samples_ok, 'seal.position_state_samples_drift',
                  label + ' rows=%d trimmed=%d ' % (len(rows), trimmed) +
                  ' '.join(drift_notes))
            rule_enforced = (ctx['enforced_all'] if seal_rule in LEGACY_RULES
                             else ctx['enforced_any'])
            enforced_keys = set(rule_enforced)
            check(sorted(enforced_keys) == sorted(seal.get('enforced_positions') or []),
                  'seal.enforced_positions_recomputed',
                  label + ' ' + str(sorted(enforced_keys)))
            allowed = {k: rule_enforced[k]['allowed'] for k in rule_enforced}
            check(allowed == (seal.get('enforced_allowed') or {}),
                  'seal.enforced_allowed_recomputed', label)
            for item in positions:
                key = str(item.get('key'))
                kill = str(item.get('kill'))
                detail = None
                if seal_rule in LEGACY_RULES:
                    cold70 = ctx['windows']['70']['cold_first'][key]
                    cold50 = ctx['windows']['50']['cold_first'][key]
                    expected_basis = ('cold70-rank1' if kill == cold70 else
                                      'cold50-rank1' if kill == cold50 else 'ai')
                    detail = next((entry for entry in ctx['windows']['70']['cold_top3'][key]
                                   if entry['code'] == kill), None)
                    allowed_codes = (cold70, cold50)
                else:
                    expected_basis = 'ai'
                    for window in stored_windows:
                        block = ctx['windows'][str(window)]
                        if kill == block['cold_first'][key]:
                            expected_basis = 'cold%d-rank1' % window
                            detail = next((entry for entry in block['cold_top3'][key]
                                           if entry['code'] == kill), None)
                            break
                    if detail is None:
                        for window in stored_windows:
                            detail = next((entry for entry in
                                           ctx['windows'][str(window)]['cold_top3'][key]
                                           if entry['code'] == kill), None)
                            if detail is not None:
                                break
                    allowed_codes = tuple(ctx['windows'][str(w)]['cold_first'][key]
                                          for w in stored_windows)
                check(item.get('basis') == expected_basis, 'seal.position_basis',
                      '%s %s kill=%s basis=%s' % (label, key, kill, item.get('basis')))
                check(item.get('enforced') is (key in enforced_keys), 'seal.position_enforced',
                      '%s %s' % (label, key))
                if key in enforced_keys:
                    check(kill in allowed_codes, 'seal.enforced_kill_allowed',
                          '%s %s kill=%s allowed=%s' % (label, key, kill,
                                                         '/'.join(allowed_codes)))
                if detail:
                    check((item.get('count'), item.get('omission'), item.get('rank')) ==
                          (detail['count'], detail['omission'], detail['rank']),
                          'seal.position_detail', '%s %s' % (label, key))
            target_slot = period_slot(target)
            published = parse_time(seal.get('published_at'))
            generated = parse_time(seal.get('generated_at'))
            reference_open = parse_time(seal.get('reference_open_time'))
            check(reference_open is not None and generated is not None and
                  reference_open <= generated, 'seal.generated_after_reference_open', label)
            check(target_slot is not None and published is not None and published < target_slot,
                  'seal.published_before_target_open',
                  label + ' target_slot=%s' % (target_slot.isoformat() if target_slot else None))
            check(seal.get('data_source') == 'data/stats_draw_history.json:first-four-balls',
                  'seal.data_source', label)

    seal_by_target = {str(item.get('target_period')): item for item in seals}
    for row in settlements:
        target = str(row.get('target_period'))
        actual = [str(value) for value in (row.get('actual') or [])]
        positions = [item for item in (row.get('positions') or []) if isinstance(item, dict)]
        seal = seal_by_target.get(target) or {}
        checks_ok = len(positions) == 4
        for item in positions:
            key = str(item.get('key'))
            kill = str(item.get('kill'))
            position_index = dict((k, i) for k, _l, i in POSITIONS).get(key)
            hit = position_index is not None and position_index < len(actual) and \
                actual[position_index] != kill
            checks_ok = checks_ok and item.get('hit') == hit and \
                str(item.get('actual')) == (actual[position_index] if position_index is not None and position_index < len(actual) else '')
        check(checks_ok, 'settle.hits_recomputed', target)
        check(row.get('group_hit') == all(item.get('hit') is True for item in positions),
              'settle.group_hit', '%s=%s' % (target, row.get('group_hit')))
        check(row.get('counted') == ['q', 'b', 's', 'g'], 'settle.counted', target)
        check(row.get('verified') is True, 'settle.verified', target)
        check(str(row.get('rule_version') or '') in
              (kill14_tail.RULE_VERSION,) + LEGACY_RULES, 'settle.rule',
              target + ' ' + str(row.get('rule_version')))
        check(row.get('expected_items') == 4 and
              abs(float(row.get('expected_group_rate') or 0) - 0.6561) < 1e-12,
              'settle.expected_fields', target)
        record = records[period_to_index.get(target, 0)] if target in period_to_index else {}
        numbers = [str(value) for value in (record.get('numbers') or [])]
        check(actual == numbers[:4], 'settle.actual_matches_draw',
              '%s vs %s' % (''.join(actual), ''.join(numbers[:4])))
        check(row.get('enforced_positions') == (seal.get('enforced_positions') or []),
              'settle.enforced_positions', target)

    latest_ref = str(seals[-1].get('ref_period')) if seals else ''
    check('<!-- KILL_FOURTEEN_START -->' in panel and '<!-- KILL_FOURTEEN_END -->' in panel,
          'panel.markers', latest_ref)
    check(panel.count('<!-- KILL_FOURTEEN_START -->') == 1 and
          panel.count('<!-- KILL_FOURTEEN_END -->') == 1, 'panel.markers_unique')
    check('<section id="kill-fourteen-ai"' in panel, 'panel.section')
    check('data-as-of="' + latest_ref + '"' in panel, 'panel.data_as_of', latest_ref)
    check('65.61%' in panel, 'panel.baseline_text')
    check('kill14-attack-text' in panel, 'panel.attack_editor')

    check('scripts/run_kill14_tail.py' in crontab_txt and 'penta5-kill14t.lock' in crontab_txt,
          'cron.baseline')
    check('scripts/run_kill14_tail.py' in live_cron and 'penta5-kill14t.lock' in live_cron,
          'cron.live')
    check('kill14_tail_cron.log' in logrotate_text, 'logrotate.entry')
    check('PENTA5_MODEL_KILL14=' in env_text, 'env.model')
    log_tail = []
    log_path = root / 'data' / 'kill14_tail_cron.log'
    if log_path.exists():
        log_tail = log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-60:]
    check(any(line.startswith('kill14 published:') for line in log_tail), 'cron.publish_log')

    baseline_json = load_json(root / 'data' / 'hitrate_baseline.json', {}) or {}
    baseline_rows = [row for row in baseline_json.get('rows') or []
                     if row.get('key') == key_name]
    check(bool(baseline_rows) and
          abs(float(baseline_rows[0].get('expected') or 0) - 0.6561) < 1e-12,
          'baseline_json.row', str(len(baseline_rows)))
    cum_json = load_json(root / 'data' / 'framework_hitrate_cumulative.json', {}) or {}
    cum_rows = [row for row in cum_json.get('rows') or [] if row.get('key') == key_name]
    check(bool(cum_rows) and abs(float(cum_rows[0].get('expected') or 0) - 0.6561) < 1e-12,
          'cumulative_json.row', str(len(cum_rows)))
    stability = load_json(root / 'data' / 'hitrate_stability.json', {}) or {}
    stable_rows = [row for row in stability.get('rows') or [] if row.get('key') == key_name]
    check(bool(stable_rows), 'stability_json.row', str(len(stable_rows)))

    rendered = kill_fourteen_view.render_block()
    check('<section id="kill-fourteen-ai"' in rendered and '65.61%' in rendered,
          'view.render', 'bytes=%d' % len(rendered))
    check('data-as-of="' + latest_ref + '"' in rendered, 'view.data_as_of', latest_ref)
    if latest_ctx is not None:
        try:
            today = kill14_tail.today_stats()
            check(abs(float(today.get('expected', {}).get('group_rate') or 0) - 0.6561) < 1e-12,
                  'stats.expected_group_rate')
            check(sorted((today.get('per_position') or {}).keys()) == ['b', 'g', 'q', 's'],
                  'stats.per_position_keys', str(sorted((today.get('per_position') or {}).keys())))
            attack = kill14_tail.build_attack(seals[-1])
            check(len(attack) == 42 and attack.endswith('四定'), 'attack.format',
                  'len=%d' % len(attack))
            mapping = {str(item.get('key')): str(item.get('kill'))
                       for item in (seals[-1].get('positions') or [])}
            segments_ok = all(label in attack and
                              mapping[key] not in attack.split(label)[1].split('四定')[0][:9]
                              for key, label in (('q', '千'), ('b', '百'), ('s', '十'), ('g', '个')))
            check(segments_ok, 'attack.excludes_kill')
        except Exception as exc:  # noqa: BLE001
            check(False, 'attack.format', '%s: %s' % (type(exc).__name__, exc))

    print('\nSUMMARY: fails=%d' % len(fails))
    if fails:
        print('FAILED: ' + '; '.join(fails))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
