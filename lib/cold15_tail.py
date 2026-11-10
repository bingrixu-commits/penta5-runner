"""定位冷码（cold15 独立框架，v1，纯确定性、不调用 AI）。

口径（写死，面板脚注同步展示）：
- 四个位置（千/百/十/个）各自独立取近 15 期出现次数最冷的 1 个码（0-9）；
- 冷序 = 窗口内出现次数升序；同次数按当前遗漏降序；再按码升序；
- 每期固定 4 项（每个位置 1 项），全部计入整组；不设沿用/换码（每期严格按窗口重算）；
- 命中 = 该位置实际开奖号 != 该位置排除码（单项随机 90%）；
- 整组命中 = 4 项同时命中；整组随机基线 = (9/10)^4 = 0.6561，
  用 10^4 开奖 x 10^4 配置的 4 位置独立组合精确枚举（等价闭式 6561/10000）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。

数据落点 data/cold15_tail/{seals,settlements}.json；由 scripts/run_cold15_tail.py 每期运行。
"""
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json

from stats_history import beijing_now, business_start, load_snapshot, parse_time, period_slot
from ai_verified_stats import atomic_json, next_period
from ai_prediction_guard import PublicationRejected

ROOT = Path('/root/penta5-runner')
DATA_DIR = ROOT / 'data' / 'cold15_tail'
SEALS_PATH = DATA_DIR / 'seals.json'
SETTLEMENTS_PATH = DATA_DIR / 'settlements.json'
STATE_PATH = ROOT / 'data' / 'state.json'

WINDOW = 15
CLOSE_SECONDS = 70
TOP_N = 1

POSITIONS = (
    ('q', '千', 0),
    ('b', '百', 1),
    ('s', '十', 2),
    ('g', '个', 3),
)
POSITION_KEYS = tuple(key for key, _label, _index in POSITIONS)
ATTACK_ORDER = '1234567890'
ATTACK_POSITION_LABELS = {'q': '千', 'b': '百', 's': '十', 'g': '个'}

RULE_VERSION = 'cold15-tail-v1-four-position-cold'
EXPECTED_ITEMS = 4
# 10^4 开奖空间 x 每位置 10 码配置，精确枚举：4 位置各自 (9/10) 独立 -> 6561/10000。
EXPECTED_GROUP_RATE = 0.6561
EXPECTED_BASIS = 'enumerated-10^4-draws-x-10^4-configs'


def _load_json(path, default, strict=False):
    path = Path(path)
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError:
        return default
    try:
        value = json.loads(raw)
    except ValueError:
        if strict:
            raise ValueError(f'invalid-json:{path.name}')
        return default
    if not isinstance(value, dict):
        if strict:
            raise ValueError(f'invalid-object:{path.name}')
        return default
    return value


def load_seals():
    """Return all sealed predictions in chronological order."""
    document = _load_json(SEALS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('cold15_tail seals records must be a list')
    return [item for item in records if isinstance(item, dict)]


def load_settlements():
    document = _load_json(SETTLEMENTS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('cold15_tail settlements records must be a list')
    return [item for item in records if isinstance(item, dict)]


def _write_seals(records):
    atomic_json(SEALS_PATH, {'version': 1, 'records': records})


def _write_settlements(records):
    atomic_json(SETTLEMENTS_PATH, {'version': 1, 'records': records})


def find_seal(seals, reference):
    reference = str(reference)
    return next((item for item in seals if str(item.get('ref_period')) == reference), None)


def latest_period():
    """Latest drawn period from the cheap state file; empty string when unknown."""
    try:
        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))
        period = str((state.get('latestRecord') or {}).get('period') or '')
        if period:
            return period
    except (OSError, ValueError):
        pass
    try:
        history = json.loads((ROOT / 'data' / 'stats_draw_history.json').read_text(encoding='utf-8'))
        records = history.get('records') or []
        if records:
            return str(records[-1].get('period') or '')
    except (OSError, ValueError):
        pass
    return ''


def _public_item(item):
    return {
        'key': item['key'], 'label': item['label'], 'code': item['code'],
        'count': item['count'], 'omission': item['omission'], 'rank': item['rank'],
    }


