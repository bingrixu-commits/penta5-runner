# -*- coding: utf-8 -*-
"""面板顶部「三数共现 · 4组避开」精简卡片。

只读 data/three_avoid2_daily.json，与下方 THREE_AVOID2 详细核验区块同源。
卡片放在面板最上方（刮刮乐之后、R1 智能深排之前），显示：
- 下一期 4 组号码（待开奖核验）；
- 最新一期 4 组号码与逐组 ✓/✗；
- 本业务日 4 组各自的单组命中率 / 命中样本。

第 4 组为 2026-11-13 新增的参考组，只做展示与单组统计，不进入 kill1/kill3 的
攻击语与整组命中。<!-- THREE_AVOID2_TOP_START/END --> 标记原地替换；标记缺失时
插入到 <!-- R1_KILLER_START --> 之前。任何异常原样返回 html。
"""
import json
from html import escape as _esc
from pathlib import Path

import three_avoid2_stats as stats

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data' / 'three_avoid2_daily.json'
START = '<!-- THREE_AVOID2_TOP_START -->'
END = '<!-- THREE_AVOID2_TOP_END -->'

STYLE = """
#three-avoid2-top{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;
  padding:12px 14px;border:1px solid #a5b4fc;border-radius:12px;background:linear-gradient(180deg,#eef2ff 0%,#f8faff 100%);
  color:#1e293b;font:14px/1.55 sans-serif;min-width:0;overflow-wrap:anywhere}
#three-avoid2-top *{box-sizing:border-box}
#three-avoid2-top .t3t-head{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;margin-bottom:6px}
#three-avoid2-top .t3t-title{font-size:16px;font-weight:900;color:#1e3a8a}
#three-avoid2-top .t3t-sub{font-size:12px;color:#64748b}
#three-avoid2-top .t3t-link{margin-left:auto;font-size:12.5px;color:#1d4ed8;text-decoration:none;font-weight:700}
#three-avoid2-top .t3t-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0}
#three-avoid2-top .t3t-label{font-size:12.5px;color:#475569;font-weight:700;min-width:168px}
#three-avoid2-top .t3t-chip{display:inline-flex;align-items:baseline;gap:6px;padding:2px 9px;
  border:1px solid #c7d2fe;border-radius:9px;background:#fff;font-variant-numeric:tabular-nums}
#three-avoid2-top .t3t-chip small{font-size:11px;color:#64748b;font-weight:700}
#three-avoid2-top .t3t-chip b{font-size:22px;letter-spacing:3px;color:#1e3a8a;line-height:1.2}
#three-avoid2-top .t3t-chip.ok{border-color:#86efac;background:#f0fdf4}
#three-avoid2-top .t3t-chip.ok b{color:#166534}
#three-avoid2-top .t3t-chip.miss{border-color:#fca5a5;background:#fef2f2}
#three-avoid2-top .t3t-chip.miss b{color:#991b1b}
#three-avoid2-top .t3t-chip.na b{color:#94a3b8}
#three-avoid2-top .t3t-note{font-size:12px;color:#92400e}
#three-avoid2-top .t3t-stats{display:flex;flex-wrap:wrap;gap:14px;margin-top:8px;padding-top:8px;
  border-top:1px dashed #c7d2fe;font-size:13px;color:#334155}
#three-avoid2-top .t3t-stat{display:inline-flex;align-items:baseline;gap:5px;white-space:nowrap}
#three-avoid2-top .t3t-stat em{font-style:normal;color:#64748b;font-size:12px}
#three-avoid2-top .t3t-stat b{font-size:15px;color:#1e3a8a;font-variant-numeric:tabular-nums}
#three-avoid2-top .t3t-foot{font-size:11.5px;color:#64748b;margin-top:6px}
"""


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _pct(value):
    return ('%.2f%%' % value) if isinstance(value, (int, float)) else '—'


