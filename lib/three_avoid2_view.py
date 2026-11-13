# -*- coding: utf-8 -*-
"""面板区块「三数共现避开 1/2/3/4 组 · 业务日核验」（高基线过滤项）。

口径（写死，避免误读）：
- 四组兄弟项：避开1组/避开2组/避开3组/避开4组（参考），每期各给 1 组 3 个数字
  并要求避开；第 4 组只做展示与单组统计，不进入 kill1/kill3 的攻击语与整组命中；
- 单项命中 = 该组 3 个数字【未全部】出现在 4 球开奖号中（避开成功），单项基线 97.96%；
- 三组合并（历史口径）= 同一期前 3 组【全部】避开成功（合取）；
- 四组合并（现行口径）= 同一期 4 组【全部】避开成功（第 4 组缺失不计入）；
- 合并基线 = 每期按该期各组号码 10^4 开奖组合精确枚举再按期平均；
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
START = '<!-- THREE_AVOID2_START -->'
END = '<!-- THREE_AVOID2_END -->'

STYLE = """
#three-avoid2{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;
  padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#1e293b;
  font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#three-avoid2 *{box-sizing:border-box}
#three-avoid2 h3{font-size:17px;color:#1e3a8a;margin:0 0 4px}
#three-avoid2 h4{font-size:14px;color:#3730a3;margin:12px 0 4px}
#three-avoid2 .t3-meta{font-size:12.5px;color:#64748b;margin-bottom:8px}
#three-avoid2 .t3-codes{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:8px 0}
#three-avoid2 .t3-code-label{font-size:12.5px;color:#64748b;font-weight:700}
#three-avoid2 .t3-code{font-size:24px;font-weight:900;letter-spacing:5px;color:#1e3a8a;background:#eef2ff;
  border:1px solid #c7d2fe;border-radius:10px;padding:3px 12px;font-variant-numeric:tabular-nums}
#three-avoid2 .t3-code small{font-size:11px;font-weight:700;letter-spacing:0;color:#64748b;margin-right:6px}
#three-avoid2 .t3-code.ok{border-color:#86efac;background:#f0fdf4;color:#166534}
#three-avoid2 .t3-code.miss{border-color:#fca5a5;background:#fef2f2;color:#991b1b}
#three-avoid2 .t3-code.pending{border-style:dashed}
#three-avoid2 .t3-code-note{font-size:12.5px;color:#475569}
#three-avoid2 table{width:100%;border-collapse:collapse;font-size:13px;color:#334155}
#three-avoid2 th,#three-avoid2 td{padding:7px 9px;text-align:left;border-top:1px dashed #c7d2fe;vertical-align:top}
#three-avoid2 th{color:#475569;font-weight:700;font-size:12px;background:rgba(99,102,241,.06)}
#three-avoid2 td.rate{font-weight:800;font-variant-numeric:tabular-nums;white-space:nowrap}
#three-avoid2 td.rate.ok{color:#15803d}
#three-avoid2 td.rate.low{color:#b45309}
#three-avoid2 td.rate.na{color:#94a3b8}
#three-avoid2 tr.t3-divider td{background:#eef2ff;color:#3730a3;font-weight:800;font-size:12.5px}
#three-avoid2 .t3-badge{display:inline-block;margin-left:6px;padding:1px 7px;border:1px solid #f59e0b;
  border-radius:999px;font-size:11px;color:#92400e;background:#fffbeb;vertical-align:2px}
#three-avoid2 .t3-foot{font-size:11.5px;color:#64748b;line-height:1.55;margin-top:8px}
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


def _code_chip(group, cls=''):
    value = group.get('value')
    label = group.get('label') or group.get('key') or '—'
    if not value:
        return '<span class="t3-code %s"><small>%s</small>—</span>' % (cls, _esc(label))
    return ('<span class="t3-code %s"><small>%s</small>%s</span>'
            % (cls, _esc(label), _esc(str(value))))


def _codes(report):
    codes = ''
    pending = report.get('pending_groups')
    if isinstance(pending, dict) and (pending.get('groups') or {}):
        chips = ''.join(_code_chip(group, 'pending') for group in pending['groups'].values())
        note = '' if pending.get('merged4_ready', True) else '<span class="t3-code-note">第4组待本期发布</span>'
        codes += ('<div class="t3-codes"><span class="t3-code-label">下一期 %s · 待开奖核验</span>%s%s</div>'
                  % (pending.get('period') or '—', chips, note))
    latest = report.get('latest_groups')
    if isinstance(latest, dict) and (latest.get('groups') or {}):
        chips = ''.join(_code_chip(group, 'ok' if group.get('success') else 'miss')
                        for group in latest['groups'].values())
        if latest.get('merged4_available'):
            merged = '四组全避开 ✓' if latest.get('merged4_success') else '未全避开 ✗'
        else:
            merged = '第4组未发布'
        actual = ' · '.join(str(item) for item in (latest.get('actual') or [])[:4])
        codes += ('<div class="t3-codes"><span class="t3-code-label">最新一期 %s · 已核验</span>%s'
                  '<span class="t3-code-note">开奖 %s · 四组合并：%s</span></div>'
                  % (latest.get('period') or '—', chips, actual, merged))
    return codes


