"""AI 双反向冷形态排除建议面板区块（kill8 独立框架）。

数据源 data/kill8_tail/{seals,settlements}.json，由 scripts/run_kill8_tail.py 每期生成（手动触发）。
与 kill1 / kill3 完全独立：独立 AI 请求 / 封存 / 结算 / 统计 / 面板区块 / 攻击语。

命中口径（写死，面板脚注同步展示）：
- 位置排除数字由用户手动完成，不进本框架计算；
- 单双形态命中 = 目标期实际四位单双形态 != 封存单双形态（反向排除项，4 字符）；
- 大小形态命中 = 目标期实际四位大小形态 != 封存大小形态（反向排除项，4 字符）；
- 整组命中 = 单双 + 大小 2 项同时命中；
- 无合尾、无连用限制；只统计开奖前已发布、开奖后按真实开奖核验的记录；
- 北京时间 05:00 清零；只使用前 4 球（千/百/十/个），第 5 球不参与。

设计要点：
- 自包含 section（id 见 _section_open）+ 内联样式，不依赖面板已有 CSS 类；
- 用 START / END 起止注释标记原地替换；
- 标记缺失时按 KILL_ONE_END -> </body> 顺序插入；
- 任何异常原样返回 html，绝不影响整页刷新。
"""
from datetime import datetime
from html import escape
import json
import math

import kill8_tail

START = '<!-- KILL_EIGHT_START -->'
END = '<!-- KILL_EIGHT_END -->'

# 整组随机基线兜底：(项数, 精确期望命中率)。
# 2 项 = 单双反向冷形态 + 大小反向冷形态；均匀配置下 10^4 全枚举平均期望 (15/16)^2 = 0.8789。
# hitrate_baseline.RULE_BASELINE[('kill8_tail', RULE_VERSION)] 未登记时使用本值。
FALLBACK_BASELINE = (2, 0.8789)

_TITLE = '🤖 AI 双反向冷形态排除建议'

STYLE = """
#kill-eight-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-eight-ai *{box-sizing:border-box}
#kill-eight-ai h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#kill-eight-ai .k8-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#kill-eight-ai .k8-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr));gap:10px;min-width:0;margin-bottom:10px}
#kill-eight-ai .k8-card{border:1px solid #ddd6fe;border-radius:8px;background:#fff;padding:10px 12px;text-align:center;min-width:0}
#kill-eight-ai .k8-pos{font-size:13px;color:#475569}
#kill-eight-ai .k8-form{font-size:26px;font-weight:700;color:#7c3aed;letter-spacing:2px;line-height:1.35}
#kill-eight-ai .k8-sub{font-size:12px;color:#64748b}
#kill-eight-ai .k8-drop{font-size:13px;color:#94a3b8;margin-bottom:6px}
#kill-eight-ai .k8-reason{font-size:13px;color:#475569;margin-bottom:8px}
#kill-eight-ai .k8-stats{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-eight-ai .k8-stats b{font-size:16px;color:#1e3a8a}
#kill-eight-ai .k8-verify{margin-top:6px;font-size:13px;color:#334155}
#kill-eight-ai .k8-attack{margin-top:8px;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-eight-ai .k8-attack-title{font-size:13px;font-weight:600;color:#3730a3}
#kill-eight-ai .k8-foot{margin-top:8px;font-size:12px;color:#94a3b8}
#kill-eight-ai .k8-empty{padding:6px 2px;font-size:14px;color:#475569}
"""


def _section_open(extra=''):
    """section 开头（id 字面量全文件仅在此处出现一次，便于 grep 计数）。"""
    return f'<section id="kill-eight-ai"{extra}>'


def _pct(value):
    return f'{value * 100:.2f}%' if isinstance(value, (int, float)) else '-'