def _chip(group, cls=''):
    value = group.get('value')
    label = group.get('label') or group.get('key') or '—'
    if not value:
        return ('<span class="t3t-chip na"><small>%s</small><b>—</b></span>'
                % _esc(str(label)))
    return ('<span class="t3t-chip %s"><small>%s</small><b>%s</b></span>'
            % (cls, _esc(str(label)), _esc(str(value))))


def _codes(report):
    rows = ''
    pending = report.get('pending_groups')
    if isinstance(pending, dict) and (pending.get('groups') or {}):
        chips = ''.join(_chip(group, '') for group in pending['groups'].values())
        note = '' if pending.get('merged4_ready', True) else '<span class="t3t-note">第4组待本期发布</span>'
        rows += ('<div class="t3t-row"><span class="t3t-label">下一期 %s · 待核验</span>%s%s</div>'
                 % (_esc(str(pending.get('period') or '—')), chips, note))
    latest = report.get('latest_groups')
    if isinstance(latest, dict) and (latest.get('groups') or {}):
        chips = ''.join(_chip(group, 'ok' if group.get('success') else 'miss')
                        for group in latest['groups'].values())
        actual = ' · '.join(str(item) for item in (latest.get('actual') or [])[:4])
        merged = ('四组全避开 ✓' if latest.get('merged4_success') else '未全避开 ✗') \
            if latest.get('merged4_available') else '第4组未发布'
        rows += ('<div class="t3t-row"><span class="t3t-label">最新 %s · 开奖 %s</span>%s'
                 '<span class="t3t-note">%s</span></div>'
                 % (_esc(str(latest.get('period') or '—')), _esc(actual or '—'), chips, merged))
    return rows


def _stats(report):
    labels = report.get('group_labels') or dict(stats.GROUP_LABELS)
    keys = report.get('group_keys') or list(stats.GROUP_KEYS)
    groups = report.get('groups_current') or {}
    parts = []
    for key in keys:
        entry = groups.get(key) or {}
        label = labels.get(key) or entry.get('label') or key
        rate = _pct(entry.get('rate')) if entry.get('total') else '—'
        total = entry.get('total') or 0
        hit = entry.get('hit') or 0
        sample = ('%d/%d' % (hit, total)) if total else '暂无样本'
        parts.append('<span class="t3t-stat"><em>%s</em><b>%s</b><small>%s</small></span>'
                     % (_esc(str(label)), _esc(rate), _esc(sample)))
    return '<div class="t3t-stats">%s</div>' % ''.join(parts)


def render_top_block(report=None):
    report = report if report is not None else _load()
    if not isinstance(report, dict) or not report.get('business_day'):
        return ''
    codes = _codes(report)
    if not codes:
        return ''
    return (
        f'{START}<section id="three-avoid2-top" data-as-of="{report.get("as_of_period") or ""}" '
        f'data-business-day="{report.get("business_day") or ""}">'
        f'<style>{STYLE}</style>'
        f'<div class="t3t-head">'
        f'<span class="t3t-title">🔎 三数共现 · 4组避开</span>'
        f'<span class="t3t-sub">每组 3 码 · 单项基线 97.96% · 第4组为参考项，不计入 kill1/kill3 整组</span>'
        f'<a class="t3t-link" href="#three-avoid2">详细核验 ↓</a>'
        f'</div>'
        f'{codes}'
        f'{_stats(report)}'
        f'<div class="t3t-foot">单组命中 = 该组 3 个数字未在 4 球开奖号中全部出现；'
        f'本业务日按北京时间 05:00 切日 · 数据截至期号 {report.get("as_of_period") or "—"}</div>'
        f'</section>{END}'
    )


def apply_three_avoid2_top_block(content):
    """把顶部精简卡片写入面板；标记存在则原地替换，否则插到 R1 killer 之前。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        block = render_top_block()
        if not block:
            return content
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + block + content[end + len(END):]
        anchor = '<!-- R1_KILLER_START -->'
        position = content.find(anchor)
        if position != -1:
            return content[:position] + block + '\n' + content[position:]
        return content
    except Exception:
        return content


if __name__ == '__main__':
    print(render_top_block())
