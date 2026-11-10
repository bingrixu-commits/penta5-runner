"""定位排除（kill14 独立框架，v2）面板区块。

数据源 data/kill14_tail/{seals,settlements}.json，由 scripts/run_kill14_tail.py 每期生成。
与既有框架完全独立：独立封存 / 结算 / 统计 / 面板区块 / 攻击语；每期调用 AI 选码。

命中口径（写死，面板脚注同步展示）：
- 四个位置（千/百/十/个）每期各排除 1 个码（0-9），整组固定 4 项全部计入整组；
- 面板依据：近 70 / 50 / 30 / 15 期各位置冷码榜，以及按「冷码榜前 3 码杀号」口径统计的逐位置连败 / 连中；
- AI 规则：某位置在 70 / 50 / 30 / 15 任一个窗口连败时，排除码必须取该位置冷码第 1 个
  （候选 = 所有处于连败窗口的冷码第 1 个，由 AI 选择）；其余位置由 AI 自主选 1 码；
- 命中 = 该位置实际开奖号 != 该位置排除码（单项 90%）；
- 整组命中 = 4 项同时命中；整组随机基线 65.61%（(9/10)^4，10^4 开奖空间精确枚举）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。
"""
from datetime import datetime
from html import escape
import json
from pathlib import Path

import kill14_tail

START = '<!-- KILL_FOURTEEN_START -->'
END = '<!-- KILL_FOURTEEN_END -->'

_TITLE = '🎯 定位排除（四位置各排 1 码 · AI 每期选码）'

STYLE = """
#kill-fourteen-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-fourteen-ai .k14-title{margin:0 0 8px;font-size:17px;font-weight:700;color:#3730a3}
#kill-fourteen-ai .k14-meta{margin:0 0 10px;padding:7px 10px;border-radius:8px;background:#e0e7ff;color:#3730a3;font-size:13px}
#kill-fourteen-ai .k14-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:10px 0}
#kill-fourteen-ai .k14-card{padding:10px 12px;border:1px solid #c7d2fe;border-radius:10px;background:#fff;box-shadow:0 1px 2px rgba(55,48,163,.06)}
#kill-fourteen-ai .k14-card-head{font-size:14px;color:#3730a3;font-weight:700}
#kill-fourteen-ai .k14-kill{margin:4px 0 2px;font-size:24px;font-weight:800;color:#b91c1c;letter-spacing:2px}
#kill-fourteen-ai .k14-basis{margin:2px 0;color:#4338ca;font-size:12.5px;font-weight:600}
#kill-fourteen-ai .k14-cold{margin:2px 0;color:#475569;font-size:12.5px}
#kill-fourteen-ai .k14-keep{color:#065f46;font-size:13px;font-family:ui-monospace,Consolas,monospace}
#kill-fourteen-ai .k14-table{width:100%;border-collapse:collapse;margin:8px 0;font-size:12.5px}
#kill-fourteen-ai .k14-table th,#kill-fourteen-ai .k14-table td{padding:3px 6px;border-bottom:1px solid #e2e8f0;text-align:left}
#kill-fourteen-ai .k14-table th{color:#3730a3;background:#e0e7ff}
#kill-fourteen-ai .k14-row-top{background:#fef9c3}
#kill-fourteen-ai .k14-dim{color:#64748b;font-size:11.5px}
#kill-fourteen-ai .k14-note{margin:4px 0;color:#64748b;font-size:12.5px}
#kill-fourteen-ai .k14-reason{margin:8px 0;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;color:#475569;font-size:13px}
#kill-fourteen-ai .k14-verify{margin:7px 0;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;color:#334155;font-size:13px}
#kill-fourteen-ai .k14-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0}
#kill-fourteen-ai .k14-stat{padding:7px 10px;border:1px solid #c7d2fe;border-radius:9px;background:#fff}
#kill-fourteen-ai .k14-stat .l{display:block;color:#64748b;font-size:12px}
#kill-fourteen-ai .k14-stat .v{display:block;color:#3730a3;font-size:16px;font-weight:700}
#kill-fourteen-ai .k14-attack{margin:10px 0;padding:9px 10px;border:1px solid #a5b4fc;border-radius:9px;background:#fff}
#kill-fourteen-ai .k14-attack-title{font-size:13px;color:#3730a3;font-weight:700}
#kill-fourteen-ai .k14-foot{margin-top:9px;color:#64748b;font-size:12px;line-height:1.65}
#kill-fourteen-ai .k14-empty{margin:10px 0;padding:16px;border:1px dashed #a5b4fc;border-radius:10px;background:#fff;color:#64748b}
#kill-fourteen-ai .k14-empty b{color:#3730a3}
"""

