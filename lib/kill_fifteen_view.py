"""分位杀号（kill15 独立框架，v4）面板区块。

数据源 data/kill15_tail/{seals,settlements}.json，由 scripts/run_kill15_tail.py 每期生成。
与既有框架完全独立：独立封存 / 结算 / 统计 / 周期台账 / 抽样复盘 / 面板区块。

命中口径（写死，面板脚注同步展示）：
- 四个号位（千/百/十/个）每期各杀 1 码（0-9），整组固定 4 项全部计入整组；
- A 组 = 遗漏系，B 组 = 对冲系（B1 热码杀 / B2 反弹杀 / B3 形态对冲）；
- A 组号位上限由盘面与滚动 Hit-A/Hit-B 决定（冷反弹活跃=1，否则 2，命中率异常再压 1）；
- v4：每 40 期为一轮，跨轮清空历史胜率偏好；B 子策略每期强制轮换；
  每 30 期自动抽样复盘一次，核对封存/结算与原始开奖（reviews.json）；
- 命中 = 该号位实际开奖号 != 该号位杀码（单项随机 90%）；
- 整组命中 = 4 项同时命中；整组随机基线 65.61%（(9/10)^4，10^4 开奖空间精确枚举）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。
"""
from html import escape
from pathlib import Path

import kill15_tail

START = '<!-- KILL_FIFTEEN_START -->'
END = '<!-- KILL_FIFTEEN_END -->'

PANEL_ID = 'kill-fifteen-ai'
TITLE = '🎯 分位杀号（四位置各杀 1 码 · A/B 配比 · AI 每期选码）'

STYLE = """
#kill-fifteen-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #a7f3d0;border-radius:12px;background:#f0fdf4;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-fifteen-ai .k15-title{margin:0 0 8px;font-size:17px;font-weight:700;color:#065f46}
#kill-fifteen-ai .k15-meta{margin:0 0 10px;padding:7px 10px;border-radius:8px;background:#d1fae5;color:#065f46;font-size:13px}
#kill-fifteen-ai .k15-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:10px 0}
#kill-fifteen-ai .k15-card{padding:10px 12px;border:1px solid #a7f3d0;border-radius:10px;background:#fff;box-shadow:0 1px 2px rgba(6,95,70,.06)}
#kill-fifteen-ai .k15-head{font-size:13px;color:#065f46;font-weight:700}
#kill-fifteen-ai .k15-kill{margin:2px 0;font-size:26px;font-weight:800;color:#b91c1c;letter-spacing:2px}
#kill-fifteen-ai .k15-tag{font-size:12px;color:#047857;font-weight:600}
#kill-fifteen-ai .k15-note{font-size:12px;color:#475569}
#kill-fifteen-ai .k15-stats{margin:8px 0;padding:8px 10px;border-radius:8px;background:#ecfdf5;font-size:13px;color:#065f46}
#kill-fifteen-ai table{width:100%;border-collapse:collapse;margin:6px 0;font-size:12.5px}
#kill-fifteen-ai th,#kill-fifteen-ai td{padding:3px 6px;border-bottom:1px solid #d1fae5;text-align:left}
#kill-fifteen-ai .k15-foot{margin-top:8px;color:#64748b;font-size:12px;line-height:1.5}
"""


def _pct(value):
    if isinstance(value, (int, float)):
        return '%.1f%%' % (100 * value)
    return '—'


def _card(key, label, item):
    if not isinstance(item, dict):
        return ''
    group = item.get('group') or '-'
    sub = item.get('b_sub')
    tag = 'A 组 · 遗漏系' if group == 'A' else ('B 组 · %s' % sub if sub else 'B 组 · 对冲')
    omission = item.get('omission')
    hot = item.get('hot30')
    note = '遗漏 %s 期 ｜ 近30期 %s 次' % (omission, hot)
    return (
        '<div class="k15-card">'
        '<div class="k15-head">%s位</div>'
        '<div class="k15-kill">%s</div>'
        '<div class="k15-tag">%s</div>'
        '<div class="k15-note">%s</div>'
        '</div>'
    ) % (escape(str(label)), escape(str(item.get('kill'))), escape(str(tag)), escape(note))


