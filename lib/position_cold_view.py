"""位置冷码面板区块:把 position_cold_report.json 渲染为自包含的 HTML 块。

设计要点:
- 自包含 <section> + 内联 <style>,不依赖面板已有 CSS 类。
- 用 <!-- POSITION_COLD_START/END --> 标记,便于每轮原地替换。
- 任何异常都原样返回 html,绝不能因为这块内容影响整页刷新。
"""
from pathlib import Path
import json

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data/position_cold_report.json'
MARKER = 'POSITION_COLD'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'

STYLE = """
#position-cold{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #a5b4fc;border-radius:12px;background:#fafbff;color:#334155;font:14px/1.65 sans-serif;min-width:0;overflow-wrap:anywhere}
#position-cold *{box-sizing:border-box}
#position-cold h3{font-size:18px;color:#3730a3;margin:0 0 4px}
#position-cold .pc-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#position-cold .pc-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,320px),1fr));gap:10px;min-width:0}
#position-cold .pc-card{min-width:0;padding:10px 12px;border:1px solid #dbe4fb;border-radius:8px;background:#fff}
#position-cold .pc-title{font-size:15px;color:#1e293b;font-weight:600;margin-bottom:8px}
#position-cold .pc-row{display:flex;align-items:baseline;gap:10px;padding:5px 0;border-top:1px dashed #e2e8f0}
#position-cold .pc-row:first-of-type{border-top:none}
#position-cold .pc-pos{width:44px;color:#475569;font-size:13px}
#position-cold .pc-value{font-size:22px;font-weight:700;color:#b91c1c;min-width:28px}
#position-cold .pc-note{font-size:12px;color:#64748b}
#position-cold .pc-foot{margin-top:10px;font-size:12px;color:#94a3b8}
"""


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def render_block(report=None):
    report = report if report is not None else _load()
    if not report:
        return (f'{START}<section id="position-cold"><h3>位置冷码</h3>'
                f'<div class="pc-meta">报告尚未生成</div></section>{END}')

    generated = str(report.get('generated_at') or '')
    as_of = str(report.get('as_of_period') or '')
    cards = []
    for block in report.get('windows') or ():
        window = block.get('window')
        rows = []
        for key in ('q', 'b', 's', 'g'):
            item = (block.get('positions') or {}).get(key) or {}
            if not item:
                continue
            omission = item.get('omission')
            count = item.get('count')
            note = f"遗漏 {omission} 期"
            if isinstance(count, int):
                note += f" · 窗口内出现 {count} 次"
            rows.append(
                f'<div class="pc-row"><span class="pc-pos">{item.get("label", key)}</span>'
                f'<span class="pc-value">{item.get("cold")}</span>'
                f'<span class="pc-note">{note}</span></div>')
        if not rows:
            continue
        periods = block.get('periods') or {}
        span = f'{periods.get("first", "")}~{periods.get("last", "")}'
        cards.append(
            f'<div class="pc-card"><div class="pc-title">最近 {window} 期 · {span}</div>'
            + ''.join(rows) + '</div>')

    if not cards:
        return (f'{START}<section id="position-cold"><h3>位置冷码</h3>'
                f'<div class="pc-meta">报告内容为空</div></section>{END}')

    return (
        f'{START}<section id="position-cold" data-as-of="{as_of}">'
        f'<style>{STYLE}</style>'
        f'<h3>位置冷码(每期开奖后重算)</h3>'
        f'<div class="pc-meta">数据截至 {as_of} · 生成于 {generated}</div>'
        f'<div class="pc-grid">{"".join(cards)}</div>'
        f'<div class="pc-foot">口径:窗口内出现次数最少者最冷,次数相同则遗漏更长者更冷。'
        f'仅作观察,不构成预测。</div>'
        f'</section>{END}')


def apply_position_cold_block(content, report=None):
    """把冷码块原地替换进面板;标记缺失时插到 COLD_OBSERVATION 之后。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        fragment = render_block(report)
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + fragment + content[end + len(END):]
        anchor = '<!-- COLD_OBSERVATION_END -->'
        position = content.find(anchor)
        if position != -1:
            position += len(anchor)
            return content[:position] + fragment + content[position:]
        closing = content.rfind('</body>')
        if closing != -1:
            return content[:closing] + fragment + content[closing:]
        return content + fragment
    except Exception:
        return content
