"""命中率归因面板区块（只读派生）：近 N 期逐项命中率 vs 精确随机基线。

数据源 data/hitrate_attribution.json（scripts/hitrate_attribution.py 生成）。
自包含 section + 内联 style，用 <!-- HITRATE_ATTRIBUTION_START/END --> 原地替换；
任何异常原样返回 html，绝不污染主链。
"""
from html import escape
from pathlib import Path
import json

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data' / 'hitrate_attribution.json'
VERDICTS_PATH = ROOT / 'data' / 'hitrate_verdicts.json'
MARKER = 'HITRATE_ATTRIBUTION'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'

STYLE = """
#hitrate-attribution{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #a3a3a3;border-radius:12px;background:#fafafa;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#hitrate-attribution *{box-sizing:border-box}
#hitrate-attribution h3{font-size:18px;color:#1f2937;margin:0 0 4px}
#hitrate-attribution .ha-meta{font-size:13px;color:#64748b;margin-bottom:8px}
#hitrate-attribution .ha-status{font-size:14px;font-weight:700;margin:6px 0 10px;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e5e7eb}
#hitrate-attribution .ha-ok{color:#15803d}
#hitrate-attribution .ha-watch{color:#b45309}
#hitrate-attribution .ha-alert{color:#b91c1c}
#hitrate-attribution .ha-fw{margin:10px 0 4px;font-weight:700;color:#1f2937}
#hitrate-attribution .ha-fw span{font-weight:400;font-size:12px;color:#64748b;margin-left:6px}
#hitrate-attribution .ha-table{width:100%;border-collapse:collapse;font-size:13px}
#hitrate-attribution .ha-table th,#hitrate-attribution .ha-table td{padding:5px 8px;border-bottom:1px solid #e5e7eb;text-align:right;white-space:nowrap}
#hitrate-attribution .ha-table th{font-weight:600;color:#475569;background:#f1f5f9}
#hitrate-attribution .ha-table th:first-child,#hitrate-attribution .ha-table td:first-child{text-align:left;white-space:normal;min-width:150px}
#hitrate-attribution .ha-ci{display:block;font-size:11px;color:#94a3b8}
#hitrate-attribution .ha-neg{color:#b91c1c}
#hitrate-attribution .ha-pos{color:#15803d}
#hitrate-attribution .ha-foot{font-size:12px;color:#64748b;margin-top:8px;line-height:1.5}
#hitrate-attribution .ha-verdict{font-size:13px;margin:0 0 10px;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e5e7eb;line-height:1.6}
#hitrate-attribution .ha-verdict b{color:#1f2937}
#hitrate-attribution .ha-verdict .ha-vsrc{font-size:11px;color:#94a3b8;margin-left:6px}
#hitrate-attribution .ha-verdict.ha-alert{border-color:#fecaca;background:#fef2f2}
#hitrate-attribution .ha-verdict.ha-watch{border-color:#fde68a;background:#fffbeb}
#hitrate-attribution .ha-verdict.ha-ok{border-color:#bbf7d0;background:#f0fdf4}
"""


def _as_of(report):
    value = str(report.get("as_of_period") or "")
    if value:
        return value
    frameworks = report.get("frameworks") if isinstance(report.get("frameworks"), dict) else {}
    periods = [str(item.get("as_of") or "") for item in frameworks.values() if isinstance(item, dict)]
    periods = [period for period in periods if period]
    return max(periods) if periods else ""


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}


