"""AI 二位置杀码 + 一合尾 + 两反向形态建议面板区块（kill2p 独立框架）。

数据源 data/kill2p_tail/{seals,settlements}.json，由 scripts/run_kill2p_tail.py 每期生成。
与 kill3/kill1 完全独立：独立 AI 请求 / 封存 / 结算 / 统计 / 面板区块 / 攻击语。

命中口径（写死，面板脚注同步展示）：
- 位置杀码 x2 = DeepSeek 从 千/百/十/个 中选恰好 2 个位置，各排除 1 个数字（2 注/期）；
  位置命中 = 该位置实际开奖号 != 排除码；其余 2 个位置不参与定位项、攻击语保留 10 码；
- 合尾命中 = 该合尾实际和值个位 != 排除码（5 种合尾选 1，hold3-until-miss 稳定化）；
- 反向形态 x2 = 单双反向（reverse_parity）与大小反向（reverse_size），各 4 个字符；
  被杀 2 个位置的字符必须取杀码形态反面，其余 2 个位置字符由 AI 自选；
  形态命中 = 目标期实际四位形态 != 封存形态（反向排除项）；
- 整组命中 = 2 位置 + 1 合尾 + 2 项反向形态同时命中（共 5 项）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 位置与杀码不做连用限制（完全由模型每期自选）；
- 只使用前 4 球（千/百/十/个），第 5 球不参与。

设计要点：
- 自包含 section（id 见 _section_open）+ 内联样式，不依赖面板已有 CSS 类；
- 用 START / END 起止注释标记原地替换；
- 标记缺失时按 KILL_ONE_END -> KILL_THREE_END -> </body> 顺序插入（kill2 已退役，不再使用 KILL_TWO 锚点）；
- 任何异常原样返回 html，绝不影响整页刷新。
"""
from datetime import datetime
from html import escape
import json
import math

import kill2p_tail

START = '<!-- KILL_TWO_P_START -->'
END = '<!-- KILL_TWO_P_END -->'

# 整组随机基线兜底：(项数, 精确期望命中率)。
# 5 项 = 2 位置 + 1 合尾 + 2 项反向形态；10^4 开奖空间均匀配置全枚举平均期望 0.6203。
# hitrate_baseline.RULE_BASELINE[('kill2p_tail', RULE_VERSION)] 未登记时使用本值。
FALLBACK_BASELINE = (5, 0.6203)

_TITLE = '🤖 AI 二位置杀码 + 一合尾 + 两反向形态建议'

_PARITY_OPPOSITE = {'单': '双', '双': '单'}
_SIZE_OPPOSITE = {'大': '小', '小': '大'}
_SHORT_LABELS = {'reverse_parity': '单双', 'reverse_size': '大小'}

STYLE = """
#kill-two-p-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-two-p-ai *{box-sizing:border-box}
#kill-two-p-ai h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#kill-two-p-ai .k2p-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#kill-two-p-ai .k2p-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr));gap:10px;min-width:0;margin-bottom:10px}
#kill-two-p-ai .k2p-card{border:1px solid #c7d2fe;border-radius:8px;background:#fff;padding:10px 12px;text-align:center;min-width:0}
#kill-two-p-ai .k2p-card.k2p-tail{border-color:#fecaca;background:#fff7f7}
#kill-two-p-ai .k2p-card.k2p-pattern{border-color:#ddd6fe;background:#faf8ff}
#kill-two-p-ai .k2p-pos{font-size:13px;color:#475569}
#kill-two-p-ai .k2p-kill{font-size:32px;font-weight:700;color:#1d4ed8;line-height:1.2}
#kill-two-p-ai .k2p-form{font-size:22px;font-weight:700;color:#7c3aed;letter-spacing:2px;line-height:1.35}
#kill-two-p-ai .k2p-card.k2p-tail .k2p-kill{color:#b91c1c}
#kill-two-p-ai .k2p-sub{font-size:12px;color:#64748b}
#kill-two-p-ai .k2p-drop{font-size:13px;color:#94a3b8;margin-bottom:6px}
#kill-two-p-ai .k2p-reason{font-size:13px;color:#475569;margin-bottom:8px}
#kill-two-p-ai .k2p-stats{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-two-p-ai .k2p-stats b{font-size:16px;color:#1e3a8a}
#kill-two-p-ai .k2p-verify{margin-top:6px;font-size:13px;color:#334155}
#kill-two-p-ai .k2p-attack{margin-top:8px;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-two-p-ai .k2p-attack-title{font-size:13px;font-weight:600;color:#3730a3}
#kill-two-p-ai .k2p-foot{margin-top:8px;font-size:12px;color:#94a3b8}
#kill-two-p-ai .k2p-empty{padding:6px 2px;font-size:14px;color:#475569}
"""


