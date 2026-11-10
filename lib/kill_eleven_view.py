"""定位杀码 3 项 建议面板区块（kill11 独立框架，v3）。

数据源 data/kill11_tail/{seals,settlements}.json，由 scripts/run_kill11_tail.py 每期生成。
与既有框架完全独立：独立 AI 请求 / 封存 / 结算 / 统计 / 面板区块 / 攻击语。

命中口径（写死，面板脚注同步展示）：
- AI 每期从 千/百/十/个 中固定选恰好 3 个不同位置，每个位置各排除 1 码，共 3 注/期、全部计入整组；
- 位置命中 = 该位置实际开奖号 != 该位置排除码（单项 90%）；
- 整组命中 = 3 个位置同时命中（整组固定 3 项）；
- 整组随机基线 = (9/10)^3 = 72.9000%（10^4 开奖 × C(4,3)*10^3=4000 配置空间精确枚举）；
- 无形态项、无合尾项、无位置/杀码连用限制；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。
"""
from datetime import datetime
from html import escape
import json
from pathlib import Path

import kill11_tail

START = '<!-- KILL_ELEVEN_START -->'
END = '<!-- KILL_ELEVEN_END -->'

_TITLE = '🎯 定位杀码 3 项（AI 选 3 个位置 · 每位置各杀 1 码）'

STYLE = """
#kill-eleven-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-eleven-ai .k1-title{margin:0 0 8px;font-size:17px;font-weight:700;color:#3730a3}
#kill-eleven-ai .k1-meta{margin:0 0 10px;padding:7px 10px;border-radius:8px;background:#e0e7ff;color:#3730a3;font-size:13px}
#kill-eleven-ai .k1-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:10px 0}
#kill-eleven-ai .k1-card{padding:10px 12px;border:1px solid #c7d2fe;border-radius:10px;background:#fff;box-shadow:0 1px 2px rgba(55,48,163,.06)}
#kill-eleven-ai .k1-card-head{font-size:14px;color:#3730a3;font-weight:700}
#kill-eleven-ai .k1-kill{margin:4px 0 2px;font-size:24px;font-weight:800;color:#b91c1c;letter-spacing:2px}
#kill-eleven-ai .k1-basis{margin:2px 0;color:#4338ca;font-size:12.5px;font-weight:600}
#kill-eleven-ai .k1-keep{color:#065f46;font-size:13px;font-family:ui-monospace,Consolas,monospace}
#kill-eleven-ai .k1-note{margin:4px 0;color:#64748b;font-size:12.5px}
#kill-eleven-ai .k1-reason{margin:8px 0;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;color:#475569;font-size:13px}
#kill-eleven-ai .k1-verify{margin:7px 0;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;color:#334155;font-size:13px}
#kill-eleven-ai .k1-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0}
#kill-eleven-ai .k1-stat{padding:7px 10px;border:1px solid #c7d2fe;border-radius:9px;background:#fff}
#kill-eleven-ai .k1-stat .l{display:block;color:#64748b;font-size:12px}
#kill-eleven-ai .k1-stat .v{display:block;color:#3730a3;font-size:16px;font-weight:700}
#kill-eleven-ai .k1-attack{margin:10px 0;padding:9px 10px;border:1px solid #a5b4fc;border-radius:9px;background:#fff}
#kill-eleven-ai .k1-attack-title{font-size:13px;color:#3730a3;font-weight:700}
#kill-eleven-ai .k1-foot{margin-top:9px;color:#64748b;font-size:12px;line-height:1.65}
#kill-eleven-ai .k1-empty{margin:10px 0;padding:16px;border:1px dashed #a5b4fc;border-radius:10px;background:#fff;color:#64748b}
#kill-eleven-ai .k1-empty b{color:#3730a3}
"""

