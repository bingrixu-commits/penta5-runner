"""面板「整组命中率 vs 随机期望」区块（累计版）。

数据源：
- data/framework_hitrate_cumulative.json（scripts/framework_hitrate_cumulative.py 每 15 分钟生成）：
  版本锁定累计、精确枚举基线、Wilson 95% 区间、双侧 p、发布覆盖率、计划期命中率、按模型分档；
- data/hitrate_baseline.json（lib/hitrate_baseline.py 每轮重算）：用于「今日 10 期」参考列与单项基线。

口径：整组命中 = 一期全部项目同时命中；随机期望 = 现行规则 10^4 组合精确枚举校准
（不是单项准确率，也不是不同项数框架之间的可比排名）。
累计样本 n<200 一律标「样本不足」，不做优劣结论；旧版本样本单独列出、不混算。

自包含 section + 内联 style；<!-- HITRATE_BASE_START/END --> 标记原地替换；
标记不存在时回退插入到真实命中率块之后；任何异常原样返回 html。
"""
from html import escape
from pathlib import Path
import json
from datetime import datetime, timedelta

ROOT = Path('/root/penta5-runner')
CUMULATIVE_PATH = ROOT / 'data' / 'framework_hitrate_cumulative.json'
BASELINE_PATH = ROOT / 'data' / 'hitrate_baseline.json'
MARKER = 'HITRATE_BASE'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'
STALE_MINUTES = 120

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
#hitrate-base .hb-warn{color:#b45309;font-weight:700}
#hitrate-base .hb-foot{font-size:11.5px;color:#64748b;line-height:1.55}
#hitrate-base .hb-note{font-size:12px;color:#92400e;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:6px 8px;margin:8px 0}
#hitrate-base .hb-big{font-size:15px;font-weight:800}
#hitrate-base .hb-sub{font-size:11px;color:#64748b}
"""


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _pct(value, digits=2):
    return f'{value * 100:.{digits}f}%' if isinstance(value, (int, float)) else '-'


def _delta(value):
    return f'{value * 100:+.2f}pp' if isinstance(value, (int, float)) else '-'


def _p(value):
    return f'{value:.3f}' if isinstance(value, (int, float)) else '-'


def _verdict_class(verdict):
    text = str(verdict or '')
    if '偏离' in text:
        return 'hb-warn'
    if '样本不足' in text or '待校准' in text:
        return 'hb-foot'
    return 'hb-good'


def _cumulative_fresh(report):
    generated = report.get('generated_at')
    if not generated:
        return False
    try:
        moment = datetime.fromisoformat(generated)
    except (TypeError, ValueError):
        return False
    return datetime.now() - moment < timedelta(minutes=STALE_MINUTES)


def _today_map(baseline):
    mapping = {}
    for row in (baseline or {}).get('rows') or ():
        key = row.get('key')
        if key:
            mapping[key] = row
    return mapping


DRIFT_STATUS_LABELS = {
    'ok': '正常', 'warn': '漂移预警', 'fail': '显著下漂',
    'insufficient': '样本不足', 'high': '高于基线(参考)', 'unknown': '未知',
}


def _drift_rate_cell(window):
    if not isinstance(window, dict) or not window.get('n'):
        return '<span class="hb-foot">-</span>'
    rate = _pct(window.get('rate'))
    z = window.get('z')
    text = ('z=%+.2f' % z) if isinstance(z, (int, float)) else ''
    if isinstance(z, (int, float)):
        if z <= -3:
            cls = 'hb-bad'
        elif z <= -2:
            cls = 'hb-warn'
        elif z >= 2:
            cls = 'hb-good'
        else:
            cls = 'hb-foot'
    else:
        cls = 'hb-foot'
    note = '' if window.get('full', True) else '<span class="hb-sub">(不足)</span>'
    return '<span class="%s">%s</span><div class="hb-sub">%s %s</div>' % (cls, rate, text, note)


def _render_drift(cumulative):
    drift = cumulative.get('drift') or {}
    drift_rows = drift.get('rows') or []
    if not drift_rows:
        return ''
    jb = cumulative.get('joint_form_baseline') or {}
    models = jb.get('models') or {}
    parts = []
    for name in ('killer', 'framework'):
        value = models.get(name)
        if not isinstance(value, dict):
            continue
        parts.append('%s %s → %s（n=%s）' % (name, _pct(value.get('before')),
                                            _pct(value.get('after')), value.get('n')))
    baseline_note = ''
    if parts:
        baseline_note = ('<div class="hb-note">版本化基线：口径分界前 AI 形态 / 分界后联合优化形态，'
                         '逐期 10^4 精确枚举均值 —— ' + '；'.join(parts) + '。'
                         '累计行的随机期望已按记录逐期加权（不是单一旧基线）。</div>')
    body = []
    for item in drift_rows:
        windows = item.get('windows') or {}
        cells = ''.join('<td>%s</td>' % _drift_rate_cell(windows.get(size) or windows.get(str(size)))
                        for size in (100, 200, 300, 500))
        cusum = item.get('cusum') or {}
        peak = cusum.get('z_peak')
        peak_text = ('%+.2fσ' % peak) if isinstance(peak, (int, float)) else '-'
        if cusum.get('side') == 'down' and isinstance(peak, (int, float)):
            peak_text += ' ↓'
        status = str(item.get('status') or 'unknown')
        label = DRIFT_STATUS_LABELS.get(status, status)
        cls = {'fail': 'hb-bad', 'warn': 'hb-warn', 'high': 'hb-good'}.get(status, 'hb-foot')
        body.append('<tr><td><b>%s</b><div class="hb-foot">n=%s · %s</div></td>%s'
                    '<td>%s</td><td class="%s">%s</td></tr>'
                    % (escape(str(item.get('label') or item.get('key'))), item.get('n'),
                       escape(str(item.get('verdict') or '')), cells, peak_text, cls, label))
    return ('<h4 style="font-size:14px;color:#1e3a8a;margin:14px 0 4px">滚动漂移监控（最近 100/200/300/500 期 · CUSUM）</h4>'
            + baseline_note
            + '<table><thead><tr><th>框架</th><th>近100</th><th>近200</th><th>近300</th><th>近500</th>'
              '<th>CUSUM 峰</th><th>状态</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table>'
            + '<div class="hb-foot">漂移监控口径：z 相对该框架自身的逐期精确随机期望；'
              '预警 = 近200/300期同向 z≤-2 或 CUSUM峰≤-3σ；显著下漂 = 单窗 z≤-3 或 CUSUM峰≤-4σ；'
              'n&lt;100 样本不足。多窗口多重比较下 |z|≈2 属正常，本表只作漂移提示，不作模型优劣结论。</div>')


def _render_cumulative(cumulative, baseline):
    rows = []
    today = _today_map(baseline)
    for item in cumulative.get('rows') or ():
        key = item.get('key')
        rule = escape(str(item.get('rule') or ''))
        window = escape(str(item.get('window') or ''))
        verdict = str(item.get('verdict') or '-')
        lo, hi = (item.get('ci95') or [None, None])[:2]
        model_split = item.get('model_split') or []
        model_text = ''
        if model_split:
            model_text = '<div class="hb-foot">模型：' + ' · '.join(
                f"{escape(str(part.get('model')))} n={part.get('n')} {_pct(part.get('rate'))}"
                for part in model_split) + '</div>'
        history = item.get('history') or []
        history_text = ''
        if history:
            history_text = '<div class="hb-foot">历史版本(不混算)：' + ' · '.join(
                f"{escape(str(part.get('revision')))} n={part.get('n')} {_pct(part.get('rate'))}"
                for part in history) + '</div>'
        post_optimized = item.get('post_optimized_n')
        boundary_text = ''
        if isinstance(post_optimized, int) and not isinstance(post_optimized, bool):
            boundary_text = ('<div class="hb-foot">口径分界后样本 %d 期（target 槽位晚于转正时刻，'
                             '形态改用联合优化）</div>' % post_optimized)
        planned_rate = item.get('planned_hit_rate')
        coverage = item.get('coverage')
        coverage_text = _pct(coverage) if coverage is not None else '-'
        planned_text = "<div class=\"hb-foot\">计划期命中 " + _pct(planned_rate) + "</div>" if planned_rate is not None else ""
        today_row = today.get(key) or {}
        today_rate = today_row.get('rate')
        today_n = today_row.get('n')
        today_text = f"{_pct(today_rate)} ({today_n}期)" if isinstance(today_rate, (int, float)) else '-'
        rows.append(
            f"<tr><td><b>{escape(str(item.get('label') or key))}</b>"
            f"<div class=\"hb-foot\">{rule} · {window} · {escape(str(item.get('first_period') or ''))}→{escape(str(item.get('last_period') or ''))}</div>"
            f"{model_text}{history_text}{boundary_text}</td>"
            f"<td>{item.get('items') if item.get('items') is not None else '-'}</td>"
            f"<td>{item.get('n', 0)}</td>"
            f"<td><span class=\"hb-big\">{_pct(item.get('rate'))}</span>"
            f"<div class=\"hb-sub\">[{_pct(lo)}, {_pct(hi)}]</div></td>"
            f"<td>{_pct(item.get('expected'))}</td>"
            f"<td>{_delta(item.get('delta'))}</td>"
            f"<td>{_p(item.get('p'))}</td>"
            f"<td class=\"{_verdict_class(verdict)}\">{escape(verdict)}</td>"
            f"<td>{coverage_text}{planned_text}</td>"
            f"<td>{today_text}</td></tr>")
    singles = ' · '.join(f"{escape(str(row['label']))} {_pct(row['baseline'])}"
                         for row in (baseline or {}).get('single_baselines') or ())
    stale_warning = ''
    if not _cumulative_fresh(cumulative):
        stale_warning = ('<div class="hb-note">累计报告超过 %d 分钟未更新（生成于 %s），'
                         '以下为上一次结果；正在等待脚本刷新。</div>' % (STALE_MINUTES, escape(str(cumulative.get('generated_at') or '-'))))
    boundary = cumulative.get('joint_form_boundary') or {}
    boundary_note = ''
    if isinstance(boundary, dict) and boundary.get('promoted_at'):
        boundary_note = ('<div class="hb-note">口径分界：%s 起 framework/killer 的 2 个反向形态改为联合优化形态'
                         '（主判断不变）；该时刻前后不是同一形态口径，跨分界累计趋势需谨慎解读。</div>'
                         % escape(str(boundary.get('promoted_at'))))
    as_of = escape(str(cumulative.get('as_of_period') or (baseline or {}).get('as_of_period') or ''))
    basis = escape(str(cumulative.get('basis') or (baseline or {}).get('basis') or ''))
    drift_html = _render_drift(cumulative)
    return (
        f'{START}<section id="hitrate-base" data-as-of="{as_of}">'
        f'<style>{STYLE}</style>'
        f'<h3>整组命中率 vs 随机期望（累计·版本锁定）</h3>'
        f'<div class="hb-meta">累计更新 {escape(str(cumulative.get("generated_at") or "-"))} · '
        f'数据截至 {as_of} · 只统计已核验样本 · 随机期望：{basis}</div>'
        f'{stale_warning}{boundary_note}'
        f'<table><thead><tr><th>框架 / 规则</th><th>项数</th><th>累计样本</th>'
        f'<th>累计命中率(95%CI)</th><th>随机期望</th><th>偏差</th><th>p</th><th>判定</th>'
        f'<th>覆盖率</th><th>今日(参考)</th></tr></thead><tbody>{"".join(rows)}</tbody></table>'
        f'{drift_html}'
        f'<div class="hb-note">整组命中率是「一期 N 项全部命中」的联合概率，不是单项准确率；'
        f'7~9 项框架的随机期望本来就只有 41%~62%。累计样本 n&lt;200 时只报数字、不下结论。'
        f'覆盖率 = 当前版本已核验期数 / 首期到末期计划期数；计划期命中率把漏发期按 0 命中计入，'
        f'用于暴露 402/停机造成的真实损失——稳定性问题会直接压低这个值，而不是提高命中率数字。</div>'
        f'<div class="hb-foot">单项随机基线：{singles}。</div>'
        f'<div class="hb-foot">单尾杀 1 码的 90% 是数学上限（10 选 1 杀 1 码）；单日 n&lt;200 一律标「样本不足」，只做波动参考，请以累计样本为准。</div>'
        f'<div class="hb-foot">随机期望按现行规则用 10^4 种开奖组合精确枚举校准（已计入合尾/形态/保留码共享同一组开奖号的相关性，'
        f'不是简单相乘）；换规则版本即重置累计，旧版本样本单独列在框架格内且不混算。</div>'
        f'<div class="hb-foot">「今日」列只做波动参考（约 10 期），不参与判定。'
        f'不同项数的框架不能按整组百分比横向排名。</div>'
        f'<div class="hb-foot"><a href="/accuracy" target="_blank" rel="noopener">→ 打开命中率看板（逐日明细）</a></div>'
        f'</section>{END}')


def _render_baseline(report):
    """累计报告不可用时的旧版渲染（今日 10 期）。"""
    if not report or not report.get('rows'):
        return (f'{START}<section id="hitrate-base"><h3>整组命中率 vs 随机期望</h3>'
                f'<div class="hb-meta">报告尚未生成</div></section>{END}')
    rows = []
    for item in report['rows']:
        verdict = str(item.get('verdict') or '-')
        rows.append(
            f"<tr><td>{escape(str(item.get('label') or item.get('key')))}"
            f"<div class=\"hb-foot\">{escape(str(item.get('rule') or ''))}</div></td>"
            f"<td>{item.get('items') if item.get('items') is not None else '-'}</td>"
            f"<td>{item.get('n', 0)}</td>"
            f"<td><b>{_pct(item.get('rate'))}</b></td>"
            f"<td>{_pct(item.get('expected'))}</td>"
            f"<td>{_delta(item.get('delta'))}</td>"
            f"<td>{_p(item.get('p'))}</td>"
            f"<td class=\"{_verdict_class(verdict)}\">{escape(verdict)}</td></tr>")
    singles = ' · '.join(f"{escape(str(row['label']))} {_pct(row['baseline'])}"
                         for row in report.get('single_baselines') or ())
    return (
        f'{START}<section id="hitrate-base" data-as-of="{escape(str(report.get("as_of_period") or ""))}">'
        f'<style>{STYLE}</style>'
        f'<h3>整组命中率 vs 随机期望（今日）</h3>'
        f'<div class="hb-meta">业务日 {escape(str(report.get("biz_date") or "-"))} · '
        f'只统计已核验样本 · 随机期望来源：{escape(str(report.get("basis") or ""))}</div>'
        f'<table><thead><tr><th>框架 / 规则</th><th>项目数</th><th>样本</th>'
        f'<th>实测整组</th><th>随机期望</th><th>偏差</th><th>p</th><th>判定</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
        f'<div class="hb-note">整组命中率是「一期 N 项全部命中」的联合概率，不是单项准确率。'
        f'7~9 项框架的随机期望本来就只有 41%~62%，所以面板上的 40%~60% ≠ 模型退化。</div>'
        f'<div class="hb-foot">单项随机基线：{singles}。</div>'
        f'<div class="hb-foot">单尾杀 1 码的 90% 是数学上限（10 选 1 杀 1 码）；单日 n&lt;200 一律标「样本不足」，只做波动参考，请以累计样本为准。</div>'
        f'<div class="hb-foot">随机期望按现行规则用 10^4 种开奖组合精确枚举校准（已计入合尾/形态/保留码共享同一组开奖号的相关性，'
        f'不是简单相乘）；换规则版本即重置校准，未知版本显示「基线待校准」。</div>'
        f'<div class="hb-foot">缺预测 / 待核验 / 冲突期不进分母；'
        f'样本 &lt;200 或 p&gt;0.05 时不能判定优于或劣于随机。不同项数的框架不能按整组百分比横向排名。</div>'
        f'<div class="hb-foot"><a href="/accuracy" target="_blank" rel="noopener">→ 打开命中率看板（逐日明细）</a></div>'
        f'</section>{END}')


def render_block(report=None):
    cumulative = _load(CUMULATIVE_PATH)
    baseline = report if isinstance(report, dict) else _load(BASELINE_PATH)
    if cumulative and cumulative.get('rows'):
        return _render_cumulative(cumulative, baseline)
    return _render_baseline(baseline)


def apply_hitrate_baseline_block(content, report=None):
    """原地替换/插入「整组命中率」块；任何异常原样返回。"""
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
