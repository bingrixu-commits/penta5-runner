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


def _item_note(row):
    item = row.get('item_stats') if isinstance(row.get('item_stats'), dict) else {}
    if not item.get('items') or item.get('avg_items') is None or item.get('item_rate') is None:
        return ''
    return ('<span class="hs-ci">平均命中 %.2f/%d 项（项级 %s）</span>'
            % (float(item['avg_items']), int(item['items']), _pct(item.get('item_rate'))))


def _model_note(row):
    if str(row.get('key') or '') != 'killer':
        return ''
    split = row.get('model_split')
    if not isinstance(split, list):
        return ''
    for item in split:
        if not isinstance(item, dict):
            continue
        model = str(item.get('model') or '')
        n = item.get('n')
        if 'v4-pro' in model and isinstance(n, int) and n > 0:
            text = ('含 %s 试跑 %s 期 %s/%s 中（样本不足，单独看不可下结论）'
                    % (model, n, item.get('hits'), n))
            return '<span class="hs-ci">%s</span>' % escape(text)
    return ''


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
    extra_lines = []
    delta = _num(row.get('delta'))
    p_value = _num(row.get('p'))
    if delta is not None and p_value is not None:
        extra_lines.append(f'<span class="hs-ci">Δ{delta * 100:+.2f}pp · p={p_value:.3f}</span>')
    drawdown = row.get('drawdown') if isinstance(row.get('drawdown'), dict) else {}
    random_ref = drawdown.get('random') if isinstance(drawdown.get('random'), dict) else {}
    if drawdown.get('current_dd_pp') is not None and drawdown.get('n') == n:
        text = f'回撤 {float(drawdown["current_dd_pp"]):.1f}pp'
        if random_ref.get('median_pp') is not None:
            text += f'（随机中位 {float(random_ref["median_pp"]):.1f}pp）'
        extra_lines.append(f'<span class="hs-ci">{text}</span>')
    cumulative = (f'<b>{_pct(row.get("rate"))}</b>'
                  f'<span class="hs-ci">n={n if n is not None else "-"}</span>'
                  + ''.join(extra_lines))
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
            f'<span class="hs-ci">{escape(str(row.get("rule") or ""))}</span>'
            f'{_item_note(row)}</td>'
            + _cell_window(w100).replace('<td', f'<td title="{escape(ci)}"', 1)
            + _cell_window(w300)
            + f'<td>{cumulative}</td>'
            + f'<td>{_pct(row.get("expected"))}</td>'
            + cover_cell
            + recent_cell
            + f'<td>{lifetime}</td>'
            + f'<td>{badge}{_model_note(row)}</td>'
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
        '「近 24 期覆盖」只看最近 24 期，判断缺口是否仍在发生；覆盖率低时命中率样本同样不完整，请结合 n 解读。自峰值回撤=当前累计率相对历史累计峰值（剔除前 25 期）的回落；随机中位=同 n 纯随机序列最大回撤中位数，用于区分真实断崖与随机波动。' + _failure_split_html(report) + '</div>'
        f'</section>{END}')


def _minutes(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '-'
    if number >= 60:
        return '%.1f 小时' % (number / 60.0)
    return '%.0f' % number


def _short_key(key):
    return {'he3_refine': 'he3', 'kill3_tail': 'kill3', 'kill1_tail': 'kill1'}.get(str(key), str(key))


def _failure_split_html(report):
    """近 24h 失败分型脚注（只读；数据缺失返回空串）。"""
    try:
        summary = report.get('summary') if isinstance(report, dict) else None
        split = summary.get('failure_split') if isinstance(summary, dict) else None
        if not isinstance(split, dict):
            return ''
        bits = []
        calls = split.get('model_calls')
        if isinstance(calls, dict):
            errors = calls.get('errors') if isinstance(calls.get('errors'), dict) else {}
            error_text = '、'.join('%s %s' % (escape(str(key)), escape(str(value)))
                                   for key, value in sorted(errors.items(),
                                                            key=lambda item: -int(item[1] or 0))
                                   if value)
            part = ('近 24h 模型调用 %s 响应 / %s 异常'
                    % (calls.get('responses'), calls.get('error_total')))
            if error_text:
                part += '（%s）' % error_text
            if calls.get('retries'):
                part += '，重试 %s' % calls.get('retries')
            bits.append(part)
        slots = split.get('kill3_slots')
        if isinstance(slots, dict):
            bits.append('kill3 发布 %s 槽 / 缺槽 %s / 版本切换 %s 次'
                        % (slots.get('published_24h'), slots.get('missing_slots_24h'),
                           slots.get('version_changes_24h')))
        egress = split.get('ai_egress')
        if isinstance(egress, dict) and egress.get('off_windows_24h'):
            part = ('AI 总闸近 24h 关闭 %s 次共 %s 分钟'
                    % (egress.get('off_windows_24h'), _minutes(egress.get('off_minutes_24h'))))
            if egress.get('balance_pause_minutes'):
                part += '（余额欠费 %s 分钟 / 手动 %s 分钟）' % (
                    _minutes(egress.get('balance_pause_minutes')),
                    _minutes(egress.get('manual_off_minutes')))
            bits.append(part)
        missing = split.get('missing_slots')
        if isinstance(missing, dict) and missing.get('unique_periods'):
            part = ('近 24h 缺期 %s 期·框架（去重 %s 槽）：AI 总闸关闭 %s'
                    % (missing.get('events'), missing.get('unique_periods'),
                       missing.get('ai_off_events')))
            balance_off = missing.get('balance_off_periods') or []
            if balance_off:
                part += '（余额欠费 %s 期）' % escape(str(len(balance_off)))
            other = missing.get('other') or []
            if other:
                labels = []
                for item in other[:4]:
                    if not isinstance(item, dict):
                        continue
                    names = '/'.join(_short_key(name) for name in (item.get('frameworks') or [])[:2])
                    labels.append(escape('%s %s' % (names, item.get('period'))))
                if len(other) > 4:
                    labels.append('…')
                part += '，其它 %s 槽（%s）' % (missing.get('other_events'), '；'.join(labels))
            bits.append(part)
        bits.append('缺期按覆盖率单列，不计入累计 n，也不按 0 命中')
        return ' 失败分型：' + '；'.join(bits) + '。'
    except Exception:
        return ''


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
        for anchor in ('<!-- HITRATE_BASE_START -->',):
            position = content.find(anchor)
            if position != -1:
                return content[:position] + fragment + content[position:]
        closing = content.rfind('</body>')
        if closing != -1:
            return content[:closing] + fragment + content[closing:]
        return content + fragment
    except Exception:
        return content
