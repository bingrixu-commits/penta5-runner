#!/usr/bin/env python3
"""kill15 分位杀号决策引擎 runner（影子跑）。

每期由 cron 调用：
  */5 * * * * flock -n /var/lock/penta5-kill15.lock bash -c \
    'sleep 25 && cd /root/penta5-runner && timeout --kill-after=5s 280 \
     python3 -u scripts/run_kill15_tail.py >> data/kill15_tail_cron.log 2>&1'

用法：
  python3 scripts/run_kill15_tail.py --dry-run           # 只打印上下文与 prompt，不调模型
  python3 scripts/run_kill15_tail.py --reference 20261112055   # 指定参考期（补跑）
  python3 scripts/run_kill15_tail.py                     # 正常一期
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'scripts'))

import kill15_tail  # noqa: E402
from stats_history import beijing_now, load_snapshot  # noqa: E402
from ai_call_control import require_ai_enabled, AICallsDisabled  # noqa: E402
from panel_io import update_panel  # noqa: E402
from kill_fifteen_view import apply_kill_fifteen_block  # noqa: E402

WAIT_SECONDS = 150


def publish_block():
    """把最新封存/结算渲染进 panel.html（与 kill3/kill14 同一管线）。"""
    update_panel(lambda page: apply_kill_fifteen_block(page))


def _snapshot(wait_seconds=WAIT_SECONDS):
    stop = time.monotonic() + wait_seconds
    while True:
        snapshot = load_snapshot(persist=False)
        latest = snapshot.get('latest') or {}
        if str(latest.get('period') or ''):
            return snapshot
        if time.monotonic() >= stop:
            raise SystemExit('kill15 source unavailable')
        time.sleep(3.0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--reference', default=None)
    args = parser.parse_args()

    handle = None
    try:
        if not args.dry_run:
            require_ai_enabled()
        snapshot = _snapshot()
        if not args.dry_run:
            kill15_tail.settle_pending(snapshot)
        if not args.dry_run:
            try:
                review = kill15_tail.run_scheduled_review(snapshot)
            except Exception as error:
                print('kill15 review failed: %s: %s'
                      % (type(error).__name__, str(error)[:160]))
            else:
                if review:
                    rates = review.get('rates') or {}
                    group = rates.get('group') or {}
                    print('kill15 review #%s bucket=%s: 样本 %s 期（%s→%s）'
                          '整组 %s/%s，不一致 %s 项，提醒 %s 项'
                          % (review.get('review_no'), review.get('review_bucket'),
                             review.get('reviewed_periods'), review.get('from_ref'),
                             review.get('to_target'), group.get('hits'), group.get('bets'),
                             len(review.get('discrepancies') or []),
                             len(review.get('warnings') or [])))
        latest = snapshot.get('latest') or {}
        reference = str(args.reference or latest.get('period') or '')
        if len(reference) != 11:
            raise SystemExit('kill15 invalid latest period: %r' % reference)

        seals = kill15_tail.load_seals()
        if kill15_tail.find_seal(seals, reference) is not None:
            print('kill15 existing seal preserved; ref=%s' % reference)
            try:
                publish_block()
                print('kill15 panel block restored')
            except Exception as error:
                print('kill15 panel restore failed: %s' % type(error).__name__)
            return 0

        rows = [list(record.get('numbers') or [])[:4] for record in (snapshot.get('records') or [])]
        context = kill15_tail.build_context(rows)
        target_period = kill15_tail.next_period(reference)
        prompt, audit = kill15_tail.build_prompt(reference, target_period, rows, context=context)
        deadline, target = kill15_tail.deadline_for(reference)
        print('kill15 context: regime=%s burst=%d a_cap=%d pools=%s'
              % (context['regime']['label'], context['regime']['cold_burst_3'], context['a_cap'],
                 {key: context['positions'][key]['pool'] for key in kill15_tail.POSITION_KEYS}))
        print('kill15 perf: hit_a=%s hit_b=%s periods=%s'
              % (context['performance']['hit_a_15'], context['performance']['hit_b_15'],
                 context['performance']['periods']))
        cycle = context['cycle']
        print('kill15 cycle: 第 %d 轮 第 %d/%d 期%s'
              % (cycle['cycle_no'] + 1, cycle['cycle_pos'], cycle['cycle_periods'],
                 '（周期软重置）' if cycle.get('is_cycle_start') else ''))
        print('kill15 window: opened=%s deadline=%s (target %s)'
              % (latest.get('opentime'), deadline.isoformat(), target))

        if args.dry_run:
            print('--- PROMPT (%d chars) ---' % len(prompt))
            print(prompt)
            return 0

        remaining = (deadline - beijing_now()).total_seconds()
        if remaining < kill15_tail.MIN_REQUEST_SECONDS:
            print('kill15 skipped: publishing window closed (%.0fs left)' % remaining)
            return 0

        prediction, generated_at, meta = kill15_tail.request_prediction(
            prompt, reference, target, deadline, context)
        seal = kill15_tail.create_seal(reference, target, latest.get('opentime'), prediction,
                                       rows, context=context, prompt=prompt, audit=audit,
                                       generated_at=generated_at, response_meta=meta)
        kill15_tail.save_seal(seal)
        kill15_tail.mark_published(reference)
        try:
            publish_block()
        except Exception as error:
            kill15_tail.mark_publication_failed(reference, 'panel-write-%s' % type(error).__name__)
            print('kill15 panel publication failed: %s' % type(error).__name__)
            return 1

        allocation = prediction['allocation']
        picked = ' · '.join(
            '%s杀%d(%s%s)' % (kill15_tail.POSITION_LABELS[key], prediction['kills'][key],
                              'A' if key in allocation['a'] else 'B',
                              ('/' + allocation['b_sub'][key]) if key in allocation['b_sub'] else '')
            for key in kill15_tail.POSITION_KEYS)
        print('kill15 sealed: ref=%s target=%s %s' % (reference, target, picked))
        print('kill15 reason: %s' % (prediction.get('reason') or '-'))
        return 0
    finally:
        try:
            if handle is not None:
                handle.close()
        except Exception:
            pass


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except AICallsDisabled as error:
        print('kill15 skipped: AI calls disabled (%s)' % error)
        raise SystemExit(0)
    except Exception as error:
        print('kill15 failed: %s: %s' % (type(error).__name__, str(error)[:200]))
        raise SystemExit(1)
