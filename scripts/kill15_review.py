#!/usr/bin/env python3
"""kill15 台账抽样复盘 CLI（v4）。

每 30 期由 runner 自动做一次；本脚本用于手动触发或人工核对：
  python3 scripts/kill15_review.py            # 到期才复盘（未到期直接退出）
  python3 scripts/kill15_review.py --force    # 立即复盘最近窗口（写入 reviews.json）
  python3 scripts/kill15_review.py --json     # 输出完整复盘记录 JSON

只读正式账本 + 原始开奖历史，不调用模型、不改封存/结算；复盘结果写
data/kill15_tail/reviews.json（append-only），发现不一致时追加 review_alerts.log。
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT / 'lib'))

import kill15_tail  # noqa: E402


def _fmt_rate(rate):
    return ('%.1f%%' % (100 * rate)) if isinstance(rate, (int, float)) else '无样本'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true',
                        help='忽略周期/去重，立即复盘最近窗口')
    parser.add_argument('--json', action='store_true', help='输出完整复盘记录')
    args = parser.parse_args()

    record = kill15_tail.run_scheduled_review(force=args.force)
    if record is None:
        print('kill15 review: 未到期或本轮 bucket 已复盘（每 %d 期一次）'
              % kill15_tail.REVIEW_PERIODS)
        return 0
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        rates = record.get('rates') or {}
        group = rates.get('group') or {}
        single = rates.get('single') or {}
        print('kill15 review #%d bucket=%d trigger=%s'
              % (record.get('review_no'), record.get('review_bucket'),
                 record.get('trigger')))
        print('样本 %d 期（%s → %s）| 整组 %s/%s = %s | 单码 %s/%s = %s'
              % (record.get('reviewed_periods'), record.get('from_ref'),
                 record.get('to_target'), group.get('hits'), group.get('bets'),
                 _fmt_rate(group.get('rate')), single.get('hits'),
                 single.get('bets'), _fmt_rate(single.get('rate'))))
        print('不一致 %d 项：%s'
              % (len(record.get('discrepancies') or []),
                 '；'.join(record.get('discrepancies') or ['无'])))
        print('提醒 %d 项：%s'
              % (len(record.get('warnings') or []),
                 '；'.join(record.get('warnings') or ['无'])))
        print('status=%s' % record.get('status'))
    return 0 if record.get('status') == 'ok' else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        print('kill15 review failed: %s: %s' % (type(error).__name__, str(error)[:200]))
        raise SystemExit(1)
