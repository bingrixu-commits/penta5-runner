"""位置冷码报告:千/百/十/个 四个位置的冷码排名。

只做描述性观察,不预测、不改任何模型。冷码口径沿用系统现有实现
(cold_observation._group):出现次数少者更冷,次数相同则当前遗漏更长者更冷;
同时给出"遗漏优先"排序作为对照,便于判断两种口径是否分歧。

产物:
  data/position_cold_report.json   机读
  data/position_cold_report.txt    人读(可直接 cat)
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
LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
TOP_N = 3
P_SINGLE = 0.1  # 单个号码在某个位置出现的概率


def rank(items, order):
    if order == 'count':
        return sorted(items, key=lambda it: (it['count'], -it['omission']))
    return sorted(items, key=lambda it: (-it['omission'], it['count']))


def build(rows, window):
    trailing = rows[-window:]
    periods = [r['period'] for r in trailing]
    numbers = [r['numbers'] for r in trailing]
    result = {}
    for index, key in enumerate(POSITION_KEYS):
        samples = [str(n[index]) for n in numbers]
        # _group 返回 current_omission,这里统一成 omission 便于后续处理
        items = [{'value': it['value'], 'count': it['count'],
                  'omission': it['current_omission'],
                  'last_seen_period': it['last_seen_period']}
                 for it in _group(DIGITS, samples, periods)['items']]
        by_count = rank(items, 'count')
        by_omission = rank(items, 'omission')
        result[key] = {
            'label': LABELS[key],
            'latest': samples[-1] if samples else None,
            'cold_by_count': [
                {'value': it['value'], 'count': it['count'], 'omission': it['omission'],
                 'last_seen_period': it['last_seen_period']}
                for it in by_count[:TOP_N]],
            'cold_by_omission': [
                {'value': it['value'], 'count': it['count'], 'omission': it['omission'],
                 'last_seen_period': it['last_seen_period']}
                for it in by_omission[:TOP_N]],
            'omit_probability': [
                round((1 - P_SINGLE) ** it['omission'], 6) for it in by_omission[:TOP_N]],
        }
    return {'window': window, 'periods': {'first': periods[0], 'last': periods[-1]},
            'positions': result}


def render(report):
    lines = []
    lines.append(f"位置冷码报告  生成于 {report['generated_at']}")
    lines.append(f"数据截至 {report['as_of_period']}  共 {report['records']} 期可用")
    lines.append("")
    for block in report['windows']:
        lines.append(f"===== 最近 {block['window']} 期  "
                     f"({block['periods']['first']} ~ {block['periods']['last']}) =====")
        lines.append(f"{'位置':<5}{'第1冷':>7}{'第2冷':>7}{'第3冷':>7}   "
                     f"|  遗漏优先 第1/第2/第3")
        for key in POSITION_KEYS:
            data = block['positions'][key]
            first = [str(it['value']) for it in data['cold_by_count']]
            omits = [f"{it['value']}({it['omission']})" for it in data['cold_by_omission']]
            lines.append(f"{data['label']:<5}{first[0]:>7}{first[1]:>7}{first[2]:>7}   "
                         f"|  {' '.join(omits)}")
        lines.append("")
        lines.append("明细(次数优先口径,即系统当前实现):")
        for key in POSITION_KEYS:
            data = block['positions'][key]
            detail = "  ".join(
                f"第{i+1}冷={it['value']}({it['count']}次/遗漏{it['omission']}/上次{it['last_seen_period']})"
                for i, it in enumerate(data['cold_by_count']))
            lines.append(f"  {data['label']}: {detail}")
        lines.append("")
    lines.append("说明:冷码=窗口内出现次数最少,次数相同则遗漏更长者更冷。")
    lines.append("      '遗漏优先'为对照口径(最久未出者最冷),两列若分歧说明次数与遗漏打架。")
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
        'revision': 'position-cold-report-v1',
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
