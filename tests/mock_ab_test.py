"""kill1 A/B 离线自测（mock 台账 + mock 结算，不调 API、不碰线上数据）。

用法：python3 mock_ab_test.py <repo_root>
"""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile

ROOT = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

tmp = Path(tempfile.mkdtemp(prefix='kill1-ab-mock-'))
os.environ['PENTA5_KILL1_AB_PRO'] = '1'
os.environ['PENTA5_KILL1_AB_SAMPLE'] = '1.0'
os.environ['PENTA5_KILL1_AB_LEDGER'] = str(tmp / 'ab_raw.jsonl')
os.environ['PENTA5_KILL1_AB_MARKER_DIR'] = str(tmp / 'ab_spawned')
os.environ['PENTA5_KILL1_AB_SETTLEMENTS'] = str(tmp / 'settlements.json')

import kill1_ab  # noqa: E402

RULES = kill1_ab.kill1_tail.RULE_VERSION
FLASH_PRED = {'positions': {'q': '2', 'b': '3'}, 'counted': ['q', 'b'],
              'tail': {'key': 'tail_qsg', 'label': '千十个', 'kill': '9'},
              'reason': 'mock'}
PRO_PRED = dict(FLASH_PRED)


def flush_ledger():
    return kill1_ab._read_records()


failures = []


def check(name, condition, detail=''):
    if condition:
        print('PASS %s %s' % (name, detail))
    else:
        failures.append(name)
        print('FAIL %s %s' % (name, detail))


flash_meta = {'requested_model': 'deepseek-flash', 'returned_model': 'deepseek-flash',
              'attempt': 1, 'elapsed_seconds': 21.5,
              'usage': {'prompt_tokens': 1200, 'completion_tokens': 60,
                        'reasoning_tokens': 10}}
kill1_ab.record_flash_raw(FLASH_PRED, flash_meta, '20261106070', '20261106071',
                          'PROMPT-A', log=lambda message: None)
check('flash_recorded', len(flush_ledger()) == 1, 'records=%d' % len(flush_ledger()))
kill1_ab.record_flash_raw(FLASH_PRED, flash_meta, '20261106070', '20261106071',
                          'PROMPT-A', log=lambda message: None)
check('flash_idempotent', len(flush_ledger()) == 1, 'records=%d' % len(flush_ledger()))

os.environ['PENTA5_KILL1_AB_DRY'] = '1'
payload = kill1_ab.spawn_pro_shadow('PROMPT-A', '20261106070', '20261106071',
                                    log=lambda message: None)
marker = tmp / 'ab_spawned' / '20261106070.json'
check('dry_spawn_marker', payload is not None and marker.exists(),
      'payload=%s' % bool(payload))
again = kill1_ab.spawn_pro_shadow('PROMPT-A', '20261106070', '20261106071',
                                  log=lambda message: None)
check('dry_spawn_idempotent', again is None)

prompt_sha = kill1_ab._sha256('PROMPT-A')
pro_record = kill1_ab._base_record('pro', '20261106070', '20261106071', prompt_sha)
pro_record.update({'status': 'ok', 'requested_model': 'deepseek-v4-pro',
                   'returned_model': 'deepseek-v4-pro', 'prediction': PRO_PRED,
                   'meta': {'elapsed_seconds': 55.0,
                            'usage': {'prompt_tokens': 1200, 'completion_tokens': 90,
                                      'reasoning_tokens': 40}},
                   'attempts': 1, 'error': None})
kill1_ab._append(pro_record)

# 第二对：flash 合尾失手、pro 命中，验证配对差异计算
flash2 = {'positions': {'q': '1', 'b': '2'}, 'counted': ['q', 'b'],
          'tail': {'key': 'tail_qsg', 'label': '千十个', 'kill': '3'}, 'reason': 'mock2'}
kill1_ab.record_flash_raw(flash2, flash_meta, '20261106072', '20261106073',
                          'PROMPT-B', log=lambda message: None)
pro2 = kill1_ab._base_record('pro', '20261106072', '20261106073', kill1_ab._sha256('PROMPT-B'))
pro2.update({'status': 'ok', 'requested_model': 'deepseek-v4-pro',
             'returned_model': 'deepseek-v4-pro', 'prediction': PRO_PRED,
             'meta': {}, 'attempts': 1, 'error': None})
kill1_ab._append(pro2)

settlements = {'version': 1, 'records': [
    {'version': 1, 'rule_version': RULES, 'ref_period': '20261106070',
     'target_period': '20261106071', 'actual': ['1', '0', '1', '1'],
     'group_hit': True, 'verified': True},
    {'version': 1, 'rule_version': RULES, 'ref_period': '20261106072',
     'target_period': '20261106073', 'actual': ['1', '0', '1', '1'],
     'group_hit': True, 'verified': True},
]}
(tmp / 'settlements.json').write_text(json.dumps(settlements), encoding='utf-8')

env = dict(os.environ)
report_script = ROOT / 'scripts' / 'kill1_ab_report.py'
out = subprocess.run([sys.executable, str(report_script), '--json'],
                     capture_output=True, text=True, env=env)
check('report_exit', out.returncode == 0, out.stderr[-300:])
report = json.loads(out.stdout)
check('pairs', report['counts']['pairs'] == 2, report['counts'])
check('group_delta', abs(report['metrics']['group']['delta_pp'] - 50.0) < 1e-9,
      report['metrics']['group']['delta_pp'])
check('group_counts', report['metrics']['group']['flash']['hits'] == 1
      and report['metrics']['group']['pro']['hits'] == 2,
      report['metrics']['group'])
check('mcnemar', abs(report['metrics']['group']['mcnemar_p'] - 1.0) < 1e-9,
      report['metrics']['group']['mcnemar_p'])
check('discordant', report['metrics']['group']['discordant']['pro_only'] == 1,
      report['metrics']['group']['discordant'])
text = subprocess.run([sys.executable, str(report_script)],
                      capture_output=True, text=True, env=env).stdout
check('text_report', ('配对样本 n=2' in text) and ('+50.00pp' in text),
      text[:200].replace(chr(10), ' | '))

print()
if failures:
    print('MOCK_FAIL', failures)
    sys.exit(1)
print('MOCK_PASS')
