"""真实命中率面板区块:只用已结算记录,标注置信区间与交叉验证结果。

数据源 data/real_hitrate.json(由 scripts/real_hitrate_report.py 每期重算)。
命中 = 该位置开奖号 != 被排除的号码,基线 90%。

自包含 section + 内联 style;<!-- REAL_HIT_START/END --> 标记原地替换;
任何异常原样返回 html。
"""
from pathlib import Path
import json

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data/real_hitrate.json'
MARKER = 'REAL_HIT'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'

STYLE = """
#real-hit{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #94a3b8;border-radius:12px;background:#f8fafc;color:#1e293b;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#real-hit *{box-sizing:border-box}
#real-hit h3{font-size:18px;color:#0f172a;margin:0 0 4px}
#real-hit .rh-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#real-hit .rh-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:10px}
#real-hit .rh-big{font-size:30px;font-weight:800;color:#0f172a}
#real-hit .rh-sub{font-size:13px;color:#64748b}
#real-hit table{width:100%;border-collapse:collapse;font-size:12.5px;color:#334155;margin-bottom:10px}
#real-hit th,#real-hit td{padding:4px 5px;text-align:right;border-top:1px dashed #cbd5e1}
#real-hit th:first-child,#real-hit td:first-child{text-align:left}
#real-hit th{color:#475569;font-weight:600}
#real-hit .rh-hi{color:#b91c1c;font-weight:700}
#real-hit .rh-ok{color:#15803d}
#real-hit .rh-foot{font-size:12px;color:#64748b;margin-top:6px}
#real-hit .rh-cap{font-size:13px;color:#334155;font-weight:600;margin:8px 0 3px}
"""


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _pct(value):
    return f'{value * 100:.2f}%' if isinstance(value, (int, float)) else '-'


def render_block(report=None):
    report = report if report is not None else _load()
    if not report or not report.get('totals'):
        return (f'{START}<section id="real-hit"><h3>真实命中率</h3>'
                f'<div class="rh-meta">报告尚未生成</div></section>{END}')

    totals = report['totals']
    headline = next((t for t in totals if '全部版本' in t.get('label', '')), totals[0])

    rows = []
    for item in totals:
        lo, hi = item.get('ci95') or [None, None]
        rows.append(
            f"<tr><td>{item['label']}</td><td>{item['bets']}</td>"
            f"<td><b>{_pct(item['rate'])}</b></td>"
            f"<td>[{_pct(lo)}, {_pct(hi)}]</td>"
            f"<td>{item.get('delta', 0) * 100:+.2f}%</td>"
            f"<td>{item.get('p_value')}</td></tr>")

    daily_rows = []
    for day in report.get('daily') or ():
        delta = (day['rate'] - 0.9) * 100
        daily_rows.append(
            f"<tr><td>{day['biz']}</td><td>{day['bets']}</td>"
            f"<td>{_pct(day['rate'])}</td><td>{delta:+.2f}%</td></tr>")

    checks = report.get('cross_checks') or []
    check_text = ' · '.join(
        f"{c['file']} 已结算 {c['verified']} 条,不一致 {c['mismatch']}"
        for c in checks) or '-'
    mismatch_total = sum(c.get('mismatch') or 0 for c in checks)

    return (
        f'{START}<section id="real-hit" data-as-of="{report.get("generated_at", "")}">'
        f'<style>{STYLE}</style>'
        f'<h3>真实命中率(纯实测,无推算)</h3>'
        f'<div class="rh-meta">{report.get("definition", "")} · 随机基线 90%</div>'
        f'<div class="rh-head"><span class="rh-big">{_pct(headline["rate"])}</span>'
        f'<span class="rh-sub">{headline["label"]} · {headline["bets"]} 注 · '
        f'95% 置信区间 [{_pct((headline.get("ci95") or [None])[0])}, '
        f'{_pct((headline.get("ci95") or [None, None])[1])}] · p={headline.get("p_value")}</span></div>'
        f'<table><thead><tr><th>模型 / 指标</th><th>投注</th><th>命中率</th>'
        f'<th>95% 置信区间</th><th>与基线差</th><th>p 值</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        + (f'<div class="rh-cap">按业务日的真实波动(解释单日数字为何忽高忽低)</div>'
           f'<table><thead><tr><th>业务日</th><th>投注</th><th>命中率</th>'
           f'<th>与基线差</th></tr></thead><tbody>{"".join(daily_rows)}</tbody></table>'
           if daily_rows else '')
        + f'<div class="rh-foot">交叉验证:{check_text}'
        f'{"(全部一致)" if mismatch_total == 0 else "(存在不一致,需排查)"}</div>'
        f'<div class="rh-foot">置信区间跨过 90% 且 p 值大于 0.05 时,'
        f'表示该模型与随机杀号无法区分。</div>'
        f'<div class="rh-foot">核算只使用前 4 球(千/百/十/个),第 5 球不参与。</div>'
        f'</section>{END}')


def apply_real_hit_block(content, report=None):
    """原地替换/插入真实命中率块;任何异常原样返回。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        fragment = render_block(report)
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + fragment + content[end + len(END):]
        for anchor in ('<!-- MODEL_SIGNI_END -->', '<!-- KILL_THREE_END -->',
                       '<!-- COLD_HIT_STATS_END -->', '<!-- POSITION_COLD_END -->'):
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
