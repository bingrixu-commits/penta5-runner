"""定位冷码（cold30 独立框架，v1）面板区块。

数据源 data/cold30_tail/{seals,settlements}.json，由 scripts/run_cold30_tail.py 每期生成。
与既有框架完全独立：独立封存 / 结算 / 统计 / 面板区块 / 攻击语；不调用 AI。

命中口径（写死，面板脚注同步展示）：
- 四个位置（千/百/十/个）各自独立取近 30 期出现次数最冷的 1 个码（0-9）；
- 冷序 = 窗口内出现次数升序，同次数按当前遗漏降序，再按码升序；
- 每期固定 4 项（每位置 1 项）全部计入整组；不设沿用/换码，每期严格按滚动窗口重算；
- 命中 = 该位置实际开奖号 != 该位置排除码（单项 90%）；
- 整组命中 = 4 项同时命中；整组随机基线 65.61%（(9/10)^4，10^4 开奖空间精确枚举）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。
"""
from datetime import datetime
from html import escape
import json
from pathlib import Path

import cold30_tail

START = '<!-- COLD_THIRTY_START -->'
END = '<!-- COLD_THIRTY_END -->'

_TITLE = '🎯 30 期定位冷码（每位置最冷 1 码 · 确定性）'

STYLE = """
#cold-thirty-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #fde68a;border-radius:12px;background:#fffdf5;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#cold-thirty-ai .c30-title{margin:0 0 8px;font-size:17px;font-weight:700;color:#92400e}
#cold-thirty-ai .c30-meta{margin:0 0 10px;padding:7px 10px;border-radius:8px;background:#fef3c7;color:#92400e;font-size:13px}
#cold-thirty-ai .c30-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:10px 0}
#cold-thirty-ai .c30-card{padding:10px 12px;border:1px solid #fde68a;border-radius:10px;background:#fff;box-shadow:0 1px 2px rgba(146,64,14,.06)}
#cold-thirty-ai .c30-card-head{font-size:14px;color:#92400e;font-weight:700}
#cold-thirty-ai .c30-kill{margin:4px 0 2px;font-size:24px;font-weight:800;color:#b91c1c;letter-spacing:2px}
#cold-thirty-ai .c30-cold{margin:2px 0;color:#475569;font-size:12.5px}
#cold-thirty-ai .c30-keep{color:#065f46;font-size:13px;font-family:ui-monospace,Consolas,monospace}
#cold-thirty-ai .c30-hit{margin:4px 0 0;padding-top:4px;border-top:1px dashed #fde68a;color:#0369a1;font-size:12.5px;font-weight:600}
#cold-thirty-ai .c30-table{width:100%;border-collapse:collapse;margin:8px 0;font-size:12.5px}
#cold-thirty-ai .c30-table th,#cold-thirty-ai .c30-table td{padding:3px 6px;border-bottom:1px solid #e2e8f0;text-align:left}
#cold-thirty-ai .c30-table th{color:#92400e;background:#fef3c7}
#cold-thirty-ai .c30-row-top{background:#fef9c3}
#cold-thirty-ai .c30-dim{color:#64748b;font-size:11.5px}
#cold-thirty-ai .c30-note{margin:4px 0;color:#64748b;font-size:12.5px}
#cold-thirty-ai .c30-reason{margin:8px 0;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;color:#475569;font-size:13px}
#cold-thirty-ai .c30-verify{margin:7px 0;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;color:#334155;font-size:13px}
#cold-thirty-ai .c30-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0}
#cold-thirty-ai .c30-stat{padding:7px 10px;border:1px solid #fde68a;border-radius:9px;background:#fff}
#cold-thirty-ai .c30-stat .l{display:block;color:#64748b;font-size:12px}
#cold-thirty-ai .c30-stat .v{display:block;color:#92400e;font-size:16px;font-weight:700}
#cold-thirty-ai .c30-attack{margin:10px 0;padding:9px 10px;border:1px solid #fcd34d;border-radius:9px;background:#fff}
#cold-thirty-ai .c30-attack-title{font-size:13px;color:#92400e;font-weight:700}
#cold-thirty-ai .c30-foot{margin-top:9px;color:#64748b;font-size:12px;line-height:1.65}
#cold-thirty-ai .c30-empty{margin:10px 0;padding:16px;border:1px dashed #fcd34d;border-radius:10px;background:#fff;color:#64748b}
#cold-thirty-ai .c30-empty b{color:#92400e}
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
    return ''.join(char for char in cold30_tail.ATTACK_ORDER if char != text)


def _position_history(stats):
    """Per-position single-code hit rate: today + same-version cumulative (verified rows only)."""
    today = stats.get('per_position') if isinstance(stats, dict) else None
    version = {}
    try:
        settlements = cold30_tail.load_settlements()
    except Exception:
        settlements = []
    for row in settlements or []:
        if not isinstance(row, dict) or row.get('verified') is not True:
            continue
        if row.get('rule_version') != cold30_tail.RULE_VERSION:
            continue
        for item in row.get('positions') or []:
            if not isinstance(item, dict):
                continue
            key = item.get('key')
            if key not in ('q', 'b', 's', 'g'):
                continue
            slot = version.setdefault(key, {'bets': 0, 'hits': 0})
            slot['bets'] += 1
            if item.get('hit') is True:
                slot['hits'] += 1
    merged = {}
    for key in ('q', 'b', 's', 'g'):
        slot = {}
        if isinstance(today, dict) and isinstance(today.get(key), dict):
            slot['today'] = today[key]
        if key in version:
            slot['version'] = version[key]
        if slot:
            merged[key] = slot
    return merged


def _position_hit_line(history, key):
    slot = history.get(key) if isinstance(history, dict) else None
    if not isinstance(slot, dict):
        return ''
    parts = []
    today = slot.get('today')
    if isinstance(today, dict) and today.get('bets'):
        parts.append('本日 %s/%s（%s）' % (_escape(today.get('hits', 0)),
                                          _escape(today.get('bets', 0)),
                                          _pct(today.get('rate'))))
    version = slot.get('version')
    if isinstance(version, dict) and version.get('bets'):
        bets = int(version.get('bets') or 0)
        hits = int(version.get('hits') or 0)
        parts.append('本版累计 %s/%s（%s）' % (_escape(hits), _escape(bets),
                                             _pct((hits / float(bets)) if bets else None)))
    if not parts:
        return ''
    return '<div class="c30-hit">单码命中率 ' + ' · '.join(parts) + '</div>'


def _position_card(item, history=None):
    if not isinstance(item, dict):
        return ''
    kill = _escape(item.get('kill'))
    label = _escape(item.get('label')) or '-'
    card = ['<div class="c30-card">']
    card.append('<div class="c30-card-head">' + label + '位</div>')
    card.append('<div class="c30-kill">杀 ' + kill + '</div>')
    card.append('<div class="c30-cold">近 %d 期出现 %s 次 · 当前遗漏 %s 期 · 冷度排名 %s</div>'
                % (cold30_tail.WINDOW,
                   _escape(item.get('count') if item.get('count') is not None else '-'),
                   _escape(item.get('omission') if item.get('omission') is not None else '-'),
                   _escape(item.get('rank') if item.get('rank') is not None else '-')))
    card.append('<div class="c30-keep">保留码 ' + _escape(_remaining(kill)) + '</div>')
    card.append(_position_hit_line(history, item.get('key')))
    card.append('</div>')
    return ''.join(card)


def _cold_table(ranking):
    grouped = {}
    for item in ranking or []:
        if isinstance(item, dict):
            grouped.setdefault(item.get('key'), []).append(item)
    if not grouped:
        return ''
    html = ['<div class="c30-note">每位置近 %d 期冷榜前 3（出现次数升序、同次数当前遗漏降序、再按码升序）</div>'
            % cold30_tail.WINDOW]
    html.append('<table class="c30-table"><thead><tr>'
                '<th>位置</th><th>第1冷</th><th>第2冷</th><th>第3冷</th>'
                '</tr></thead><tbody>')
    for key, label in (('q', '千'), ('b', '百'), ('s', '十'), ('g', '个')):
        items = sorted((item for item in grouped.get(key) or [] if isinstance(item, dict)),
                       key=lambda item: _rank_value(item.get('rank')))
        cells = []
        for index in range(3):
            if index < len(items):
                item = items[index]
                css = ' class="c30-row-top"' if index == 0 else ''
                cells.append('<td%s>杀 %s <span class="c30-dim">%s次 / 遗漏%s</span></td>'
                             % (css, _escape(item.get('code')),
                                _escape(item.get('count')), _escape(item.get('omission'))))
            else:
                cells.append('<td>-</td>')
        html.append('<tr><td>%s</td>%s</tr>' % (_escape(label), ''.join(cells)))
    html.append('</tbody></table>')
    return ''.join(html)


def _rank_value(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '上期核验：尚无已结算记录（开奖后按真实开奖核验）。'
    actual = ''.join(_escape(value) for value in (settlement.get('actual') or []))
    parts = []
    for item in settlement.get('positions') or []:
        if not isinstance(item, dict):
            continue
        parts.append('%s 实际%s/杀%s %s' % (_escape(item.get('label') or '-'),
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
    position = stats.get('position') or {}
    expected = stats.get('expected') or {}
    version_total = stats.get('version_total') or 0
    version_hits = stats.get('version_hits') or 0
    try:
        version_rate = (int(version_hits) / float(version_total)) if int(version_total) else None
    except (TypeError, ValueError):
        version_rate = None
    items = [
        ('今日整组', '%s/%s（%s）' % (_escape(stats.get('group_hits', 0)),
                                    _escape(stats.get('total', 0)),
                                    _pct(stats.get('group_rate')))),
        ('今日单注', '%s/%s（%s）' % (_escape(position.get('hits', 0)),
                                    _escape(position.get('bets', 0)),
                                    _pct(position.get('rate')))),
        ('当前连中/连败', '%s / %s' % (_escape(stats.get('current_win_streak', 0)),
                                     _escape(stats.get('current_loss_streak', 0)))),
        ('本版最长连败', '%s 期' % _escape(stats.get('max_loss_streak', 0))),
        ('本版最长连中', '%s 期' % _escape(stats.get('max_win_streak', 0))),
        ('本版累计', '%s/%s（%s）' % (_escape(version_hits), _escape(version_total),
                                    _pct(version_rate))),
        ('待核验', '%s 期' % _escape(stats.get('pending', 0))),
        ('整组理论基线', _pct(expected.get('group_rate'))),
    ]
    html = ['<div class="c30-stats">']
    for label, value in items:
        html.append('<div class="c30-stat"><span class="l">' + label
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
        if (isinstance(item, dict) and item.get('key') == 'cold30_tail'
                and item.get('rule') == cold30_tail.RULE_VERSION):
            row = item
            break
    if row is None:
        return ('<div class="c30-note">累计命中率：同版本尚无累计样本'
                '（自动跟随 data/hitrate_stability.json 刷新）。</div>')
    ci = row.get('ci95') or []
    ci_text = ('[%s, %s]' % (_pct(ci[0]), _pct(ci[1]))) if len(ci) == 2 else '-'
    return ('<div class="c30-note">本版累计：n=%s · 命中 %s · 命中率 %s · 理论 %s · 差值 %s · '
            'CI95 %s · p=%s · %s</div>'
            % (_escape(row.get('n')), _escape(row.get('hits')),
               _pct(row.get('rate')), _pct(row.get('expected')),
               _escape(row.get('delta')), ci_text, _escape(row.get('p')),
               _escape(row.get('cum_verdict'))))


def _version_html(info):
    if not isinstance(info, dict) or not info:
        return ''
    return ('<div class="c30-note">口径 %s · 已发布 %s 期（%s → %s）</div>'
            % (_escape(info.get('rule_version')), _escape(info.get('count')),
               _escape(info.get('first_ref') or '-'), _escape(info.get('last_ref') or '-')))


def _attack_html(seal):
    if not isinstance(seal, dict) or not seal.get('published_at'):
        return ''
    try:
        text = cold30_tail.build_attack(seal)
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
        "var b=document.getElementById('cold30-attack-text');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('cold30-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="c30-attack">'
            '<div class="c30-attack-title">攻击语（复制到平台：四位置各保留 9 码 + 四定）</div>'
            '<textarea id="cold30-attack-text" readonly rows="2" aria-label="最新30期定位冷码攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #fcd34d;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea>'
            '<button id="cold30-attack-copy" type="button" onclick="' + copy_action + '" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#d97706;color:#fff;cursor:pointer;">'
            '复制攻击语</button>'
            '<span id="cold30-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            '<script id="cold30-attack-data" type="application/json">' + payload + '</script>')


def _foot_html():
    return ('<div class="c30-foot">口径（写死）：四个位置（千/百/十/个）各自独立统计近 '
            + str(cold30_tail.WINDOW)
            + ' 期出现次数，取最冷 1 个码作为排除项（出现次数升序、同次数当前遗漏降序、再按码升序），'
            '每期固定 4 项全部计入整组，不设沿用/换码（每期严格按滚动窗口重算）；'
            '命中 = 该位置实际开奖号 != 排除码（单项 90%）；整组命中 = 4 项同时命中；'
            '整组随机基线 = (9/10)^4 = 65.61%，用 10^4 开奖空间精确枚举校验；'
            '只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；'
            '只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘；'
            '本框架纯确定性，不调用 AI。</div>')


def render_block():
    try:
        summary = cold30_tail.current_summary()
    except Exception:
        summary = {}
    if not isinstance(summary, dict):
        summary = {}
    stats = summary.get('stats')
    if not isinstance(stats, dict):
        try:
            stats = cold30_tail.today_stats()
        except Exception:
            stats = {}
    history = _position_history(stats)
    seal = summary.get('seal') or summary.get('previous')
    settlement = summary.get('settlement')
    if not isinstance(seal, dict):
        html = ['<section id="cold-thirty-ai">',
                '<div class="c30-title">' + _TITLE + '</div>',
                '<div class="c30-empty"><b>等待首期封存。</b> 本框架每期开奖后本地重算近 '
                + str(cold30_tail.WINDOW) + ' 期四个位置的 10 码冷榜，每位置取最冷 1 码并封存；'
                '不调用 AI，不依赖历史退役框架。</div>',
                _foot_html(), '</section>']
        return ''.join(html)
    positions = [item for item in (seal.get('positions') or []) if isinstance(item, dict)]
    ref = str(seal.get('ref_period') or '')
    target = str(seal.get('target_period') or '')
    html = ['<section id="cold-thirty-ai" data-as-of="' + _escape(ref) + '">']
    html.append('<div class="c30-title">' + _TITLE + '</div>')
    html.append('<div class="c30-meta">参考期 %s · 目标期 %s · 窗口 %s 期 · 生成 %s · 数据源 前四球（千/百/十/个）</div>'
                % (_escape(ref), _escape(target), _escape(seal.get('window')),
                   _clock(seal.get('generated_at'))))
    html.append('<div class="c30-grid">' + ''.join(_position_card(item, history) for item in positions) + '</div>')
    html.append(_cold_table(seal.get('cold_ranking') or []))
    reason = seal.get('reason')
    if reason:
        html.append('<div class="c30-reason">' + _escape(reason) + '</div>')
    state = str(seal.get('publication_state') or 'unknown')
    if seal.get('published_at'):
        publish_text = '发布 %s（%s）' % (_clock(seal.get('published_at')),
                                        '已发布' if state == 'published' else _escape(state))
    else:
        publish_text = '发布状态 %s（未写发布戳）' % _escape(state)
    html.append('<div class="c30-note">' + publish_text + ' · 关盘按目标开奖槽位倒推 70 秒 · '
                '整组 %s 项 · 理论基线 %s</div>'
                % (_escape(seal.get('expected_items')), _pct(seal.get('expected_group_rate'))))
    if not isinstance(settlement, dict):
        try:
            settlements = cold30_tail.load_settlements()
        except Exception:
            settlements = []
        settlement = settlements[-1] if settlements else None
    html.append('<div class="c30-verify">' + _settlement_line(settlement) + '</div>')
    html.append(_stats_html(stats))
    html.append(_cumulative_html())
    try:
        html.append(_version_html(cold30_tail.version_info()))
    except Exception:
        pass
    html.append(_attack_html(seal))
    html.append(_foot_html())
    html.append('</section>')
    return ''.join(html)


def apply_cold_thirty_block(content, report=None):
    """把 cold30 区块原地写入面板 content（START/END 标记替换；缺失时按兜底锚点插入）。"""
    try:
        if not isinstance(content, str):
            return content
        block = '<style>' + STYLE + '</style>' + render_block()
        if START in content and END in content:
            head, rest = content.split(START, 1)
            _old, tail_part = rest.split(END, 1)
            return head + START + block + END + tail_part
        if '<!-- KILL_THIRTEEN_END -->' in content:
            return content.replace('<!-- KILL_THIRTEEN_END -->',
                                   '<!-- KILL_THIRTEEN_END -->' + START + block + END, 1)
        if '<!-- KILL_TWELVE_END -->' in content:
            return content.replace('<!-- KILL_TWELVE_END -->',
                                   '<!-- KILL_TWELVE_END -->' + START + block + END, 1)
        marker = '</body>'
        if marker in content:
            return content.replace(marker, START + block + END + marker, 1)
        return content
    except Exception:
        if report is not None:
            try:
                report('cold30-block-failed')
            except Exception:
                pass
        return content
