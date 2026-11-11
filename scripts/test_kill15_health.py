#!/usr/bin/env python3
"""kill15 健康巡检探针自测：用临时目录合成封存账本，不读真实运营数据。

覆盖 ops_health_check.check_kill15 的三类探针：
  kill15_tail    封存/发布及时性
  kill15_policy  v2/v3 规则合规（池内选码 / A 上下限 / B 子策略分散 / 惯性护栏 / 自检 / 版本漂移）
  kill15_retry   近 6h 重试/失败统计

用法：python3 scripts/test_kill15_health.py
"""
import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ops_health_check as health  # noqa: E402

FAILED = []
TZ = health.TZ
NOW = datetime.now(TZ)
PROMPT = 'kill15-tail-prompt-v3-inertia-guard'
POOLS = {'q': [1, 2, 3, 4, 5], 'b': [2, 3, 4, 5, 6],
         's': [3, 4, 5, 6, 7], 'g': [4, 5, 6, 7, 8]}
SUB_ORDER = {'B1': 'B1', 'B2': 'B2', 'B3': 'B3'}


def make_seal(ref, target, a, b, subs, prompt_version=PROMPT, published=True,
              self_check=True, policy=True, a_cap=1, kills=None):
    positions = {}
    kill_map = kills or {key: POOLS[key][0] for key in POOLS}
    for key in POOLS:
        positions[key] = {
            'key': key,
            'label': {'q': '千', 'b': '百', 's': '十', 'g': '个'}[key],
            'kill': int(kill_map[key]),
            'group': 'A' if key in a else 'B',
            'b_sub': subs.get(key),
            'in_pool': True,
        }
    seal = {
        'version': 1,
        'ref_period': ref,
        'target_period': target,
        'generated_at': NOW.isoformat(),
        'publication_state': 'published' if published else 'prepared',
        'published_at': NOW.isoformat() if published else None,
        'allocation': {'a': list(a), 'b': list(b), 'b_sub': dict(subs)},
        'positions': positions,
        'a_cap': a_cap,
        'candidate_pools': {key: list(POOLS[key]) for key in POOLS},
        'prompt_version': prompt_version,
        'self_check': {key: True for key in health.KILL15_SELF_CHECK_KEYS} if self_check else {},
        'policy': ({'a_min': 1, 'allocation_reuse_forbidden': True,
                    'scheme_reuse_forbidden': True, 'scheme_repeat_window': 2,
                    'b_sub_dispersion_required': True}
                   if policy else {}),
        'mode': 'shadow',
    }
    return seal


def build_root(seals, cron_lines=None):
    """Build a throwaway project root with only what check_kill15 needs."""
    tmp = Path(tempfile.mkdtemp(prefix='kill15-health-'))
    (tmp / 'lib').mkdir()
    (tmp / 'data' / 'kill15_tail').mkdir(parents=True)
    (tmp / 'lib' / 'kill15_tail.py').write_text(
        "PROMPT_VERSION = '%s'\n" % PROMPT, encoding='utf-8')
    (tmp / 'data' / 'kill15_tail' / 'seals.json').write_text(
        json.dumps({'version': 1, 'records': seals}, ensure_ascii=False), encoding='utf-8')
    log = tmp / 'data' / 'kill15_tail_cron.log'
    if cron_lines is None:
        log.write_text('', encoding='utf-8')
    else:
        log.write_text('\n'.join(cron_lines) + '\n', encoding='utf-8')
    return tmp


def make_ctx(root, latest_ref, age_seconds=60):
    return {
        'root': root,
        'now': NOW,
        'latest': {'ref': latest_ref,
                   'opentime': (NOW - timedelta(seconds=age_seconds)).isoformat(),
                   'opened': NOW - timedelta(seconds=age_seconds),
                   'source': 'selftest'},
        'state': {},
        'backup_base': Path('/root/backups'),
    }


def check_entries(ctx):
    entries = health.check_kill15(ctx)
    return {entry['name']: entry for entry in entries}


def expect(name, entries, status, needle=None):
    entry = entries.get(name)
    if entry is None:
        FAILED.append('%s: 探针缺失' % name)
        return
    if entry.get('status') != status:
        FAILED.append('%s: 状态 %r != %r (%s)'
                      % (name, entry.get('status'), status, entry.get('detail')))
        return
    if needle and needle not in str(entry.get('detail')):
        FAILED.append('%s: detail 不含 %r: %s' % (name, needle, entry.get('detail')))


VALID_SEALS = [
    make_seal('20261112070', '20261112071', ['q'], ['b', 's', 'g'],
              {'b': 'B1', 's': 'B2', 'g': 'B3'}),
    make_seal('20261112072', '20261112073', ['s'], ['q', 'b', 'g'],
              {'q': 'B2', 'b': 'B3', 'g': 'B1'}),
    make_seal('20261112074', '20261112075', ['b'], ['q', 's', 'g'],
              {'q': 'B1', 's': 'B2', 'g': 'B3'}),
]


def test_valid():
    root = build_root(VALID_SEALS)
    entries = check_entries(make_ctx(root, '20261112074'))
    expect('kill15_tail', entries, 'ok', 'published_at')
    expect('kill15_policy', entries, 'ok', '合规')
    expect('kill15_retry', entries, 'ok', 'runs=0')
    print('PASS valid: 正常封存 + 规则合规 + 无重试')


