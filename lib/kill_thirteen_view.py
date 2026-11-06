"""合尾双冷杀号（kill13 独立框架，v1）面板区块。

数据源 data/kill13_tail/{seals,settlements}.json，由 scripts/run_kill13_tail.py 每期生成。
与既有框架完全独立：独立封存 / 结算 / 统计 / 面板区块 / 攻击语；不调用 AI。

命中口径（写死，面板脚注同步展示）：
- 候选池 = 5 种合尾组合 × 尾码 0-9，共 50 个候选；
- 每期取近 50 期冷榜（出现次数升序、同次数遗漏降序）前 2 个作为排除项，2 注/期全部计入整组；
- cold-hold-top2 稳定化：上期 2 项仍在冷榜前 2 就沿用，掉出前 2 的换成本期前 2 缺项；无连用上限；
- 命中 = 该合尾实际和值个位 != 排除码（单项 90%）；
- 整组命中 = 2 项同时命中；整组随机基线 80.82%（10^4 开奖 × 1225 配置精确枚举）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。
"""
from datetime import datetime
from html import escape
import json
from pathlib import Path

import kill13_tail

START = '<!-- KILL_THIRTEEN_START -->'
END = '<!-- KILL_THIRTEEN_END -->'

_TITLE = '🎯 合尾双冷杀号（近 50 期最冷 2 合尾 · 确定性）'

STYLE = """
#kill-thirteen-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #a7f3d0;border-radius:12px;background:#f7fffb;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-thirteen-ai .k13-title{margin:0 0 8px;font-size:17px;font-weight:700;color:#065f46}
#kill-thirteen-ai .k13-meta{margin:0 0 10px;padding:7px 10px;border-radius:8px;background:#ecfdf5;color:#047857;font-size:13px}
#kill-thirteen-ai .k13-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px;margin:10px 0}
#kill-thirteen-ai .k13-card{padding:10px 12px;border:1px solid #d1fae5;border-radius:10px;background:#fff;box-shadow:0 1px 2px rgba(6,78,59,.06)}
#kill-thirteen-ai .k13-card-head{font-size:14px;color:#047857;font-weight:700}
#kill-thirteen-ai .k13-kill{margin:4px 0 2px;font-size:24px;font-weight:800;color:#b91c1c;letter-spacing:2px}
#kill-thirteen-ai .k13-cold{margin:2px 0;color:#475569;font-size:12.5px}
#kill-thirteen-ai .k13-keep{color:#065f46;font-size:13px;font-family:ui-monospace,Consolas,monospace}
#kill-thirteen-ai .k13-table{width:100%;border-collapse:collapse;margin:8px 0;font-size:12.5px}
#kill-thirteen-ai .k13-table th,#kill-thirteen-ai .k13-table td{padding:3px 6px;border-bottom:1px solid #e2e8f0;text-align:left}
#kill-thirteen-ai .k13-table th{color:#047857;background:#ecfdf5}
#kill-thirteen-ai .k13-row-top{background:#fef9c3}
#kill-thirteen-ai .k13-hold{margin:8px 0;padding:8px 10px;border:1px dashed #6ee7b7;border-radius:9px;background:#f0fdfa;font-size:13px}
#kill-thirteen-ai .k13-hold-title{font-weight:700;color:#047857;margin-bottom:3px}
#kill-thirteen-ai .k13-tag{display:inline-block;margin:2px 6px 2px 0;padding:1px 8px;border-radius:99px;background:#d1fae5;color:#065f46;font-size:12px}
#kill-thirteen-ai .k13-reason{margin:8px 0;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;color:#475569;font-size:13px}
#kill-thirteen-ai .k13-note{margin:4px 0;color:#64748b;font-size:12.5px}
#kill-thirteen-ai .k13-verify{margin:7px 0;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;color:#334155;font-size:13px}
#kill-thirteen-ai .k13-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0}
#kill-thirteen-ai .k13-stat{padding:7px 10px;border:1px solid #d1fae5;border-radius:9px;background:#fff}
#kill-thirteen-ai .k13-stat .l{display:block;color:#64748b;font-size:12px}
#kill-thirteen-ai .k13-stat .v{display:block;color:#065f46;font-size:16px;font-weight:700}
#kill-thirteen-ai .k13-attack{margin:10px 0;padding:9px 10px;border:1px solid #a7f3d0;border-radius:9px;background:#fff}
#kill-thirteen-ai .k13-attack-title{font-size:13px;color:#047857;font-weight:700}
#kill-thirteen-ai .k13-foot{margin-top:9px;color:#64748b;font-size:12px;line-height:1.65}
#kill-thirteen-ai .k13-empty{margin:10px 0;padding:16px;border:1px dashed #a7f3d0;border-radius:10px;background:#fff;color:#64748b}
#kill-thirteen-ai .k13-empty b{color:#065f46}
"""