def _clock(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return '-'
    return moment.strftime('%H:%M:%S')


def _stats_html(stats):
    stats = stats if isinstance(stats, dict) else {}
    total = stats.get('total') or 0
    pending = stats.get('pending') or 0
    late = stats.get('late') or 0
    if total:
        parity = stats.get('parity') or {}
        size = stats.get('size') or {}
        join = ' · '.join([
            '整组命中率（2 项全中：单双 + 大小） <b>%s</b>（%s/%s 期）'
            % (_pct(stats.get('group_rate')), stats.get('group_hits'), total),
            '单双冷形态（随机基线93.75%%） <b>%s</b>（%s/%s 期）'
            % (_pct(parity.get('rate')), parity.get('hits'), parity.get('bets')),
            '大小冷形态（随机基线93.75%%） <b>%s</b>（%s/%s 期）'
            % (_pct(size.get('rate')), size.get('hits'), size.get('bets')),
            '待核验 <b>%s</b> 期' % pending,
        ])
    else:
        join = ('今日暂无已核验样本 · 待核验 <b>%s</b> 期 · '
                '最早一期开奖后核验（北京时间 05:00 清零）' % pending)
    if late:
        join += ' · 未计入（发布晚于开奖）%s 期' % late
    legacy = stats.get('legacy') or {}
    if legacy.get('total'):
        join += (' · 旧口径（历史版本封存）%s 期（整组 %s 中）已归档不计入'
                 % (legacy.get('total'), legacy.get('group_hits')))
    win = stats.get('current_win_streak') or 0
    loss = stats.get('current_loss_streak') or 0
    if win:
        join += ' · 当前 <b style="color:#15803d">连中 %s 期</b>' % win
    elif loss:
        join += ' · 当前 <b style="color:#b91c1c">连败 %s 期</b>' % loss
    join += ' · 今日四手连败 <b>%s</b> 次（不重叠计数）' % (stats.get('four_loss_cycles') or 0)
    if total and total < 200:
        sigma = 100.0 * math.sqrt(0.9375 * 0.0625 / total)
        join += (' · 单项样本 n=%s（&lt;200 属小样本：93.75%% 项 1σ≈%.1fpp、2σ≈%.1fpp），'
                 '单日单项只作波动参考' % (total, sigma, 2 * sigma))
    return '<div class="k8-stats"><span>%s</span></div>' % join


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '已发布，等待目标期开奖核验。'
    parts = ['目标期已开奖：' + ''.join(str(value) for value in (settlement.get('actual') or []))]
    parity = settlement.get('parity') or {}
    if isinstance(parity, dict) and parity:
        parts.append('单双形态 %s %s（实际 %s）' % (
            parity.get('form', '-'),
            '命中' if parity.get('hit') is True else '未中', parity.get('actual', '-')))
    size = settlement.get('size') or {}
    if isinstance(size, dict) and size:
        parts.append('大小形态 %s %s（实际 %s）' % (
            size.get('form', '-'),
            '命中' if size.get('hit') is True else '未中', size.get('actual', '-')))
    if settlement.get('verified') is not True:
        parts.append('发布晚于开奖，未计入统计')
    else:
        parts.append('整组 ' + ('<b style="color:#15803d">命中</b>'
                                if settlement.get('group_hit') is True
                                else '<b style="color:#b91c1c">未中</b>'))
    return '；'.join(parts) + '。'


def _version_html():
    """口径追溯行：rule_version、自 ref 起期数、整组精确基线（未登记回退 FALLBACK_BASELINE）。"""
    try:
        rule = str(getattr(kill8_tail, 'RULE_VERSION', '') or '')
        info = {}
        try:
            info = kill8_tail.version_info() or {}
            rule = str(info.get('rule_version') or rule)
        except Exception:
            info = {}
        baseline = None
        try:
            from hitrate_baseline import RULE_BASELINE
            baseline = RULE_BASELINE.get(('kill8_tail', rule))
        except Exception:
            baseline = None
        if not baseline:
            baseline = FALLBACK_BASELINE
        parts = ['口径 ' + escape(rule)]
        first = info.get('first_ref')
        if first:
            parts.append('自 ref ' + escape(str(first)) + ' 起（本版 '
                         + str(int(info.get('count') or 0)) + ' 期）')
        parts.append('整组精确基线 %.2f%%（%d 项）'
                     % (float(baseline[1]) * 100.0, int(baseline[0])))
        parts.append('kill8 独立框架：与 kill1 / kill3 独立封存 / 结算 / 统计')
        return '<div class="k8-drop">' + ' · '.join(parts) + '</div>'
    except Exception:
        return ''


def render_block():
    try:
        data = kill8_tail.current_summary()
        data = data if isinstance(data, dict) else {}
        stats = data.get('stats') or {}
        seal = data.get('seal')
        if not isinstance(seal, dict):
            body = '<div class="k8-empty">本期 AI 建议尚未发布（手动触发脚本后计算，或本轮调用失败）。</div>'
            previous = data.get('previous')
            if isinstance(previous, dict):
                body += ('<div class="k8-meta">最近一次：参考期 %s · 目标期 %s</div>'
                         % (escape(str(previous.get('ref_period', ''))),
                            escape(str(previous.get('target_period', '')))))
            return ('%s%s<style>%s</style><h3>%s</h3>%s%s%s%s%s'
                    % (START, _section_open(), STYLE, _TITLE, body,
                       _stats_html(stats), _version_html(), _foot_html(), END))
        cards = []
        parity_form = seal.get('parity_form')
        if isinstance(parity_form, str) and len(parity_form) == 4:
            cards.append(
                '<div class="k8-card"><div class="k8-pos">%s</div>'
                '<div class="k8-form">%s</div>'
                '<div class="k8-sub">排除该四位单双形态（计入整组）</div></div>'
                % (escape(str(kill8_tail.REVERSE_LABELS['parity'])), escape(parity_form)))
        size_form = seal.get('size_form')
        if isinstance(size_form, str) and len(size_form) == 4:
            cards.append(
                '<div class="k8-card"><div class="k8-pos">%s</div>'
                '<div class="k8-form">%s</div>'
                '<div class="k8-sub">排除该四位大小形态（计入整组）</div></div>'
                % (escape(str(kill8_tail.REVERSE_LABELS['size'])), escape(size_form)))
        reason = escape(str(seal.get('reason') or '')) or '（无）'
        scope_html = ('<div class="k8-drop">整组 2 项：单双冷形态 + 大小冷形态；'
                      '无合尾、无连用限制（每期按冷热重选）；'
                      '位置排除数字由用户手动完成，不在此框架内计算</div>')
        model = seal.get('model_label') or kill8_tail.model_label()
        meta = ('参考期 %s ｜ 目标期 %s ｜ AI 计算 %s（北京时间） ｜ %s'
                % (escape(str(seal.get('ref_period', ''))),
                   escape(str(seal.get('target_period', ''))),
                   escape(_clock(seal.get('generated_at'))), escape(str(model))))
        settlement = data.get('settlement')
        state = seal.get('publication_state')
        if isinstance(settlement, dict):
            verify = _settlement_line(settlement)
        elif state == 'published':
            verify = '已发布（%s），等待目标期开奖核验。' % escape(_clock(seal.get('published_at')))
        else:
            verify = '已封存，等待发布确认。'
        as_of = escape(str(seal.get('ref_period', '')))
        return ('%s%s<style>%s</style><h3>%s</h3>'
                '<div class="k8-meta">%s</div>'
                '<div class="k8-grid">%s</div>%s'
                '<div class="k8-reason">理由：%s</div>'
                '%s%s<div class="k8-verify">%s</div>%s%s%s'
                % (START, _section_open(' data-as-of="%s"' % as_of), STYLE, _TITLE,
                   meta, ''.join(cards), scope_html, reason,
                   _stats_html(stats), _version_html(), verify, _attack_html(seal),
                   _foot_html(), END))
    except Exception:
        return ''


def _attack_html(seal):
    """反向冷形态攻击语 + 复制按钮（复制脚本沿用 kill1 同款，只换元素 id）。"""
    if not isinstance(seal, dict) or not seal.get('published_at'):
        return ''
    try:
        text = kill8_tail.build_attack(seal)
    except Exception:
        return ''
    if not text:
        return ''
    payload = json.dumps({'version': 1,
                          'reference_period': seal.get('ref_period'),
                          'target_period': seal.get('target_period'),
                          'published_at': seal.get('published_at'),
                          'attack': text},
                         ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, new in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e')):
        payload = payload.replace(old, new)
    copy_action = (
        "var b=document.getElementById('kill8-attack-text');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('kill8-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="k8-attack">'
            '<div class="k8-attack-title">攻击语（形态排除；位置排除数字由用户手动完成）</div>'
            '<textarea id="kill8-attack-text" readonly rows="2" aria-label="AI 双反向冷形态排除攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #c7d2fe;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea>'
            '<button id="kill8-attack-copy" type="button" onclick="' + copy_action + '" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#4f46e5;color:#fff;cursor:pointer;">'
            '复制攻击语</button>'
            '<span id="kill8-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            '<script id="kill8-attack-data" type="application/json">' + payload + '</script>')


def _foot_html():
    return ('<div class="k8-foot">'
            '位置排除数字由用户手动完成，<b>不进本框架计算</b>；本框架由 DeepSeek 决定：'
            '从 16 种四位单双形态（单/双，千百十个顺序）中选 1 种、'
            '从 16 种四位大小形态（大/小）中选 1 种，作为本期「冷形态反向排除项」。<br>'
            '冷形态定位依据：近 50 期出现次数少、当前遗漏长（仅历史统计，不代表未来概率）。<br>'
            '命中 = 目标期实际四位单双形态<b>不等于</b>封存单双形态，'
            '且实际四位大小形态<b>不等于</b>封存大小形态（2 项全中才算整组命中）。<br>'
            '无合尾、无连用限制（每期按冷热重选，不沿用上期）；'
            '旧口径（历史版本封存）保留但不计入本统计；'
            '只统计开奖前发布、开奖后核验的记录；北京时间 05:00 清零；'
            '只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。'
            '</div>')


def apply_kill_eight_block(content, report=None):
    """原地替换/插入 AI 双反向冷形态排除区块；任何异常原样返回 content。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        fragment = render_block()
        if not fragment:
            return content
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + fragment + content[end + len(END):]
        for anchor in ('<!-- KILL_ONE_END -->',):
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
