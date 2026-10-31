#!/usr/bin/env python3
"""kill2 v3『五项目径』只读影子对照：同一批封存号码，只按 5 项判定整组命中。

现网 kill2_tail v2 整组 7 项 = 2 个计入位置 + 合尾 + 二数合 + 三数合 + 2 反向形态
（登记精确基线 57.26%）。候选 v3『五项目径』不新增 AI 请求、不改封存号码，只把整组
改为 2 个计入位置 + 合尾 + 2 反向形态（二数合/三数合仅展示、不计入；机械基线
61.91%，与 kill3 v4/v5 的 5 项口径同构）。

本脚本只读 data/kill2_tail/{seals,settlements}.json，只写 data/kill2_v35_shadow.json：
- 逐条 verified 结算按封存字段重算 5 项命中（sealed5_hit），并取结算里的 7 项 group_hit；
- backfill = forward 起点之前的全部已结算期；forward = 起点之后的新增结算。起点在首次
  运行时写入 JSON 并持久化，之后只累加、不重复计入（按 target_period 去重）；
- 不修改封存/结算/统计/面板，不调用模型；逐条异常只记录到 errors 后跳过；
- 门禁只给状态（burn-in/ready/not-ready），绝不自动改现网口径。

server cron only，5 分钟节拍（cron 建议行由 ops 另行安装，本脚本不自行安装）。
"""
import argparse
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path('/root/penta5-runner')
DATA_REL = Path('data') / 'kill2_tail'
OUT_REL = Path('data') / 'kill2_v35_shadow.json'
TZ = ZoneInfo('Asia/Shanghai')
VERSION = 1
POLICY = 'kill2-tail-v3-five-item-2pos-tail-2pattern'
BASELINE_5 = [5, 0.6191]
BASELINE_7 = [7, 0.5726]
GATE_MIN_N = 100
COUNTED_POSITIONS = 2
POSITION_KEYS = ('q', 'b', 's', 'g')
PATTERN_FIELDS = ('reverse_parity', 'reverse_size')


def beijing_now():
    return datetime.now(TZ)


def parse_period(value):
    """11 位期号 YYYYMMDDNNN（NNN 1..288），非法返回 None。"""
    text = str(value or '').strip()
    if len(text) != 11 or not text.isdigit():
        return None
    try:
        day = datetime.strptime(text[:8], '%Y%m%d')
    except ValueError:
        return None
    index = int(text[8:])
    if not 1 <= index <= 288:
        return None
    return day, index


def next_period(value):
    parsed = parse_period(value)
    if not parsed:
        return None
    day, index = parsed
    if index < 288:
        return day.strftime('%Y%m%d') + '%03d' % (index + 1)
    return (day + timedelta(days=1)).strftime('%Y%m%d') + '001'


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def records(document):
    rows = document.get('records') if isinstance(document, dict) else None
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _hit(entry):
    """取结算子项里的 bool hit；字段缺失/非 bool 返回 None。"""
    if not isinstance(entry, dict):
        return None
    value = entry.get('hit')
    return value if isinstance(value, bool) else None


def evaluate(settlement, seal_rule):
    """按封存结果重算单期 5 项命中并读取 7 项 group_hit。

    返回 (detail, reason)：不可评估时 detail=None、reason 为原因字符串。
    """
    if settlement.get('verified') is not True:
        return None, 'unverified'
    target = str(settlement.get('target_period') or '')
    if parse_period(target) is None:
        return None, 'invalid-target-period'
    positions = settlement.get('positions')
    if not isinstance(positions, dict) or not positions:
        return None, 'positions-missing'
    display_only = settlement.get('display_only')
    counted = settlement.get('counted')
    if not isinstance(counted, list) or not counted:
        counted = [key for key in positions if key != display_only]
    counted = [key for key in counted if key in positions]
    if len(counted) != COUNTED_POSITIONS:
        return None, 'counted-positions-not-2'
    position_hits = {}
    for key in counted:
        value = _hit(positions.get(key))
        if value is None:
            return None, 'position-hit-missing:' + key
        position_hits[key] = value
    tail_hit = _hit(settlement.get('tail'))
    if tail_hit is None:
        return None, 'tail-hit-missing'
    patterns = settlement.get('patterns')
    if not isinstance(patterns, dict):
        return None, 'patterns-missing'
    pattern_hits = {}
    for field in PATTERN_FIELDS:
        value = _hit(patterns.get(field))
        if value is None:
            return None, 'pattern-hit-missing:' + field
        pattern_hits[field] = value
    sealed5 = bool(all(position_hits.values()) and tail_hit
                   and all(pattern_hits.values()))
    he2_hit = _hit(settlement.get('he2_keep'))
    he3_hit = _hit(settlement.get('he3_keep'))
    group7 = settlement.get('group_hit')
    if not isinstance(group7, bool):
        if he2_hit is None or he3_hit is None:
            return None, 'group-hit-missing'
        group7 = bool(sealed5 and he2_hit and he3_hit)
    rule_version = settlement.get('rule_version')
    if not rule_version:
        rule_version = seal_rule.get(str(settlement.get('ref_period') or ''))
    return {
        'target_period': target,
        'ref_period': str(settlement.get('ref_period') or ''),
        'rule_version': rule_version,
        'counted': counted,
        'positions': position_hits,
        'tail_hit': tail_hit,
        'pattern_hits': pattern_hits,
        'he2_hit': he2_hit,
        'he3_hit': he3_hit,
        'sealed5_hit': sealed5,
        'sealed7_hit': bool(group7),
    }, None