def build_candidates(rows, window=WINDOW):
    """Rank the 10 digits of each position over the trailing window, coldest first.

    cold order = (occurrences asc, current omission desc, digit asc).
    """
    rows = [list(row)[:4] for row in rows]
    rows = [row for row in rows if len(row) == 4]
    if len(rows) < window:
        raise ValueError('insufficient-cold15-history')
    window_rows = rows[-window:]
    size = len(window_rows)
    ranked_by_position = {}
    for key, label, index in POSITIONS:
        values = [int(row[index]) for row in window_rows]
        counts = [0] * 10
        last_index = {}
        for position, value in enumerate(values):
            counts[value] += 1
            last_index[value] = position
        if sum(counts) != size:
            raise ValueError('cold15-position-count-mismatch')
        ranked = []
        for code in range(10):
            count = counts[code]
            omission = (size - 1 - last_index[code]) if code in last_index else size
            ranked.append({
                'key': key,
                'label': label,
                'code': str(code),
                'count': count,
                'omission': omission,
            })
        ranked.sort(key=lambda item: (item['count'], -item['omission'], int(item['code'])))
        for rank, item in enumerate(ranked, 1):
            item['rank'] = rank
        ranked_by_position[key] = ranked
    return ranked_by_position


def select_cold_positions(rows, window=WINDOW):
    """Pick the single coldest digit of every position (four items per period)."""
    ranked = build_candidates(rows, window=window)
    positions = []
    for key, label, _index in POSITIONS:
        top = ranked[key][0]
        positions.append({
            'key': key,
            'label': label,
            'kill': top['code'],
            'count': top['count'],
            'omission': top['omission'],
            'rank': top['rank'],
        })
    cold_ranking = []
    for key, _label, _index in POSITIONS:
        for item in ranked[key][:3]:
            cold_ranking.append(_public_item(item))
    reason = '近%d期定位冷码：' % window + '、'.join(
        '%s杀%s（出%d次/遗漏%d期）' % (item['label'], item['kill'], item['count'],
                                     item['omission'])
        for item in positions) + '；四位置各 1 码，整组 4 项。'
    return {
        'positions': positions,
        'cold_ranking': cold_ranking,
        'reason': reason[:200],
        'window': len(rows[-window:]),
    }