def _row(day_text, entry, sealed_text=None, note_text=None):
    if not isinstance(entry, dict) or not entry.get('total'):
        note = note_text or '暂无已核验记录，等待开奖结算'
        return ('<tr><td>%s</td><td class="rate na">—</td><td>0/0</td><td>—</td><td>—</td>'
                '<td>%s</td></tr>' % (day_text, note))
    cls = 'ok' if not entry.get('sample_low') else 'low'
    note = ('样本不足(n&lt;%d)' % stats.MIN_SAMPLE) if entry.get('sample_low') else '已核验'
    if sealed_text:
        note = '%s · %s' % (sealed_text, note)
    if note_text:
        note = '%s · %s' % (note_text, note)
    return ('<tr><td>%s</td><td class="rate %s">%s</td><td>%d/%d</td><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (day_text, cls, _pct(entry.get('rate')), entry.get('hit', 0), entry.get('total', 0),
               _pct(entry.get('baseline')), _delta(entry.get('delta')), note))


def _group_rows(report):
    labels = report.get('group_labels') or dict(stats.GROUP_LABELS)
    keys = report.get('group_keys') or list(stats.GROUP_KEYS)
    groups_current = report.get('groups_current') or {}
    groups_previous = report.get('groups_previous') or {}
    previous_day = (report.get('previous') or {}).get('business_day') or '—'
    rows = '<tr class="t3-divider"><td colspan="6">本业务日 %s（进行中）</td></tr>' % _esc(
        str(report.get('business_day') or '—'))
    for key in keys:
        entry = groups_current.get(key) or {}
        label = labels.get(key) or entry.get('label') or key
        note = '第4组自 2026-11-13 起上线' if key == 'threeDigitAvoid3' else None
        rows += _row('%s（单项）' % _esc(label), entry, note_text=note)
    rows += ('<tr class="t3-divider"><td colspan="6">上一业务日 %s（已封盘/已结束）</td></tr>'
             % _esc(str(previous_day)))
    for key in keys:
        entry = groups_previous.get(key) or {}
        label = labels.get(key) or entry.get('label') or key
        note = '第4组此时尚未上线' if key == 'threeDigitAvoid3' else None
        rows += _row('%s（单项）' % _esc(label), entry, note_text=note)
    return rows


def _merged_rows(report):
    previous = report.get('previous') or {}
    sealed_text = '已封盘' if previous.get('sealed') else '已结束'
    merged4_current = report.get('merged4_current') or {}
    merged4_previous = report.get('merged4_previous') or {}
    rows = _row('四组合并（1+2+3+4 全避开）· 本业务日 %s（进行中）'
                % (report.get('business_day') or '—'), merged4_current,
                note_text='第4组上线后开始累计')
    rows += _row('四组合并（1+2+3+4 全避开）· 上一业务日 %s（%s）'
                 % (previous.get('business_day') or '—', sealed_text), merged4_previous,
                 note_text='第4组此时尚未上线')
    rows += _row('三组合并（1+2+3，历史口径）· 本业务日 %s（进行中）'
                 % (report.get('business_day') or '—'), report.get('current') or {})
    rows += _row('三组合并（1+2+3，历史口径）· 上一业务日 %s（%s）'
                 % (previous.get('business_day') or '—', sealed_text), previous)
    return rows


def render_block(report=None):
    report = report if report is not None else _load()
    if not isinstance(report, dict) or not report.get('business_day'):
        return (f'{START}<section id="three-avoid2"><h3>三数共现避开 1/2/3/4 组 · 业务日核验</h3>'
                f'<div class="t3-meta">统计尚未生成，等待下一轮面板重建</div></section>{END}')
    codes = _codes(report)
    return (
        f'{START}<section id="three-avoid2" data-as-of="{report.get("as_of_period") or ""}" '
        f'data-business-day="{report.get("business_day") or ""}">'
        f'<style>{STYLE}</style>'
        f'<h3>🧪 三数共现避开 1/2/3/4 组 · 业务日核验<span class="t3-badge">高基线过滤项</span></h3>'
        f'<div class="t3-meta">单项：该组 3 个数字未在 4 球开奖号中全部出现（避开成功）· '
        f'四组合并：同一期 4 组全部避开成功才记命中（第4组缺失不计入）· '
        f'三组合并为历史口径（1+2+3）· 合并基线按每期各组号码 10^4 精确枚举按期平均 · '
        f'高基线通过率 ≠ 预测能力</div>'
        f'{codes}'
        f'<h4>单组统计（每组各算 1 项 · 单项基线 97.96%）</h4>'
        f'<table><thead><tr><th>口径</th><th>命中率</th><th>命中/样本</th><th>基线</th><th>与基线差</th>'
        f'<th>说明</th></tr></thead><tbody>{_group_rows(report)}</tbody></table>'
        f'<h4>合并口径（同组全避开才算命中）</h4>'
        f'<table><thead><tr><th>口径</th><th>命中率</th><th>命中/样本</th><th>合并基线</th><th>与基线差</th>'
        f'<th>说明</th></tr></thead><tbody>{_merged_rows(report)}</tbody></table>'
        f'<div class="t3-foot">第4组为 2026-11-13 新增的面板参考组，只做展示与单组统计，'
        f'不进入 kill1/kill3 的攻击语与整组命中 · '
        f'数据截至期号 {report.get("as_of_period") or "—"}（{report.get("as_of_time") or "—"}）'
        f' · 由 lib/three_avoid2_stats.py 从 data/state.json 已校验预测逐期结算'
        f' · 每期开奖结算后面板自动重建 · 样本不足标准 n&lt;{stats.MIN_SAMPLE}</div>'
        f'</section>{END}'
    )


def apply_three_avoid2_block(content):
    """把「三数共现避开 1/2/3/4 组」区块写入面板；任何异常原样返回。"""
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
