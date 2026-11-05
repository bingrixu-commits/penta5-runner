"""kill1 pro-vs-flash 同周期影子 A/B（只读、幂等、fail-open）。

预注册设计（详见 kill1-ab-experiment-plan.md，仓库本地文档）：
- 主体：现网 kill1 每期仍用 flash 发布；发布前把 flash 的原始预测追加到
  data/kill1_tail/ab_raw.jsonl（arm=flash），并 spawn 一个独立进程用同一 prompt
  再请求一次 pro，只追加 arm=pro 记录。影子不发布、不写封存、不上面板。
- 评估：scripts/kill1_ab_report.py 用 data/kill1_tail/settlements.json 的
  actual 四位开奖号复算位置/合尾/整组命中，同 reference 配对 McNemar 检验。
- 幂等：同一 reference 的 flash 记录只写一次；pro 影子用 marker 文件（O_EXCL）
  保证每期最多 spawn 一次；pro 记录本身也按 reference 去重。
- 失败隔离：所有异常在挂钩函数内部吞掉并打印一行，绝不影响主体发布。
- 开关：PENTA5_KILL1_AB_PRO=0 关闭全部；PENTA5_KILL1_AB_DRY=1 只写 marker 不真调；
  PENTA5_KILL1_AB_SAMPLE 采样率（默认 1.0=全量，按 reference 确定性采样）；
  PENTA5_KILL1_AB_MODEL 影子模型（默认 deepseek-v4-pro）；
  PENTA5_KILL1_AB_LEDGER / PENTA5_KILL1_AB_MARKER_DIR 可改落点（测试用）。
"""
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import random
import re
import subprocess
import sys
import time

ROOT = Path('/root/penta5-runner')
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / 'lib') not in sys.path:
    sys.path.insert(0, str(ROOT / 'lib'))

import kill1_tail  # noqa: E402
from stats_history import beijing_now  # noqa: E402

DEFAULT_MODEL = 'deepseek-v4-pro'
DEFAULT_DEADLINE_SECONDS = 420.0
MIN_REMAINING_SECONDS = 20.0
SAMPLING_NAMESPACE = 'kill1-ab-pro-v1'
PROMPT_ENV = 'PENTA5_KILL1_AB_MODE'


def _env(name, default=''):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip()


def _enabled():
    return _env('PENTA5_KILL1_AB_PRO', '1') != '0'


def _dry_run():
    return _env('PENTA5_KILL1_AB_DRY', '0') == '1'


def _sha256(text):
    return sha256(text.encode('utf-8')).hexdigest()


def ledger_path():
    override = _env('PENTA5_KILL1_AB_LEDGER')
    if override:
        return Path(override)
    return kill1_tail.DATA_DIR / 'ab_raw.jsonl'


def marker_dir():
    override = _env('PENTA5_KILL1_AB_MARKER_DIR')
    if override:
        return Path(override)
    return kill1_tail.DATA_DIR / 'ab_spawned'


def shadow_model():
    value = _env('PENTA5_KILL1_AB_MODEL') or DEFAULT_MODEL
    if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', value):
        return DEFAULT_MODEL
    return value


def deadline_seconds():
    raw = _env('PENTA5_KILL1_AB_DEADLINE', str(int(DEFAULT_DEADLINE_SECONDS)))
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_DEADLINE_SECONDS
    return max(60.0, min(600.0, value))


def sample_rate():
    raw = _env('PENTA5_KILL1_AB_SAMPLE', '1.0')
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    return max(0.0, min(1.0, value))


def sample_gate(reference):
    """按 reference 确定性采样，重启/重跑结果一致。"""
    rate = sample_rate()
    if rate >= 1.0:
        return True
    if rate <= 0.0:
        return False
    rng = random.Random('%s|%s' % (SAMPLING_NAMESPACE, reference))
    return rng.random() < rate


def _read_records(path=None):
    path = Path(path) if path is not None else ledger_path()
    records = []
    try:
        with path.open('r', encoding='utf-8') as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if isinstance(item, dict):
                    records.append(item)
    except FileNotFoundError:
        return []
    return records


def arm_exists(reference, arm, path=None):
    for record in _read_records(path):
        if record.get('reference') == reference and record.get('arm') == arm:
            return True
    return False


def _append(record):
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    with path.open('a', encoding='utf-8') as handle:
        handle.write(line + '\n')
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass


def _base_record(arm, reference, target, prompt_sha):
    return {
        'version': 1,
        'arm': arm,
        'rule_version': kill1_tail.RULE_VERSION,
        'reference': str(reference),
        'target': str(target),
        'prompt_sha256': prompt_sha,
        'recorded_at': beijing_now().isoformat(),
    }


def _marker_path(reference):
    return marker_dir() / ('%s.json' % reference)


def record_flash_raw(prediction, meta, reference, target, prompt, log=print):
    """把主体当期的 flash 原始预测追加到 A/B 台账；重复调用幂等。"""
    if not _enabled():
        return None
    if not isinstance(prediction, dict) or not isinstance(prediction.get('tail'), dict):
        log('AB_SHADOW flash skipped: invalid prediction')
        return None
    reference = str(reference)
    if arm_exists(reference, 'flash'):
        return None
    record = _base_record('flash', reference, target, _sha256(prompt))
    record.update({
        'status': 'ok',
        'requested_model': (meta or {}).get('requested_model'),
        'returned_model': (meta or {}).get('returned_model'),
        'prediction': prediction,
        'meta': meta,
        'attempts': (meta or {}).get('attempt'),
        'error': None,
    })
    _append(record)
    log('AB_SHADOW flash raw recorded ref=%s target=%s' % (reference, target))
    return record


def spawn_pro_shadow(prompt, reference, target, log=print):
    """为同一 prompt spawn 一次 pro 影子请求（独立进程、不等待）。"""
    if not _enabled():
        return None
    reference = str(reference)
    if arm_exists(reference, 'pro'):
        return None
    if not sample_gate(reference):
        log('AB_SHADOW pro skipped by sampling ref=%s' % reference)
        return None
    marker = _marker_path(reference)
    marker.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = marker.open('x', encoding='utf-8')
    except FileExistsError:
        return None
    model = shadow_model()
    payload = {
        'reference': reference,
        'target': str(target),
        'model': model,
        'prompt_sha256': _sha256(prompt),
        'spawned_at': beijing_now().isoformat(),
        'pid': None,
    }
    if _dry_run():
        payload['dry_run'] = True
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        handle.close()
        log('AB_SHADOW pro dry-run marker written ref=%s' % reference)
        return payload
    command = [
        sys.executable,
        str(ROOT / 'scripts' / 'kill1_ab_shadow.py'),
        '--reference', reference,
        '--target', str(target),
        '--prompt-sha', payload['prompt_sha256'],
        '--mode', 'pro',
    ]
    env = dict(os.environ)
    env[PROMPT_ENV] = '1'
    log_path = kill1_tail.DATA_DIR / 'ab_shadow.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with log_path.open('a', encoding='utf-8') as log_file:
            proc = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdin=subprocess.PIPE,
                stdout=log_file,
                stderr=log_file,
                start_new_session=True,
                env=env,
                text=True,
                encoding='utf-8',
            )
            if proc.stdin is not None:
                proc.stdin.write(prompt)
                proc.stdin.close()
        payload['pid'] = proc.pid
        with handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        log('AB_SHADOW pro spawned ref=%s pid=%s model=%s' % (reference, proc.pid, model))
        return payload
    except Exception as error:
        try:
            handle.close()
        except Exception:
            pass
        try:
            marker.unlink()
        except OSError:
            pass
        log('AB_SHADOW pro spawn failed ref=%s %s: %s'
            % (reference, type(error).__name__, str(error)[:120]))
        return None