_BASIS_TEXT = {
    'hot-repeat-neighbor': '热码/重码/邻码',
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
    return ''.join(char for char in kill11_tail.ATTACK_ORDER if char != text)


def _basis_text(value):
    if not value:
        return '-'
    return _BASIS_TEXT.get(str(value), str(value))


def _position_card(key, label, kill, basis=None):
    card = ['<div class="k1-card">']
    card.append('<div class="k1-card-head">' + _escape(label) + '</div>')
    card.append('<div class="k1-kill">排除 ' + _escape(kill) + '</div>')
    card.append('<div class="k1-basis">' + _escape(_basis_text(basis)) + '</div>')
    card.append('<div class="k1-keep">保留码 ' + _escape(_remaining(kill)) + '</div>')
    card.append('</div>')
    return ''.join(card)


def _positions_note(seal):
    positions = seal.get('positions') or {}
    if not isinstance(positions, dict):
        return ''
    picked = [key for key, _label, _index in kill11_tail.POSITIONS if key in positions]
    labels = [kill11_tail.POSITION_LABELS[key] for key in picked]
    free = [label for key, label, _index in kill11_tail.POSITIONS if key not in positions]
    return ('<div class="k1-note">本期自选 %s 个位置：%s；未选位置：%s（攻击语保留 10 码）</div>'
            % (_escape(len(labels)), _escape('、'.join(labels)),
               _escape('、'.join(free) if free else '无')))


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '上期核验：尚无已结算记录（开奖后按真实开奖核验）。'
    actual = ''.join(_escape(value) for value in (settlement.get('actual') or []))
    raw = settlement.get('positions') or {}
    if isinstance(raw, list):
        raw = {str(item.get('key')): item for item in raw if isinstance(item, dict)}
    parts = []
    for key, label, _index in kill11_tail.POSITIONS:
        item = raw.get(key)
        if not isinstance(item, dict):
            continue
        parts.append('%s 实际%s/排%s %s'
                     % (_escape(label), _escape(item.get('actual')),
                        _escape(item.get('kill')), '✓' if item.get('hit') else '✗'))
    legacy = []
    tail = settlement.get('tail')
    if isinstance(tail, dict):
        legacy.append('旧版合尾实际%s/%s %s'
                      % (_escape(tail.get('actual')), _escape(tail.get('kill')),
                         '✓' if tail.get('hit') else '✗'))
    form = settlement.get('form')
    if isinstance(form, dict):
        legacy.append('旧版形态实际%s/%s %s'
                      % (_escape(form.get('actual')), _escape(form.get('form')),
                         '✓' if form.get('hit') else '✗'))
    group_text = '整组命中' if settlement.get('group_hit') else '整组未全中'
    verified_text = '已核验' if settlement.get('verified') else '未计入（非开奖前发布）'
    extra = ('；历史版本项：' + '；'.join(legacy)) if legacy else ''
    return ('上期核验：目标期 %s · 开奖 %s · %s · %s · %s%s'
            % (_escape(settlement.get('target_period')), actual or '-',
               '；'.join(parts) or '-', group_text, verified_text, extra))


def _stats_html(stats):
    if not isinstance(stats, dict):
        stats = {}
    position = stats.get('positions') or {}
    expected = stats.get('expected') or {}
    items = [
        ('今日整组', '%s/%s（%s）' % (_escape(stats.get('group_hits', 0)),
                                    _escape(stats.get('total', 0)),
                                    _pct(stats.get('group_rate')))),
        ('今日单注', '%s/%s（%s）' % (_escape(position.get('hits', 0)),
                                    _escape(position.get('bets', 0)),
                                    _pct(position.get('rate')))),
        ('当前连中/连败', '%s / %s' % (_escape(stats.get('current_win_streak', 0)),
                                     _escape(stats.get('current_loss_streak', 0)))),
        ('今日最长连败', '%s 期' % _escape(stats.get('max_loss_streak', 0))),
        ('待核验', '%s 期' % _escape(stats.get('pending', 0))),
        ('整组理论基线', _pct(expected.get('group_rate'))),
    ]
    html = ['<div class="k1-stats">']
    for label, value in items:
        html.append('<div class="k1-stat"><span class="l">' + label
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
        if (isinstance(item, dict) and item.get('key') == 'kill11_tail'
                and item.get('rule') == kill11_tail.RULE_VERSION):
            row = item
            break
    if row is None:
        return ('<div class="k1-note">累计命中率：同版本尚无累计样本'
                '（自动跟随 data/hitrate_stability.json 刷新）。</div>')
    ci = row.get('ci95') or []
    ci_text = ('[%s, %s]' % (_pct(ci[0]), _pct(ci[1]))) if len(ci) == 2 else '-'
    return ('<div class="k1-note">本版累计：n=%s · 命中 %s · 命中率 %s · 理论 %s · 差值 %s · '
            'CI95 %s · p=%s · %s</div>'
            % (_escape(row.get('n')), _escape(row.get('hits')),
               _pct(row.get('rate')), _pct(row.get('expected')),
               _escape(row.get('delta')), ci_text, _escape(row.get('p')),
               _escape(row.get('cum_verdict'))))


def _version_html(info):
    if not isinstance(info, dict) or not info:
        return ''
    return ('<div class="k1-note">口径 %s · 已发布 %s 期（%s → %s）</div>'
            % (_escape(info.get('rule_version')), _escape(info.get('count')),
               _escape(info.get('first_ref') or '-'), _escape(info.get('last_ref') or '-')))


def _attack_html(seal):
    if not isinstance(seal, dict) or not seal.get('published_at'):
        return ''
    try:
        text = kill11_tail.build_attack(seal)
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
        "var b=document.getElementById('kill11-attack-text');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('kill11-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="k1-attack">'
            '<div class="k1-attack-title">攻击语（复制到平台：3 个被杀位置各保留 9 码 + '
            '1 个未选位置 10 码 + 四定）</div>'
            '<textarea id="kill11-attack-text" readonly rows="4" aria-label="AI 三定位杀码攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #a5b4fc;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea>'
            '<button id="kill11-attack-copy" type="button" onclick="' + copy_action + '" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#4338ca;color:#fff;cursor:pointer;">'
            '复制攻击语</button>'
            '<span id="kill11-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            '<script id="kill11-attack-data" type="application/json">' + payload + '</script>')


def _foot_html():
    return ('<div class="k1-foot">口径（写死）：AI 每期从 千/百/十/个 四个位置中固定选恰好 3 个'
            '不同位置，每个位置各排除 1 个码（0-9），共 3 注/期且全部计入整组；'
            '排除码须取自该位置近 50 期热码 top3、上期重码、上期邻码（±1，0↔9 循环），'
            '不得以上期冷码 / 长遗漏码为依据；位置与杀码不做连用限制（完全由模型每期自选）；'
            '命中 = 该位置实际开奖号 != 该位置排除码（单项 90%）；整组命中 = 3 项同时命中；'
            '整组随机基线 = (9/10)^3 = 72.9000%，用 10^4 开奖 × C(4,3)*10^3=4000 配置空间'
            '精确枚举校验（29,160,000 / 40,000,000）；'
            '只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；'
            '只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。</div>')


def render_block():
    try:
        summary = kill11_tail.current_summary()
    except Exception:
        summary = {}
    if not isinstance(summary, dict):
        summary = {}
    stats = summary.get('stats')
    if not isinstance(stats, dict):
        try:
            stats = kill11_tail.today_stats()
        except Exception:
            stats = {}
    seal = summary.get('seal') or summary.get('previous')
    settlement = summary.get('settlement')
    if not isinstance(seal, dict):
        html = ['<section id="kill-eleven-ai">',
                '<div class="k1-title">' + _TITLE + '</div>',
                '<div class="k1-empty"><b>等待首期封存。</b> 本框架每期开奖后由 AI 从 千/百/十/个 '
                '中固定选 3 个不同位置，各排除 1 码并封存（无单双形态、无合尾）。</div>',
                _foot_html(), '</section>']
        return ''.join(html)
    positions = seal.get('positions') or {}
    if not isinstance(positions, dict):
        positions = {}
    ref = str(seal.get('ref_period') or '')
    target = str(seal.get('target_period') or '')
    html = ['<section id="kill-eleven-ai" data-as-of="' + _escape(ref) + '">']
    html.append('<div class="k1-title">' + _TITLE + '</div>')
    html.append('<div class="k1-meta">参考期 %s · 目标期 %s · AI 自选 3 个位置 · 生成 %s · '
                '模型 %s · 提示词 %s · 数据源 前四球（千/百/十/个）</div>'
                % (_escape(ref), _escape(target), _clock(seal.get('generated_at')),
                   _escape(seal.get('model_label') or seal.get('model') or '-'),
                   _escape(seal.get('prompt_version') or '-')))
    cards = []
    for key, label, _index in kill11_tail.POSITIONS:
        if key in positions:
            cards.append(_position_card(key, label, positions[key]))
    html.append('<div class="k1-grid">' + ''.join(cards) + '</div>')
    html.append(_positions_note(seal))
    reason = seal.get('reason')
    if reason:
        html.append('<div class="k1-reason">AI 理由：' + _escape(reason) + '</div>')
    state = str(seal.get('publication_state') or 'unknown')
    if seal.get('published_at'):
        publish_text = '发布 %s（%s）' % (_clock(seal.get('published_at')),
                                        '已发布' if state == 'published' else _escape(state))
    else:
        publish_text = '发布状态 %s（未写发布戳）' % _escape(state)
    html.append('<div class="k1-note">' + publish_text + ' · 关盘按目标开奖槽位倒推 70 秒 · '
                '整组 %s 项 · 理论基线 %s</div>'
                % (_escape(seal.get('expected_items')), _pct(seal.get('expected_group_rate'))))
    if not isinstance(settlement, dict):
        try:
            settlements = kill11_tail.load_settlements()
        except Exception:
            settlements = []
        settlement = settlements[-1] if settlements else None
    html.append('<div class="k1-verify">' + _settlement_line(settlement) + '</div>')
    html.append(_stats_html(stats))
    html.append(_cumulative_html())
    try:
        html.append(_version_html(kill11_tail.version_info()))
    except Exception:
        pass
    html.append(_attack_html(seal))
    html.append(_foot_html())
    html.append('</section>')
    return ''.join(html)


def apply_kill_eleven_block(content, report=None):
    """把 kill11 区块原地写入面板 content（START/END 标记替换；缺失时按兜底锚点插入）。"""
    try:
        if not isinstance(content, str):
            return content
        block = '<style>' + STYLE + '</style>' + render_block()
        if START in content and END in content:
            head, rest = content.split(START, 1)
            _old, tail_part = rest.split(END, 1)
            return head + START + block + END + tail_part
        if '<!-- KILL_THREE_END -->' in content:
            return content.replace('<!-- KILL_THREE_END -->',
                                   '<!-- KILL_THREE_END -->' + START + block + END, 1)
        marker = '</body>'
        if marker in content:
            return content.replace(marker, START + block + END + marker, 1)
        return content
    except Exception:
        if report is not None:
            try:
                report('kill11-block-failed')
            except Exception:
                pass
        return content