def _section_open(extra=''):
    """section 开头（id 字面量全文件仅在此处出现一次，便于 grep 计数）。"""
    return f'<section id="kill-two-p-ai"{extra}>'


def _pct(value):
    return f'{value * 100:.2f}%' if isinstance(value, (int, float)) else '-'


def _clock(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return '-'
    return moment.strftime('%H:%M:%S')


def _position_rows():
    """把 kill2p_tail.POSITIONS 归一到 ((key, label, index), ...)。"""
    rows = getattr(kill2p_tail, 'POSITIONS', ()) or ()
    result = []
    if isinstance(rows, dict):
        for key, value in rows.items():
            if isinstance(value, dict):
                result.append((str(key), str(value.get('label') or key), value.get('index')))
            else:
                result.append((str(key), str(value), None))
        return tuple(result)
    for row in rows:
        if isinstance(row, (tuple, list)) and len(row) >= 2:
            result.append((str(row[0]), str(row[1]), row[2] if len(row) > 2 else None))
        elif isinstance(row, str):
            result.append((row, row, None))
    return tuple(result)


def _reverse_fields():
    """((field, label), ...)：reverse_parity 单双反向形态 + reverse_size 大小反向形态。"""
    fields = getattr(kill2p_tail, 'REVERSE_KEYS', ('reverse_parity', 'reverse_size')) or ()
    labels = getattr(kill2p_tail, 'REVERSE_LABELS', None)
    defaults = {'reverse_parity': '单双反向形态', 'reverse_size': '大小反向形态'}
    result = []
    for field in fields:
        field = str(field)
        label = ''
        if isinstance(labels, dict):
            label = str(labels.get(field) or '')
        result.append((field, label or defaults.get(field, field)))
    return tuple(result)


def _tail_label(tail):
    if isinstance(tail, dict):
        label = tail.get('label')
        if label:
            return str(label)
        key = tail.get('key')
    else:
        key = None
    tails = getattr(kill2p_tail, 'TAILS', {}) or {}
    entry = tails.get(key) if isinstance(tails, dict) and key else None
    if isinstance(entry, dict):
        return str(entry.get('label') or '-')
    return '-'


def _rule_version():
    try:
        info = kill2p_tail.version_info() or {}
        return str(info.get('rule_version') or getattr(kill2p_tail, 'RULE_VERSION', '') or '')
    except Exception:
        return str(getattr(kill2p_tail, 'RULE_VERSION', '') or '')


def _rule_baseline():
    """整组精确基线：(项数, 随机期望)。优先 hitrate_baseline.RULE_BASELINE，未登记回退 FALLBACK_BASELINE。"""
    rule = _rule_version()
    try:
        from hitrate_baseline import RULE_BASELINE
        base = RULE_BASELINE.get(('kill2p_tail', rule)) if rule else None
        if isinstance(base, (tuple, list)) and len(base) >= 2:
            return int(base[0]), float(base[1])
    except Exception:
        pass
    return FALLBACK_BASELINE


def _stats_html(stats):
    stats = stats if isinstance(stats, dict) else {}
    total = stats.get('total') or 0
    pending = stats.get('pending') or 0
    late = stats.get('late') or 0
    counted = getattr(kill2p_tail, 'COUNTED_POSITIONS', 2)
    if total:
        positions = stats.get('positions') or {}
        tail_stats = stats.get('tail') or {}
        patterns = stats.get('patterns') or {}
        pattern_bits = []
        for field, label in _reverse_fields():
            entry = patterns.get(field) or {}
            if isinstance(entry, dict) and entry:
                short = _SHORT_LABELS.get(field, label)
                pattern_bits.append('%s %s/%s' % (short, entry.get('hits'), entry.get('bets')))
        pattern_detail = '（' + ' · '.join(pattern_bits) + '）' if pattern_bits else ''
        pattern_text = ' · '.join(pattern_bits) if pattern_bits else '-'
        base = _rule_baseline()
        base_text = ('，精确基线 %s（%d 项）' % (_pct(base[1]), base[0])) if base else ''
        join = ' · '.join([
            '整组命中率（5 项全中：2 位置 + 合尾 + 2 形态%s） <b>%s</b>（%s/%s 期）'
            % (base_text, _pct(stats.get('group_rate')), stats.get('group_hits'), total),
            '位置杀码（%s 注/期，随机基线90%%） <b>%s</b>（%s/%s 注）'
            % (counted, _pct(positions.get('rate')), positions.get('hits'), positions.get('bets')),
            '合尾杀码（随机基线90%%） <b>%s</b>（%s/%s 期）'
            % (_pct(tail_stats.get('rate')), tail_stats.get('hits'), tail_stats.get('bets')),
            '反向形态（2 项，单项随机基线93.75%%） <b>%s</b>%s'
            % (pattern_text, pattern_detail),
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
    return '<div class="k2p-stats"><span>%s</span></div>' % join


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '已发布，等待目标期开奖核验。'
    positions = settlement.get('positions') or {}
    counted = settlement.get('counted') or list(positions)
    counted = [key for key in counted if key in positions]
    parts = ['目标期已开奖：' + ''.join(str(value) for value in (settlement.get('actual') or []))]
    position_hits = 0
    for key in counted:
        entry = positions.get(key) or {}
        if not isinstance(entry, dict):
            continue
        label = kill2p_tail.POSITION_LABELS.get(key, key)
        if entry.get('hit') is True:
            position_hits += 1
        parts.append('%s杀 %s %s（实际 %s）' % (
            label, entry.get('kill', '-'),
            '命中' if entry.get('hit') is True else '未中', entry.get('actual', '-')))
    if counted:
        parts.append('位置 %s/%s 注命中' % (position_hits, len(counted)))
    tail = settlement.get('tail') or {}
    if isinstance(tail, dict) and tail:
        parts.append('%s合尾杀 %s %s（实际 %s）' % (
            tail.get('label') or _tail_label(tail), tail.get('kill', '-'),
            '命中' if tail.get('hit') is True else '未中', tail.get('actual', '-')))
    patterns = settlement.get('patterns') or {}
    for field, label in _reverse_fields():
        entry = patterns.get(field) or {}
        if isinstance(entry, dict) and entry:
            parts.append('%s %s %s（实际 %s）' % (
                label, entry.get('form') or '',
                '命中' if entry.get('hit') is True else '未中', entry.get('actual', '-')))
    if settlement.get('verified') is not True:
        parts.append('发布晚于开奖，未计入统计')
    else:
        parts.append('整组 ' + ('<b style="color:#15803d">命中</b>'
                                if settlement.get('group_hit') is True
                                else '<b style="color:#b91c1c">未中</b>'))
    return '；'.join(parts) + '。'


def _form_derivation(seal):
    """形态推导行：被杀 2 个位置取杀码形态反面，其余 2 个位置字符由 AI 自选。"""
    positions = seal.get('positions') or {}
    if not isinstance(positions, dict) or not positions:
        return ''
    parts = []
    for key, label, _index in _position_rows():
        if key not in positions:
            continue
        try:
            reverse_parity, reverse_size = kill2p_tail.reverse_attributes(int(positions[key]))
        except (TypeError, ValueError):
            return ''
        killed_parity = _PARITY_OPPOSITE.get(reverse_parity, reverse_parity)
        killed_size = _SIZE_OPPOSITE.get(reverse_size, reverse_size)
        parts.append('%s杀 %s（%s/%s）→ 必须取反 %s/%s' % (
            label, positions[key], killed_parity, killed_size, reverse_parity, reverse_size))
    if not parts:
        return ''
    return ('形态推导（按 %s 个被杀位置）：%s；其余 2 个位置字符由 AI 自选；'
            '两项形态各 4 字符，按千百十个顺序（单双/大小）'
            % (len(parts), ' · '.join(parts)))


def _version_html():
    """口径追溯行：rule_version、自 ref 起期数、整组精确基线（未登记回退 FALLBACK_BASELINE）。"""
    try:
        rule = str(getattr(kill2p_tail, 'RULE_VERSION', '') or '')
        info = {}
        try:
            info = kill2p_tail.version_info() or {}
            rule = str(info.get('rule_version') or rule)
        except Exception:
            info = {}
        baseline = _rule_baseline()
        parts = ['口径 ' + escape(rule)]
        first = info.get('first_ref')
        if first:
            parts.append('自 ref ' + escape(str(first)) + ' 起（本版 '
                         + str(int(info.get('count') or 0)) + ' 期）')
        parts.append('整组精确基线 %.2f%%（%d 项）'
                     % (float(baseline[1]) * 100.0, int(baseline[0])))
        parts.append('kill2p 独立框架：与 kill1/kill3 独立封存 / 结算 / 统计')
        return '<div class="k2p-drop">' + ' · '.join(parts) + '</div>'
    except Exception:
        return ''


def _card(css_class, head, value, sub):
    return ('<div class="k2p-card%s"><div class="k2p-pos">%s</div>'
            '<div class="k2p-kill">%s</div>'
            '<div class="k2p-sub">%s</div></div>'
            % ((' ' + css_class) if css_class else '',
               escape(str(head)), escape(str(value)), escape(str(sub))))


def _form_card(label, form, sub):
    return ('<div class="k2p-card k2p-pattern"><div class="k2p-pos">%s</div>'
            '<div class="k2p-form">%s</div>'
            '<div class="k2p-sub">%s</div></div>'
            % (escape(str(label)), escape(str(form)), escape(str(sub))))


def render_block():
    try:
        data = kill2p_tail.current_summary()
        data = data if isinstance(data, dict) else {}
        stats = data.get('stats') or {}
        seal = data.get('seal')
        if not isinstance(seal, dict):
            body = '<div class="k2p-empty">本期 AI 建议尚未发布（等待开奖后计算，或本轮调用失败）。</div>'
            previous = data.get('previous')
            if isinstance(previous, dict):
                body += ('<div class="k2p-meta">最近一次：参考期 %s · 目标期 %s</div>'
                         % (escape(str(previous.get('ref_period', ''))),
                            escape(str(previous.get('target_period', '')))))
            return ('%s%s<style>%s</style><h3>%s</h3>%s%s%s%s%s'
                    % (START, _section_open(), STYLE, _TITLE, body,
                       _stats_html(stats), _version_html(), _foot_html(), END))
        cards = []
        positions = seal.get('positions') or {}
        if isinstance(positions, dict):
            for key, label, _index in _position_rows():
                if key not in positions:
                    continue
                cards.append(_card('', label, positions.get(key, '-'),
                                   '排除 1 码（计入整组）'))
        tail = seal.get('tail') or {}
        if isinstance(tail, dict) and tail:
            cards.append(_card('k2p-tail', _tail_label(tail) + '合尾',
                               tail.get('kill', '-'), '排除 1 个和尾（计入整组）'))
        for field, label in _reverse_fields():
            form = seal.get(field)
            if isinstance(form, str) and len(form) == 4:
                cards.append(_form_card(label, form,
                                        '排除该四位形态（计入整组，被杀位取反）'))
        labels = {key: label for key, label, _index in _position_rows()}
        unselected = [label for key, label, _index in _position_rows() if key not in positions]
        scope_html = ('<div class="k2p-drop">整组 5 项：2 位置 + 1 合尾 + 2 反向形态'
                      '（单双/大小各 1 项）；未选位置：%s（不参与定位项，攻击语保留 10 码）；'
                      '位置与杀码不做连用限制（完全由模型每期自选）</div>'
                      % escape('、'.join(unselected) if unselected else '-'))
        tail_policy = seal.get('tail_policy') or {}
        policy_html = ''
        if isinstance(tail_policy, dict) and tail_policy:
            note = str(tail_policy.get('note') or '').strip()
            model_tail = tail_policy.get('model_tail') or {}
            candidate = ''
            tails = getattr(kill2p_tail, 'TAILS', {}) or {}
            if (isinstance(model_tail, dict) and isinstance(tails, dict)
                    and model_tail.get('key') in tails):
                candidate = ('AI 候选：%s 合尾 杀 %s'
                             % (tails[model_tail['key']]['label'], model_tail.get('kill')))
            bits = [escape(note) if note
                    else 'hold3-until-miss（命中自动沿用，最多自动沿用 3 期）']
            if candidate:
                bits.append(escape(candidate))
            policy_html = '<div class="k2p-drop">尾码稳定化：%s</div>' % ' · '.join(bits)
        derivation = _form_derivation(seal)
        derive_html = '<div class="k2p-drop">%s</div>' % escape(derivation) if derivation else ''
        reason = escape(str(seal.get('reason') or '')) or '（无）'
        if (isinstance(tail_policy, dict)
                and tail_policy.get('reused_previous') is True
                and '尾码按稳定化沿用上期' not in str(seal.get('reason') or '')):
            reason += ('<span style="color:#92400e;">（尾码已按稳定化沿用上期，'
                       '模型理由中的合尾为换码候选）</span>')
        try:
            model = seal.get('model_label') or kill2p_tail.model_label()
        except Exception:
            model = seal.get('model_label') or ''
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
                '<div class="k2p-meta">%s</div>'
                '<div class="k2p-grid">%s</div>%s%s%s'
                '<div class="k2p-reason">理由：%s</div>'
                '%s%s<div class="k2p-verify">%s</div>%s%s%s'
                % (START, _section_open(' data-as-of="%s"' % as_of), STYLE, _TITLE,
                   meta, ''.join(cards), scope_html, policy_html, derive_html, reason,
                   _stats_html(stats), _version_html(), verify, _attack_html(seal),
                   _foot_html(), END))
    except Exception:
        return ''


def _attack_html(seal):
    """与 kill3 同格式的攻击语 + 复制按钮（复制脚本沿用 kill1/kill3 同款，只换元素 id）。"""
    if not isinstance(seal, dict) or not seal.get('published_at'):
        return ''
    try:
        text = kill2p_tail.build_attack(seal)
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
        "var b=document.getElementById('kill2p-attack-text');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('kill2p-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="k2p-attack">'
            '<div class="k2p-attack-title">攻击语（复制到平台，与八项同格式）</div>'
            '<textarea id="kill2p-attack-text" readonly rows="3" '
            'aria-label="AI 二位置+一合尾+两反向形态攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #c7d2fe;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea>'
            '<button id="kill2p-attack-copy" type="button" onclick="' + copy_action + '" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#4f46e5;color:#fff;cursor:pointer;">'
            '复制攻击语</button>'
            '<span id="kill2p-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            '<script id="kill2p-attack-data" type="application/json">' + payload + '</script>')


def _foot_html():
    hold = getattr(kill2p_tail, 'TAIL_HOLD_MAX', 3)
    return ('<div class="k2p-foot">'
            '由 DeepSeek 决定：从 千/百/十/个 中选恰好 2 个位置各排除 1 码（2 注/期），'
            '其余 2 个位置不参与定位项、攻击语保留 10 码；'
            '从 5 种合尾中选 1 种排除 1 个和尾数字（稳定化：命中自动沿用、最多自动沿用 '
            + str(hold) + ' 期；失手或满 ' + str(hold) + ' 期后由模型重选，重选可与上期同码；'
            'AI 尾项为换码候选）；并给出单双反向 / 大小反向两项四位形态，'
            '被杀的 2 个位置的字符必须取杀码形态反面，其余 2 个位置字符由 AI 自选。<br>'
            '位置与杀码不做连用限制（用户明确选择「不限制，完全由模型选」）。<br>'
            '命中 = 该位置实际开奖号<b>不在</b>排除码上（2 个位置计入，2 注/期）；'
            '合尾命中 = 该合尾实际和值个位<b>不在</b>排除码上；'
            '反向形态命中 = 实际四位单双/大小形态<b>不等于</b>封存形态（反向排除项）。<br>'
            '2 个位置 + 1 个合尾 + 2 项反向形态共 5 项同时命中才算整组命中（整组精确基线 62.03%，'
            '5 项均匀配置 10^4 空间枚举）；旧口径（历史版本封存）保留但不计入本统计；'
            '只统计开奖前发布、开奖后核验的记录；北京时间 05:00 清零；'
            '只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。'
            '</div>')


def apply_kill_two_p_block(content, report=None):
    """原地替换/插入 AI 二位置+一合尾+两反向形态区块；任何异常原样返回 content。"""
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
        # 半截标记不重复插入，避免污染后续 live refresh 的成对标记校验。
        if start != -1 or end != -1:
            return content
        for anchor in ('<!-- KILL_ONE_END -->', '<!-- KILL_THREE_END -->'):
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