def _pct(value, digits=2):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return '-'
    return ('%.' + str(digits) + 'f%%') % (float(value) * 100.0)


def _clock(value):
    if not value:
        return '-'
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00')).strftime('%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return escape(text)


def _escape(value):
    return escape(str(value if value is not None else ''))


def _remaining(kill):
    text = str(kill if kill is not None else '')
    return ''.join(char for char in kill13_tail.ATTACK_ORDER if char != text)


def _tail_label(item):
    if not isinstance(item, dict):
        return '-'
    label = item.get('label')
    if not label:
        label = (kill13_tail.TAILS.get(item.get('key')) or {}).get('label')
    return str(label or '-')


def _tail_card(item):
    kill = _escape(item.get('kill'))
    card = ['<div class="k13-card">']
    card.append('<div class="k13-card-head">' + _escape(_tail_label(item)) + '合尾</div>')
    card.append('<div class="k13-kill">杀 ' + kill + '</div>')
    count = item.get('count')
    omission = item.get('omission')
    rank = item.get('rank')
    card.append('<div class="k13-cold">近 %d 期出现 %s 次 · 当前遗漏 %s 期 · 冷度排名 %s</div>'
                % (kill13_tail.WINDOW,
                   _escape(count if count is not None else '-'),
                   _escape(omission if omission is not None else '-'),
                   _escape(rank if rank is not None else '-')))
    card.append('<div class="k13-keep">保留码 ' + _escape(_remaining(kill)) + '</div>')
    card.append('</div>')
    return ''.join(card)


def _cold_table(ranking):
    rows = [item for item in (ranking or []) if isinstance(item, dict)]
    if not rows:
        return ''
    html = ['<div class="k13-note">近 %d 期合尾冷榜（前 %d，出现次数升序、同次数遗漏降序）</div>'
            % (kill13_tail.WINDOW, len(rows))]
    html.append('<table class="k13-table"><thead><tr>'
                '<th>排名</th><th>合尾</th><th>排除码</th><th>出现次数</th><th>当前遗漏</th>'
                '</tr></thead><tbody>')
    for item in rows:
        try:
            rank_value = int(item.get('rank') or 0)
        except (TypeError, ValueError):
            rank_value = 0
        css = ' class="k13-row-top"' if 0 < rank_value <= kill13_tail.TOP_N else ''
        html.append('<tr%s><td>%s</td><td>%s合</td><td>杀 %s</td><td>%s</td><td>%s</td></tr>'
                    % (css, _escape(item.get('rank')), _escape(_tail_label(item)),
                       _escape(item.get('kill')), _escape(item.get('count')),
                       _escape(item.get('omission'))))
    html.append('</tbody></table>')
    return ''.join(html)


