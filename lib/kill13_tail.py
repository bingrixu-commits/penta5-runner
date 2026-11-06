"""合尾双冷杀号（kill13 独立框架，v1，纯确定性、不调用 AI）。

口径（写死，面板脚注同步展示）：
- 候选池 = 5 种合尾组合（千百十个 / 千百十 / 千百个 / 千十个 / 百十个）× 尾码 0-9，共 50 个候选；
- 冷序 = 近 50 期出现次数升序；同次数按当前遗漏降序；再按组合固定序（all,qbs,qbg,qsg,bsg）、尾码升序；
- 每期取冷序前 2 个作为排除项（2 注/期，全部计入整组）；
- 稳定化 cold-hold-top2：每期重算 50 期冷榜，上期封存的 2 项只要仍在冷榜前 2 就沿用，
  掉出前 2 的项换成本期冷榜前 2 里缺的项；记录 kept/swapped 轨迹（不设连用上限）；
- 命中 = 该合尾实际和值个位 != 排除码（单项随机 90%）；
- 整组命中 = 2 项同时命中；整组随机基线 = 10^4 开奖 × C(50,2)=1225 配置精确枚举 = 0.8082
  （990/1225 = 0.808163...，不能写成 0.9^2：两项来自同一 50 候选池、互不重复）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。

数据落点 data/kill13_tail/{seals,settlements}.json；由 scripts/run_kill13_tail.py 每期运行。
"""
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import json

from stats_history import beijing_now, business_start, load_snapshot, parse_time, period_slot
from ai_verified_stats import atomic_json, next_period
from ai_prediction_guard import PublicationRejected

ROOT = Path('/root/penta5-runner')
DATA_DIR = ROOT / 'data' / 'kill13_tail'
SEALS_PATH = DATA_DIR / 'seals.json'
SETTLEMENTS_PATH = DATA_DIR / 'settlements.json'
STATE_PATH = ROOT / 'data' / 'state.json'

WINDOW = 50
CLOSE_SECONDS = 70
TOP_N = 2

TAILS = {
    'tail_all': {'label': '千百十个', 'indices': (0, 1, 2, 3)},
    'tail_qbs': {'label': '千百十', 'indices': (0, 1, 2)},
    'tail_qbg': {'label': '千百个', 'indices': (0, 1, 3)},
    'tail_qsg': {'label': '千十个', 'indices': (0, 2, 3)},
    'tail_bsg': {'label': '百十个', 'indices': (1, 2, 3)},
}
TAIL_KEYS = tuple(TAILS)
TAIL_INDEX = {key: index for index, key in enumerate(TAIL_KEYS)}

ATTACK_ORDER = '1234567890'
ATTACK_POSITION_LABELS = {'q': '千', 'b': '百', 's': '十', 'g': '个'}
ATTACK_TAIL_LABELS = {
    'tail_all': '千百十个合', 'tail_qbs': '千百十合', 'tail_qbg': '千百个合',
    'tail_qsg': '千十个合', 'tail_bsg': '百十个合',
}

RULE_VERSION = 'kill13-tail-v1-two-cold-hewei'
HOLD_POLICY = 'cold-hold-top2'
EXPECTED_ITEMS = 2
# 10^4 开奖 x C(50,2)=1225 配置精确枚举：990/1225 = 0.8081632653...，按项目口径登记 4 位。
EXPECTED_GROUP_RATE = 0.8082
EXPECTED_BASIS = 'enumerated-10^4-draws-x-1225-configs'


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
        raise ValueError('kill13_tail seals records must be a list')
    return [item for item in records if isinstance(item, dict)]


def load_settlements():
    document = _load_json(SETTLEMENTS_PATH, {'version': 1, 'records': []})
    records = document.get('records')
    if not isinstance(records, list):
        raise ValueError('kill13_tail settlements records must be a list')
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


def tail_value(row, key):
    """Sum-tail value of one draw row (first four digits only)."""
    return sum(int(row[index]) for index in TAILS[key]['indices']) % 10


def tail_actual(numbers, key):
    return sum(int(numbers[index]) for index in TAILS[key]['indices']) % 10


def _public_item(item):
    return {
        'key': item['key'], 'label': item['label'], 'kill': item['kill'],
        'count': item['count'], 'omission': item['omission'], 'rank': item['rank'],
    }


def build_candidates(rows, window=WINDOW):
    """Rank all 50 sum-tail candidates over the trailing window, coldest first.

    cold order = (occurrences asc, current omission desc, combo order, tail digit).
    """
    rows = [list(row)[:4] for row in rows]
    rows = [row for row in rows if len(row) == 4]
    if len(rows) < window:
        raise ValueError('insufficient-kill13-history')
    window_rows = rows[-window:]
    size = len(window_rows)
    ranked = []
    for key in TAIL_KEYS:
        values = [tail_value(row, key) for row in window_rows]
        counts = [0] * 10
        last_index = {}
        for index, value in enumerate(values):
            counts[value] += 1
            last_index[value] = index
        if sum(counts) != size:
            raise ValueError('kill13-tail-count-mismatch')
        for code in range(10):
            count = counts[code]
            omission = (size - 1 - last_index[code]) if code in last_index else size
            ranked.append({
                'key': key,
                'label': TAILS[key]['label'],
                'kill': str(code),
                'count': count,
                'omission': omission,
            })
    ranked.sort(key=lambda item: (item['count'], -item['omission'],
                                  TAIL_INDEX[item['key']], int(item['kill'])))
    for index, item in enumerate(ranked, 1):
        item['rank'] = index
    return ranked