def test_scheme_repeat():
    repeat = make_seal('20261112074', '20261112075', ['q'], ['b', 's', 'g'],
                       {'b': 'B1', 's': 'B2', 'g': 'B3'})
    root = build_root([VALID_SEALS[0], VALID_SEALS[1], repeat])
    entries = check_entries(make_ctx(root, '20261112074'))
    expect('kill15_policy', entries, 'fail', '惯性护栏')
    print('PASS scheme-repeat: 完整方案与前 2 期重复 -> fail')


def test_a_zero():
    zero = make_seal('20261112074', '20261112075', [], ['q', 'b', 's', 'g'],
                     {'q': 'B1', 'b': 'B2', 's': 'B3', 'g': 'B3'})
    root = build_root([VALID_SEALS[0], VALID_SEALS[1], zero])
    entries = check_entries(make_ctx(root, '20261112074'))
    expect('kill15_policy', entries, 'fail', 'A=0')
    print('PASS a-zero: A=0 -> fail')


def test_dup_subs():
    dup = make_seal('20261112074', '20261112075', ['b'], ['q', 's', 'g'],
                    {'q': 'B1', 's': 'B1', 'g': 'B3'})
    root = build_root([VALID_SEALS[0], VALID_SEALS[1], dup])
    entries = check_entries(make_ctx(root, '20261112074'))
    expect('kill15_policy', entries, 'fail', 'B 子策略重复')
    print('PASS dup-subs: B 子策略重复 -> fail')


def test_out_of_pool():
    outside = make_seal('20261112074', '20261112075', ['b'], ['q', 's', 'g'],
                        {'q': 'B1', 's': 'B2', 'g': 'B3'},
                        kills={'q': 9, 'b': 2, 's': 3, 'g': 4})
    root = build_root([VALID_SEALS[0], VALID_SEALS[1], outside])
    entries = check_entries(make_ctx(root, '20261112074'))
    expect('kill15_policy', entries, 'fail', '不在候选池')
    print('PASS out-of-pool: 池外杀码 -> fail')


def test_self_check_missing():
    missing = make_seal('20261112074', '20261112075', ['b'], ['q', 's', 'g'],
                        {'q': 'B1', 's': 'B2', 'g': 'B3'}, self_check=False)
    root = build_root([VALID_SEALS[0], VALID_SEALS[1], missing])
    entries = check_entries(make_ctx(root, '20261112074'))
    expect('kill15_policy', entries, 'fail', '强制自检')
    print('PASS self-check-missing: v3 缺自检 -> fail')


def test_prompt_drift():
    v2 = make_seal('20261112074', '20261112075', ['b'], ['q', 's', 'g'],
                   {'q': 'B1', 's': 'B2', 'g': 'B3'},
                   prompt_version='kill15-tail-prompt-v2-anticollapse')
    root = build_root([VALID_SEALS[0], VALID_SEALS[1], v2])
    entries = check_entries(make_ctx(root, '20261112074'))
    expect('kill15_policy', entries, 'warn', '漂移')
    print('PASS prompt-drift: prompt_version 与 lib 期望不一致 -> warn')


def test_retry_and_failure():
    ts = (NOW - timedelta(seconds=120)).astimezone().isoformat()
    lines = [
        'AI_HTTP_PHASE {"ref":"20261112074","attempt":1,"phase":"start","at":"%s"}' % ts,
        'AI_HTTP_PHASE {"ref":"20261112074","attempt":1,"phase":"complete","at":"%s","status":200}' % ts,
        'AI_HTTP_PHASE {"ref":"20261112074","attempt":2,"phase":"start","at":"%s"}' % ts,
        'kill15 failed: RuntimeError: selftest',
    ]
    root = build_root(VALID_SEALS, cron_lines=lines)
    entries = check_entries(make_ctx(root, '20261112074'))
    expect('kill15_retry', entries, 'warn', 'retries=1')
    print('PASS retry: 重试 + 失败 -> warn')


def test_seal_lag():
    stale = make_seal('20261112070', '20261112071', ['q'], ['b', 's', 'g'],
                      {'b': 'B1', 's': 'B2', 'g': 'B3'})
    root = build_root([stale])
    entries = check_entries(make_ctx(root, '20261112080', age_seconds=1200))
    expect('kill15_tail', entries, 'fail', 'missing seal')
    print('PASS seal-lag: 最新期无封存且超过宽限 -> fail')


def test_unpublished():
    prepared = make_seal('20261112074', '20261112075', ['b'], ['q', 's', 'g'],
                         {'q': 'B1', 's': 'B2', 'g': 'B3'}, published=False)
    root = build_root([VALID_SEALS[0], VALID_SEALS[1], prepared])
    entries = check_entries(make_ctx(root, '20261112074'))
    expect('kill15_tail', entries, 'warn', 'not published')
    print('PASS unpublished: 已封存未发布 -> warn')


def main():
    for test in (test_valid, test_scheme_repeat, test_a_zero, test_dup_subs,
                 test_out_of_pool, test_self_check_missing, test_prompt_drift,
                 test_retry_and_failure, test_seal_lag, test_unpublished):
        try:
            test()
        except Exception as error:
            FAILED.append('%s 崩溃: %s: %s' % (test.__name__, type(error).__name__,
                                               str(error)[:200]))
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