def _hold_html(policy):
    if not isinstance(policy, dict) or not policy:
        return ''
    html = ['<div class="k13-hold">']
    html.append('<div class="k13-hold-title">冷榜沿用（cold-hold-top2）</div>')
    note = policy.get('note')
    if note:
        html.append('<div>' + _escape(note) + '</div>')
    kept = [item for item in (policy.get('kept') or []) if isinstance(item, dict)]
    swapped = [item for item in (policy.get('swapped') or []) if isinstance(item, dict)]
    if kept:
        tags = ''.join('<span class="k13-tag">沿用 ' + _escape(_tail_label(item))
                       + '合杀' + _escape(item.get('kill')) + '</span>' for item in kept)
        html.append('<div>沿用项：' + tags + '</div>')
    if swapped:
        parts = []
        for pair in swapped:
            source = pair.get('from') if isinstance(pair.get('from'), dict) else None
            target = pair.get('to') if isinstance(pair.get('to'), dict) else None
            source_text = ('%s合杀%s' % (_escape(_tail_label(source)), _escape(source.get('kill')))) if source else '—'
            target_text = ('%s合杀%s' % (_escape(_tail_label(target)), _escape(target.get('kill')))) if target else '—'
            parts.append(source_text + ' → ' + target_text)
        html.append('<div>换码：' + '；'.join(parts) + '</div>')
    html.append('</div>')
    return ''.join(html)


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '上期核验：尚无已结算记录（开奖后按真实开奖核验）。'
    actual = ''.join(_escape(value) for value in (settlement.get('actual') or []))
    parts = []
    for item in settlement.get('tails') or []:
        if not isinstance(item, dict):
            continue
        parts.append('%s合 实际%s/杀%s %s' % (_escape(_tail_label(item)),
                                           _escape(item.get('actual')),
                                           _escape(item.get('kill')),
                                           '✓' if item.get('hit') else '✗'))
    group_text = '整组命中' if settlement.get('group_hit') else '整组未全中'
    verified_text = '已核验' if settlement.get('verified') else '未计入（非开奖前发布）'
    return ('上期核验：目标期 %s · 开奖 %s · %s · %s · %s'
            % (_escape(settlement.get('target_period')), actual or '-',
               '；'.join(parts) or '-', group_text, verified_text))


def _stats_html(stats):
    if not isinstance(stats, dict):
        stats = {}
    tail = stats.get('tail') or {}
    expected = stats.get('expected') or {}
    items = [
        ('今日整组', '%s/%s（%s）' % (_escape(stats.get('group_hits', 0)),
                                    _escape(stats.get('total', 0)),
                                    _pct(stats.get('group_rate')))),
        ('今日单注', '%s/%s（%s）' % (_escape(tail.get('hits', 0)),
                                    _escape(tail.get('bets', 0)),
                                    _pct(tail.get('rate')))),
        ('今日沿用/换码', '%s / %s 项' % (_escape(stats.get('kept_total', 0)),
                                        _escape(stats.get('swapped_total', 0)))),
        ('当前连中/连败', '%s / %s' % (_escape(stats.get('current_win_streak', 0)),
                                     _escape(stats.get('current_loss_streak', 0)))),
        ('本版最长连败', '%s 期' % _escape(stats.get('max_loss_streak', 0))),
        ('四手连败累计', '%s 次（今日 %s 次）' % (_escape(stats.get('four_loss_cycles_total', 0)),
                                              _escape(stats.get('four_loss_cycles', 0)))),
        ('待核验', '%s 期' % _escape(stats.get('pending', 0))),
        ('整组理论基线', _pct(expected.get('group_rate'))),
    ]
    html = ['<div class="k13-stats">']
    for label, value in items:
        html.append('<div class="k13-stat"><span class="l">' + label
                    + '</span><span class="v">' + value + '</span></div>')
    html.append('</div>')
    return ''.join(html)