def _post_pro(messages, timeout, model, api_key):
    import requests
    payload = {
        'model': model,
        'messages': messages,
        'response_format': {'type': 'json_object'},
        'thinking': {'type': 'enabled'},
        'reasoning_effort': kill1_tail.REASONING_EFFORT,
        'max_tokens': kill1_tail.MAX_TOKENS,
    }
    response = requests.post(
        'https://api.deepseek.com/chat/completions',
        json=payload,
        headers={'Authorization': 'Bearer %s' % api_key,
                 'Content-Type': 'application/json'},
        timeout=(10, timeout),
    )
    try:
        if response.status_code != 200:
            raise kill1_tail.ProviderHTTPError('provider-http-%d' % response.status_code,
                                               response.status_code)
        return response.json()
    finally:
        close = getattr(response, 'close', None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


def run_pro_shadow(reference, target, prompt, log=print):
    if not _enabled():
        log('AB_SHADOW pro disabled ref=%s' % reference)
        return 0
    if arm_exists(reference, 'pro'):
        log('AB_SHADOW pro already recorded ref=%s' % reference)
        return 0
    model = shadow_model()
    api_key = kill1_tail.resolve_api_key()
    deadline = beijing_now() + timedelta(seconds=deadline_seconds())
    prompt_sha = _sha256(prompt)
    messages = [{'role': 'user', 'content': prompt}]
    last_error = 'no-attempt'
    last_status = None
    attempts = 0
    for attempt in (1, 2, 3):
        attempts = attempt
        remaining = (deadline - beijing_now()).total_seconds()
        if remaining < MIN_REMAINING_SECONDS:
            last_error = 'deadline-exhausted'
            log('AB_SHADOW pro deadline exhausted ref=%s' % reference)
            break
        timeout = max(10.0, min(90.0, remaining - 10.0))
        started = time.monotonic()
        try:
            envelope = _post_pro(messages, timeout, model, api_key)
            choices = envelope.get('choices')
            if not isinstance(choices, list) or not choices:
                raise ValueError('missing-choices')
            choice = choices[0] if isinstance(choices[0], dict) else {}
            if choice.get('finish_reason') != 'stop':
                raise ValueError('unfinished-response')
            message = choice.get('message') if isinstance(choice.get('message'), dict) else {}
            prediction = kill1_tail.normalize_response(
                kill1_tail._extract_json(message.get('content')))
            meta = kill1_tail._metadata(reference, attempt, model, api_key, envelope,
                                        time.monotonic() - started)
            record = _base_record('pro', reference, target, prompt_sha)
            record.update({
                'status': 'ok',
                'requested_model': model,
                'returned_model': meta.get('returned_model'),
                'prediction': prediction,
                'meta': meta,
                'attempts': attempt,
                'error': None,
            })
            _append(record)
            log('AB_SHADOW pro ok ref=%s target=%s attempt=%d elapsed=%.1fs'
                % (reference, target, attempt, meta.get('elapsed_seconds') or 0.0))
            return 0
        except Exception as error:
            last_error = type(error).__name__
            status = getattr(error, 'status', None)
            last_status = status if type(status) is int else None
            detail = ' '.join(str(error).split())[:120]
            log('AB_SHADOW pro attempt %d/3 rejected ref=%s type=%s%s detail=%s'
                % (attempt, reference, last_error,
                   '' if last_status is None else ' http=%s' % last_status,
                   detail))
            if last_status in kill1_tail.NONRETRYABLE_HTTP_STATUSES:
                break
            if attempt == 3:
                break
            detail = str(error)[:100]
            messages = [
                {'role': 'user', 'content': prompt},
                {'role': 'user', 'content': kill1_tail.RETRY_INSTRUCTION.format(error=detail)},
            ]
            pause = max(0.0, min(4.0,
                                 (deadline - beijing_now()).total_seconds() - 25.0))
            if pause:
                time.sleep(pause)
    record = _base_record('pro', reference, target, prompt_sha)
    record.update({
        'status': 'fail',
        'requested_model': model,
        'returned_model': None,
        'prediction': None,
        'meta': None,
        'attempts': attempts,
        'error': {'type': last_error, 'http_status': last_status},
    })
    _append(record)
    log('AB_SHADOW pro fail ref=%s type=%s attempts=%d'
        % (reference, last_error, attempts))
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description='kill1 pro 影子请求（内部使用）')
    parser.add_argument('--reference', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--prompt-sha', default='')
    parser.add_argument('--mode', default='')
    args = parser.parse_args(argv)
    if _env(PROMPT_ENV) != '1' or args.mode != 'pro':
        print('AB_SHADOW wrong invocation mode')
        return 2
    if not re.fullmatch(r'[0-9]{11}', args.reference or ''):
        print('AB_SHADOW bad reference')
        return 2
    prompt = sys.stdin.read()
    if not prompt.strip():
        print('AB_SHADOW empty prompt')
        return 2
    prompt_sha = _sha256(prompt)
    if args.prompt_sha and args.prompt_sha != prompt_sha:
        print('AB_SHADOW prompt sha mismatch')
        return 2
    return run_pro_shadow(args.reference, args.target, prompt, log=print)


if __name__ == '__main__':
    sys.exit(main())