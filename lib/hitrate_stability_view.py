"""命中率稳定性 / 断崖护栏面板区块（派生展示，不改任何封存数据）。

数据源 data/hitrate_stability.json（scripts/hitrate_stability.py 生成）。
自包含 section + 内联 style，用 <!-- HITRATE_STABILITY_START/END --> 原地替换；
任何异常原样返回 html。
"""
from html import escape
from pathlib import Path
import json

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data' / 'hitrate_stability.json'
MARKER = 'HITRATE_STABILITY'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'

STYLE = """
#hitrate-stability{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #a3a3a3;border-radius:12px;background:#fafafa;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#hitrate-stability *{box-sizing:border-box}
#hitrate-stability h3{font-size:18px;color:#1f2937;margin:0 0 4px}
#hitrate-stability .hs-meta{font-size:13px;color:#64748b;margin-bottom:8px}
#hitrate-stability .hs-status{font-size:14px;font-weight:700;margin:6px 0 10px;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e5e7eb}
#hitrate-stability .hs-ok{color:#15803d}
#hitrate-stability .hs-watch{color:#b45309}
#hitrate-stability .hs-alert{color:#b91c1c}
#hitrate-stability .hs-table{width:100%;border-collapse:collapse;font-size:13px}
#hitrate-stability .hs-table th,#hitrate-stability .hs-table td{padding:6px 8px;border-bottom:1px solid #e5e7eb;text-align:right;white-space:nowrap}
#hitrate-stability .hs-table th{font-weight:600;color:#475569;background:#f1f5f9;text-align:right}
#hitrate-stability .hs-table th:first-child,#hitrate-stability .hs-table td:first-child{text-align:left;white-space:normal;min-width:150px}
#hitrate-stability .hs-table td b{font-size:14px;color:#0f172a}
#hitrate-stability .hs-ci{display:block;font-size:11px;color:#94a3b8}
#hitrate-stability .hs-badge{display:inline-block;padding:1px 7px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid}
#hitrate-stability .hs-badge-ok{color:#15803d;border-color:#86efac;background:#f0fdf4}
#hitrate-stability .hs-badge-watch{color:#b45309;border-color:#fcd34d;background:#fffbeb}
#hitrate-stability .hs-badge-alert{color:#b91c1c;border-color:#fca5a5;background:#fef2f2}
#hitrate-stability .hs-reasons{margin:8px 0 0;padding-left:18px;font-size:13px;color:#b45309}
#hitrate-stability .hs-foot{margin-top:10px;font-size:12px;color:#94a3b8}
"""

LEVEL_TEXT = {'alert': '断崖', 'watch': '观察', 'ok': '平稳'}


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _pct(value, digits=1):
    try:
        return f'{float(value) * 100:.{digits}f}%'
    except (TypeError, ValueError):
        return '-'


def _ci(value):
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return f'{float(value[0]) * 100:.1f}~{float(value[1]) * 100:.1f}%'
        except (TypeError, ValueError):
            return ''
    return ''


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _cell_window(window):
    if not isinstance(window, dict):
        return '<td>-</td>'
    rate = window.get('rate')
    if rate is None:
        return '<td>-</td>'
    z = _num(window.get('z'))
    z_text = f'z={z:+.2f}' if z is not None else ''
    mark = ' style="color:#b45309"' if z is not None and z <= -2.0 else ''
    return (f'<td{mark}><b>{_pct(rate)}</b><span class="hs-ci">{z_text}</span></td>')


def _row_cells(row):
    level = (row.get('cliff') or {}).get('level') or 'ok'
    badge = ('<span class="hs-badge hs-badge-%s">%s</span>'
             % (level, LEVEL_TEXT.get(level, '平稳')))
    w100 = row.get('w100') if isinstance(row.get('w100'), dict) else None
    w300 = row.get('w300') if isinstance(row.get('w300'), dict) else None
    n = row.get('n')
    cumulative = f'<b>{_pct(row.get("rate"))}</b><span class="hs-ci">n={n if n is not None else "-"}</span>'
    churn = row.get('churn') if isinstance(row.get('churn'), dict) else {}
    if churn.get('observed_change') and churn.get('age_hours') is not None:
        lifetime = f'{churn["age_hours"]:.1f}h'
        if isinstance(churn.get('rule_n'), int):
            lifetime += f'/<span class="hs-ci">{churn["rule_n"]} 期</span>'
    else:
        lifetime = '-'
    cover = _pct(row.get('coverage_24h'))
    cov = _num(row.get('coverage_24h'))
    note = str(row.get('coverage_note') or '')
    recent_cov = _num(row.get('coverage_recent24'))
    cover_cell = (f'<td title="{escape(note)}" style="color:#b45309">{cover}</td>'
                  if cov is not None and cov < 0.95 else f'<td>{cover}</td>')
    recent_cell = (f'<td title="{escape(note)}" style="color:#b45309">{_pct(recent_cov)}</td>'
                   if recent_cov is not None and recent_cov < 0.95
                   else f'<td>{_pct(recent_cov)}</td>')
    ci = _ci(w100.get('ci95')) if w100 else ''
    return ('<tr>'
            f'<td>{escape(str(row.get("label") or row.get("key") or "-"))}'
            f'<span class="hs-ci">{escape(str(row.get("rule") or ""))}</span></td>'
            + _cell_window(w100).replace('<td', f'<td title="{escape(ci)}"', 1)
            + _cell_window(w300)
            + f'<td>{cumulative}</td>'
            + f'<td>{_pct(row.get("expected"))}</td>'
            + cover_cell
            + recent_cell
            + f'<td>{lifetime}</td>'
            + f'<td>{badge}</td>'
            '</tr>')


