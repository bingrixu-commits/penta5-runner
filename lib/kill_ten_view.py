"""AI 二数合 + 三数合建议面板区块（kill10 独立框架）。

数据源 data/kill10_tail/{seals,settlements}.json，由 scripts/run_kill10_tail.py 每期生成（cron 每 5 分钟自动触发）。
独立 AI 请求 / 封存 / 结算 / 统计 / 面板区块 / 攻击语，不与其它框架共享数据。

命中口径（写死，面板脚注同步展示）：
- 二数合：he2_keep 从 02468 中恰好保留 4 个不同数字（5 个候选全部枚举）；
  前 4 球（千/百/十/个）任取 2 位的 6 种组合和值个位，任一落入保留集即命中；单项随机基线 98.08%；
- 三数合：he3_keep 从 0-9 中恰好保留 5 个不同数字且必须 2 奇 3 偶或 3 奇 2 偶（200 个候选）；
  前 4 球任取 3 位的 4 种组合和值个位，任一落入保留集即命中；单项随机基线 93.75%；
- 整组命中 = 二数合与三数合同时命中（2 项全中）；
  整组随机期望 0.92022400（10^4 开奖空间 × 配置空间精确枚举，非单项相乘）；
- 每期由模型完全自主重选（不 hold 不轮换），无候选池、无形态、无定位排除；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；
- 北京时间 05:00 清零；只使用前 4 球（千/百/十/个），第 5 球不参与。

设计要点：
- 自包含 section（id 见 _section_open）+ 内联样式，不依赖面板已有 CSS 类；
- 用 START / END 起止注释标记原地替换；
- 标记缺失时按 KILL_ONE_END -> </body> 顺序插入；
- 任何异常 render_block 返回空串、apply_kill_ten_block 原样返回 html，绝不影响整页刷新。
"""
from datetime import datetime
from html import escape
import math

import kill10_tail

START = '<!-- KILL_TEN_START -->'
END = '<!-- KILL_TEN_END -->'

# 整组随机基线兜底：(项数, 精确期望命中率)。
# 2 项 = 二数合 he2 保留 4 码 + 三数合 he3 保留 5 码，整组命中要求两项同时命中。
# 期望值由 10^4 种开奖组合 × 配置空间精确枚举校准（不按单项相乘：he2 配置 5 种 × he3 配置 200 种，
# 两项共享同一组开奖号，存在相关性）：E = 0.92022400。
# 单项基线：he2 = 0.9808；he3 = 0.9375。
# hitrate_baseline.RULE_BASELINE[('kill10_tail', RULE_VERSION)] 未登记时回退本值。
FALLBACK_BASELINE = (2, 0.92022400)

_TITLE = '🤖 AI 二数合 + 三数合建议'

STYLE = """
#kill-ten-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-ten-ai *{box-sizing:border-box}
#kill-ten-ai h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#kill-ten-ai .k10-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#kill-ten-ai .k10-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,160px),1fr));gap:10px;min-width:0;margin-bottom:10px}
#kill-ten-ai .k10-card{border:1px solid #ddd6fe;border-radius:8px;background:#fff;padding:10px 12px;text-align:center;min-width:0}
#kill-ten-ai .k10-pos{font-size:13px;color:#475569}
#kill-ten-ai .k10-keep{font-size:26px;font-weight:700;color:#047857;letter-spacing:2px;line-height:1.35}
#kill-ten-ai .k10-sub{font-size:12px;color:#64748b}
#kill-ten-ai .k10-drop{font-size:13px;color:#94a3b8;margin-bottom:6px}
#kill-ten-ai .k10-reason{font-size:13px;color:#475569;margin-bottom:8px}
#kill-ten-ai .k10-stats{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-ten-ai .k10-stats b{font-size:16px;color:#1e3a8a}
#kill-ten-ai .k10-verify{margin-top:6px;font-size:13px;color:#334155}
#kill-ten-ai .k10-attack{margin-top:8px;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-ten-ai .k10-attack-title{font-size:13px;font-weight:600;color:#3730a3}
#kill-ten-ai .k10-foot{margin-top:8px;font-size:12px;color:#94a3b8}
#kill-ten-ai .k10-empty{padding:6px 2px;font-size:14px;color:#475569}
"""


def _section_open(extra=''):
    """section 开头（id 字面量全文件仅在此处出现一次，便于 grep 计数）。"""
    return f'<section id="kill-ten-ai"{extra}>'


def _pct(value):
    return f'{value * 100:.2f}%' if isinstance(value, (int, float)) else '-'