def render_block():
    seals = kill15_tail.load_seals()
    settlements = kill15_tail.load_settlements()
    stats = kill15_tail.today_stats()
    try:
        reviews = kill15_tail.load_reviews()
    except (OSError, ValueError):
        reviews = []

    if not seals:
        return '%s<section id="%s"><h3 class="k15-title">%s</h3>' \
               '<p class="k15-note">暂无封存记录</p></section>%s' % (START, PANEL_ID, TITLE, END)

    seal = seals[-1]
    allocation = seal.get('allocation') or {}
    positions = seal.get('positions') or {}
    target = seal.get('target_period')
    reference = seal.get('ref_period')

    cards = ''.join(_card(key, label, positions.get(key))
                    for key, label, _index in kill15_tail.POSITIONS)
    a_text = '、'.join(kill15_tail.POSITION_LABELS.get(k, k) for k in allocation.get('a') or []) or '无'
    b_text = '、'.join('%s=%s' % (kill15_tail.POSITION_LABELS.get(k, k), v)
                       for k, v in (allocation.get('b_sub') or {}).items()) or '无'

    regime = seal.get('regime') or {}
    perf = seal.get('performance') or {}
    cycle = seal.get('cycle') or {}
    if cycle:
        cycle_text = '第%d轮 %d/%d期' % (int(cycle.get('cycle_no') or 0) + 1,
                                       int(cycle.get('cycle_pos') or 1),
                                       int(cycle.get('cycle_periods') or 40))
    else:
        cycle_text = '-'
    # 复盘状态只认 scheduled 记录（forced 为手动核对，不计入合规）；到期未跑明确提示。
    rule_version = kill15_tail.RULE_VERSION
    review_periods = int(kill15_tail.REVIEW_PERIODS)
    settled_total = len([row for row in settlements
                         if str(row.get('rule_version') or '') == rule_version])
    scheduled = {}
    for row in reviews:
        if str(row.get('trigger') or 'scheduled') != 'scheduled':
            continue
        if str(row.get('rule_version') or '') != rule_version:
            continue
        try:
            bucket = int(row.get('review_bucket'))
        except (TypeError, ValueError):
            continue
        if bucket >= 1:
            scheduled[bucket] = row
    expected = settled_total // review_periods
    missing = [bucket for bucket in range(1, expected + 1) if bucket not in scheduled]
    if missing:
        review_text = '复盘待执行（应 %d 次，缺第 %s 次）' % (
            expected, ','.join(str(bucket) for bucket in missing))
    elif scheduled:
        bucket = max(scheduled)
        ok = str(scheduled[bucket].get('status') or '') == 'ok'
        review_text = '复盘%s（第 %d 次，%s 期）' % (
            '通过' if ok else '异常', bucket,
            scheduled[bucket].get('reviewed_periods') or '?')
    else:
        remain = review_periods - (settled_total % review_periods)
        review_text = '复盘待到期（%d/%d 期，还差 %d 期）' % (
            settled_total, review_periods, remain)
    meta = ('参考期 %s → 目标期 %s ｜ 盘面 %s ｜ A %s ｜ B %s ｜ 周期 %s ｜ %s ｜ 规则 %s ｜ 状态 %s'
            % (reference, target, regime.get('label') or '-', a_text, b_text,
               cycle_text, review_text,
               seal.get('rule_version') or '-', seal.get('publication_state') or '-'))

    recent_rows = []
    for row in settlements[-5:]:
        pos = row.get('positions') or {}
        detail = ' '.join('%s%d→%d%s' % (kill15_tail.POSITION_LABELS.get(k, k),
                                         v.get('kill'), v.get('actual'),
                                         '✓' if v.get('hit') else '✗')
                          for k, v in pos.items() if isinstance(v, dict))
        recent_rows.append('<tr><td>%s</td><td>%s</td><td>%s</td></tr>'
                           % (escape(str(row.get('target_period'))),
                              'HIT' if row.get('group_hit') else 'miss', escape(detail)))
    recent_html = ''
    if recent_rows:
        recent_html = ('<table><thead><tr><th>目标期</th><th>整组</th><th>明细</th></tr></thead>'
                       '<tbody>%s</tbody></table>' % ''.join(recent_rows))

    stats_cycle = stats.get('cycle') or {}
    cycle_note = ''
    if stats_cycle:
        cycle_note = '｜ 本轮 第 %d/%d 期' % (int(stats_cycle.get('cycle_pos') or 1),
                                           int(stats_cycle.get('cycle_periods') or 40))
    # 全代际累计（版本升级后“本代际”会清零，需同时展示全部，避免看起来像坏了）
    all_total = len([row for row in settlements if isinstance(row.get('positions'), dict)])
    all_hits = sum(1 for row in settlements if row.get('group_hit') is True)
    all_html = ('｜ 全部代际 整组 %s（%d/%d）'
                % (_pct((all_hits / float(all_total)) if all_total else None), all_hits, all_total))
    stats_html = ('今日整组 %s（%s/%s）｜ 单码 %s（%s/%s）｜ 近15期 Hit-A %s ／ Hit-B %s ｜ 累计封存 %s 期%s%s'
                  % (_pct(stats.get('group_rate')), stats.get('group_hits'), stats.get('total'),
                     _pct((stats.get('position') or {}).get('rate')),
                     (stats.get('position') or {}).get('hits'),
                     (stats.get('position') or {}).get('bets'),
                     _pct(perf.get('hit_a_15')), _pct(perf.get('hit_b_15')),
                     len(seals), cycle_note, all_html))

    foot = ('口径：每号位杀 1 码，命中=实际开奖≠杀码（单项随机 90%）；'
            '整组 4 项全中才算命中，随机基线 65.61%。'
            'A 组为遗漏系、B 组为对冲系，A 组号位上限随盘面与滚动命中率自动收紧；'
            '四个杀码必须落在后端候选池内，且不得与上期完全相同。'
            'v4：每 40 期为一轮、跨轮清空胜率偏好；B 子策略每期强制轮换；'
            '每 30 期自动抽样复盘，逐项核对封存/结算与原始开奖。'
            'v1/v2/v3 旧记录保留在原账本，统计按规则代际切分。')

    return (
        '%s<section id="%s">'
        '<style>%s</style>'
        '<h3 class="k15-title">%s</h3>'
        '<div class="k15-meta">%s</div>'
        '<div class="k15-grid">%s</div>'
        '<div class="k15-stats">%s</div>'
        '%s'
        '<div class="k15-foot">%s</div>'
        '</section>%s'
    ) % (START, PANEL_ID, STYLE, escape(TITLE), escape(meta), cards, escape(stats_html),
         recent_html, escape(foot), END)


def apply_kill_fifteen_block(content, report=None):
    block = render_block()
    if START in content and END in content:
        head, rest = content.split(START, 1)
        _old, tail = rest.split(END, 1)
        return head + block + tail
    marker = '</body>'
    if marker in content:
        return content.replace(marker, block + marker, 1)
    return content + block