def _load_verdicts():
    try:
        payload = json.loads(VERDICTS_PATH.read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def _verdict_strip(verdicts):
    items = verdicts.get('items') if isinstance(verdicts, dict) else None
    if not isinstance(items, list) or not items:
        return ''
    confirmed = [item for item in items if isinstance(item, dict) and item.get('status') == 'confirmed-deficit']
    watch = [item for item in items if isinstance(item, dict) and item.get('watch')]
    pending = [item for item in items if isinstance(item, dict) and item.get('status') == 'pending']
    judged = verdicts.get('family_size')
    if not isinstance(judged, int):
        judged = len(items) - len(pending)
    if confirmed:
        level, label = 'ha-alert', '确认持续亏损 %d 项' % len(confirmed)
    elif watch:
        level, label = 'ha-watch', '无确认亏损，仅近期偏离 %d 项' % len(watch)
    else:
        level, label = 'ha-ok', '无确认亏损'
    bits = []
    for item in confirmed:
        bits.append('%s %s（n=%s, p=%s）' % (
            escape(str(item.get('label') or item.get('key'))),
            _pp(_num(item.get('delta_pp'))),
            item.get('n'), '-' if item.get('p') is None else '%.4f' % item.get('p')))
    for item in watch:
        bits.append('%s %s' % (
            escape(str(item.get('label') or item.get('key'))),
            escape(str(item.get('note') or '近期偏离'))))
    alpha = verdicts.get('alpha_each')
    rule = 'n≥%s 才判 · Bonferroni α=%s' % (
        (verdicts.get('rule') or {}).get('min_n', 200),
        '-' if not isinstance(alpha, (int, float)) else '%.4f' % alpha)
    extra = (' · ' + '；'.join(bits)) if bits else ''
    source = '生成 %s · as_of %s' % (escape(str(verdicts.get('generated_at') or '-')),
                                     escape(str(verdicts.get('as_of_period') or '-')))
    return ('<div class="ha-verdict %s"><b>全史判定</b>：%s · 可判 %d/%d 项（%s）%s'
            '<span class="ha-vsrc">%s</span></div>' % (
                level, escape(label), judged, len(items), escape(rule), extra, source))


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _pct(value, digits=1):
    return '-' if value is None else '%.*f%%' % (digits, value * 100.0)


def _pp(value):
    if value is None:
        return '-'
    return '%+.2fpp' % value


def _zcell(z):
    if z is None:
        return '<td>-</td>'
    css = 'ha-neg' if z <= -1.96 else ('ha-pos' if z >= 1.96 else '')
    style = ' style="color:#b91c1c"' if z <= -1.96 else (' style="color:#15803d"' if z >= 1.96 else '')
    return '<td%s>%+.2f</td>' % (style, z)


def _rows(framework):
    items = framework.get('items') or []
    chunks = []
    for item in items:
        if not isinstance(item, dict):
            continue
        delta = _num(item.get('delta_pp'))
        style = ''
        if delta is not None and delta <= -2.0:
            style = ' style="color:#b91c1c"'
        elif delta is not None and delta >= 2.0:
            style = ' style="color:#15803d"'
        chunks.append(
            '<tr>'
            '<td>%s<span class="ha-ci">精确基线 %s</span></td>'
            '<td>%d</td>'
            '<td>%s</td>'
            '<td>%s</td>'
            '<td%s>%s</td>'
            '%s'
            '</tr>' % (
                escape(str(item.get('label') or item.get('key') or '-')),
                _pct(_num(item.get('baseline')), 2),
                item.get('n') if isinstance(item.get('n'), int) else 0,
                _pct(_num(item.get('rate'))),
                _pct(_num(item.get('baseline')), 2),
                style, _pp(delta),
                _zcell(_num(item.get('z')))))
    return ''.join(chunks)


def render_block(report=None):
    report = report if report is not None else _load()
    frameworks = report.get('frameworks') if isinstance(report, dict) else None
    if not isinstance(frameworks, dict) or not frameworks:
        return (f'{START}<section id="hitrate-attribution"><h3>命中率归因</h3>'
                f'<div class="ha-meta">归因报告尚未生成</div></section>{END}')
    attention = ((report.get('summary') or {}).get('attention') or [])
    level_class = 'ha-alert' if attention else 'ha-ok'
    level_text = ('有 %d 项偏离 ±1.96σ' % len(attention)) if attention else '各单项均在噪声范围内'
    blocks = []
    for key in ('killer', 'framework', 'six'):
        framework = frameworks.get(key)
        if not isinstance(framework, dict):
            continue
        head = ('<div class="ha-fw">%s<span>近 %s 期 · 口径 %s%s · 整组 %s（基线 %s）</span></div>'
                % (escape(str(framework.get('label') or key)),
                   escape(str(framework.get('window') or '-')),
                   escape(str(framework.get('rule') or '-')),
                   ' · 混合口径' if framework.get('rule_mixed') else '',
                   _pct(_num(framework.get('group_rate'))),
                   _pct(_num(framework.get('group_baseline')))))
        table = ('<table class="ha-table"><thead><tr>'
                 '<th>项目</th><th>n</th><th>命中率</th><th>精确基线</th><th>Δpp</th><th>z</th>'
                 '</tr></thead><tbody>' + _rows(framework) + '</tbody></table>')
        blocks.append(head + table)
    reasons = []
    for item in attention:
        if isinstance(item, dict):
            reasons.append('<li>%s · %s：%s（z=%s, n=%s）</li>' % (
                escape(str(item.get('framework') or '-')),
                escape(str(item.get('item') or '-')),
                escape(_pp(_num(item.get('delta_pp')))),
                item.get('z'), item.get('n')))
    reasons_html = ('<ul class="ha-reasons">%s</ul>' % ''.join(reasons)) if reasons else ''
    verdict_html = _verdict_strip(_load_verdicts())
    return (
        f'{START}<section id="hitrate-attribution" data-as-of="{escape(str(_as_of(report)))}">'
        f'<style>{STYLE}</style>'
        f'<h3>命中率归因（单项 vs 精确基线）</h3>'
        f'<div class="ha-meta">生成 {escape(str(report.get("generated_at") or "-"))}'
        f' · 窗口近 {escape(str(report.get("window") or "-"))} 期已核验封存'
        f' · 基线为该期封存参数在 10^4 开奖空间上的精确枚举均值（不是单项概率相乘）</div>'
        f'<div class="ha-status {level_class}">{escape(level_text)}</div>'
        f'{reasons_html}' + verdict_html + ''.join(blocks) +
        '<div class="ha-foot">单项 z 为二项近似（基线逐期枚举后取均值），只作归因线索：'
        'n≥50 且 |z|≥1.96 才列入关注。整组命中率是多项同时命中的联合口径，'
        '不能由单项相加或相乘得出；诊断「为什么整组偏低」应看本条逐项 Δpp，而不是单日整组百分比。'
        '全史判定读 verified 账本全量（n≥200 才判、Bonferroni α），近期窗口显著只记 watch，不等于确认亏损。</div>'
        f'</section>{END}')


def apply_hitrate_attribution_block(content, report=None):
    """原地替换/插入归因区块；任何异常原样返回。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        fragment = render_block(report)
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + fragment + content[end + len(END):]
        for anchor in ('<!-- HITRATE_STABILITY_START -->', '<!-- HITRATE_BASE_START -->',
                       '<!-- MODEL_SIGNI_START -->'):
            position = content.find(anchor)
            if position != -1:
                return content[:position] + fragment + content[position:]
        closing = content.rfind('</body>')
        if closing != -1:
            return content[:closing] + fragment + content[closing:]
        return content + fragment
    except Exception:
        return content
