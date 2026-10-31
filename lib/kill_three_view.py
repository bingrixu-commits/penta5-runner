"""AI 三位置杀码 + 一合尾建议面板区块（替代原规则版三位置杀码）。

数据源 data/kill3_tail/{seals,settlements}.json，由 scripts/run_kill3_tail.py 每期生成。

命中口径（写死，面板脚注同步展示）：
- 位置命中 = 该位置实际开奖号 != 杀码；
- 合尾命中 = 该合尾实际和值个位 != 排除码；
- 反向形态命中 = 目标期实际四位单双/大小形态 != 封存形态（反向排除项）；
- 整组命中 = 3 个位置 + 1 个合尾 + 2 项反向形态同时命中（共 6 项）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与。

设计要点：
- 自包含 <section id="kill-three-ai"> + 内联样式，不依赖面板已有 CSS 类；
- 用 <!-- KILL_THREE_START/END --> 标记原地替换（沿用旧标记，run_stats 收尾链路无需改）；
- 标记缺失时插入到 <!-- COLD_HIT_STATS_END --> 之后；
- 任何异常原样返回 html，绝不影响整页刷新。
"""
from datetime import datetime
from html import escape
import json
import math

import kill3_tail

START = '<!-- KILL_THREE_START -->'
END = '<!-- KILL_THREE_END -->'

STYLE = """
#kill-three-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-three-ai *{box-sizing:border-box}
#kill-three-ai h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#kill-three-ai .k3-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#kill-three-ai .k3-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr));gap:10px;min-width:0;margin-bottom:10px}
#kill-three-ai .k3-card{border:1px solid #c7d2fe;border-radius:8px;background:#fff;padding:10px 12px;text-align:center;min-width:0}
#kill-three-ai .k3-card.k3-tail{border-color:#fecaca;background:#fff7f7}
#kill-three-ai .k3-card.k3-pattern{border-color:#ddd6fe;background:#faf8ff}
#kill-three-ai .k3-pos{font-size:13px;color:#475569}
#kill-three-ai .k3-kill{font-size:32px;font-weight:700;color:#1d4ed8;line-height:1.2}
#kill-three-ai .k3-form{font-size:22px;font-weight:700;color:#7c3aed;letter-spacing:2px;line-height:1.35}
#kill-three-ai .k3-card.k3-tail .k3-kill{color:#b91c1c}
#kill-three-ai .k3-sub{font-size:12px;color:#64748b}
#kill-three-ai .k3-drop{font-size:13px;color:#94a3b8;margin-bottom:6px}
#kill-three-ai .k3-reason{font-size:13px;color:#475569;margin-bottom:8px}
#kill-three-ai .k3-stats{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-three-ai .k3-stats b{font-size:16px;color:#1e3a8a}
#kill-three-ai .k3-verify{margin-top:6px;font-size:13px;color:#334155}
#kill-three-ai .k3-attack{margin-top:8px;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-three-ai .k3-attack-title{font-size:13px;font-weight:600;color:#3730a3}
#kill-three-ai .k3-foot{margin-top:8px;font-size:12px;color:#94a3b8}
#kill-three-ai .k3-empty{padding:6px 2px;font-size:14px;color:#475569}
"""


def _pct(value):
    return f'{value * 100:.2f}%' if isinstance(value, (int, float)) else '-'
def _wilson(hits, n, z=1.96):
    """Wilson 95% CI（比例）；样本为 0 时返回 None。"""
    try:
        hits, n = int(hits), int(n)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    p = hits / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half




