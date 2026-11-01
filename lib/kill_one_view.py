"""AI 单位置杀码 + 一合尾 + 一形态建议面板区块（kill1 独立框架）。

数据源 data/kill1_tail/{seals,settlements}.json，由 scripts/run_kill1_tail.py 每期生成。
与 kill3 完全独立：独立 AI 请求 / 封存 / 结算 / 统计 / 面板区块 / 攻击语。

命中口径（写死，面板脚注同步展示）：
- 位置命中 = 该位置实际开奖号 != 排除码（恰好 1 个位置，1 注/期）；
- 合尾命中 = 该合尾实际和值个位 != 排除码（5 种合尾选 1）；
- 反向形态命中 = 目标期实际四位单双/大小形态 != 封存形态（反向排除项，4 字符）；
- 整组命中 = 1 位置 + 1 合尾 + 1 项反向形态同时命中（共 3 项）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与。

设计要点：
- 自包含 section（id 见 _section_open）+ 内联样式，不依赖面板已有 CSS 类；
- 用 START / END 起止注释标记原地替换；
- 标记缺失时按 KILL_THREE_END -> </body> 顺序插入；
- 任何异常原样返回 html，绝不影响整页刷新。
"""
from datetime import datetime
from html import escape
import json
import math

import kill1_tail

START = '<!-- KILL_ONE_START -->'
END = '<!-- KILL_ONE_END -->'

# 整组随机基线兜底：(项数, 精确期望命中率)。
# 3 项 = 1 位置 + 1 合尾 + 1 项反向形态；均匀配置下 10^4 全枚举平均期望 0.7538。
# hitrate_baseline.RULE_BASELINE[('kill1_tail', RULE_VERSION)] 未登记时使用本值。
FALLBACK_BASELINE = (3, 0.7538)

_TITLE = '🤖 AI 单位置杀码 + 一合尾 + 一形态建议'

_PARITY_OPPOSITE = {'单': '双', '双': '单'}
_SIZE_OPPOSITE = {'大': '小', '小': '大'}

STYLE = """
#kill-one-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-one-ai *{box-sizing:border-box}
#kill-one-ai h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#kill-one-ai .k1-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#kill-one-ai .k1-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr));gap:10px;min-width:0;margin-bottom:10px}
#kill-one-ai .k1-card{border:1px solid #c7d2fe;border-radius:8px;background:#fff;padding:10px 12px;text-align:center;min-width:0}
#kill-one-ai .k1-card.k1-tail{border-color:#fecaca;background:#fff7f7}
#kill-one-ai .k1-card.k1-pattern{border-color:#ddd6fe;background:#faf8ff}
#kill-one-ai .k1-pos{font-size:13px;color:#475569}
#kill-one-ai .k1-kill{font-size:32px;font-weight:700;color:#1d4ed8;line-height:1.2}
#kill-one-ai .k1-form{font-size:22px;font-weight:700;color:#7c3aed;letter-spacing:2px;line-height:1.35}
#kill-one-ai .k1-card.k1-tail .k1-kill{color:#b91c1c}
#kill-one-ai .k1-sub{font-size:12px;color:#64748b}
#kill-one-ai .k1-drop{font-size:13px;color:#94a3b8;margin-bottom:6px}
#kill-one-ai .k1-reason{font-size:13px;color:#475569;margin-bottom:8px}
#kill-one-ai .k1-stats{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-one-ai .k1-stats b{font-size:16px;color:#1e3a8a}
#kill-one-ai .k1-verify{margin-top:6px;font-size:13px;color:#334155}
#kill-one-ai .k1-attack{margin-top:8px;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-one-ai .k1-attack-title{font-size:13px;font-weight:600;color:#3730a3}
#kill-one-ai .k1-foot{margin-top:8px;font-size:12px;color:#94a3b8}
#kill-one-ai .k1-empty{padding:6px 2px;font-size:14px;color:#475569}
"""