def _clock(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return '-'
    return moment.strftime('%H:%M:%S')


def _stats_html(stats):
    """统计行：整组 2 项命中率 + 二数合/三数合单项保留率 + 待核验/连中连败/累计四手连败 + 小样本波动。"""
    stats = stats if isinstance(stats, dict) else {}
    total = stats.get('total') or 0
    pending = stats.get('pending') or 0
    late = stats.get('late') or 0
    if total:
        he2 = stats.get('he2_keep') or {}
        he3 = stats.get('he3_keep') or {}
        parts = ['整组命中率（2 项全中） <b>%s</b>（%s/%s 期）'
                 % (_pct(stats.get('group_rate')), stats.get('group_hits'), total)]
        parts.append('二数合保留（随机基线98.08%%） <b>%s</b>（%s/%s 期）'
                     % (_pct(he2.get('rate')), he2.get('hits'), he2.get('bets')))
        parts.append('三数合保留（随机基线93.75%%） <b>%s</b>（%s/%s 期）'
                     % (_pct(he3.get('rate')), he3.get('hits'), he3.get('bets')))
        parts.append('待核验 <b>%s</b> 期' % pending)
        join = ' · '.join(parts)
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
    join += (' · 累计四手连败 <b>%s</b> 次（自本版本上线起，跨天不重叠计数）'
             % (stats.get('four_loss_cycles_total') or 0))
    if total and total < 200:
        base_rate = FALLBACK_BASELINE[1]
        sigma = 100.0 * math.sqrt(base_rate * (1.0 - base_rate) / total)
        join += (' · 整组样本 n=%s（&lt;200 属小样本：%.2f%% 整组 1σ≈%.1fpp、2σ≈%.1fpp），'
                 '单日整组只作波动参考' % (total, base_rate * 100.0, sigma, 2 * sigma))
    return '<div class="k10-stats"><span>%s</span></div>' % join


def _settlement_line(settlement):
    """结算行：目标期实际和值个位 + 二数合/三数合逐项命中 + 整组命中。"""
    if not isinstance(settlement, dict):
        return '已发布，等待目标期开奖核验。'
    parts = ['目标期已开奖：' + ''.join(str(value) for value in (settlement.get('actual') or []))]
    for field, label in (('he2_keep', '二数合'), ('he3_keep', '三数合')):
        item = settlement.get(field) or {}
        if isinstance(item, dict) and item:
            parts.append('%s保留 %s %s（实际 %s）' % (
                label, item.get('keep', '-'),
                '命中' if item.get('hit') is True else '未中', item.get('actual', '-')))
    if settlement.get('verified') is not True:
        parts.append('发布晚于开奖，未计入统计')
    else:
        parts.append('整组 ' + ('<b style="color:#15803d">命中</b>'
                                if settlement.get('group_hit') is True
                                else '<b style="color:#b91c1c">未中</b>'))
    return '；'.join(parts) + '。'


def _version_html(seal=None):
    """口径追溯行：rule_version、自 ref 起期数、整组精确基线（未登记按 FALLBACK_BASELINE 回退）。"""
    try:
        rule = str(getattr(kill10_tail, 'RULE_VERSION', '') or '')
        info = {}
        try:
            info = kill10_tail.version_info() or {}
            rule = str(info.get('rule_version') or rule)
        except Exception:
            info = {}
        seal = seal if isinstance(seal, dict) else {}
        display_rule = str(seal.get('rule_version') or '') or rule
        baseline = None
        try:
            from hitrate_baseline import RULE_BASELINE
        except Exception:
            RULE_BASELINE = {}
        for candidate in (display_rule, rule):
            if candidate and not baseline:
                baseline = RULE_BASELINE.get(('kill10_tail', candidate))
        if not baseline:
            baseline = FALLBACK_BASELINE
        parts = ['口径 ' + escape(display_rule)]
        first = info.get('first_ref')
        if first:
            parts.append('自 ref ' + escape(str(first)) + ' 起（本版 '
                         + str(int(info.get('count') or 0)) + ' 期）')
        parts.append('整组精确基线 %.2f%%（%d 项）'
                     % (float(baseline[1]) * 100.0, int(baseline[0])))
        parts.append('kill10 独立框架：与 kill1 / kill3 / kill8 独立封存 / 结算 / 统计')
        return '<div class="k10-drop">' + ' · '.join(parts) + '</div>'
    except Exception:
        return ''


def _keep_card(label, value, sub):
    return ('<div class="k10-card"><div class="k10-pos">%s</div>'
            '<div class="k10-keep">%s</div>'
            '<div class="k10-sub">%s</div></div>'
            % (escape(str(label)), escape(value), escape(sub)))


def _attack_html(seal):
    """完整攻击语（AI 二数合 + 三数合）+ 复制按钮；seal 为 None 时只有空攻击语。"""
    phrase = ''
    if isinstance(seal, dict):
        try:
            phrase = kill10_tail.build_attack(seal)
        except Exception:
            phrase = ''
    copy_action = (
        "var a=document.getElementById('k10-attack');if(!a)return;"
        "var t=a.value||a.textContent||'';var s=document.getElementById('k10-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var x=document.createElement('textarea');x.value=t;"
        "x.setAttribute('readonly','');x.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(x);x.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}x.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="k10-attack">'
            '<div class="k10-attack-title">完整攻击语（AI 二数合 + 三数合）</div>'
            '<textarea id="k10-attack" readonly rows="2" '
            'aria-label="kill10 完整攻击语（二数合 + 三数合）" '
            'style="display:block;width:100%%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #c7d2fe;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            '%s</textarea>'
            '<button id="k10-attack-copy" type="button" onclick="%s" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#4f46e5;color:#fff;cursor:pointer;">'
            '复制完整攻击语</button>'
            '<span id="k10-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            % (escape(phrase), copy_action))


def _foot_html():
    return ('<div class="k10-foot">'
            '本框架由 DeepSeek 每期在固定候选空间中自主选择两个保留码集合：<br>'
            '二数合：从 02468 中恰好保留 4 个不同数字（5 个候选全部枚举），前 4 球任取两位的 6 种和值个位'
            '任一落入保留集即命中（单项随机基线 98.08%）。<br>'
            '三数合：从 0-9 中恰好保留 5 个不同数字且必须 2 奇 3 偶或 3 奇 2 偶（200 个候选），'
            '前 4 球任取三位的 4 种和值个位任一落入保留集即命中（单项随机基线 93.75%）。<br>'
            '命中 = 二数合与三数合<b>同时</b>命中（2 项全中才算整组命中，整组随机期望 92.02%）。<br>'
            '无连用限制（每期重选，不沿用上期）；旧口径（历史版本封存）保留但不计入本统计；'
            '只统计开奖前发布、开奖后核验的记录；累计四手连败自本版本上线起跨天叠加：'
            '连续 4 期整组未全中记 1 次、8 期记 2 次，赢一次清零，不随 05:00 清零；'
            '北京时间 05:00 清零（用于当日命中率等当日统计）；'
            '只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。'
            '</div>')


def render_block():
    try:
        data = kill10_tail.current_summary()
        data = data if isinstance(data, dict) else {}
        stats = data.get('stats') or {}
        seal = data.get('seal')
        if not isinstance(seal, dict):
            body = ('<div class="k10-empty">本期 AI 建议尚未发布（cron 每 5 分钟自动结算上一期并封存下一期，'
                    '或本轮调用失败）。</div>')
            previous = data.get('previous')
            if isinstance(previous, dict):
                body += ('<div class="k10-meta">最近一次：参考期 %s · 目标期 %s</div>'
                         % (escape(str(previous.get('ref_period', ''))),
                            escape(str(previous.get('target_period', '')))))
            return ('%s%s<style>%s</style><h3>%s</h3>%s%s%s%s%s'
                    % (START, _section_open(), STYLE, _TITLE, body,
                       _attack_html(None), _stats_html(stats), _version_html(None),
                       _foot_html() + '</section>' + END))
        cards = []
        he2_keep = seal.get('he2_keep')
        if isinstance(he2_keep, str) and he2_keep:
            cards.append(_keep_card('二数合', he2_keep,
                                    '保留 4 码，任一两位和值个位落入即命中（计入整组）'))
        he3_keep = seal.get('he3_keep')
        if isinstance(he3_keep, str) and he3_keep:
            cards.append(_keep_card('三数合', he3_keep,
                                    '保留 5 码，任一三位和值个位落入即命中（计入整组）'))
        reason = escape(str(seal.get('reason') or '')) or '（无）'
        scope_html = ('<div class="k10-drop">整组 2 项：二数合保留 4 码（从 02468 中保留 4 个不同数字）'
                      '+ 三数合保留 5 码（从 0-9 中保留 5 个不同数字且 2 奇 3 偶或 3 奇 2 偶）；'
                      '无连用限制（每期重选）；模型输出经本地严格校验（不补默认）</div>')
        model = seal.get('model_label') or kill10_tail.model_label()
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
                '<div class="k10-meta">%s</div>'
                '<div class="k10-grid">%s</div>%s'
                '<div class="k10-reason">理由：%s</div>'
                '%s%s<div class="k10-verify">%s</div>%s%s%s'
                % (START, _section_open(' data-as-of="%s"' % as_of), STYLE, _TITLE,
                   meta, ''.join(cards), scope_html, reason,
                   _stats_html(stats), _version_html(seal), verify, _attack_html(seal),
                   _foot_html(), '</section>' + END))
    except Exception:
        return ''


def apply_kill_ten_block(content, report=None):
    """原地替换/插入 AI 二数合 + 三数合区块；任何异常原样返回 content。"""
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