def _cumulative_html():
    path = Path('/root/penta5-runner/data/hitrate_stability.json')
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return ''
    if not isinstance(report, dict):
        return ''
    row = None
    for item in report.get('rows') or []:
        if (isinstance(item, dict) and item.get('key') == 'kill13_tail'
                and item.get('rule') == kill13_tail.RULE_VERSION):
            row = item
            break
    if row is None:
        return ('<div class="k13-note">累计命中率：同版本尚无累计样本'
                '（自动跟随 data/hitrate_stability.json 刷新）。</div>')
    ci = row.get('ci95') or []
    ci_text = ('[%s, %s]' % (_pct(ci[0]), _pct(ci[1]))) if len(ci) == 2 else '-'
    return ('<div class="k13-note">本版累计：n=%s · 命中 %s · 命中率 %s · 理论 %s · 差值 %s · '
            'CI95 %s · p=%s · %s</div>'
            % (_escape(row.get('n')), _escape(row.get('hits')),
               _pct(row.get('rate')), _pct(row.get('expected')),
               _escape(row.get('delta')), ci_text, _escape(row.get('p')),
               _escape(row.get('cum_verdict'))))


def _version_html(info):
    if not isinstance(info, dict) or not info:
        return ''
    return ('<div class="k13-note">口径 %s · 已发布 %s 期（%s → %s）</div>'
            % (_escape(info.get('rule_version')), _escape(info.get('count')),
               _escape(info.get('first_ref') or '-'), _escape(info.get('last_ref') or '-')))