def create_seal(reference, target, reference_open_time, prediction, rows, generated_at=None):
    """Freeze the deterministic selection before the target period opens."""
    reference = str(reference)
    target = str(target)
    if not reference or not target:
        raise ValueError('cold15 reference/target period required')
    positions = (prediction or {}).get('positions') if isinstance(prediction, dict) else None
    if not isinstance(positions, list) or len(positions) != len(POSITIONS):
        raise ValueError('cold15 prediction requires four positions')
    if tuple(str(item.get('key')) for item in positions) != POSITION_KEYS:
        raise ValueError('cold15 prediction position order mismatch')
    generated_at = generated_at or beijing_now()
    window_rows = [list(row)[:4] for row in rows][-WINDOW:]
    if len(window_rows) < WINDOW:
        raise ValueError('insufficient-cold15-history')
    payload = json.dumps([[int(value) for value in row] for row in window_rows],
                         ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    seal = {
        'version': 1,
        'rule_version': RULE_VERSION,
        'ref_period': reference,
        'target_period': target,
        'reference_open_time': reference_open_time,
        'generated_at': generated_at.isoformat(),
        'published_at': None,
        'publication_state': 'prepared',
        'positions': positions,
        'position_count': len(positions),
        'cold_ranking': prediction.get('cold_ranking') or [],
        'reason': str(prediction.get('reason') or '')[:200],
        'window': len(window_rows),
        'expected_items': EXPECTED_ITEMS,
        'expected_group_rate': EXPECTED_GROUP_RATE,
        'expected_basis': EXPECTED_BASIS,
        'input_sha256': sha256(payload.encode('utf-8')).hexdigest(),
        'data_source': 'data/stats_draw_history.json:first-four-balls',
    }
    return seal


def save_seal(seal):
    reference = str(seal.get('ref_period') or '')
    if not reference:
        raise ValueError('cold15 seal requires ref_period')
    seals = load_seals()
    if find_seal(seals, reference) is not None:
        raise PublicationRejected('existing-cold15-seal-cannot-be-replaced')
    seals.append(seal)
    _write_seals(seals)


def mark_published(reference, published_at=None):
    reference = str(reference)
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        raise ValueError('cold15-seal-not-found')
    if seal.get('publication_state') == 'published' and seal.get('published_at'):
        return seal
    seal['publication_state'] = 'published'
    seal['published_at'] = (published_at or beijing_now()).isoformat()
    _write_seals(seals)
    return seal


def mark_publication_failed(reference, error):
    reference = str(reference)
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        return
    seal['publication_state'] = 'prepared'
    seal['publication_error'] = str(error)[:120]
    _write_seals(seals)


def settle_pending(snapshot=None, now=None):
    """Settle every sealed target period that has opened; append-only."""
    snapshot = snapshot or load_snapshot(persist=False)
    records = {}
    for record in snapshot.get('records') or []:
        if isinstance(record, dict):
            records[str(record.get('period'))] = record
    seals = load_seals()
    settlements = load_settlements()
    settled = {str(item.get('target_period')) for item in settlements}
    added = []
    for seal in seals:
        target = str(seal.get('target_period') or next_period(seal.get('ref_period')) or '')
        if not target or target in settled:
            continue
        record = records.get(target)
        if record is None:
            continue
        numbers = [str(value) for value in (record.get('numbers') or [])]
        if len(numbers) < 4:
            continue
        actual = [int(numbers[index]) for index in range(4)]
        positions = []
        for item in seal.get('positions') or []:
            if not isinstance(item, dict):
                continue
            key = item.get('key')
            kill = str(item.get('kill') or '')
            if key not in POSITION_KEYS or len(kill) != 1 or not kill.isdigit():
                continue
            index = POSITION_KEYS.index(key)
            actual_code = actual[index]
            positions.append({
                'key': key,
                'label': item.get('label') or ATTACK_POSITION_LABELS[key],
                'kill': kill,
                'actual': str(actual_code),
                'hit': actual_code != int(kill),
            })
        if len(positions) != len(POSITIONS):
            continue
        state = str(seal.get('publication_state') or '')
        published = parse_time(seal.get('published_at'))
        drawn = parse_time(record.get('opentime'))
        verified = bool(state == 'published' and published is not None
                        and drawn is not None and published < drawn)
        settlement = {
            'version': 1,
            'rule_version': seal.get('rule_version') or RULE_VERSION,
            'ref_period': seal.get('ref_period'),
            'target_period': target,
            'published_at': seal.get('published_at'),
            'publication_state': state,
            'drawn_at': record.get('opentime'),
            'settled_at': (now or beijing_now()).isoformat(),
            'actual': [str(value) for value in actual],
            'positions': positions,
            'position_count': len(positions),
            'counted': [item['key'] for item in positions],
            'expected_items': seal.get('expected_items', EXPECTED_ITEMS),
            'expected_group_rate': seal.get('expected_group_rate', EXPECTED_GROUP_RATE),
            'expected_basis': seal.get('expected_basis', EXPECTED_BASIS),
            'group_hit': all(item['hit'] for item in positions),
            'verified': verified,
        }
        settlements.append(settlement)
        settled.add(target)
        added.append(settlement)
    if added:
        _write_settlements(settlements)
    return added


def _business_key(value):
    moment = parse_time(value)
    return business_start(moment) if moment is not None else None


def _streak_stats(rows):
    ordered = sorted((row for row in rows if isinstance(row, dict)),
                     key=lambda row: str(row.get('target_period') or ''))
    win = loss = max_win = max_loss = 0
    for row in ordered:
        if row.get('group_hit'):
            win += 1
            loss = 0
            max_win = max(max_win, win)
        else:
            loss += 1
            win = 0
            max_loss = max(max_loss, loss)
    return {
        'current_win_streak': win,
        'current_loss_streak': loss,
        'max_win_streak': max_win,
        'max_loss_streak': max_loss,
    }


def today_stats(now=None):
    """Verified business-day (05:00) aggregate plus pending/late counts."""
    now = now or beijing_now()
    day = business_start(now)
    settlements = load_settlements()
    verified = [row for row in settlements
                if row.get('verified') is True
                and _business_key(row.get('settled_at')) == day]
    verified_all = [row for row in settlements if row.get('verified') is True]
    todays = [row for row in verified if row.get('rule_version') == RULE_VERSION]
    total = len(todays)
    hits = sum(1 for row in todays if row.get('group_hit') is True)
    item_bets = item_hits = 0
    per_position = {}
    for row in todays:
        for item in row.get('positions') or []:
            if not isinstance(item, dict):
                continue
            key = item.get('key')
            slot = per_position.setdefault(key, {'label': item.get('label'), 'bets': 0,
                                                 'hits': 0, 'rate': None})
            slot['bets'] += 1
            item_bets += 1
            if item.get('hit') is True:
                slot['hits'] += 1
                item_hits += 1
    for slot in per_position.values():
        slot['rate'] = (slot['hits'] / float(slot['bets'])) if slot['bets'] else None
    streak = _streak_stats(todays)
    version_rows = [row for row in verified_all if row.get('rule_version') == RULE_VERSION]
    all_streak = _streak_stats(version_rows)
    latest = latest_period()
    settled = {str(row.get('target_period')) for row in settlements}
    pending = 0
    for seal in load_seals():
        if seal.get('publication_state') != 'published':
            continue
        target = str(seal.get('target_period') or '')
        if target and target > latest and target not in settled:
            pending += 1
    late = sum(1 for row in settlements
               if row.get('verified') is False and _business_key(row.get('settled_at')) == day)
    expected_sum = 0.0
    expected_records = 0
    expected_items = set()
    for row in todays:
        rate = row.get('expected_group_rate')
        if not isinstance(rate, (int, float)) or not 0 < float(rate) < 1:
            rate = EXPECTED_GROUP_RATE
        expected_sum += float(rate)
        expected_records += 1
        try:
            expected_items.add(int(row.get('expected_items') or EXPECTED_ITEMS))
        except (TypeError, ValueError):
            expected_items.add(EXPECTED_ITEMS)
    return {
        'business_start': day.isoformat(),
        'total': total,
        'group_hits': hits,
        'group_rate': (hits / float(total)) if total else None,
        'position': {'bets': item_bets, 'hits': item_hits,
                     'rate': (item_hits / float(item_bets)) if item_bets else None},
        'per_position': per_position,
        'current_win_streak': streak['current_win_streak'],
        'current_loss_streak': streak['current_loss_streak'],
        'max_win_streak': all_streak['max_win_streak'],
        'max_loss_streak': all_streak['max_loss_streak'],
        'pending': pending,
        'late': late,
        'expected': {
            'records': expected_records,
            'group_rate': (expected_sum / expected_records) if expected_records else None,
            'group_expected': round(expected_sum, 2),
            'items_min': min(expected_items) if expected_items else None,
            'items_max': max(expected_items) if expected_items else None,
            'basis': EXPECTED_BASIS,
        },
        'version_total': len(version_rows),
        'version_hits': sum(1 for row in version_rows if row.get('group_hit') is True),
        'latest_period': latest,
    }


def version_info():
    seals = load_seals()
    published = [seal for seal in seals
                 if seal.get('publication_state') == 'published'
                 and seal.get('rule_version') == RULE_VERSION]
    return {
        'rule_version': RULE_VERSION,
        'count': len(published),
        'first_ref': published[0].get('ref_period') if published else None,
        'last_ref': published[-1].get('ref_period') if published else None,
    }


def current_summary(now=None):
    seals = load_seals()
    settlements = load_settlements()
    seal = seals[-1] if seals else None
    settlement = None
    if seal is not None:
        target = str(seal.get('target_period') or '')
        for row in reversed(settlements):
            if str(row.get('target_period') or '') == target:
                settlement = row
                break
    previous = seals[-2] if len(seals) >= 2 else None
    return {
        'latest_period': latest_period(),
        'seal': seal,
        'settlement': settlement,
        'previous': previous,
        'stats': today_stats(now),
    }


def deadline_for(reference):
    target = next_period(reference)
    if not target:
        raise ValueError('cold15 invalid reference period')
    slot = period_slot(target)
    if slot is None:
        raise ValueError('cold15 invalid target period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target


def _remaining(value):
    text = str(value).strip()
    if len(text) != 1 or text not in ATTACK_ORDER:
        raise ValueError('attack kill digit must be 0..9')
    return ''.join(char for char in ATTACK_ORDER if char != text)


def build_attack(seal):
    """四位置各保留 9 码 → 四定（与既有位置框架同格式）。"""
    if not isinstance(seal, dict):
        raise ValueError('attack requires a sealed prediction')
    mapping = {}
    for item in seal.get('positions') or []:
        if isinstance(item, dict):
            mapping[str(item.get('key') or '')] = str(item.get('kill') or '')
    parts = []
    for key, label in (('q', '千'), ('b', '百'), ('s', '十'), ('g', '个')):
        kill = mapping.get(key)
        if kill is None:
            raise ValueError('attack requires all four positions')
        parts.append(label + _remaining(kill))
    parts.append('四定')
    return ''.join(parts)
