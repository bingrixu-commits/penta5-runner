"""位置冷码报告:千/百/十/个 每个位置只输出一个冷码。

口径沿用系统现有实现(cold_observation._group):窗口内出现次数最少者更冷,
次数相同则当前遗漏更长者更冷;并列时按号码升序(与系统实现一致)。
这正是 AI 冷码七项框架系列消费的 'cold' 字段口径,保持一致（该框架已于 2026-11-01 退役，此段保留作历史口径说明）。

只做描述性观察,不预测、不改任何模型。合尾冷码不在本报告范围内。

产物:
  data/position_cold_report.json   机读(含次数/遗漏/上次出现)
  data/position_cold_report.txt    人读(每位置一个数,可直接 cat)
"""
from pathlib import Path
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stats_history import TZ, load_snapshot, validate_conflicting_periods  # noqa: E402
from cold_observation import _group, POSITION_KEYS, DIGITS  # noqa: E402

ROOT = Path('/root/penta5-runner')
OUT_JSON = ROOT / 'data/position_cold_report.json'
OUT_TEXT = ROOT / 'data/position_cold_report.txt'
WINDOWS = (30, 20)
# 30 期取第 2 冷:面板上方「冷码观察 · 近30期每项第1名」已经给了第 1 冷,
# 这里再给第 1 冷属于重复;20 期上方没有,仍取第 1 冷。
RANK_BY_WINDOW = {30: 2, 20: 1}
LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}


def cold_pick(items, rank=1):
    """系统口径的冷码排序:次数升序,其次遗漏降序(稳定排序保留号码升序)。

    rank=1 为第 1 冷,rank=2 为第 2 冷。
    """
    ranked = sorted(items, key=lambda it: (it['count'], -it['omission']))
    index = max(0, min(rank - 1, len(ranked) - 1))
    return ranked[index]


def build(rows, window):
    trailing = rows[-window:]
    periods = [r['period'] for r in trailing]
    numbers = [r['numbers'] for r in trailing]
    positions = {}
    picks = []
    for index, key in enumerate(POSITION_KEYS):
        samples = [str(n[index]) for n in numbers]
        items = [{'value': it['value'], 'count': it['count'],
                  'omission': it['current_omission'],
                  'last_seen_period': it['last_seen_period']}
                 for it in _group(DIGITS, samples, periods)['items']]
        rank = RANK_BY_WINDOW.get(window, 1)
        chosen = cold_pick(items, rank)
        positions[key] = {
            'label': LABELS[key],
            'rank': rank,
            'cold': chosen['value'],
            'count': chosen['count'],
            'omission': chosen['omission'],
            'last_seen_period': chosen['last_seen_period'],
            'latest': samples[-1] if samples else None,
        }
        picks.append(f"{LABELS[key]} {chosen['value']}(遗漏{chosen['omission']})")
    return {'window': window,
            'rank': RANK_BY_WINDOW.get(window, 1),
            'periods': {'first': periods[0], 'last': periods[-1]},
            'positions': positions,
            'line': '  '.join(picks)}


def render(report):
    lines = [
        f"位置冷码报告  生成于 {report['generated_at']}",
        f"数据截至 {report['as_of_period']}  共 {report['records']} 期可用",
        "",
    ]
    for block in report['windows']:
        rank = block.get('rank', 1)
        lines.append(f"最近 {block['window']} 期 · 第{rank}冷 "
                     f"({block['periods']['first']}~{block['periods']['last']})")
        lines.append(f"  {block['line']}")
        lines.append("")
    lines.append("口径:窗口内出现次数最少者最冷;次数相同则遗漏更长者更冷(与系统 cold 字段一致)。")
    return "\n".join(lines) + "\n"


def main():
    snapshot = load_snapshot(state_path=ROOT / 'data/state.json',
                             cache_path=ROOT / 'data/stats_draw_history.json',
                             persist=False)
    conflicts = validate_conflicting_periods(snapshot.get('conflicting_periods', ()))
    rows = sorted((r for r in snapshot['records'] if r['period'] not in conflicts),
                  key=lambda row: (row['opentime'], row['period']))
    if len(rows) < max(WINDOWS):
        print(f'insufficient records: {len(rows)}', flush=True)
        return 1

    report = {
        'generated_at': datetime.now(TZ).isoformat(),
        'revision': 'position-cold-report-v3-rank2',
        'records': len(rows),
        'as_of_period': rows[-1]['period'],
        'conflicts_excluded': len(conflicts),
        'windows': [build(rows, window) for window in WINDOWS],
    }

    temp = OUT_JSON.with_suffix('.json.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, OUT_JSON)

    text = render(report)
    temp_text = OUT_TEXT.with_suffix('.txt.tmp')
    temp_text.write_text(text, encoding='utf-8')
    os.replace(temp_text, OUT_TEXT)

    print(text, flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