def _attack_html(seal):
    if not isinstance(seal, dict) or not seal.get('published_at'):
        return ''
    try:
        text = kill13_tail.build_attack(seal)
    except Exception:
        return ''
    payload = json.dumps({'version': 1,
                          'reference_period': seal.get('ref_period'),
                          'target_period': seal.get('target_period'),
                          'published_at': seal.get('published_at'),
                          'attack': text},
                         ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    payload = payload.replace('&', chr(92) + 'u0026').replace('<', chr(92) + 'u003c')
    payload = payload.replace('>', chr(92) + 'u003e')
    copy_action = (
        "var b=document.getElementById('kill13-attack-text');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('kill13-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="k13-attack">'
            '<div class="k13-attack-title">攻击语（复制到平台：四位置全保留 10 码 + 四定 + 两个合尾 9 码）</div>'
            '<textarea id="kill13-attack-text" readonly rows="3" aria-label="合尾双冷2合尾攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #a7f3d0;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea>'
            '<button id="kill13-attack-copy" type="button" onclick="' + copy_action + '" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#059669;color:#fff;cursor:pointer;">'
            '复制攻击语</button>'
            '<span id="kill13-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            '<script id="kill13-attack-data" type="application/json">' + payload + '</script>')


def _foot_html():
    return ('<div class="k13-foot">口径（写死）：候选池 = 5 种合尾组合（千百十个 / 千百十 / 千百个 / '
            '千十个 / 百十个）× 尾码 0-9，共 50 个候选；每期按近 ' + str(kill13_tail.WINDOW)
            + ' 期冷榜取最冷 2 个合尾作为排除项（出现次数升序、同次数当前遗漏降序、再按组合序/尾码），'
            '2 注/期全部计入整组；稳定化 cold-hold-top2：上期 2 项仍在冷榜前 2 就原样沿用，'
            '掉出前 2 的换成本期前 2 缺项，不设连用上限；命中 = 该合尾实际和值个位 != 排除码（单项 90%）；'
            '整组命中 = 2 项同时命中；整组随机基线 = 10^4 开奖 × C(50,2)=1225 配置精确枚举 80.82%'
            '（990/1225，不是 0.9^2）；只统计开奖前已发布、开奖后按真实开奖核验的记录；'
            '北京时间 05:00 清零；只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘；'
            '本框架纯确定性，不调用 AI。</div>')


def render_block():
    try:
        summary = kill13_tail.current_summary()
    except Exception:
        summary = {}
    if not isinstance(summary, dict):
        summary = {}
    stats = summary.get('stats')
    if not isinstance(stats, dict):
        try:
            stats = kill13_tail.today_stats()
        except Exception:
            stats = {}
    seal = summary.get('seal') or summary.get('previous')
    settlement = summary.get('settlement')
    if not isinstance(seal, dict):
        html = ['<section id="kill-thirteen-ai">',
                '<div class="k13-title">' + _TITLE + '</div>',
                '<div class="k13-empty"><b>等待首期封存。</b> 本框架每期开奖后本地重算近 '
                + str(kill13_tail.WINDOW) + ' 期 50 个合尾候选的冷榜，取最冷 2 个合尾并封存；'
                '不调用 AI，不依赖历史退役框架。</div>',
                _foot_html(), '</section>']
        return ''.join(html)
    tails = [item for item in (seal.get('tails') or []) if isinstance(item, dict)]
    ref = str(seal.get('ref_period') or '')
    target = str(seal.get('target_period') or '')
    html = ['<section id="kill-thirteen-ai" data-as-of="' + _escape(ref) + '">']
    html.append('<div class="k13-title">' + _TITLE + '</div>')
    html.append('<div class="k13-meta">参考期 %s · 目标期 %s · 窗口 %s 期 · 生成 %s · 数据源 前四球（千/百/十/个）</div>'
                % (_escape(ref), _escape(target), _escape(seal.get('window')),
                   _clock(seal.get('generated_at'))))
    html.append('<div class="k13-grid">' + ''.join(_tail_card(item) for item in tails) + '</div>')
    html.append(_cold_table(seal.get('cold_ranking') or []))
    html.append(_hold_html(seal.get('tail_policy') or {}))
    reason = seal.get('reason')
    if reason:
        html.append('<div class="k13-reason">' + _escape(reason) + '</div>')
    state = str(seal.get('publication_state') or 'unknown')
    if seal.get('published_at'):
        publish_text = '发布 %s（%s）' % (_clock(seal.get('published_at')),
                                        '已发布' if state == 'published' else _escape(state))
    else:
        publish_text = '发布状态 %s（未写发布戳）' % _escape(state)
    html.append('<div class="k13-note">' + publish_text + ' · 关盘按目标开奖槽位倒推 70 秒 · '
                '整组 %s 项 · 理论基线 %s</div>'
                % (_escape(seal.get('expected_items')), _pct(seal.get('expected_group_rate'))))
    if not isinstance(settlement, dict):
        try:
            settlements = kill13_tail.load_settlements()
        except Exception:
            settlements = []
        settlement = settlements[-1] if settlements else None
    html.append('<div class="k13-verify">' + _settlement_line(settlement) + '</div>')
    html.append(_stats_html(stats))
    html.append(_cumulative_html())
    try:
        html.append(_version_html(kill13_tail.version_info()))
    except Exception:
        pass
    html.append(_attack_html(seal))
    html.append(_foot_html())
    html.append('</section>')
    return ''.join(html)


def apply_kill_thirteen_block(content, report=None):
    """把 kill13 区块原地写入面板 content（START/END 标记替换；缺失时按兜底锚点插入）。"""
    try:
        if not isinstance(content, str):
            return content
        block = '<style>' + STYLE + '</style>' + render_block()
        if START in content and END in content:
            head, rest = content.split(START, 1)
            _old, tail_part = rest.split(END, 1)
            return head + START + block + END + tail_part
        if '<!-- KILL_TWELVE_END -->' in content:
            return content.replace('<!-- KILL_TWELVE_END -->',
                                   '<!-- KILL_TWELVE_END -->' + START + block + END, 1)
        if '<!-- KILL_ELEVEN_END -->' in content:
            return content.replace('<!-- KILL_ELEVEN_END -->',
                                   '<!-- KILL_ELEVEN_END -->' + START + block + END, 1)
        marker = '</body>'
        if marker in content:
            return content.replace(marker, START + block + END + marker, 1)
        return content
    except Exception:
        if report is not None:
            try:
                report('kill13-block-failed')
            except Exception:
                pass
        return content