def _previous_items(previous_tail):
    """Normalize previous tails (list of dicts / one prediction dict) to (key, kill) map."""
    if isinstance(previous_tail, dict):
        values = previous_tail.get('tails')
        if values is None and 'tail' in previous_tail:
            values = [previous_tail.get('tail')]
        previous_tail = values or []
    result = []
    seen = set()
    for item in previous_tail or []:
        if not isinstance(item, dict):
            continue
        key = item.get('key')
        kill = str(item.get('kill') or '')
        if key not in TAILS or len(kill) != 1 or not kill.isdigit():
            continue
        if (key, kill) in seen:
            continue
        seen.add((key, kill))
        result.append({'key': key, 'label': TAILS[key]['label'], 'kill': kill})
    return result


def select_cold_two(rows, previous_tail=None):
    """Pick the two coldest sum-tails, keeping last period's tails while still top-2."""
    ranked = build_candidates(rows)
    top = ranked[:TOP_N]
    selected = [_public_item(item) for item in top]
    top_pairs = {(item['key'], item['kill']) for item in top}
    previous = _previous_items(previous_tail)
    previous_pairs = {(item['key'], item['kill']) for item in previous}
    kept, dropped = [], []
    for item in previous:
        if (item['key'], item['kill']) in top_pairs:
            kept.append({'key': item['key'], 'label': item['label'], 'kill': item['kill'],
                         'count': next((top_item['count'] for top_item in top
                                        if top_item['key'] == item['key'] and top_item['kill'] == item['kill']), None)})
        else:
            dropped.append(item)
    added = []
    for item in top:
        pair = (item['key'], item['kill'])
        if pair not in previous_pairs:
            added.append(_public_item(item))
    swapped = []
    if previous:
        for index in range(max(len(dropped), len(added))):
            source = dropped[index] if index < len(dropped) else None
            target = added[index] if index < len(added) else None
            swapped.append({
                'from': ({'key': source['key'], 'label': source['label'], 'kill': source['kill']}
                         if source else None),
                'to': ({'key': target['key'], 'label': target['label'], 'kill': target['kill']}
                       if target else None),
            })
    if not previous:
        note = '首期无沿用（全量取冷榜前 2）'
    elif not swapped:
        note = '上期 2 项仍在冷榜前 2，原样沿用'
    else:
        note = '沿用 %d 项、换码 %d 项（掉出冷榜前 2 的换成本期前 2 缺项）' % (len(kept), len(added))
    policy = {
        'policy': HOLD_POLICY,
        'hold_max': None,
        'kept': kept,
        'swapped': swapped,
        'kept_count': len(kept),
        'swapped_count': len(added),
        'cold_top': [_public_item(item) for item in top],
        'note': note,
    }
    reason = '近%d期冷榜前2：' % WINDOW + '、'.join(
        '%s合杀%s（出%d次/遗漏%d期）' % (item['label'], item['kill'], item['count'], item['omission'])
        for item in top) + '；' + note + '。'
    return {
        'tails': selected,
        'tail_policy': policy,
        'cold_ranking': [_public_item(item) for item in ranked[:6]],
        'reason': reason[:200],
        'window': len(rows[-WINDOW:]),
    }


