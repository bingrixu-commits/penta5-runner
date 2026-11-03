"""AI 四形态排除（面板候选池挑 3 + 全同形态挑 1）+ 二数合 + 三数合建议面板区块（kill9 独立框架）。

数据源 data/kill9_tail/{seals,settlements}.json，由 scripts/run_kill9_tail.py 每期生成（cron 每 5 分钟自动触发）。
与 kill1 / kill3 / kill8 完全独立：独立 AI 请求 / 封存 / 结算 / 统计 / 面板区块 / 攻击语。

命中口径（写死，面板脚注同步展示）：
- 四个形态均为反向排除项：3 个从面板 kill1/kill3/kill8 本期已发布反向形态池中挑，
  另 1 个从 4 个全同形态（大大大大/小小小小/双双双双/单单单单）中挑 1 个；
  命中 = 目标期实际四位形态 != 封存形态（4 字符，逐位约束至少一位不满足）；
- 二数合：he2_keep 从 02468 保留 4 码，前 4 球任取 2 位 6 种组合和值个位，任一落入保留集即命中；
- 三数合：he3_keep 从 0-9 保留 5 码（2 奇 3 偶或 3 奇 2 偶），任取 3 位 4 种组合和值个位，任一落入保留集即命中；
- 整组命中 = 4 形态（3 池选 + 1 全同）+ 二数合 + 三数合 6 项同时命中；
- 每期由模型完全自主重选（不 hold 不轮换），无手动位置排除输入；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；
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

import kill9_tail

START = '<!-- KILL_NINE_START -->'
END = '<!-- KILL_NINE_END -->'

# 整组随机基线兜底：(项数, 精确期望命中率)。
# 6 项 = 3 池选形态 + 1 全同形态（4 选 1）+ 二数合 + 三数合。
# 精确枚举（不用单项相乘）：E = E_pool3 × E_draw_all_he
#   E_pool3 = 池来源模型（kill1 单位置形态 1 个且类型 1/2 均匀、kill3/kill8 各 parity+size 各 1 个，16 种均匀）去重后均匀挑 3 的
#             E[(1 - k_p/16)(1 - k_s/16)]，超几何精确枚举 = 859005/1048576 = 0.81921101（同 v3）；
#   E_draw_all_he = (1/10^4)·Σ_{10^4 开奖}[(1 - n_all(draw)/4)·h2(draw)·h3(draw)] = 2161303/2500000 = 0.8645212
#                   （n_all = 开奖满足几个全同形态；1 - n_all/4 = 4 个全同形态随机挑 1 个的命中概率；
#                     h2/h3 = 随机保留集命中比例，5/200 个候选）；
#   E = 0.81921101 × 0.8645212 = 0.70822528。
# 单项：池选 3 形态各 15/16=0.9375；全同形态 15/16=0.9375；he2=0.9808；he3=0.9375。
# 算法取证见 ops/README.md v4 台账（_enum_kill9_v4.py / _calc_v4.py）。
# hitrate_baseline.RULE_BASELINE[('kill9_tail', RULE_VERSION)] 未登记时使用本值。
FALLBACK_BASELINE = (6, 0.70822528)

_TITLE = '🤖 AI 四形态排除（池选三 + 全同一）+ 二数合 + 三数合建议'

STYLE = """
#kill-nine-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-nine-ai *{box-sizing:border-box}
#kill-nine-ai h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#kill-nine-ai .k9-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#kill-nine-ai .k9-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,160px),1fr));gap:10px;min-width:0;margin-bottom:10px}
#kill-nine-ai .k9-card{border:1px solid #ddd6fe;border-radius:8px;background:#fff;padding:10px 12px;text-align:center;min-width:0}
#kill-nine-ai .k9-pos{font-size:13px;color:#475569}
#kill-nine-ai .k9-form{font-size:26px;font-weight:700;color:#7c3aed;letter-spacing:2px;line-height:1.35}
#kill-nine-ai .k9-keep{font-size:22px;font-weight:700;color:#047857;letter-spacing:2px;line-height:1.35}
#kill-nine-ai .k9-sub{font-size:12px;color:#64748b}
#kill-nine-ai .k9-drop{font-size:13px;color:#94a3b8;margin-bottom:6px}
#kill-nine-ai .k9-reason{font-size:13px;color:#475569;margin-bottom:8px}
#kill-nine-ai .k9-stats{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-nine-ai .k9-stats b{font-size:16px;color:#1e3a8a}
#kill-nine-ai .k9-verify{margin-top:6px;font-size:13px;color:#334155}
#kill-nine-ai .k9-attack{margin-top:8px;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-nine-ai .k9-attack-title{font-size:13px;font-weight:600;color:#3730a3}
#kill-nine-ai .k9-foot{margin-top:8px;font-size:12px;color:#94a3b8}
#kill-nine-ai .k9-empty{padding:6px 2px;font-size:14px;color:#475569}
"""


def _section_open(extra=''):
    """section 开头（id 字面量全文件仅在此处出现一次，便于 grep 计数）。"""
    return f'<section id="kill-nine-ai"{extra}>'


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
        form_rows = stats.get('forms')
        form_rows = form_rows if isinstance(form_rows, list) else []
        he2 = stats.get('he2_keep') or {}
        he3 = stats.get('he3_keep') or {}
        parts = ['整组命中率（6 项全中） <b>%s</b>（%s/%s 期）'
                 % (_pct(stats.get('group_rate')), stats.get('group_hits'), total)]
        for index in range(4):
            item = form_rows[index] if index < len(form_rows) else {}
            item = item if isinstance(item, dict) else {}
            base = '75.00' if index == 3 else '93.75'
            parts.append('形态%d（随机基线%s%%） <b>%s</b>（%s/%s 期）'
                         % (index + 1, base, _pct(item.get('rate')),
                            item.get('hits', '-'), item.get('bets', total)))
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
    join += ' · 今日四手连败 <b>%s</b> 次（不重叠计数）' % (stats.get('four_loss_cycles') or 0)
    if total and total < 200:
        sigma = 100.0 * math.sqrt(0.70822528 * 0.29177472 / total)
        join += (' · 整组样本 n=%s（&lt;200 属小样本：70.82%% 整组 1σ≈%.1fpp、2σ≈%.1fpp），'
                 '单日整组只作波动参考' % (total, sigma, 2 * sigma))
    return '<div class="k9-stats"><span>%s</span></div>' % join


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '已发布，等待目标期开奖核验。'
    parts = ['目标期已开奖：' + ''.join(str(value) for value in (settlement.get('actual') or []))]
    form_items = settlement.get('forms')
    if isinstance(form_items, list):
        for index, item in enumerate(form_items, start=1):
            if not isinstance(item, dict) or not item:
                continue
            parts.append('形态%d %s %s（实际 %s）' % (
                index, item.get('form', '-'),
                '命中' if item.get('hit') is True else '未中', item.get('actual', '-')))
    else:
        for field, label in (('size', '大小'), ('parity', '单双'),
                             ('all', '全同'), ('mixed', '混合')):
            item = settlement.get(field) or {}
            if isinstance(item, dict) and item:
                parts.append('%s形态 %s %s（实际 %s）' % (
                    label, item.get('form', '-'),
                    '命中' if item.get('hit') is True else '未中', item.get('actual', '-')))
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


def _version_html():
    """口径追溯行：rule_version、自 ref 起期数、整组精确基线（未登记回退 FALLBACK_BASELINE）。"""
    try:
        rule = str(getattr(kill9_tail, 'RULE_VERSION', '') or '')
        info = {}
        try:
            info = kill9_tail.version_info() or {}
            rule = str(info.get('rule_version') or rule)
        except Exception:
            info = {}
        baseline = None
        try:
            from hitrate_baseline import RULE_BASELINE
            baseline = RULE_BASELINE.get(('kill9_tail', rule))
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
        parts.append('kill9 独立框架：与 kill1 / kill3 / kill8 独立封存 / 结算 / 统计')
        return '<div class="k9-drop">' + ' · '.join(parts) + '</div>'
    except Exception:
        return ''


def _form_card(label, value, sub):
    return ('<div class="k9-card"><div class="k9-pos">%s</div>'
            '<div class="k9-form">%s</div>'
            '<div class="k9-sub">%s</div></div>'
            % (escape(str(label)), escape(value), escape(sub)))


def _keep_card(label, value, sub):
    return ('<div class="k9-card"><div class="k9-pos">%s</div>'
            '<div class="k9-keep">%s</div>'
            '<div class="k9-sub">%s</div></div>'
            % (escape(str(label)), escape(value), escape(sub)))


def render_block():
    try:
        data = kill9_tail.current_summary()
        data = data if isinstance(data, dict) else {}
        stats = data.get('stats') or {}
        seal = data.get('seal')
        if not isinstance(seal, dict):
            body = ('<div class="k9-empty">本期 AI 建议尚未发布（cron 每 5 分钟自动结算上一期并封存下一期，'
                    '或本轮调用失败）。</div>')
            previous = data.get('previous')
            if isinstance(previous, dict):
                body += ('<div class="k9-meta">最近一次：参考期 %s · 目标期 %s</div>'
                         % (escape(str(previous.get('ref_period', ''))),
                            escape(str(previous.get('target_period', '')))))
            return ('%s%s<style>%s</style><h3>%s</h3>%s%s%s%s%s%s'
                    % (START, _section_open(), STYLE, _TITLE, body,
                       _attack_html(None), _stats_html(stats), _version_html(),
                       _foot_html(), '</section>' + END))
        cards = []
        pool_index = {}
        for entry in (seal.get('form_pool') or []):
            if isinstance(entry, dict) and entry.get('form'):
                pool_index[entry['form']] = entry
        sealed_forms = seal.get('forms') if isinstance(seal.get('forms'), list) else []
        sealed_kinds = seal.get('form_kinds') if isinstance(seal.get('form_kinds'), list) else []
        for index, value in enumerate(sealed_forms, start=1):
            if not isinstance(value, str) or len(value) != 4:
                continue
            info = pool_index.get(value) or {}
            sources = '、'.join(info.get('source_labels') or info.get('sources') or [])
            sub = '排除该四位形态（计入整组）'
            if sources:
                sub = '来源 %s；%s' % (sources, sub)
            if info.get('stale'):
                sub += '；来源期 %s' % str(info.get('target_period') or '非本期')
            kind = sealed_kinds[index - 1] if index - 1 < len(sealed_kinds) else None
            if kind in ('size', 'parity'):
                sub += '（%s）' % kill9_tail.FORM_LABELS.get(kind, kind)
            cards.append(_form_card('形态%d' % index, value, sub))
        all_form = seal.get('all_form')
        if isinstance(all_form, str) and len(all_form) == 4:
            cards.append(_form_card('形态4', all_form,
                                    '全同形态（4 个中挑 1，原样使用；计入整组）'))
        he2_keep = seal.get('he2_keep')
        if isinstance(he2_keep, str) and he2_keep:
            cards.append(_keep_card('二数合', he2_keep,
                                    '保留 4 码，任一两位和值个位落入即命中（计入整组）'))
        he3_keep = seal.get('he3_keep')
        if isinstance(he3_keep, str) and he3_keep:
            cards.append(_keep_card('三数合', he3_keep,
                                    '保留 5 码，任一三位和值个位落入即命中（计入整组）'))
        reason = escape(str(seal.get('reason') or '')) or '（无）'
        scope_html = ('<div class="k9-drop">整组 6 项：3 个池选形态（从面板 kill1/kill3/kill8 本期已发布'
                      '反向形态池中挑选）+ 1 个全同形态（从 大大大大/小小小小/双双双双/单单单单 挑 1）'
                      '+ 二数合保留4码 + 三数合保留5码；无连用限制（每期重选）；'
                      '模型只能从候选池与全同形态中挑选，不得改写或自造（本地校验强制）</div>')
        model = seal.get('model_label') or kill9_tail.model_label()
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
                '<div class="k9-meta">%s</div>'
                '<div class="k9-grid">%s</div>%s'
                '<div class="k9-reason">理由：%s</div>'
                '%s%s<div class="k9-verify">%s</div>%s%s%s'
                % (START, _section_open(' data-as-of="%s"' % as_of), STYLE, _TITLE,
                   meta, ''.join(cards), scope_html, reason,
                   _stats_html(stats), _version_html(), verify, _attack_html(seal),
                   _foot_html(), '</section>' + END))
    except Exception:
        return ''


def _attack_html(seal):
    """完整攻击语（AI 四形态排除 + 二数合 + 三数合）+ 复制按钮；seal 为 None 时只有空攻击语。"""
    phrase = ''
    if isinstance(seal, dict):
        try:
            phrase = kill9_tail.build_attack(seal)
        except Exception:
            phrase = ''
    copy_action = (
        "var a=document.getElementById('k9-attack');if(!a)return;"
        "var t=a.value||a.textContent||'';var s=document.getElementById('k9-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var x=document.createElement('textarea');x.value=t;"
        "x.setAttribute('readonly','');x.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(x);x.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}x.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="k9-attack">'
            '<div class="k9-attack-title">完整攻击语（AI 四形态排除 + 二数合 + 三数合）</div>'
            '<textarea id="k9-attack" readonly rows="2" '
            'aria-label="kill9 完整攻击语（四形态排除 + 二数合 + 三数合）" '
            'style="display:block;width:100%%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #c7d2fe;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            '%s</textarea>'
            '<button id="k9-attack-copy" type="button" onclick="%s" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#4f46e5;color:#fff;cursor:pointer;">'
            '复制完整攻击语</button>'
            '<span id="k9-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            % (escape(phrase), copy_action))


def _foot_html():
    return ('<div class="k9-foot">'
            '本框架的 4 个形态由 DeepSeek 选出：其中 3 个从候选池中挑选——候选池 = 面板 kill1（单位置杀码+一形态）、'
            'kill3（三位置杀码）、kill8（双反向冷形态）本期已发布的四位反向形态去重后汇总（来源期如实标注）；'
            '另 1 个从 4 个全同形态（大大大大/小小小小/双双双双/单单单单）中挑 1 个（原样使用，不得与池选形态重复）。'
            '模型只能挑互不相同的四位形态作为本期「反向形态排除项」，不得改写、不得自造；'
            '另从 02468 保留 4 码（二数合）、从 0-9 保留 5 码且 2 奇 3 偶或 3 奇 2 偶（三数合）。<br>'
            '命中 = 4 个形态的目标期实际四位形态<b>均不等于</b>封存形态，'
            '且二数合/三数合任一组合和值个位落入保留集（6 项全中才算整组命中）。<br>'
            '无连用限制（每期重选，不沿用上期），无手动位置排除输入；'
            '旧口径（历史版本封存）保留但不计入本统计；'
            '只统计开奖前发布、开奖后核验的记录；北京时间 05:00 清零；'
            '只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。'
            '</div>')


def apply_kill_nine_block(content, report=None):
    """原地替换/插入 AI 四形态排除 + 二数合 + 三数合区块；任何异常原样返回 content。"""
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