def compute_periods(settlements, seals, errors):
    """返回 ({target_period: detail}, skipped_unverified)；逐条异常只记录。"""
    seal_rule = {}
    for seal in seals:
        ref = str(seal.get('ref_period') or '')
        if ref:
            seal_rule.setdefault(ref, seal.get('rule_version'))
    details = {}
    skipped = 0
    for settlement in settlements:
        if settlement.get('verified') is not True:
            skipped += 1
            continue
        target = str(settlement.get('target_period') or '')
        try:
            detail, reason = evaluate(settlement, seal_rule)
        except Exception as error:  # 只记录不抛
            errors.append({'target_period': target,
                           'error': 'evaluate-exception:' + type(error).__name__})
            continue
        if detail is None:
            errors.append({'target_period': target, 'error': reason})
            continue
        target = detail['target_period']
        if target in details:
            errors.append({'target_period': target, 'error': 'duplicate-target-period'})
            continue
        expected7 = None
        if isinstance(detail['he2_hit'], bool) and isinstance(detail['he3_hit'], bool):
            expected7 = bool(detail['sealed5_hit'] and detail['he2_hit'] and detail['he3_hit'])
        if expected7 is not None and detail['sealed7_hit'] != expected7:
            errors.append({'target_period': target, 'error': 'group-hit-inconsistent',
                           'group_hit': detail['sealed7_hit'], 'expected_7item': expected7})
        details[target] = detail
    return details, skipped


def merge_periods(existing_periods, details):
    """持久化 periods 与本次重算结果合并：重算覆盖同 key，按 target_period 去重排序。"""
    merged = {}
    for row in existing_periods if isinstance(existing_periods, list) else []:
        if not isinstance(row, dict):
            continue
        target = str(row.get('target_period') or '')
        if parse_period(target) is None:
            continue
        if not isinstance(row.get('sealed5_hit'), bool) or not isinstance(row.get('sealed7_hit'), bool):
            continue
        merged[target] = {'target_period': target,
                          'sealed5_hit': row['sealed5_hit'],
                          'sealed7_hit': row['sealed7_hit'],
                          'rule_version': row.get('rule_version')}
    for target, detail in details.items():
        merged[target] = {'target_period': target,
                          'sealed5_hit': detail['sealed5_hit'],
                          'sealed7_hit': detail['sealed7_hit'],
                          'rule_version': detail['rule_version']}
    return [merged[target] for target in sorted(merged)]


def summarize(rows):
    n = len(rows)
    hits5 = sum(1 for row in rows if row.get('sealed5_hit') is True)
    hits7 = sum(1 for row in rows if row.get('sealed7_hit') is True)
    rate5 = round(hits5 / n, 6) if n else None
    rate7 = round(hits7 / n, 6) if n else None
    delta_pp = round((hits5 - hits7) / n * 100.0, 3) if n else None
    return {'n': n, 'hits5': hits5, 'rate5': rate5,
            'hits7': hits7, 'rate7': rate7, 'delta_pp': delta_pp}