_BASIS_TEXT = {
    'cold70-rank1': '70 期冷码第 1',
    'cold50-rank1': '50 期冷码第 1',
    'cold30-rank1': '30 期冷码第 1',
    'cold15-rank1': '15 期冷码第 1',
    'ai': 'AI 自选',
}


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
    return ''.join(char for char in kill14_tail.ATTACK_ORDER if char != text)


def _basis_window(item):
    basis = str(item.get('basis') or '')
    if basis.startswith('cold') and basis.endswith('-rank1'):
        digits = basis[4:-6]
        if digits.isdigit():
            return int(digits)
    return None


def _basis_text(item):
    basis = str(item.get('basis') or '')
    text = _BASIS_TEXT.get(basis, basis or '-')
    if item.get('enforced') is True:
        return '连败强制 · ' + text
    return text


def _position_card(item):
    if not isinstance(item, dict):
        return ''
    kill = _escape(item.get('kill'))
    label = _escape(item.get('label')) or '-'
    card = ['<div class="k14-card">']
    card.append('<div class="k14-card-head">' + label + '位</div>')
    card.append('<div class="k14-kill">排除 ' + kill + '</div>')
    card.append('<div class="k14-basis">' + _escape(_basis_text(item)) + '</div>')
    window = _basis_window(item)
    source = ('近 %d 期' % window) if window else '冷码榜'
    card.append('<div class="k14-cold">%s出现 %s 次 · 当前遗漏 %s 期 · 冷度排名 %s</div>'
                % (source,
                   _escape(item.get('count') if item.get('count') is not None else '-'),
                   _escape(item.get('omission') if item.get('omission') is not None else '-'),
                   _escape(item.get('rank') if item.get('rank') is not None else '-')))
    card.append('<div class="k14-keep">保留码 ' + _escape(_remaining(kill)) + '</div>')
    card.append('</div>')
    return ''.join(card)


