"""面板「整组命中率 vs 随机期望」区块。

数据源 data/hitrate_baseline.json（由 lib/hitrate_baseline.py 每轮重算）。
口径：整组命中 = 一期全部项目同时命中；随机期望 = 现行规则 10^4 组合精确枚举校准
（不是单项准确率，也不是不同项数框架之间的可比排名）。

自包含 section + 内联 style；<!-- HITRATE_BASE_START/END --> 标记原地替换；
标记不存在时回退插入到真实命中率块之后；任何异常原样返回 html。
"""
from html import escape
from pathlib import Path
import json

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data' / 'hitrate_baseline.json'
MARKER = 'HITRATE_BASE'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'

STYLE = """
#hitrate-base{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #a5b4fc;border-radius:12px;background:#f8faff;color:#1e293b;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#hitrate-base *{box-sizing:border-box}
#hitrate-base h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#hitrate-base .hb-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#hitrate-base table{width:100%;border-collapse:collapse;font-size:12.5px;color:#334155;margin-bottom:10px}
#hitrate-base th,#hitrate-base td{padding:5px 6px;text-align:right;border-top:1px dashed #c7d2fe;vertical-align:top}
#hitrate-base th:first-child,#hitrate-base td:first-child{text-align:left}
#hitrate-base th{color:#475569;font-weight:600}
#hitrate-base .hb-good{color:#15803d;font-weight:700}
#hitrate-base .hb-bad{color:#b91c1c;font-weight:700}
#hitrate-base .hb-foot{font-size:12px;color:#64748b;margin-top:4px;line-height:1.7}
#hitrate-base .hb-note{font-size:12px;color:#92400e;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:6px 8px;margin:8px 0}
"""


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _pct(value):
    return f'{value * 100:.2f}%' if isinstance(value, (int, float)) else '-'


def _delta(value):
    return f'{value * 100:+.2f}pp' if isinstance(value, (int, float)) else '-'


def _p(value):
    return f'{value:.3f}' if isinstance(value, (int, float)) else '-'


def render_block(report=None):
    report = report if report is not None else _load()
    if not report or not report.get('rows'):
        return (f'{START}<section id="hitrate-base"><h3>整组命中率 vs 随机期望</h3>'
                f'<div class="hb-meta">报告尚未生成</div></section>{END}')
    rows = []
    for item in report['rows']:
        verdict = str(item.get('verdict') or '-')
        css = 'hb-bad' if verdict == '偏离随机基线' else 'hb-good'
        rows.append(
            f"<tr><td>{escape(str(item.get('label') or item.get('key')))}"
            f"<div class=\"hb-foot\">{escape(str(item.get('rule') or ''))}</div></td>"
            f"<td>{item.get('items') if item.get('items') is not None else '-'}</td>"
            f"<td>{item.get('n', 0)}</td>"
            f"<td><b>{_pct(item.get('rate'))}</b></td>"
            f"<td>{_pct(item.get('expected'))}</td>"
            f"<td>{_delta(item.get('delta'))}</td>"
            f"<td>{_p(item.get('p'))}</td>"
            f"<td class=\"{css}\">{escape(verdict)}</td></tr>")
    singles = ' · '.join(f"{escape(str(row['label']))} {_pct(row['baseline'])}"
                         for row in report.get('single_baselines') or ())
    return (
        f'{START}<section id="hitrate-base" data-as-of="{escape(str(report.get("as_of_period") or ""))}">'
        f'<style>{STYLE}</style>'
        f'<h3>整组命中率 vs 随机期望（解释 40%~62% 从哪来）</h3>'
        f'<div class="hb-meta">业务日 {escape(str(report.get("biz_date") or "-"))} · '
        f'只统计已核验样本 · 随机期望来源：{escape(str(report.get("basis") or ""))}</div>'
        f'<table><thead><tr><th>框架 / 规则</th><th>项目数</th><th>样本</th>'
        f'<th>实测整组</th><th>随机期望</th><th>偏差</th><th>p</th><th>判定</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        f'<div class="hb-note">整组命中率是「一期 N 项全部命中」的联合概率，不是单项准确率。'
        f'7~9 项框架的随机期望本来就只有 41%~62%，所以面板上的 40%~60% ≠ 模型退化。</div>'
        f'<div class="hb-foot">单项随机基线：{singles}。</div>'
        f'<div class="hb-foot">随机期望按现行规则用 10^4 种开奖组合精确枚举校准（已计入合尾/形态/保留码共享同一组开奖号的相关性，'
        f'不是简单相乘）；换规则版本即重置校准，未知版本显示「基线待校准」。</div>'
        f'<div class="hb-foot">缺预测 / 待核验 / 冲突期不进分母；'
        f'样本 &lt;30 或 p&gt;0.05 时不能判定优于或劣于随机。不同项数的框架不能按整组百分比横向排名。</div>'
        f'</section>{END}')


def apply_hitrate_baseline_block(content, report=None):
    """原地替换/插入「整组命中率 vs 随机期望」块；任何异常原样返回。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        fragment = render_block(report)
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + fragment + content[end + len(END):]
        for anchor in ('<!-- REAL_HIT_END -->', '<!-- MODEL_SIGNI_END -->',
                       '<!-- KILL_THREE_END -->', '<!-- COLD_HIT_STATS_END -->'):
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