def render_block(report=None):
    report = report if report is not None else _load()
    if not report or not isinstance(report.get('rows'), list):
        return (f'{START}<section id="hitrate-stability"><h3>命中率稳定性 / 断崖护栏</h3>'
                f'<div class="hs-meta">稳定性报告尚未生成</div></section>{END}')

    summary = report.get('summary') if isinstance(report.get('summary'), dict) else {}
    status = summary.get('status') or 'ok'
    level_class = {'fail': 'hs-alert', 'warn': 'hs-watch'}.get(status, 'hs-ok')
    level_text = {'fail': '断崖/口径清零告警', 'warn': '有观察项', 'ok': '全部平稳'}.get(status, '全部平稳')
    alerts = summary.get('alerts') or []
    watch = summary.get('watch') or []

    reasons = []
    for item in alerts:
        reasons.append(f"<li>{escape(str(item.get('label') or item.get('key')))}: "
                       f"{escape('; '.join(item.get('reasons') or []))}</li>")
    for item in watch:
        reasons.append(f"<li>{escape(str(item.get('label') or item.get('key')))}: "
                       f"{escape('; '.join(item.get('reasons') or []))}</li>")
    reasons_html = (f'<ul class="hs-reasons">{"".join(reasons)}</ul>' if reasons else '')

    rows_html = ''.join(_row_cells(row) for row in report['rows'] if isinstance(row, dict))

    return (
        f'{START}<section id="hitrate-stability" data-as-of="{escape(str(report.get("as_of_period") or ""))}">'
        f'<style>{STYLE}</style>'
        f'<h3>命中率稳定性 / 断崖护栏</h3>'
        f'<div class="hs-meta">as_of {escape(str(report.get("as_of_period") or "-"))}'
        f' · 判定看滚动 100/300 期与累计显著性；单日仅约 22 期(1σ≈±10pp)，日百分比不作结论</div>'
        f'<div class="hs-status {level_class}">{escape(level_text)}'
        f'（告警 {len(alerts)} · 观察 {len(watch)}）</div>'
        f'{reasons_html}'
        '<table class="hs-table"><thead><tr>'
        '<th>框架</th><th>滚动100期</th><th>滚动300期</th><th>累计</th>'
        '<th>精确基线</th><th>24h窗口覆盖</th><th>近24期覆盖</th><th>口径存活</th><th>判定</th>'
        '</tr></thead><tbody>' + rows_html + '</tbody></table>'
        '<div class="hs-foot">断崖规则：24h 覆盖率&lt;80% 且尾部仍漏≥3 期；滚动100期 z≤-2.58 且 300 期 z≤-1.0；'
        '口径切换后存活&lt;24h 或 &lt;30 期。「24h 窗口覆盖」会把窗口内历史缺口一并计入；'
        '「近 24 期覆盖」只看最近 24 期，判断缺口是否仍在发生；覆盖率低时命中率样本同样不完整，请结合 n 解读。</div>'
        f'</section>{END}')


def apply_hitrate_stability_block(content, report=None):
    """原地替换/插入稳定性区块；任何异常原样返回。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        fragment = render_block(report)
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + fragment + content[end + len(END):]
        for anchor in ('<!-- MODEL_SIGNI_START -->', '<!-- HITRATE_BASE_START -->'):
            position = content.find(anchor)
            if position != -1:
                return content[:position] + fragment + content[position:]
        closing = content.rfind('</body>')
        if closing != -1:
            return content[:closing] + fragment + content[closing:]
        return content + fragment
    except Exception:
        return content