def gate_status(forward):
    n = int(forward.get('n') or 0)
    hits5 = int(forward.get('hits5') or 0)
    rate5 = forward.get('rate5')
    start = forward.get('start_period')
    pct = ('%.2f%%' % (100.0 * rate5)) if isinstance(rate5, (int, float)) else '-'
    if start is None:
        return {'status': 'burn-in', 'min_n': GATE_MIN_N,
                'note': '尚无已结算样本，forward 起点未建立；出现首个已结算期后自动建立起点并累计。'
                        '现网 7 项口径与封存不动。'}
    if n < GATE_MIN_N:
        return {'status': 'burn-in', 'min_n': GATE_MIN_N,
                'note': ('前瞻起点 %s，n=%d/%d 期，5 项命中 %d 次（%s）。预登记判据：'
                         '前瞻 n>=%d 且前瞻 5 项命中率>=机械基线 61.91%% 才进入转正评审；'
                         '未达标前现网 7 项口径与封存一律不动，脚本不自动切换。'
                         % (start, n, GATE_MIN_N, hits5, pct, GATE_MIN_N))}
    if isinstance(rate5, (int, float)) and rate5 >= BASELINE_5[1]:
        return {'status': 'ready', 'min_n': GATE_MIN_N,
                'note': ('前瞻起点 %s，n=%d 期，5 项命中 %d 次（%s）>= 机械基线 61.91%%。'
                         '仅表示达到转正评审条件，仍需 Lead/用户确认后另行变更现网 revision；'
                         '影子脚本本身不修改任何现网口径与封存。'
                         % (start, n, hits5, pct))}
    return {'status': 'not-ready', 'min_n': GATE_MIN_N,
            'note': ('前瞻起点 %s，n=%d 期，5 项命中 %d 次（%s）< 机械基线 61.91%%，'
                     '差 %.2fpp。建议延长观察或放弃五项目径；现网 7 项口径不动。'
                     % (start, n, hits5, pct, (BASELINE_5[1] - rate5) * 100.0))}


def build(root=ROOT):
    """只读构造影子账本；返回 (document, details, skipped_unverified)。"""
    root = Path(root)
    errors = []
    seals_document = read_json(root / DATA_REL / 'seals.json')
    settlements_document = read_json(root / DATA_REL / 'settlements.json')
    if seals_document is None:
        errors.append({'error': 'seals-unreadable'})
    if settlements_document is None:
        errors.append({'error': 'settlements-unreadable'})
    seals = records(seals_document)
    settlements = records(settlements_document)
    details, skipped = compute_periods(settlements, seals, errors)
    out_path = root / OUT_REL
    existing = read_json(out_path)
    if existing is None and out_path.exists():
        errors.append({'error': 'shadow-json-unreadable'})
    if not isinstance(existing, dict):
        existing = {}
    periods = merge_periods(existing.get('periods'), details)
    # forward 起点只写一次：已持久化则原样沿用，否则取首次运行时的最新已结算期之后。
    start = None
    forward_existing = existing.get('forward')
    if isinstance(forward_existing, dict) and parse_period(forward_existing.get('start_period')):
        start = str(forward_existing['start_period'])
    if start is None and periods:
        start = next_period(periods[-1]['target_period'])
    backfill_rows = []
    forward_rows = []
    for row in periods:
        if start is not None and row['target_period'] >= start:
            forward_rows.append(row)
        else:
            backfill_rows.append(row)
    backfill = summarize(backfill_rows)
    forward_summary = summarize(forward_rows)
    forward = {'start_period': start,
               'n': forward_summary['n'],
               'hits5': forward_summary['hits5'],
               'rate5': forward_summary['rate5'],
               'hits7': forward_summary['hits7'],
               'rate7': forward_summary['rate7']}
    document = {
        'version': VERSION,
        'policy': POLICY,
        'baseline_5': BASELINE_5,
        'baseline_7': BASELINE_7,
        'backfill': backfill,
        'periods': periods,
        'forward': forward,
        'gate': gate_status(forward),
        'checked_at': beijing_now().isoformat(timespec='seconds'),
        'errors': errors,
    }
    return document, details, skipped


