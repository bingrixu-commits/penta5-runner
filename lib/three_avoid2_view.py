# -*- coding: utf-8 -*-
"""面板区块「三数共现避开3组 · 业务日核验」（高基线过滤项）。

口径（写死，避免误读）：
- 命中 = 模型给出的 3 个数字【未全部】出现在 4 球开奖号中（避开成功）；
- 随机基线 = 97.96%（10^4 精确枚举，与 /accuracy EXACT_BASELINES 同值）；
- 业务日 = 北京时间 05:00 切日；高基线通过率 ≠ 预测能力。

数据源 data/three_avoid2_daily.json（lib/three_avoid2_stats.py 每期结算后重算，
历史按日保留于 data/three_avoid2_history.json）。
自包含 section + 内联 style；<!-- THREE_AVOID2_START/END --> 标记原地替换；
标记缺失时回退插入到 <!-- HITRATE_BASE_END --> 之后；任何异常原样返回 html。
"""
import json
from html import escape as _esc
from pathlib import Path

import three_avoid2_stats as stats

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data' / 'three_avoid2_daily.json'
MARKER = 'THREE_AVOID2'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'

STYLE = """
#three-avoid2{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;
  padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#1e293b;
  font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#three-avoid2 *{box-sizing:border-box}
#three-avoid2 h3{font-size:17px;color:#1e3a8a;margin:0 0 4px}
#three-avoid2 .t3-meta{font-size:12.5px;color:#64748b;margin-bottom:8px}
#three-avoid2 table{width:100%;border-collapse:collapse;font-size:13px;color:#334155}
#three-avoid2 th,#three-avoid2 td{padding:7px 9px;text-align:left;border-top:1px dashed #c7d2fe;vertical-align:top}
#three-avoid2 th{color:#475569;font-weight:700;font-size:12px;background:rgba(99,102,241,.06)}
#three-avoid2 td.rate{font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
#three-avoid2 td.rate.ok{color:#15803d}
#three-avoid2 td.rate.low{color:#b45309}
#three-avoid2 .t3-badge{display:inline-block;margin-left:6px;padding:1px 7px;border:1px solid #f59e0b;
  border-radius:999px;font-size:11px;color:#92400e;background:#fffbeb;vertical-align:2px}
#three-avoid2 .t3-foot{font-size:11.5px;color:#64748b;line-height:1.55;margin-top:8px}
#three-avoid2 .t3-codes{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:8px 0}
#three-avoid2 .t3-code-label{font-size:12.5px;color:#64748b;font-weight:700}
#three-avoid2 .t3-code{font-size:26px;font-weight:900;letter-spacing:6px;color:#1e3a8a;background:#eef2ff;
  border:1px solid #c7d2fe;border-radius:10px;padding:4px 14px;font-variant-numeric:tabular-nums}
#three-avoid2 .t3-code.ok{border-color:#86efac;background:#f0fdf4;color:#166534}
#three-avoid2 .t3-code.miss{border-color:#fca5a5;background:#fef2f2;color:#991b1b}
#three-avoid2 .t3-code.pending{border-style:dashed}
#three-avoid2 .t3-code-note{font-size:12.5px;color:#475569}
"""


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _pct(value):
    return ('%.2f%%' % value) if isinstance(value, (int, float)) else '—'


def _delta(value):
    return ('%+.2fpp' % value) if isinstance(value, (int, float)) else '—'


def _row(day_text, entry, sealed_text):
    if not isinstance(entry, dict) or not entry.get('total'):
        return ('<tr><td>%s</td><td class="rate">—</td><td>0/0</td><td>—</td>'
                '<td>暂无已核验记录，等待开奖结算</td></tr>' % day_text)
    cls = 'ok' if not entry.get('sample_low') else 'low'
    note = ('样本不足(n&lt;%d)' % stats.MIN_SAMPLE) if entry.get('sample_low') else '已核验'
    if sealed_text:
        note = '%s · %s' % (sealed_text, note)
    return ('<tr><td>%s</td><td class="rate %s">%s</td><td>%d/%d</td><td>%s</td><td>%s</td></tr>'
            % (day_text, cls, _pct(entry.get('rate')), entry.get('hit', 0), entry.get('total', 0),
               _delta(entry.get('delta')), note))