def _clock(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return '-'
    return moment.strftime('%H:%M:%S')


def _stats_html(stats):
    total = stats.get('total') or 0
    pending = stats.get('pending') or 0
    late = stats.get('late') or 0
    if total:
        tail_stats = stats.get('tail') or {}
        tail_ci = _wilson(tail_stats.get('hits'), tail_stats.get('bets'))
        tail_ci_text = (f'，95%CI {tail_ci[0] * 100:.1f}–{tail_ci[1] * 100:.1f}%'
                        if tail_ci else '')
        patterns = stats.get('patterns') or {}
        parity = patterns.get('reverse_parity') or {}
        size = patterns.get('reverse_size') or {}
        join = ' · '.join([
            f'整组命中率（6 项全中：3 位置 + 合尾 + 2 形态） <b>{_pct(stats.get("group_rate"))}</b>'
            f'（{stats.get("group_hits")}/{total} 期）',
            f'位置杀码（3 注/期，随机基线90%） <b>{_pct(stats.get("positions", {}).get("rate"))}</b>'
            f'（{stats.get("positions", {}).get("hits")}/{stats.get("positions", {}).get("bets")} 注）',
            f'合尾杀码（随机基线90%） <b>{_pct(tail_stats.get("rate"))}</b>'
            f'（{tail_stats.get("hits")}/{tail_stats.get("bets")} 期{tail_ci_text}）',
            f'单双反向（随机基线93.75%） <b>{_pct(parity.get("rate"))}</b>（{parity.get("hits")}/{total} 期）',
            f'大小反向（随机基线93.75%） <b>{_pct(size.get("rate"))}</b>（{size.get("hits")}/{total} 期）',
            f'待核验 <b>{pending}</b> 期',
        ])
    else:
        join = (f'今日暂无已核验样本 · 待核验 <b>{pending}</b> 期 · '
                f'最早一期开奖后核验（北京时间 05:00 清零）')
    if late:
        join += f' · 未计入（发布晚于开奖）{late} 期'
    legacy = stats.get('legacy') or {}
    if legacy.get('total'):
        join += (f' · 旧口径（v1–v6、v7）{legacy.get("total")} 期'
                 f'（整组 {legacy.get("group_hits")} 中）已归档不计入')
    win, loss = stats.get('current_win_streak') or 0, stats.get('current_loss_streak') or 0
    if win:
        join += f' · 当前 <b style="color:#15803d">连中 {win} 期</b>'
    elif loss:
        join += f' · 当前 <b style="color:#b91c1c">连败 {loss} 期</b>'
    join += f' · 今日四手连败 <b>{stats.get("four_loss_cycles") or 0}</b> 次（不重叠计数）'
    if total and total < 200:
        sigma = 100.0 * math.sqrt(0.9 * 0.1 / total)
        join += (f' · 单项样本 n={total}（&lt;200 属小样本：90% 项 1σ≈{sigma:.1f}pp、'
                 f'2σ≈{2 * sigma:.1f}pp），单日单项只作波动参考')
    return f'<div class="k3-stats"><span>{join}</span></div>'


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '已发布，等待目标期开奖核验。'
    positions = settlement.get('positions') or {}
    counted = settlement.get('counted') or list(positions)
    hit_positions = sum(1 for key in counted
                        if (positions.get(key) or {}).get('hit') is True)
    tail = settlement.get('tail') or {}
    tail_hit = tail.get('hit') is True
    tail_label = tail.get('label') or ''
    group = settlement.get('group_hit') is True
    actual = settlement.get('actual') or []
    parts = ['目标期已开奖：' + ''.join(str(value) for value in actual),
             f'位置 {hit_positions}/{len(counted)} 命中',
             f'{tail_label}合尾 {"命中" if tail_hit else "未中"}（实际 {tail.get("actual", "-")}）']
    patterns = settlement.get('patterns') or {}
    for field, label in (('reverse_parity', '单双形态'), ('reverse_size', '大小形态')):
        entry = patterns.get(field) or {}
        if entry:
            parts.append(f'{label}{"命中" if entry.get("hit") else "未中"}（实际 {entry.get("actual", "-")}）')
    if settlement.get('verified') is not True:
        parts.append('发布晚于开奖，未计入统计')
    else:
        parts.append('整组 ' + ('<b style="color:#15803d">命中</b>' if group else '<b style="color:#b91c1c">未中</b>'))
    return '；'.join(parts) + '。'


def _form_derivation(seal):
    """Show how the two reverse forms were derived from the three killed digits."""
    positions = seal.get('positions') or {}
    if not isinstance(positions, dict):
        return ''
    parts = []
    for key, label, _index in kill3_tail.POSITIONS:
        if key not in positions:
            continue
        try:
            parity, size = kill3_tail.reverse_attributes(int(positions[key]))
        except (TypeError, ValueError):
            return ''
        parts.append(f'{label}{positions[key]}→{parity}/{size}')
    if not parts:
        return ''
    dropped = kill3_tail.POSITION_LABELS.get(seal.get('dropped'), seal.get('dropped') or '-')
    return ('形态取反（按 3 个被杀位置）：' + ' · '.join(parts)
            + f'；弃位{dropped}字符自选')


def _version_html():
    """口径追溯行：当前 revision、自哪期起、本版期数、精确基线（只读）。"""
    try:
        info = kill3_tail.version_info()
    except Exception:
        return ''
    try:
        rule = str(info.get('rule_version') or '')
        first = info.get('first_ref')
        count = int(info.get('count') or 0)
        base = None
        try:
            from hitrate_baseline import RULE_BASELINE
            base = RULE_BASELINE.get(('kill3_tail', rule))
        except Exception:
            base = None
        parts = ['口径 ' + escape(rule)]
        if first:
            parts.append('自 ref ' + escape(str(first)) + ' 起（本版 ' + str(count) + ' 期）')
        if base:
            parts.append('整组精确基线 %.2f%%（%d 项）' % (base[1] * 100.0, base[0]))
        parts.append('口径已冻结：变更走影子验证（ops/kill3-stability-plan.md）')
        return '<div class="k3-drop">' + ' · '.join(parts) + '</div>'
    except Exception:
        return ''


def render_block():
    try:
        data = kill3_tail.current_summary()
        stats = data['stats']
        seal = data['seal']
        if not isinstance(seal, dict):
            body = '<div class="k3-empty">本期 AI 建议尚未发布（等待开奖后计算，或本轮调用失败）。</div>'
            previous = data.get('previous')
            if isinstance(previous, dict):
                body += (f'<div class="k3-meta">最近一次：参考期 {escape(str(previous.get("ref_period", "")))}'
                         f' · 目标期 {escape(str(previous.get("target_period", "")))}</div>')
            return (f'{START}<section id="kill-three-ai"><style>{STYLE}</style>'
                    f'<h3>🤖 AI 三位置杀码 + 一合尾建议</h3>{body}{_stats_html(stats)}{_version_html()}'
                    f'{_foot_html()}</section>{END}')
        positions = seal.get('positions') or {}
        display_only = seal.get('display_only')
        counted_note = ('其中 2 个计入整组、1 个仅展示' if display_only
                        else '全部计入整组')
        cards = []
        for key, label, _index in kill3_tail.POSITIONS:
            if key not in positions:
                continue
            sub = ('排除 1 码（仅展示，不计入整组）' if key == display_only
                   else '排除 1 码')
            cards.append(
                f'<div class="k3-card"><div class="k3-pos">{label}</div>'
                f'<div class="k3-kill">{escape(str(positions.get(key, "-")))}</div>'
                f'<div class="k3-sub">{sub}</div></div>')
        tail = seal.get('tail') or {}
        if tail:
            cards.append(
                f'<div class="k3-card k3-tail"><div class="k3-pos">{escape(str(tail.get("label", "")))}合尾</div>'
                f'<div class="k3-kill">{escape(str(tail.get("kill", "-")))}</div>'
                f'<div class="k3-sub">排除 1 个和尾</div></div>')
        for field, label in (('reverse_parity', '单双反向形态'), ('reverse_size', '大小反向形态')):
            form = seal.get(field)
            if isinstance(form, str) and len(form) == 4:
                cards.append(
                    f'<div class="k3-card k3-pattern"><div class="k3-pos">{label}</div>'
                    f'<div class="k3-form">{escape(form)}</div>'
                    f'<div class="k3-sub">排除该四位形态</div></div>')
        dropped = kill3_tail.POSITION_LABELS.get(seal.get('dropped'), seal.get('dropped') or '-')
        reason = escape(str(seal.get('reason') or '')) or '（无）'
        seal_tail_policy = seal.get('tail_policy') or {}
        if (seal_tail_policy.get('reused_previous') is True
                and '尾码按稳定化沿用上期' not in str(seal.get('reason') or '')):
            reason += ('<span style="color:#92400e;">（尾码已按稳定化沿用上期，'
                       '模型理由中的合尾为换码候选）</span>')
        derivation = _form_derivation(seal)
        derive_html = (f'<div class="k3-drop">{escape(derivation)}</div>'
                       if derivation else '')
        tail_policy = seal.get('tail_policy') or {}
        restrict_html = ''
        if tail_policy.get('policy'):
            note = tail_policy.get('note') or ''
            model_tail = tail_policy.get('model_tail') or {}
            candidate = ''
            if model_tail.get('key') in kill3_tail.TAILS:
                candidate = ('（AI 候选：' + escape(kill3_tail.TAILS[model_tail['key']]['label'])
                             + ' 杀 ' + escape(str(model_tail.get('kill'))) + '）')
            restrict_html = (f'<div class="k3-drop">尾码稳定化：{escape(str(note))}'
                             f'{candidate}</div>')
        position_limit = seal.get('position_restriction') or {}
        position_parts = []
        for key, digit in (position_limit.get('blocked_digits') or {}).items():
            position_parts.append(
                f'{kill3_tail.POSITION_LABELS.get(key, key)}杀码 {digit} 已连用 '
                f'{kill3_tail.MAX_CONSECUTIVE_PERIODS} 期（含中间弃位）→ 本期不得再用')
        blocked_drop = position_limit.get('blocked_drop')
        if blocked_drop:
            position_parts.append(
                f'放弃位置 {kill3_tail.POSITION_LABELS.get(blocked_drop, blocked_drop)} 已连用 '
                f'{kill3_tail.MAX_CONSECUTIVE_PERIODS} 期 → 本期必须换放弃位置')
        position_html = ''
        if position_parts:
            position_html = ('<div class="k3-drop">位置限制：'
                             + escape('；'.join(position_parts)) + '</div>')
        model = seal.get('model_label') or kill3_tail.model_label()
        meta = (f'参考期 {escape(str(seal.get("ref_period", "")))} ｜ '
                f'目标期 {escape(str(seal.get("target_period", "")))} ｜ '
                f'AI 计算 {_clock(seal.get("generated_at"))}（北京时间） ｜ {escape(str(model))}')
        settlement = data.get('settlement')
        state = seal.get('publication_state')
        if isinstance(settlement, dict):
            verify = _settlement_line(settlement)
        elif state == 'published':
            verify = '已发布（' + _clock(seal.get('published_at')) + '），等待目标期开奖核验。'
        else:
            verify = '已封存，等待发布确认。'
        return (f'{START}<section id="kill-three-ai" data-as-of="{escape(str(seal.get("ref_period", "")))}">'
                f'<style>{STYLE}</style>'
                f'<h3>🤖 AI 三位置杀码 + 一合尾建议</h3>'
                f'<div class="k3-meta">{meta}</div>'
                f'<div class="k3-grid">{"".join(cards)}</div>'
                f'<div class="k3-drop">放弃位置：{escape(str(dropped))}'
                f'（AI 选 3 个位置，{counted_note}）</div>'
                f'{restrict_html}{position_html}{derive_html}'
                f'<div class="k3-reason">理由：{reason}</div>'
                f'{_stats_html(stats)}{_version_html()}'
                f'<div class="k3-verify">{verify}</div>'
                f'{_attack_html(seal)}'
                f'{_foot_html()}</section>{END}')
    except Exception:
        return ''


def _attack_html(seal):
    """与八项独立框架同格式的攻击语 + 复制按钮（可被全局攻击语编辑框接管）。"""
    if not isinstance(seal, dict) or not seal.get('published_at'):
        return ''
    try:
        text = kill3_tail.build_attack(seal)
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
        "var b=document.getElementById('kill3-attack-text');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('kill3-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="k3-attack">'
            '<div class="k3-attack-title">攻击语（复制到平台，与八项同格式）</div>'
            '<textarea id="kill3-attack-text" readonly rows="3" aria-label="AI 三位置+一合尾攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #c7d2fe;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea>'
            '<button id="kill3-attack-copy" type="button" onclick="' + copy_action + '" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#4f46e5;color:#fff;cursor:pointer;">'
            '复制攻击语</button>'
            '<span id="kill3-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            '<script id="kill3-attack-data" type="application/json">' + payload + '</script>')


def _foot_html():
    return ('<div class="k3-foot">'
            '由 DeepSeek 决定：从千/百/十/个中选 3 个位置各排除 1 码、放弃 1 个位置；'
            '3 个位置全部计入整组与攻击语；'
            '合尾从 5 种中选 1 种排除 1 个和尾数字（稳定化：命中自动沿用、最多自动沿用 3 期；失手或满 3 期后由模型重选，重选可与上期同码；AI 尾项为换码候选），并给出两项四位反向形态。<br>'
            '同一位置杀码最多连用 3 期（中间放弃该位不清零）、同一放弃位置最多连用 3 期，'
            '满后下一期必须更换。<br>'
            '命中 = 该位置实际开奖号<b>不在</b>杀码上（3 个位置全部计入）；'
            '合尾命中 = 该合尾实际和值个位<b>不在</b>排除码上；'
            '反向形态命中 = 实际四位单双/大小形态<b>不等于</b>封存形态'
            '（3 个被杀位置都取杀码形态反面；弃位字符自选）。<br>'
            '3 个位置 + 1 个合尾 + 2 项反向形态共 6 项同时命中才算整组命中；'
            '旧口径（v1–v6、v7）的封存记录保留但不计入本统计；'
            '只统计开奖前发布、开奖后核验的记录；北京时间 05:00 清零；只使用前 4 球（千/百/十/个），第 5 球不参与；'
            '平台提前 70 秒关盘。'
            '</div>')


def apply_kill_three_block(content, report=None):
    """原地替换/插入 AI 三位置+一合尾区块；任何异常原样返回 content。"""
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
        for anchor in ('<!-- COLD_HIT_STATS_END -->', '<!-- POSITION_COLD_END -->'):
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