def write_atomic(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'))
    handle, temporary = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp',
                                         dir=str(path.parent))
    try:
        with os.fdopen(handle, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(payload + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _failed_items(detail):
    failed = []
    for key in detail['counted']:
        if not detail['positions'][key]:
            failed.append('position:' + key)
    if not detail['tail_hit']:
        failed.append('tail')
    for field in PATTERN_FIELDS:
        if not detail['pattern_hits'][field]:
            failed.append(field)
    return failed


def print_reconcile(document, details):
    """逐期打印 5 项/7 项对账明细（人工核对 / 事后取证用，默认 cron 不打印）。"""
    print('== kill2 v35 shadow reconcile checked_at=%s ==' % document['checked_at'],
          flush=True)
    for row in document['periods']:
        target = row['target_period']
        detail = details.get(target)
        if detail is None:
            print('RECONCILE %s rule=%s sealed5=%s sealed7=%s (persisted-only)'
                  % (target, row.get('rule_version'), row['sealed5_hit'],
                     row['sealed7_hit']), flush=True)
            continue
        positions = ','.join('%s=%s' % (key, 'hit' if detail['positions'][key] else 'MISS')
                             for key in detail['counted'])
        tags = []
        if detail['sealed5_hit'] and (detail['he2_hit'] is False or detail['he3_hit'] is False):
            tags.append('he2/he3未中但5项命中')
        if not detail['sealed5_hit']:
            tags.append('5项未中:' + ','.join(_failed_items(detail)))
        print('RECONCILE %s rule=%s counted=%s pos[%s] tail=%s par=%s siz=%s '
              'he2=%s he3=%s => sealed5=%s sealed7=%s%s'
              % (target, detail['rule_version'], ','.join(detail['counted']), positions,
                 'hit' if detail['tail_hit'] else 'MISS',
                 'hit' if detail['pattern_hits']['reverse_parity'] else 'MISS',
                 'hit' if detail['pattern_hits']['reverse_size'] else 'MISS',
                 detail['he2_hit'], detail['he3_hit'],
                 detail['sealed5_hit'], detail['sealed7_hit'],
                 (' [' + '; '.join(tags) + ']') if tags else ''), flush=True)
    v2 = {'n': 0, 'h5': 0, 'h7': 0}
    for target in sorted(details):
        detail = details[target]
        if 'kill2-tail-v2' in str(detail.get('rule_version') or ''):
            v2['n'] += 1
            v2['h5'] += int(detail['sealed5_hit'])
            v2['h7'] += int(detail['sealed7_hit'])
    backfill = document['backfill']
    forward = document['forward']
    print('RECONCILE backfill n=%d sealed5=%d/%d sealed7=%d/%d delta_pp=%s errors=%d'
          % (backfill['n'], backfill['hits5'], backfill['n'], backfill['hits7'],
             backfill['n'], backfill['delta_pp'], len(document['errors'])), flush=True)
    if v2['n']:
        print('RECONCILE v2_only n=%d sealed5=%d/%d sealed7=%d/%d'
              % (v2['n'], v2['h5'], v2['n'], v2['h7'], v2['n']), flush=True)
    print('RECONCILE forward start=%s n=%d sealed5=%d rate5=%s sealed7=%d rate7=%s'
          % (forward['start_period'], forward['n'], forward['hits5'],
             forward['rate5'], forward['hits7'], forward['rate7']), flush=True)
    for error in document['errors']:
        print('RECONCILE_ERROR ' + json.dumps(error, ensure_ascii=False, sort_keys=True),
              flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='kill2 v3 five-item shadow ledger (read-only, own JSON only)')
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--reconcile', action='store_true',
                        help='print per-period 5-item/7-item reconciliation lines')
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        document, details, skipped = build(root)
    except Exception as error:  # 只记录不抛：全局失败也只打印 + 非零退出
        print('KILL2_V35_SHADOW_FAILURE phase=build error_type=%s error=%s'
              % (type(error).__name__, error), flush=True)
        return 1
    try:
        write_atomic(root / OUT_REL, document)
    except Exception as error:
        print('KILL2_V35_SHADOW_FAILURE phase=write error_type=%s error=%s'
              % (type(error).__name__, error), flush=True)
        return 1
    if args.reconcile:
        print_reconcile(document, details)
    summary = {'status': 'ok',
               'checked_at': document['checked_at'],
               'policy': document['policy'],
               'backfill': document['backfill'],
               'forward': document['forward'],
               'gate': document['gate'],
               'periods': len(document['periods']),
               'skipped_unverified': skipped,
               'errors': document['errors']}
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True,
                     separators=(',', ':')), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
