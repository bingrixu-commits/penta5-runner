"""冷码杀号战绩面板区块:命中 = 冷码未在该位置开出。

设计要点:
- 自包含 <section> + 内联 <style>,不依赖面板已有 CSS 类。
- 用 <!-- COLD_HIT_STATS_START/END --> 标记原地替换;标记缺失时插到
  <!-- POSITION_COLD_END --> 之后。
- 任何异常都原样返回 html,绝不能因为这块内容影响整页刷新。
- 数据源为 data/cold_hit_stats.json(由 scripts/cold_hit_stats.py 定期重算)。
"""
from pathlib import Path
import json

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data/cold_hit_stats.json'
MARKER = 'COLD_HIT_STATS'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'

STYLE = """
#cold-hit-stats{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8fafc;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#cold-hit-stats *{box-sizing:border-box}
#cold-hit-stats h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#cold-hit-stats .ch-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#cold-hit-stats .ch-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,330px),1fr));gap:10px;min-width:0}
#cold-hit-stats .ch-card{min-width:0;padding:10px 12px;border:1px solid #dbeafe;border-radius:8px;background:#fff}
#cold-hit-stats .ch-title{font-size:15px;font-weight:600;color:#1e293b;margin-bottom:8px}
#cold-hit-stats .ch-main{display:flex;align-items:baseline;gap:10px;margin-bottom:6px}
#cold-hit-stats .ch-rate{font-size:26px;font-weight:700;color:#b91c1c}
#cold-hit-stats .ch-vs{font-size:12px;color:#64748b}
#cold-hit-stats .ch-streak{font-size:13px;color:#334155;margin-bottom:8px}
#cold-hit-stats .ch-streak b{font-weight:700}
#cold-hit-stats .ch-win{color:#15803d}
#cold-hit-stats .ch-loss{color:#b91c1c}
#cold-hit-stats table{width:100%;border-collapse:collapse;font-size:12px;color:#475569}
#cold-hit-stats th,#cold-hit-stats td{padding:3px 4px;text-align:right;border-top:1px dashed #e2e8f0}
#cold-hit-stats th:first-child,#cold-hit-stats td:first-child{text-align:left}
#cold-hit-stats .ch-foot{margin-top:10px;font-size:12px;color:#94a3b8}
"""


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _current_cell(item):
    """表格里的「当前」列：连中 N / 连败 N。"""
    win = item.get('current_win_streak')
    loss = item.get('current_loss_streak')
    if isinstance(win, int) and win > 0:
        return f'<b style="color:#15803d">连中 {win}</b>'
    if isinstance(loss, int) and loss > 0:
        return f'<b style="color:#b91c1c">连败 {loss}</b>'
    return '-'


def _current_chip(item):
    """卡片头部「当前」状态（进行中的连中/连败）。"""
    win = item.get('current_win_streak')
    loss = item.get('current_loss_streak')
    if isinstance(win, int) and win > 0:
        return f'当前 <b style="color:#15803d">连中 {win} 手</b> (进行中)'
    if isinstance(loss, int) and loss > 0:
        return f'当前 <b style="color:#b91c1c">连败 {loss} 手</b> (进行中)'
    return '当前 <b>-</b>'


def _pct(value):
    return f'{value * 100:.2f}%' if isinstance(value, (int, float)) else '-'


def _card(block):
    window = block.get('window')
    tops = block.get('tops') or {}
    top3 = tops.get('3') or {}
    if not top3:
        return ''
    rows = []
    for label, key in (('Top1', '1'), ('Top2', '2'), ('Top3', '3')):
        item = tops.get(key) or {}
        rows.append(
            f'<tr><td>{label}</td><td>{_pct(item.get("rate"))}</td>'
            f'<td>{_pct(item.get("baseline"))}</td>'
            f'<td>{item.get("max_win_streak", "-")}</td>'
            f'<td>{item.get("max_loss_streak", "-")}</td>'
            f'<td>{_current_cell(item)}</td></tr>')

    position_rows = []
    for key in ('q', 'b', 's', 'g'):
        entry = (block.get('positions') or {}).get(key) or {}
        item = entry.get('3') or {}
        if not item:
            continue
        position_rows.append(
            f'<tr><td>{entry.get("label", key)}</td><td>{_pct(item.get("rate"))}</td>'
            f'<td>{item.get("max_win_streak", "-")}</td>'
            f'<td>{item.get("max_loss_streak", "-")}</td>'
            f'<td>{_current_cell(item)}</td></tr>')

    return (
        f'<div class="ch-card">'
        f'<div class="ch-title">窗口 {window} 期 · 共 {block.get("samples", "-")} 期样本</div>'
        f'<div class="ch-main"><span class="ch-rate">{_pct(top3.get("rate"))}</span>'
        f'<span class="ch-vs">杀 3 码命中率 · 理论 {_pct(top3.get("baseline"))}</span></div>'
        f'<div class="ch-streak">最多连中 <b class="ch-win">{top3.get("max_win_streak", "-")}</b> 手 · '
        f'最多连败 <b class="ch-loss">{top3.get("max_loss_streak", "-")}</b> 手 · '
        f'{_current_chip(top3)}</div>'
        f'<table><thead><tr><th>杀号数</th><th>命中率</th><th>理论</th><th>最多连中</th><th>最多连败</th><th>当前</th></tr>'
        f'</thead><tbody>{"".join(rows)}</tbody></table>'
        + (f'<table style="margin-top:8px"><thead><tr><th>位置</th><th>杀3码命中</th>'
           f'<th>最多连中</th><th>最多连败</th><th>当前</th></tr></thead><tbody>{"".join(position_rows)}</tbody></table>'
           if position_rows else '')
        + '</div>')


def render_block(report=None):
    report = report if report is not None else _load()
    if not report:
        return (f'{START}<section id="cold-hit-stats"><h3>冷码杀号战绩</h3>'
                f'<div class="ch-meta">统计尚未生成</div></section>{END}')

    cards = [_card(block) for block in (report.get('windows') or ())]
    cards = [c for c in cards if c]
    if not cards:
        return (f'{START}<section id="cold-hit-stats"><h3>冷码杀号战绩</h3>'
                f'<div class="ch-meta">统计内容为空</div></section>{END}')

    return (
        f'{START}<section id="cold-hit-stats" data-as-of="{report.get("as_of_period", "")}">'
        f'<style>{STYLE}</style>'
        f'<h3>冷码杀号战绩</h3>'
        f'<div class="ch-meta">按系统冷码口径取每位置前 N 个冷码作杀号,'
        f'数据截至 {report.get("as_of_period", "")} · 计算于 {report.get("generated_at", "")}</div>'
        f'<div class="ch-grid">{"".join(cards)}</div>'
        f'<div class="ch-foot">命中 = 该位置实际开奖号<b>不在</b>冷码集合内(杀号成功)。'
        f'理论命中率 = (10-N)/10,即杀1码 90% / 杀2码 80% / 杀3码 70%。'
        f'连败 = 连续未命中(即冷码真的开出)的最长手数。'
        f'<br>核算只使用前 4 球(千、百、十、个)，第 5 球不参与；'
        f'位置按 千/百/十/个 对齐；当前连中/连败按已结算期连续计算。</div>'
        f'</section>{END}')


def apply_cold_hit_stats_block(content, report=None):
    """原地替换/插入 Top3 战绩块;任何异常原样返回。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        fragment = render_block(report)
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + fragment + content[end + len(END):]
        anchor = '<!-- POSITION_COLD_END -->'
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
