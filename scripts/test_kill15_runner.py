#!/usr/bin/env python3
"""kill15 runner 自测：验证「台账维护不受 AI 开关影响」与 AI 关闭时的降级路径。

不读真实账本、不调用模型、不写任何正式数据：只 monkeypatch 模块函数。
用法：python3 scripts/test_kill15_runner.py
"""
import sys
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT / 'lib'))
sys.path.insert(0, str(ROOT / 'scripts'))

import kill15_tail  # noqa: E402
from ai_call_control import AICallsDisabled  # noqa: E402
import run_kill15_tail as runner  # noqa: E402

FAILED = []


def main():
    settle_original = kill15_tail.settle_pending
    review_original = kill15_tail.run_scheduled_review
    request_original = kill15_tail.request_prediction
    calls = []

    # ---- 1. 台账维护顺序：先结算，再复盘；异常不抛出 ----
    try:
        kill15_tail.settle_pending = (
            lambda snapshot: calls.append(('settle', snapshot.get('marker'))) or [])
        kill15_tail.run_scheduled_review = (
            lambda snapshot: calls.append(('review', snapshot.get('marker')))
            or [{'review_no': 1, 'review_bucket': 0, 'trigger': 'forced',
                 'reviewed_periods': 1, 'rates': {'group': {'hits': 1, 'bets': 1}},
                 'discrepancies': [], 'warnings': []}])
        records = runner.maintain_ledger({'marker': 'snap-1'})
        if [kind for kind, _marker in calls] != ['settle', 'review']:
            FAILED.append('台账维护顺序错误: %r' % (calls,))
        if not records:
            FAILED.append('maintain_ledger 未返回复盘记录')
        else:
            print('PASS ledger-order: settle -> review，AI 开关不参与该路径')
    finally:
        kill15_tail.settle_pending = settle_original
        kill15_tail.run_scheduled_review = review_original

    # ---- 2. AI 关闭：request_or_skip 返回 None，不抛异常 ----
    def _disabled(*args, **kwargs):
        raise AICallsDisabled('ai-api-stopped')

    kill15_tail.request_prediction = _disabled
    try:
        result = runner.request_or_skip('p', '20261112090', '20261112091', None, {})
        if result is not None:
            FAILED.append('AI 关闭时 request_or_skip 应返回 None')
        else:
            print('PASS ai-disabled: AICallsDisabled -> 跳过本期预测，退出码 0 路径')
    except AICallsDisabled:
        FAILED.append('AI 关闭时 request_or_skip 未吞掉 AICallsDisabled')
    except Exception as error:
        FAILED.append('AI 关闭时 request_or_skip 抛出异常: %s: %s'
                      % (type(error).__name__, error))
    finally:
        kill15_tail.request_prediction = request_original

    # ---- 3. AI 正常：透传三元组 ----
    sentinel = ({'allocation': {}, 'kills': {}}, 'generated', {'model': 'selftest'})
    kill15_tail.request_prediction = lambda *args, **kwargs: sentinel
    try:
        result = runner.request_or_skip('p', '20261112090', '20261112091', None, {})
        if result != sentinel:
            FAILED.append('AI 正常时 request_or_skip 未透传预测结果')
        else:
            print('PASS ai-enabled: 预测结果透传不变')
    finally:
        kill15_tail.request_prediction = request_original

    # ---- 4. dry-run 不维护台账（main 中由 args.dry_run 控制）----
    source = Path(runner.__file__).read_text(encoding='utf-8')
    if 'if not args.dry_run:\n            maintain_ledger(snapshot)' not in source:
        FAILED.append('缺少 dry-run 跳过台账维护的保护')
    if 'require_ai_enabled' in source:
        FAILED.append('runner 入口仍直接依赖 require_ai_enabled')
    else:
        print('PASS dry-run/entry: dry-run 不写账本；入口不再被 AI 开关整体阻断')

    print()
    if FAILED:
        print('SELFTEST FAILED: %d 项' % len(FAILED))
        for item in FAILED:
            print(' -', item)
        return 1
    print('SELFTEST OK: 全部通过')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as error:
        print('SELFTEST CRASH: %s: %s' % (type(error).__name__, str(error)[:300]))
        raise SystemExit(1)