def render_block(report=None):
    report = report if report is not None else _load()
    if not isinstance(report, dict) or not report.get('business_day'):
        return (f'{START}<section id="three-avoid2"><h3>三数共现避开3组 · 业务日核验</h3>'
                f'<div class="t3-meta">统计尚未生成，等待下一轮面板重建</div></section>{END}')
    current = report.get('current') or {}
    previous = report.get('previous') or {}
    sealed_text = '已封盘' if previous.get('sealed') else '已结束'
    codes = ''
    latest = report.get('latest_value') or {}
    pending = report.get('pending_value') or {}
    if isinstance(pending, dict) and pending.get('value'):
        codes += ('<div class="t3-codes"><span class="t3-code-label">下一期 %s · 待开奖核验</span>'
                  '<span class="t3-code pending">%s</span></div>'
                  % (pending.get('period') or '—', _esc(pending['value'])))
    if isinstance(latest, dict) and latest.get('value'):
        outcome = '避开成功 ✓' if latest.get('success') else '未避开 ✗'
        actual = ' · '.join(str(item) for item in (latest.get('actual') or [])[:4])
        codes += ('<div class="t3-codes"><span class="t3-code-label">最新一期 %s · 已核验</span>'
                  '<span class="t3-code %s">%s</span><span class="t3-code-note">开奖 %s · %s</span></div>'
                  % (latest.get('period') or '—', 'ok' if latest.get('success') else 'miss',
                     _esc(latest['value']), actual, outcome))
    rows = _row('本业务日 %s（进行中）' % report['business_day'], current, None)
    rows += _row('上一业务日 %s（%s）' % (previous.get('business_day') or '—', sealed_text),
                 previous, None)
    return (
        f'{START}<section id="three-avoid2" data-as-of="{report.get("as_of_period") or ""}" '
        f'data-business-day="{report.get("business_day") or ""}">'
        f'<style>{STYLE}</style>'
        f'<h3>🧪 三数共现避开3组 · 业务日核验<span class="t3-badge">高基线过滤项</span></h3>'
        f'<div class="t3-meta">命中口径：模型给出的 3 个数字未在 4 球开奖号中全部出现'
        f'（避开成功）· 随机基线 97.96%（10^4 精确枚举）· 高基线通过率 ≠ 预测能力</div>'
        f'{codes}'
        f'<table><thead><tr><th>口径</th><th>命中率</th><th>命中/样本</th><th>与基线差</th>'
        f'<th>说明</th></tr></thead><tbody>{rows}</tbody></table>'
        f'<div class="t3-foot">数据截至期号 {report.get("as_of_period") or "—"}'
        f'（{report.get("as_of_time") or "—"}）· 由 lib/three_avoid2_stats.py 从 '
        f'data/state.json 已校验预测逐期结算 · 每期开奖结算后面板自动重建 · '
        f'样本不足标准 n&lt;{stats.MIN_SAMPLE}</div>'
        f'</section>{END}'
    )


def apply_three_avoid2_block(content):
    """把「三数共现避开3组」区块写入面板；任何异常原样返回。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        try:
            stats.compute()  # 每次面板重建先刷新派生数据（异常不阻断渲染旧值）
        except Exception:
            pass
        block = render_block()
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + block + content[end + len(END):]
        anchor = '<!-- HITRATE_BASE_END -->'
        position = content.find(anchor)
        if position != -1:
            position += len(anchor)
            return content[:position] + block + content[position:]
        return content
    except Exception:
        return content


if __name__ == '__main__':
    print(render_block())
