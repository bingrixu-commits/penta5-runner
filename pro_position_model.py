"""Prospective Pro single exclusions for four positions; independent evidence."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json
import os
import re
import time

from ai_prediction_guard import (PublicationRejected, find_seal, publication_rejection,
                                 publish_saved, read_stats, store_prepared)
from ai_operation_window import ERROR_CODES as WINDOW_ERROR_CODES, OperationWindow
from ai_verified_stats import atomic_json, next_period
from killer_ai_client import KillerResponseError, request_killer_response
from ai_strategy_validation import (pro4_validator, validation_report,
                                    PRO4_RETRY_ADDITION)
from position_input_features import build_position_features
from strategy_features import build_strategy, PRO4_STRATEGY, LEGACY_PRO4_STRATEGY
from scripts.run_ai_ready_job import expected_reference
from stat50_compare import history_from_snapshot, validate_history
from stats_history import beijing_now, load_snapshot, parse_period, parse_time, period_slot

ROOT = Path('/root/penta5-runner')
LEGACY_REVISION = 'pro-four-v1'
FEATURES_REVISION = 'pro-four-v2-position-features'
WEIGHTED_REVISION = 'pro-four-v3-recent-weighted'
REVISION = 'pro-four-v4-isolated-weighted'
SUPPORTED_REVISIONS = (LEGACY_REVISION, FEATURES_REVISION, WEIGHTED_REVISION, REVISION)
MODEL = 'deepseek-v4-pro'
MARKER = 'R1_PRO_COMPARE'
POSITION_KEYS = ('k1', 'k2', 'k3', 'k4')
PRO_FAMILY = re.compile(r'deepseek-(?:v4(?:\.1)?-)?pro(?:-[0-9]{4})?')
RETRY_INSTRUCTION = (
    '上次响应未通过校验。只返回完整JSON对象，恰好含k1、k2、k3、k4、reason五个字段。'
    'k1至k4各为恰好1个0至9整数的数组；不得使用布尔值、小数、字符串、缺失或默认补位。'
    'reason为简短字符串，不得输出合尾、形态、双排号码或其他字段。')


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _aware(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parse_time(value) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _log(log, message):
    try:
        log(message)
    except PublicationRejected:
        raise
    except Exception:
        pass


def validate_response(payload):
    if not isinstance(payload, dict) or set(payload) != {*POSITION_KEYS, 'reason'}:
        raise ValueError('pro-four-response-fields')
    result = {}
    for key in POSITION_KEYS:
        values = payload[key]
        if (not isinstance(values, list) or len(values) != 1
                or type(values[0]) is not int or not 0 <= values[0] <= 9):
            raise ValueError('pro-four-requires-one-integer-per-position')
        result[key] = list(values)
    reason = payload['reason']
    if not isinstance(reason, str) or len(reason) > 200:
        raise ValueError('invalid-pro-four-reason')
    try:
        reason.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('invalid-pro-four-reason') from None
    result['reason'] = reason
    return result


def _input_doc(reference, history, *, revision=REVISION):
    if revision not in SUPPORTED_REVISIONS:
        raise ValueError('unsupported-pro-four-input-revision')
    codes = [''.join(map(str, row['numbers'])) for row in history]
    prompt = (
        f'Pro四位置独立单排；参考期{reference}，只预测下一期{next_period(reference)}。\n'
        + ', '.join(codes) + '\n'
        '以上恰好50期真实开奖，从旧到新，每条仅前四球，依次为千、百、十、个；前导0有效。'
        '只分析这50期，对下期千百十个四个位置各选择一个独立排除数字。'
        '冷热、遗漏和相邻模式只描述历史，不能保证随机开奖命中，不能声称稳定优势。'
        '输出完整JSON，恰好五个字段：k1、k2、k3、k4均为只含一个0至9整数的数组，'
        '分别代表千百十个位排除码，reason为60字以内简短说明。'
        '各位置允许选择相同数字。不得输出合尾、形态、双排、默认补位或其他字段。'
    )
    result = {'version': 1, 'revision': revision, 'reference_period': reference,
              'target_period': next_period(reference), 'history': deepcopy(history), 'prompt': prompt}
    if revision == FEATURES_REVISION:
        # Feature values depend only on the already sealed 50 draws. Using the
        # last actual opening time keeps saved input reconstruction independent
        # of today's clock and preserves legacy v1 input documents verbatim.
        features = build_position_features(history, reference, parse_time(history[-1]['opentime']))
        result['position_features'] = features
        result['prompt'] += (
            '\n【程序统一计算的同一50期四位置统计】\n'
            '以下JSON的q/b/s/g依次为千/百/十/个。counts和omissions的数组下标均为数字0至9；'
            'counts为这50期出现次数，sample_size为有效样本数。omissions为截至参考期连续未出的期数，'
            '本期出现为0；omission_censored=true表示该数字在这50期未出现，遗漏只能确认至少50期，'
            '不能当成精确50期或推断回补概率。以上统计只描述同一50期历史，不增加历史输入或改变输出字段。\n'
            + json.dumps(features, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
    elif revision in (WEIGHTED_REVISION, REVISION):
        strategy_id = LEGACY_PRO4_STRATEGY if revision == WEIGHTED_REVISION else PRO4_STRATEGY
        strategy = build_strategy(history, reference, strategy_id,
                                  now=parse_time(history[-1]['opentime']))
        result['strategy'] = strategy
        result['prompt'] = (
            f'Pro四位置独立单排；参考期{reference}，只预测下一期{next_period(reference)}。\n'
            + ', '.join(codes) + '\n'
            '以上恰好50期真实开奖，从旧到新，每条仅前四球，依次为千、百、十、个；前导0有效。'
            '本任务使用独立的近期半衰期加权思路：半衰期固定为10期，重点分析最近10期与此前40期的变化，'
            '依据下方程序预先计算的同一50期策略特征，自主为千、百、十、个各选择1个排除数字。'
            '不读取其它模型结果，不模仿其它模型，不为与其它模型异号而强行换码；各位置允许排除相同数字。'
            '不要将遗漏回补、冷号延续、邻码或固定数字偏好作为必然规律；统计只描述历史，不能保证随机开奖命中或稳定优势。'
            '输出完整JSON，恰好五个字段：k1、k2、k3、k4均为只含一个0至9整数的数组，'
            '分别代表千百十个位排除码，reason为60字以内简短说明，说明本期采用的近期加权依据。'
            '不得输出合尾、形态、双排、默认补位或其他字段。\n'
            '【本任务独立策略及可复核特征】\n'
            + json.dumps(strategy, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
        if revision == REVISION:
            result['prompt'] = (
                f'Pro四位置独立单排；参考期{reference}，只预测下一期{next_period(reference)}。\n'
                + ', '.join(codes) + '\n'
                '以上恰好50期连续真实开奖，从旧到新，每条仅含前四球，依次为千、百、十、个；前导0有效。'
                '本任务只采用固定近期指数加权方法：半衰期为10期，最新一期权重为1，'
                '每向前一期按2^(-间隔/10)衰减，不调整半衰期。'
                '依据下方同一50期预先计算的weighted_counts，千、百、十、个分别自主选择加权计数最小的1个排除数字。'
                '同分允许任意合法同分数字；近10期与此前40期计数仅作描述，不能参与选择或破同分。'
                '不读取或复用其他模型当前结果，不叠加其他决策方法；各位置允许排除相同数字，不得为了异号强行换码。'
                '加权统计只描述历史，不能证明下一期概率优势，也不能编造历史战绩或保证未来命中。'
                '最终仅输出完整严格JSON对象，恰好k1、k2、k3、k4、reason五个字段。'
                'k1至k4分别代表千百十个位排除码，均为恰好含一个0至9整数的数组；'
                'reason用60字以内简短中文说明本次加权依据，不输出分析过程。'
                '不得输出合尾、形态、双排、默认补位或其他字段。\n'
                '【本任务唯一策略及可复核特征】\n'
                + json.dumps(strategy, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False))
    return result


def _body(seal):
    return {key: deepcopy(seal.get(key)) for key in
            ('revision', 'ref_period', 'history', 'history_sha256',
             'input_doc', 'input_sha256', 'response', 'model_metadata')}


def _valid_metadata(metadata, reference):
    return (isinstance(metadata, dict) and metadata.get('ref') == reference
            and metadata.get('requested_model') == MODEL
            and type(metadata.get('attempt')) is int and 1 <= metadata['attempt'] <= 3
            and metadata.get('finish_reason') == 'stop'
            and isinstance(metadata.get('returned_model'), str)
            and PRO_FAMILY.fullmatch(metadata['returned_model']) is not None)


def validate_publication(seal, reference, now, *, published=True):
    """Self-contained immutable evidence; independent of rolling source files."""
    from pro_position_view import card_fragment
    if (not isinstance(now, datetime) or now.tzinfo is None
            or not isinstance(reference, str) or not parse_period(reference)
            or reference != parse_period(reference)[0]
            or not isinstance(seal, dict) or seal.get('revision') not in SUPPORTED_REVISIONS
            or seal.get('ref_period') != reference
            or seal.get('target_period') != next_period(reference)
            or type(seal.get('evidence_version')) is not int or seal['evidence_version'] != 2
            or seal.get('seal_sha256') != _digest(_body(seal))):
        raise ValueError('invalid-pro-four-seal')
    generated, opened = _aware(seal.get('generated_at')), _aware(seal.get('reference_open_time'))
    cutoff, reference_slot = period_slot(next_period(reference)), period_slot(reference)
    if (generated is None or opened is None or not reference_slot <= opened < reference_slot + timedelta(minutes=1)
            or not opened <= generated < cutoff or generated > now):
        raise ValueError('invalid-pro-four-generation-evidence')
    history = validate_history(seal.get('history'), reference, generated)
    if (seal.get('history') != history or seal.get('history_sha256') != _digest(history)
            or seal.get('reference_open_time') != history[-1]['opentime']):
        raise ValueError('invalid-pro-four-history')
    original_input = _input_doc(reference, history, revision=seal['revision'])
    if seal.get('input_doc') != original_input or seal.get('input_sha256') != _digest(original_input):
        raise ValueError('invalid-pro-four-input')
    response = validate_response(seal.get('response'))
    if seal.get('response') != response:
        raise ValueError('invalid-pro-four-response')
    metadata = seal.get('model_metadata')
    if not _valid_metadata(metadata, reference):
        raise ValueError('unverified-pro-four-model')
    if seal.get('card_html') != card_fragment(reference, response, generated):
        raise ValueError('invalid-pro-four-card')
    state = seal.get('publication_state')
    if published or state == 'published':
        published_at = _aware(seal.get('published_at'))
        if (state != 'published' or published_at is None
                or not generated <= published_at < cutoff or published_at > now):
            raise ValueError('invalid-pro-four-publication-evidence')
    elif state != 'prepared':
        raise ValueError('unpublished-pro-four-state')
    return seal


def _loader(root):
    return lambda **kw: load_snapshot(state_path=Path(root) / 'data/state.json',
                                      cache_path=Path(root) / 'data/stats_draw_history.json', **kw)


def _source(reference, snapshot, now):
    if expected_reference(now) != reference:
        raise PublicationRejected('pro-four-reference-advanced')
    rejection = publication_rejection(reference, snapshot, now)
    if rejection:
        raise PublicationRejected(rejection)
    return history_from_snapshot(snapshot, reference, now)


def _checked_loader(reference, history, loader, clock):
    def checked(**kwargs):
        snapshot = loader(**kwargs)
        try:
            if _source(reference, snapshot, clock()) != history:
                raise ValueError('pro-four-history-changed')
        except (ValueError, TypeError, KeyError, OSError, OverflowError) as error:
            raise PublicationRejected('pro-four-source-evidence-changed') from error
        return snapshot
    return checked


def _status(root, status, reference, clock, error_code=None):
    atomic_json(Path(root) / 'data/pro_position_status.json',
        {'version': 1, 'revision': REVISION, 'status': status, 'reference_period': reference,
         'target_period': next_period(reference), 'updated_at': clock().isoformat(),
         'requested_model': MODEL, 'error_code': error_code})


def _writer(root, writer):
    if writer is not None:
        return writer
    from pro_position_view import compose_pro_transform
    from panel_io import update_panel
    return lambda transform: update_panel(lambda html: compose_pro_transform(html, transform),
                                          path=Path(root) / 'panel.html')


def run_once(*, root=ROOT, post=None, api_key=None, loader=None, clock=beijing_now,
             monotonic=time.monotonic, sleep=time.sleep, writer=None, log=print):
    from ai_call_control import AICallsDisabled,require_ai_enabled
    from pro_position_view import card_fragment
    root, loader = Path(root), loader or _loader(root)
    try:require_ai_enabled(root=root,clock=clock)
    except AICallsDisabled:return {'status':'paused','published':False,'ref_period':None}
    reference = expected_reference(clock())
    operation_window = OperationWindow(reference, clock)
    path = root / 'data/pro_position_seals.json'
    ledger = read_stats(path)
    if (ledger.get('version', 1) != 1
            or ledger.get('revision', REVISION) not in SUPPORTED_REVISIONS):
        raise ValueError('invalid-pro-four-ledger')
    ledger.update(version=1, revision=REVISION)
    attempts, seals = ledger.setdefault('request_attempts', {}), ledger.setdefault('prediction_seals', {})
    if not isinstance(attempts, dict) or not isinstance(seals, dict):
        raise ValueError('invalid-pro-four-index')
    owned_attempt=False;response_received=False
    try:
        existing = find_seal(ledger, reference)
        if existing is not None:
            validate_publication(existing, reference, clock(), published=False)
            history = validate_history(existing['history'], reference, clock())
            published = publish_saved(ledger, path, reference, MARKER,
                _checked_loader(reference, history, loader, clock), writer=_writer(root, writer), clock=clock,
                operation_window=operation_window)
            _status(root, 'preserved' if published else 'rejected', reference, clock)
            return {'status': 'preserved' if published else 'rejected', 'published': published, 'ref_period': reference}
        if reference in attempts:
            raise PublicationRejected('pro-four-already-attempted')
        old = read_stats(root / 'data/pro_compare_seals.json')
        old_attempts = old.get('request_attempts', {})
        if not isinstance(old_attempts, dict):
            raise ValueError('invalid-legacy-pro-attempts')
        if reference in old_attempts or find_seal(old, reference) is not None:
            raise PublicationRejected('legacy-pro-already-attempted')
        _status(root, 'waiting', reference, clock)
        history = _source(reference, loader(persist=False), clock())
        input_doc = _input_doc(reference, history)
        checked_loader = _checked_loader(reference, history, loader, clock)
        checked_loader(persist=False)
        request_budget = operation_window.request_budget()
        if api_key is None:
            from app_secrets import get_secret
            api_key = os.getenv('DEEPSEEK_API_KEY') or get_secret('PENTA5_FRAMEWORK_API_KEY')
            if api_key.startswith('sk-sk-'):
                api_key = api_key[3:]
        if post is None:
            import requests
            post = requests.post
        require_ai_enabled(root=root,clock=clock)
        attempts[reference] = {'started_at': clock().isoformat(), 'revision': REVISION,
                               'input_doc': input_doc, 'input_sha256': _digest(input_doc)}
        atomic_json(path, ledger)  # Durable tombstone before any paid attempt.
        owned_attempt=True
        _status(root, 'requesting', reference, clock)
        metadata, validated_attempts = {}, []
        def capture(message):
            try:
                if isinstance(message, str) and message.startswith('AI_RESPONSE_META '):
                    candidate = json.loads(message[len('AI_RESPONSE_META '):])
                    if (isinstance(candidate, dict) and candidate.get('ref') == reference
                            and type(candidate.get('attempt')) is int and 1 <= candidate['attempt'] <= 3):
                        metadata[candidate['attempt']] = candidate
                elif isinstance(message, str):
                    accepted = re.fullmatch(r'Killer response validated; ref=([0-9]{11}); attempt=([1-3]); publication pending\.', message)
                    if accepted and accepted.group(1) == reference:
                        validated_attempts.append(int(accepted.group(2)))
            except PublicationRejected:
                raise
            except (ValueError, TypeError, KeyError):
                pass
            _log(log, message)
        response_validator = pro4_validator(input_doc.get('strategy'), validate_response, revision=REVISION)
        response, generated = request_killer_response(input_doc['prompt'], reference,
            post=post, api_key=api_key, model=MODEL, load_snapshot=checked_loader, clock=clock,
            log=capture, monotonic=monotonic, sleep=sleep,
            validator=response_validator, retry_instruction=RETRY_INSTRUCTION + PRO4_RETRY_ADDITION,
            request_budget_seconds=request_budget, operation_window=operation_window)
        model_metadata = metadata.get(validated_attempts[-1], {}) if validated_attempts else {}
        if not _valid_metadata(model_metadata, reference):
            attempts[reference]['model_metadata'] = model_metadata
            attempts[reference]['result'] = 'returned-model-not-pro'
            atomic_json(path, ledger)
            raise PublicationRejected('returned-model-not-pro')
        response_received=True
        report = validation_report(response_validator)
        if report:
            model_metadata = dict(model_metadata, strategy_validation=report)
        require_ai_enabled(root=root,clock=clock)
        candidate = {'revision': REVISION, 'ref_period': reference, 'history': history,
                     'history_sha256': _digest(history), 'input_doc': input_doc,
                     'input_sha256': _digest(input_doc), 'response': response, 'model_metadata': model_metadata}
        operation_window.require_open()
        candidate['seal_sha256'] = _digest(_body(candidate))
        prepared = store_prepared(ledger, path, checked_loader(persist=False), candidate,
            card_fragment(reference, response, generated), generated_at=generated, enqueue=False)
        validate_publication(prepared, reference, clock(), published=False)
        published = publish_saved(ledger, path, reference, MARKER, checked_loader,
                                  writer=_writer(root, writer), clock=clock,
                operation_window=operation_window)
        _status(root, 'published' if published else 'rejected', reference, clock)
        _log(log, f'PRO_FOUR ref={reference} target={next_period(reference)} published={published}')
        return {'status': 'published' if published else 'rejected', 'published': published, 'ref_period': reference}
    except AICallsDisabled as error:
        if owned_attempt and getattr(error,'requests_sent',1)==0 and not response_received and reference not in seals:
            attempts.pop(reference,None);atomic_json(path,ledger)
        _status(root,'paused',reference,clock,'manual-api-stopped')
        return {'status':'paused','published':False,'ref_period':reference}
    except Exception as error:
        code = (str(error) if isinstance(error, PublicationRejected) and str(error) in WINDOW_ERROR_CODES else
                'provider-insufficient-balance' if isinstance(error, KillerResponseError)
                and str(error) == 'Nonretryable killer provider HTTP 402' else
                'publication-rejected' if isinstance(error, PublicationRejected) else 'inference-or-evidence-error')
        _status(root, 'rejected' if isinstance(error, PublicationRejected) else 'failed', reference, clock, code)
        raise


def select_current(root, snapshot):
    reference, now = (snapshot.get('latest') or {}).get('period'), snapshot.get('now') or beijing_now()
    try:
        history = _source(reference, snapshot, now)
        seal = find_seal(read_stats(Path(root) / 'data/pro_position_seals.json'), reference)
        validate_publication(seal, reference, now)
        return seal if seal['history'] == history else None
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        return None


def main():
    import fcntl
    with (ROOT / 'data/pro_compare_job.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        try:
            result = run_once()
            code = 0 if result['published'] or result.get('status')=='paused' else 1
        except Exception as error:
            _log(print, 'Pro four-position not published; error_type=' + type(error).__name__)
            code = 1
        try:
            from pro_position_view import refresh_pro_panel
            refresh_pro_panel(root=ROOT)
        except Exception as error:
            _log(print, 'Pro four-position display deferred; error_type=' + type(error).__name__)
        return code


if __name__ == '__main__':
    raise SystemExit(main())