def _section_open(extra=''):
    """section 开头（id 字面量全文件仅在此处出现一次，便于 grep 计数）。"""
    return f'<section id="kill-one-ai"{extra}>'


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
        positions = stats.get('positions') or {}
        tail_stats = stats.get('tail') or {}
        form_stats = stats.get('form') or {}
        types = form_stats.get('types') or {}
        type_bits = []
        for key, short in (('reverse_parity', '单双'), ('reverse_size', '大小')):
            entry = types.get(key) or {}
            if isinstance(entry, dict) and entry:
                type_bits.append('%s %s/%s' % (short, entry.get('hits'), entry.get('bets')))
        form_detail = ('；' + ' · '.join(type_bits)) if type_bits else ''
        join = ' · '.join([
            '整组命中率（3 项全中：1 位置 + 合尾 + 1 形态） <b>%s</b>（%s/%s 期）'
            % (_pct(stats.get('group_rate')), stats.get('group_hits'), total),
            '位置杀码（1 注/期，随机基线90%%） <b>%s</b>（%s/%s 注）'
            % (_pct(positions.get('rate')), positions.get('hits'), positions.get('bets')),
            '合尾杀码（随机基线90%%） <b>%s</b>（%s/%s 期）'
            % (_pct(tail_stats.get('rate')), tail_stats.get('hits'), tail_stats.get('bets')),
            '反向形态（单双/大小按 form_type，随机基线93.75%%） <b>%s</b>（%s/%s 期%s）'
            % (_pct(form_stats.get('rate')), form_stats.get('hits'),
               form_stats.get('bets'), form_detail),
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
        sigma = 100.0 * math.sqrt(0.9 * 0.1 / total)
        join += (' · 单项样本 n=%s（&lt;200 属小样本：90%% 项 1σ≈%.1fpp、2σ≈%.1fpp），'
                 '单日单项只作波动参考' % (total, sigma, 2 * sigma))
    return '<div class="k1-stats"><span>%s</span></div>' % join


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '已发布，等待目标期开奖核验。'
    positions = settlement.get('positions') or {}
    counted = settlement.get('counted') or list(positions)
    parts = ['目标期已开奖：' + ''.join(str(value) for value in (settlement.get('actual') or []))]
    for key in counted:
        entry = positions.get(key) or {}
        if not isinstance(entry, dict):
            continue
        label = kill1_tail.POSITION_LABELS.get(key, key)
        parts.append('%s杀 %s %s（实际 %s）' % (
            label, entry.get('kill', '-'),
            '命中' if entry.get('hit') is True else '未中', entry.get('actual', '-')))
    tail = settlement.get('tail') or {}
    if isinstance(tail, dict) and tail:
        parts.append('%s合尾杀 %s %s（实际 %s）' % (
            tail.get('label') or '', tail.get('kill', '-'),
            '命中' if tail.get('hit') is True else '未中', tail.get('actual', '-')))
    form = settlement.get('form') or {}
    if isinstance(form, dict) and form:
        parts.append('%s %s %s（实际 %s）' % (
            form.get('label') or form.get('type') or '反向形态', form.get('form') or '',
            '命中' if form.get('hit') is True else '未中', form.get('actual', '-')))
    if settlement.get('verified') is not True:
        parts.append('发布晚于开奖，未计入统计')
    else:
        parts.append('整组 ' + ('<b style="color:#15803d">命中</b>'
                                if settlement.get('group_hit') is True
                                else '<b style="color:#b91c1c">未中</b>'))
    return '；'.join(parts) + '。'


def _form_derivation(seal):
    """形态推导行：被杀位置取杀码形态反面，其余 3 个位置字符由 AI 自选。"""
    positions = seal.get('positions') or {}
    if not isinstance(positions, dict):
        return ''
    parts = []
    for key, label, _index in kill1_tail.POSITIONS:
        if key not in positions:
            continue
        try:
            reverse_parity, reverse_size = kill1_tail.reverse_attributes(int(positions[key]))
        except (TypeError, ValueError):
            return ''
        killed_parity = _PARITY_OPPOSITE.get(reverse_parity, reverse_parity)
        killed_size = _SIZE_OPPOSITE.get(reverse_size, reverse_size)
        parts.append('%s杀 %s（%s/%s）→ 形态必须取反 %s/%s' % (
            label, positions[key], killed_parity, killed_size, reverse_parity, reverse_size))
    if not parts:
        return ''
    form_type = str(seal.get('form_type') or '')
    form_label = kill1_tail.REVERSE_LABELS.get(form_type, form_type or '反向形态')
    return ('形态推导（%s）：%s；被杀位置取杀码形态反面；其余 3 个位置字符由 AI 自选'
            % (form_label, ' · '.join(parts)))


def _version_html():
    """口径追溯行：rule_version、自 ref 起期数、整组精确基线（未登记回退 FALLBACK_BASELINE）。"""
    try:
        rule = str(getattr(kill1_tail, 'RULE_VERSION', '') or '')
        info = {}
        try:
            info = kill1_tail.version_info() or {}
            rule = str(info.get('rule_version') or rule)
        except Exception:
            info = {}
        baseline = None
        try:
            from hitrate_baseline import RULE_BASELINE
            baseline = RULE_BASELINE.get(('kill1_tail', rule))
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
        parts.append('kill1 独立框架：与 kill3 独立封存 / 结算 / 统计')
        return '<div class="k1-drop">' + ' · '.join(parts) + '</div>'
    except Exception:
        return ''


def render_block():
    try:
        data = kill1_tail.current_summary()
        data = data if isinstance(data, dict) else {}
        stats = data.get('stats') or {}
        seal = data.get('seal')
        if not isinstance(seal, dict):
            body = '<div class="k1-empty">本期 AI 建议尚未发布（等待开奖后计算，或本轮调用失败）。</div>'
            previous = data.get('previous')
            if isinstance(previous, dict):
                body += ('<div class="k1-meta">最近一次：参考期 %s · 目标期 %s</div>'
                         % (escape(str(previous.get('ref_period', ''))),
                            escape(str(previous.get('target_period', '')))))
            return ('%s%s<style>%s</style><h3>%s</h3>%s%s%s%s%s'
                    % (START, _section_open(), STYLE, _TITLE, body,
                       _stats_html(stats), _version_html(), _foot_html(), END))
        cards = []
        positions = seal.get('positions') or {}
        if isinstance(positions, dict):
            for key, label, _index in kill1_tail.POSITIONS:
                if key not in positions:
                    continue
                cards.append(
                    '<div class="k1-card"><div class="k1-pos">%s</div>'
                    '<div class="k1-kill">%s</div>'
                    '<div class="k1-sub">排除 1 码（计入整组）</div></div>'
                    % (escape(str(label)), escape(str(positions.get(key, '-')))))
        tail = seal.get('tail') or {}
        if isinstance(tail, dict) and tail:
            cards.append(
                '<div class="k1-card k1-tail"><div class="k1-pos">%s合尾</div>'
                '<div class="k1-kill">%s</div>'
                '<div class="k1-sub">排除 1 个和尾（计入整组）</div></div>'
                % (escape(str(tail.get('label', ''))), escape(str(tail.get('kill', '-')))))
        form = seal.get('form')
        form_type = str(seal.get('form_type') or '')
        if isinstance(form, str) and len(form) == 4:
            form_label = kill1_tail.REVERSE_LABELS.get(form_type, form_type or '反向形态')
            cards.append(
                '<div class="k1-card k1-pattern"><div class="k1-pos">%s</div>'
                '<div class="k1-form">%s</div>'
                '<div class="k1-sub">排除该四位形态（计入整组）</div></div>'
                % (escape(str(form_label)), escape(form)))
        reason = escape(str(seal.get('reason') or '')) or '（无）'
        tail_policy = seal.get('tail_policy') or {}
        if (isinstance(tail_policy, dict)
                and tail_policy.get('reused_previous') is True
                and '尾码按稳定化沿用上期' not in str(seal.get('reason') or '')):
            reason += ('<span style="color:#92400e;">（尾码已按稳定化沿用上期，'
                       '模型理由中的合尾为换码候选）</span>')
        derivation = _form_derivation(seal)
        derive_html = '<div class="k1-drop">%s</div>' % escape(derivation) if derivation else ''
        policy_html = ''
        if isinstance(tail_policy, dict) and tail_policy:
            note = str(tail_policy.get('note') or '').strip()
            model_tail = tail_policy.get('model_tail') or {}
            candidate = ''
            if isinstance(model_tail, dict) and model_tail.get('key') in kill1_tail.TAILS:
                candidate = ('AI 候选：%s 合尾 杀 %s'
                             % (kill1_tail.TAILS[model_tail['key']]['label'],
                                model_tail.get('kill')))
            bits = [escape(note) if note
                    else 'hold3-until-miss（命中自动沿用，最多自动沿用 3 期）']
            if candidate:
                bits.append(escape(candidate))
            policy_html = '<div class="k1-drop">尾码稳定化：%s</div>' % ' · '.join(bits)
        scope_html = ('<div class="k1-drop">整组 3 项：1 位置 + 1 合尾 + 1 形态；'
                      '位置与杀码不做连用限制（完全由模型每期自选）</div>')
        model = seal.get('model_label') or kill1_tail.model_label()
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
                '<div class="k1-meta">%s</div>'
                '<div class="k1-grid">%s</div>%s%s%s'
                '<div class="k1-reason">理由：%s</div>'
                '%s%s<div class="k1-verify">%s</div>%s%s%s'
                % (START, _section_open(' data-as-of="%s"' % as_of), STYLE, _TITLE,
                   meta, ''.join(cards), scope_html, policy_html, derive_html, reason,
                   _stats_html(stats), _version_html(), verify, _attack_html(seal),
                   _foot_html(), END))
    except Exception:
        return ''


def _attack_html(seal):
    """与 kill3 同格式的攻击语 + 复制按钮（复制脚本沿用 kill3 同款，只换元素 id）。"""
    if not isinstance(seal, dict) or not seal.get('published_at'):
        return ''
    try:
        text = kill1_tail.build_attack(seal)
    except Exception:
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
        "var b=document.getElementById('kill1-attack-text');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('kill1-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="k1-attack">'
            '<div class="k1-attack-title">攻击语（复制到平台，与八项同格式）</div>'
            '<textarea id="kill1-attack-text" readonly rows="3" aria-label="AI 单位置+一合尾+一形态攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #c7d2fe;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea>'
            '<button id="kill1-attack-copy" type="button" onclick="' + copy_action + '" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#4f46e5;color:#fff;cursor:pointer;">'
            '复制攻击语</button>'
            '<span id="kill1-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            '<script id="kill1-attack-data" type="application/json">' + payload + '</script>')


def _foot_html():
    return ('<div class="k1-foot">'
            '由 DeepSeek 决定：从千/百/十/个中选 1 个位置排除 1 码（1 注/期）；'
            '从 5 种合尾中选 1 种排除 1 个和尾数字（稳定化：命中自动沿用、最多自动沿用 3 期；'
            '失手或满 3 期后由模型重选，重选可与上期同码；AI 尾项为换码候选）；'
            '从单双反向 / 大小反向中选 1 种四位形态，被杀位置的字符必须取杀码形态反面，'
            '其余 3 个位置字符由 AI 自选。<br>'
            '定位依据（v2 起）：每位置近 50 期热码 top3 + 上期该位置重码 / 邻码（±1，0↔9）；'
            '不以冷码 / 长遗漏为依据（v1 冷码口径已归档）。<br>'
            '位置与杀码不做连用限制（用户明确选择「不限制，完全由模型选」）。<br>'
            '命中 = 该位置实际开奖号<b>不在</b>排除码上（1 个位置计入）；'
            '合尾命中 = 该合尾实际和值个位<b>不在</b>排除码上；'
            '反向形态命中 = 实际四位单双/大小形态<b>不等于</b>封存形态。<br>'
            '1 个位置 + 1 个合尾 + 1 项反向形态共 3 项同时命中才算整组命中；'
            '旧口径（历史版本封存）保留但不计入本统计；'
            '只统计开奖前发布、开奖后核验的记录；北京时间 05:00 清零；'
            '只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。'
            '</div>')


def apply_kill_one_block(content, report=None):
    """原地替换/插入 AI 单位置+一合尾+一形态区块；任何异常原样返回 content。"""
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
        for anchor in ('<!-- KILL_THREE_END -->',):
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