def _rank_value(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _cold_table(ranking):
    if not isinstance(ranking, dict) or not ranking:
        return ''
    html = []
    for window in [str(value) for value in kill14_tail.STATS_WINDOWS]:
        block = ranking.get(window)
        if not isinstance(block, dict) or not block:
            continue
        html.append('<div class="k14-note">近 %s 期各位置冷榜前 3（出现次数升序、同次数当前遗漏降序、'
                    '再按码升序）</div>' % _escape(window))
        html.append('<table class="k14-table"><thead><tr>'
                    '<th>位置</th><th>第1冷</th><th>第2冷</th><th>第3冷</th>'
                    '</tr></thead><tbody>')
        for key, label, _index in kill14_tail.POSITIONS:
            items = sorted((item for item in (block.get(key) or []) if isinstance(item, dict)),
                           key=lambda item: _rank_value(item.get('rank')))
            cells = []
            for index in range(3):
                if index < len(items):
                    item = items[index]
                    css = ' class="k14-row-top"' if index == 0 else ''
                    cells.append('<td%s>%s <span class="k14-dim">%s次 / 遗漏%s</span></td>'
                                 % (css, _escape(item.get('code')),
                                    _escape(item.get('count')), _escape(item.get('omission'))))
                else:
                    cells.append('<td>-</td>')
            html.append('<tr><td>%s</td>%s</tr>' % (_escape(label), ''.join(cells)))
        html.append('</tbody></table>')
    return ''.join(html)


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '上期核验：尚无已结算记录（开奖后按真实开奖核验）。'
    actual = ''.join(_escape(value) for value in (settlement.get('actual') or []))
    parts = []
    for item in settlement.get('positions') or []:
        if not isinstance(item, dict):
            continue
        parts.append('%s 实际%s/排%s %s' % (_escape(item.get('label') or '-'),
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
    enforced = stats.get('enforced') or {}
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
        ('强制位单注', '%s/%s（%s）' % (_escape(enforced.get('hits', 0)),
                                     _escape(enforced.get('bets', 0)),
                                     _pct(enforced.get('rate')))),
        ('当前连中/连败', '%s / %s' % (_escape(stats.get('current_win_streak', 0)),
                                     _escape(stats.get('current_loss_streak', 0)))),
        ('本版最长连败', '%s 期' % _escape(stats.get('max_loss_streak', 0))),
        ('本版累计', '%s/%s（%s）' % (_escape(version_hits), _escape(version_total),
                                    _pct(version_rate))),
        ('待核验', '%s 期' % _escape(stats.get('pending', 0))),
        ('整组理论基线', _pct(expected.get('group_rate'))),
    ]
    html = ['<div class="k14-stats">']
    for label, value in items:
        html.append('<div class="k14-stat"><span class="l">' + label
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
        if (isinstance(item, dict) and item.get('key') == 'kill14_tail'
                and item.get('rule') == kill14_tail.RULE_VERSION):
            row = item
            break
    if row is None:
        return ('<div class="k14-note">累计命中率：同版本尚无累计样本'
                '（自动跟随 data/hitrate_stability.json 刷新）。</div>')
    ci = row.get('ci95') or []
    ci_text = ('[%s, %s]' % (_pct(ci[0]), _pct(ci[1]))) if len(ci) == 2 else '-'
    return ('<div class="k14-note">本版累计：n=%s · 命中 %s · 命中率 %s · 理论 %s · 差值 %s · '
            'CI95 %s · p=%s · %s</div>'
            % (_escape(row.get('n')), _escape(row.get('hits')),
               _pct(row.get('rate')), _pct(row.get('expected')),
               _escape(row.get('delta')), ci_text, _escape(row.get('p')),
               _escape(row.get('cum_verdict'))))


def _version_html(info):
    if not isinstance(info, dict) or not info:
        return ''
    return ('<div class="k14-note">口径 %s · 已发布 %s 期（%s → %s）</div>'
            % (_escape(info.get('rule_version')), _escape(info.get('count')),
               _escape(info.get('first_ref') or '-'), _escape(info.get('last_ref') or '-')))


def _enforced_html(seal):
    keys = [str(key) for key in (seal.get('enforced_positions') or [])]
    if not keys:
        return ''
    allowed = seal.get('enforced_allowed') or {}
    labels = []
    for key in keys:
        label = kill14_tail.POSITION_LABELS.get(key, key)
        codes = [str(code) for code in (allowed.get(key) or [])]
        labels.append(label + '位（冷码第 1：' + '/'.join(codes) + '）'
                      if codes else label + '位')
    free = [label for key, label in kill14_tail.POSITION_LABELS.items() if key not in keys]
    return ('<div class="k14-note">本期强制位置（任一连败窗口 → 取该位置冷码第 1 个）：%s；'
            '自主位置：%s</div>'
            % (_escape('、'.join(labels)), _escape('、'.join(free) if free else '无')))


def _attack_html(seal):
    if not isinstance(seal, dict) or not seal.get('published_at'):
        return ''
    try:
        text = kill14_tail.build_attack(seal)
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
        "var b=document.getElementById('kill14-attack-text');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('kill14-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="k14-attack">'
            '<div class="k14-attack-title">攻击语（复制到平台：四位置各保留 9 码 + 四定）</div>'
            '<textarea id="kill14-attack-text" readonly rows="2" aria-label="定位排除攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #a5b4fc;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea>'
            '<button id="kill14-attack-copy" type="button" onclick="' + copy_action + '" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#4338ca;color:#fff;cursor:pointer;">'
            '复制攻击语</button>'
            '<span id="kill14-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            '<script id="kill14-attack-data" type="application/json">' + payload + '</script>')


def _foot_html():
    return ('<div class="k14-foot">口径（写死）：四个位置（千/百/十/个）每期各排除 1 个码（0-9），'
            '整组固定 4 项全部计入整组；面板依据为近 70 / 50 / 30 / 15 期各位置冷码榜（出现次数升序、'
            '同次数当前遗漏降序、再按码升序）与按冷码榜前 3 码杀号口径统计的逐位置连败 / 连中；'
            'AI 规则：某位置在 70 / 50 / 30 / 15 任一个窗口连败时，排除码必须取该位置冷码第 1 个'
            '（候选 = 所有连败窗口的冷码第 1 个，由 AI 选择），其余位置由 AI 自主选 1 码；不设沿用 / 换码，每期重选；'
            '命中 = 该位置实际开奖号 != 排除码（单项 90%）；整组命中 = 4 项同时命中；'
            '整组随机基线 = (9/10)^4 = 65.61%，用 10^4 开奖空间精确枚举校验；'
            '只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；'
            '只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。</div>')


def render_block():
    try:
        summary = kill14_tail.current_summary()
    except Exception:
        summary = {}
    if not isinstance(summary, dict):
        summary = {}
    stats = summary.get('stats')
    if not isinstance(stats, dict):
        try:
            stats = kill14_tail.today_stats()
        except Exception:
            stats = {}
    seal = summary.get('seal') or summary.get('previous')
    settlement = summary.get('settlement')
    if not isinstance(seal, dict):
        html = ['<section id="kill-fourteen-ai">',
                '<div class="k14-title">' + _TITLE + '</div>',
                '<div class="k14-empty"><b>等待首期封存。</b> 本框架每期开奖后由 AI 读取面板冷码榜与逐位置'
                '连败 / 连中状态，四个位置各排除 1 码并封存；连败位置强制取该位置冷码第 1 个。</div>',
                _foot_html(), '</section>']
        return ''.join(html)
    positions = [item for item in (seal.get('positions') or []) if isinstance(item, dict)]
    ref = str(seal.get('ref_period') or '')
    target = str(seal.get('target_period') or '')
    html = ['<section id="kill-fourteen-ai" data-as-of="' + _escape(ref) + '">']
    html.append('<div class="k14-title">' + _TITLE + '</div>')
    html.append('<div class="k14-meta">参考期 %s · 目标期 %s · 生成 %s · 模型 %s · 提示词 %s · '
                '数据源 前四球（千/百/十/个）</div>'
                % (_escape(ref), _escape(target), _clock(seal.get('generated_at')),
                   _escape(seal.get('model_label') or seal.get('model') or '-'),
                   _escape(seal.get('prompt_version') or '-')))
    html.append('<div class="k14-grid">' + ''.join(_position_card(item) for item in positions) + '</div>')
    html.append(_enforced_html(seal))
    html.append(_cold_table(seal.get('cold_ranking') or {}))
    reason = seal.get('reason')
    if reason:
        html.append('<div class="k14-reason">AI 理由：' + _escape(reason) + '</div>')
    state = str(seal.get('publication_state') or 'unknown')
    if seal.get('published_at'):
        publish_text = '发布 %s（%s）' % (_clock(seal.get('published_at')),
                                        '已发布' if state == 'published' else _escape(state))
    else:
        publish_text = '发布状态 %s（未写发布戳）' % _escape(state)
    html.append('<div class="k14-note">' + publish_text + ' · 关盘按目标开奖槽位倒推 70 秒 · '
                '整组 %s 项 · 理论基线 %s</div>'
                % (_escape(seal.get('expected_items')), _pct(seal.get('expected_group_rate'))))
    if not isinstance(settlement, dict):
        try:
            settlements = kill14_tail.load_settlements()
        except Exception:
            settlements = []
        settlement = settlements[-1] if settlements else None
    html.append('<div class="k14-verify">' + _settlement_line(settlement) + '</div>')
    html.append(_stats_html(stats))
    html.append(_cumulative_html())
    try:
        html.append(_version_html(kill14_tail.version_info()))
    except Exception:
        pass
    html.append(_attack_html(seal))
    html.append(_foot_html())
    html.append('</section>')
    return ''.join(html)


def apply_kill_fourteen_block(content, report=None):
    """把 kill14 区块原地写入面板 content（START/END 标记替换；缺失时按兜底锚点插入）。"""
    try:
        if not isinstance(content, str):
            return content
        block = '<style>' + STYLE + '</style>' + render_block()
        if START in content and END in content:
            head, rest = content.split(START, 1)
            _old, tail_part = rest.split(END, 1)
            return head + START + block + END + tail_part
        for anchor in ('<!-- COLD_FIFTEEN_END -->', '<!-- COLD_THIRTY_END -->',
                       '<!-- KILL_THIRTEEN_END -->', '<!-- KILL_TWELVE_END -->'):
            if anchor in content:
                return content.replace(anchor, anchor + START + block + END, 1)
        marker = '</body>'
        if marker in content:
            return content.replace(marker, START + block + END + marker, 1)
        return content
    except Exception:
        if report is not None:
            try:
                report('kill14-block-failed')
            except Exception:
                pass
        return content