def create_seal(reference, target, reference_open_time, prediction, rows, generated_at=None):
    """Freeze the deterministic selection before the target period opens."""
    reference = str(reference)
    target = str(target)
    if not reference or not target:
        raise ValueError('kill13 reference/target period required')
    if not isinstance(prediction, dict) or not prediction.get('tails'):
        raise ValueError('kill13 prediction requires two tails')
    generated_at = generated_at or beijing_now()
    window_rows = [list(row)[:4] for row in rows][-WINDOW:]
    if len(window_rows) < WINDOW:
        raise ValueError('insufficient-kill13-history')
    payload = json.dumps([[int(value) for value in row] for row in window_rows],
                         ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    tails = prediction['tails']
    seal = {
        'version': 1,
        'rule_version': RULE_VERSION,
        'ref_period': reference,
        'target_period': target,
        'reference_open_time': reference_open_time,
        'generated_at': generated_at.isoformat(),
        'published_at': None,
        'publication_state': 'prepared',
        'tails': tails,
        'tail_count': len(tails),
        'tail_policy': prediction.get('tail_policy'),
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
        raise ValueError('kill13 seal requires ref_period')
    seals = load_seals()
    if find_seal(seals, reference) is not None:
        raise PublicationRejected('existing-kill13-seal-cannot-be-replaced')
    seals.append(seal)
    _write_seals(seals)


def mark_published(reference, published_at=None):
    reference = str(reference)
    seals = load_seals()
    seal = find_seal(seals, reference)
    if seal is None:
        raise ValueError('kill13-seal-not-found')
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
        tails = []
        for item in seal.get('tails') or []:
            if not isinstance(item, dict):
                continue
            key = item.get('key')
            kill = str(item.get('kill') or '')
            if key not in TAILS or len(kill) != 1 or not kill.isdigit():
                continue
            actual_tail = tail_actual(actual, key)
            tails.append({
                'key': key,
                'label': TAILS[key]['label'],
                'kill': kill,
                'actual': str(actual_tail),
                'hit': actual_tail != int(kill),
            })
        if not tails:
            continue
        state = str(seal.get('publication_state') or '')
        published = parse_time(seal.get('published_at'))
        drawn = parse_time(record.get('opentime'))
        verified = bool(state == 'published' and published is not None
                        and drawn is not None and published < drawn)
        policy = seal.get('tail_policy') or {}
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
            'tails': tails,
            'tail_count': len(tails),
            'counted': [item['key'] for item in tails],
            'kept_count': policy.get('kept_count'),
            'swapped_count': policy.get('swapped_count'),
            'expected_items': seal.get('expected_items', EXPECTED_ITEMS),
            'expected_group_rate': seal.get('expected_group_rate', EXPECTED_GROUP_RATE),
            'expected_basis': seal.get('expected_basis', EXPECTED_BASIS),
            'group_hit': all(item['hit'] for item in tails),
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
    win = loss = max_win = max_loss = four = 0
    for row in ordered:
        if row.get('group_hit'):
            win += 1
            loss = 0
            max_win = max(max_win, win)
        else:
            loss += 1
            win = 0
            max_loss = max(max_loss, loss)
            if loss % 4 == 0:
                four += 1
    return {
        'current_win_streak': win,
        'current_loss_streak': loss,
        'max_win_streak': max_win,
        'max_loss_streak': max_loss,
        'four_loss_cycles': four,
    }


def today_stats(now=None):
    """Verified business-day (05:00) aggregate plus pending/late counts, mirroring kill12."""
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
    per_key = {}
    for row in todays:
        for item in row.get('tails') or []:
            if not isinstance(item, dict):
                continue
            key = item.get('key')
            slot = per_key.setdefault(key, {'label': item.get('label'), 'bets': 0, 'hits': 0,
                                            'rate': None})
            slot['bets'] += 1
            item_bets += 1
            if item.get('hit') is True:
                slot['hits'] += 1
                item_hits += 1
    for slot in per_key.values():
        slot['rate'] = (slot['hits'] / float(slot['bets'])) if slot['bets'] else None
    kept_total = sum(int(row.get('kept_count') or 0) for row in todays)
    swapped_total = sum(int(row.get('swapped_count') or 0) for row in todays)
    streak = _streak_stats(todays)
    four_loss_total = _streak_stats(
        [row for row in verified_all if row.get('rule_version') == RULE_VERSION])['four_loss_cycles']
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
        'tail': {'bets': item_bets, 'hits': item_hits,
                 'rate': (item_hits / float(item_bets)) if item_bets else None},
        'per_tail': per_key,
        'kept_total': kept_total,
        'swapped_total': swapped_total,
        'current_win_streak': streak['current_win_streak'],
        'current_loss_streak': streak['current_loss_streak'],
        'max_win_streak': streak['max_win_streak'],
        'max_loss_streak': streak['max_loss_streak'],
        'four_loss_cycles': streak['four_loss_cycles'],
        'four_loss_cycles_total': four_loss_total,
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
        'version_total': sum(1 for row in verified_all if row.get('rule_version') == RULE_VERSION),
        'version_hits': sum(1 for row in verified_all if row.get('rule_version') == RULE_VERSION
                            and row.get('group_hit') is True),
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
        raise ValueError('kill13 invalid reference period')
    slot = period_slot(target)
    if slot is None:
        raise ValueError('kill13 invalid target period')
    return slot - timedelta(seconds=CLOSE_SECONDS), target


def _remaining(value):
    text = str(value).strip()
    if len(text) != 1 or text not in ATTACK_ORDER:
        raise ValueError('attack kill digit must be 0..9')
    return ''.join(char for char in ATTACK_ORDER if char != text)


def build_attack(seal):
    """与既有框架同格式：四位置（未设位置排除=10 码）+ 四定 + 两个合尾排除段。"""
    if not isinstance(seal, dict):
        raise ValueError('attack requires a sealed prediction')
    parts = []
    for key, label in (('q', '千'), ('b', '百'), ('s', '十'), ('g', '个')):
        parts.append(ATTACK_POSITION_LABELS[key] + ATTACK_ORDER)
    parts.append('四定')
    tails = seal.get('tails') or []
    if len(tails) != TOP_N:
        raise ValueError('kill13 attack requires exactly two tails')
    for item in tails:
        if not isinstance(item, dict):
            raise ValueError('kill13 attack tail must be an object')
        label = ATTACK_TAIL_LABELS.get(item.get('key'))
        if not label:
            raise ValueError('kill13 attack unknown tail key')
        parts.append(label + _remaining(item.get('kill')))
    return ''.join(parts)
