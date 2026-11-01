"""AI 二位置杀码 + 一合尾 + 二数合/三数合建议面板区块。

数据源 data/kill2_tail/{seals,settlements}.json，由 scripts/run_kill2_tail.py 每期生成。

命中口径（写死，面板脚注同步展示）：
- 位置命中 = 该位置实际开奖号 != 杀码（2 个位置全部计入，其余 2 个位置不参与定位项）；
- 合尾命中 = 该合尾实际和值个位 != 排除码；
- 二数合命中 = 前 4 球任取 2 位的 6 种组合求和取个位，任一落入保留 4 码即命中（保留集语义，非杀码）；
- 三数合命中 = 前 4 球任取 3 位的 4 种组合求和取个位，任一落入保留 5 码即命中；
- 反向形态命中 = 目标期实际四位单双/大小形态 != 封存形态（反向排除项，被杀 2 位取反、未选 2 位字符自选）；
- 整组命中 = 2 位置 + 1 合尾 + 二数合 + 三数合 + 2 项反向形态同时命中（共 7 项）；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与。

设计要点：
- 自包含 <section id="kill-two-ai"> + 内联样式，不依赖面板已有 CSS 类；
- 用 <!-- KILL_TWO_START/END --> 标记原地替换；
- 标记缺失时插入到 <!-- KILL_THREE_END --> 之后（再退 COLD_HIT_STATS_END / POSITION_COLD_END / </body>）；
- 任何异常原样返回 html，绝不影响整页刷新。
"""
from datetime import datetime
from html import escape
import json
import math

import kill2_tail

START = '<!-- KILL_TWO_START -->'
END = '<!-- KILL_TWO_END -->'
TITLE = '🤖 AI 二位置杀码 + 一合尾 + 二数合/三数合建议'
# 规格 §1：整组 = 2 定位 + 1 合尾 + 二数合留 4 码 + 三数合留 5 码 + 2 反向形态，
# 2 位置 + 合尾 + 二数合 + 三数合 + 2 形态，10^4 精确枚举期望 0.5708（精确枚举，非相乘）。仅在现行 revision
# 尚未登记进 hitrate_baseline.RULE_BASELINE 时兜底展示；登记后以登记值为准。
FALLBACK_BASELINE = (7, 0.5708)
REGISTERED_RULE = 'kill2-tail-v2-two-positions-he2he3-rot3'

STYLE = """
#kill-two-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-two-ai *{box-sizing:border-box}
#kill-two-ai h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#kill-two-ai .k2-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#kill-two-ai .k2-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr));gap:10px;min-width:0;margin-bottom:10px}
#kill-two-ai .k2-card{border:1px solid #c7d2fe;border-radius:8px;background:#fff;padding:10px 12px;text-align:center;min-width:0}
#kill-two-ai .k2-card.k2-tail{border-color:#fecaca;background:#fff7f7}
#kill-two-ai .k2-card.k2-keep{border-color:#bbf7d0;background:#f0fdf4}
#kill-two-ai .k2-card.k2-pattern{border-color:#ddd6fe;background:#faf8ff}
#kill-two-ai .k2-pos{font-size:13px;color:#475569}
#kill-two-ai .k2-kill{font-size:32px;font-weight:700;color:#1d4ed8;line-height:1.2}
#kill-two-ai .k2-card.k2-tail .k2-kill{color:#b91c1c}
#kill-two-ai .k2-card.k2-keep .k2-kill{font-size:26px;letter-spacing:3px;color:#15803d}
#kill-two-ai .k2-form{font-size:22px;font-weight:700;color:#7c3aed;letter-spacing:2px;line-height:1.35}
#kill-two-ai .k2-sub{font-size:12px;color:#64748b}
#kill-two-ai .k2-drop{font-size:13px;color:#94a3b8;margin-bottom:6px}
#kill-two-ai .k2-reason{font-size:13px;color:#475569;margin-bottom:8px}
#kill-two-ai .k2-stats{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-two-ai .k2-stats b{font-size:16px;color:#1e3a8a}
#kill-two-ai .k2-verify{margin-top:6px;font-size:13px;color:#334155}
#kill-two-ai .k2-attack{margin-top:8px;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-two-ai .k2-attack-title{font-size:13px;font-weight:600;color:#3730a3}
#kill-two-ai .k2-foot{margin-top:8px;font-size:12px;color:#94a3b8}
#kill-two-ai .k2-empty{padding:6px 2px;font-size:14px;color:#475569}
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


def _positions():
    """把 kill2_tail.POSITIONS 归一到 ((key, label, index), ...)。"""
    rows = getattr(kill2_tail, 'POSITIONS', ()) or ()
    result = []
    if isinstance(rows, dict):
        for key, value in rows.items():
            if isinstance(value, dict):
                label = value.get('label')
                index = value.get('index')
            else:
                label, index = value, None
            result.append((str(key), str(label or key), index))
        return tuple(result)
    for row in rows:
        if isinstance(row, (tuple, list)) and len(row) >= 2:
            result.append((str(row[0]), str(row[1]), row[2] if len(row) > 2 else None))
        elif isinstance(row, str):
            result.append((row, row, None))
    return tuple(result)


def _tail_label_by_key(key):
    text = str(key or '')
    if not text:
        return '-'
    tails = getattr(kill2_tail, 'TAILS', {}) or {}
    entry = tails.get(key) if isinstance(tails, dict) else None
    if isinstance(entry, dict) and entry.get('label'):
        return str(entry['label'])
    if isinstance(entry, str) and entry:
        return entry
    bare = text[5:] if text.startswith('tail_') else text
    return {'all': '千百十个', 'qbs': '千百十', 'qbg': '千百个',
            'qsg': '千十个', 'bsg': '百十个'}.get(bare, text)


def _tail_label(tail):
    if isinstance(tail, dict):
        label = tail.get('label')
        if label:
            return str(label)
        return _tail_label_by_key(tail.get('key'))
    return '-'


def _reverse_fields():
    fields = getattr(kill2_tail, 'REVERSE_KEYS', ('reverse_parity', 'reverse_size'))
    labels = getattr(kill2_tail, 'REVERSE_LABELS', None)
    defaults = {'reverse_parity': '单双反向形态', 'reverse_size': '大小反向形态'}
    result = []
    for field in fields or ():
        field = str(field)
        label = ''
        if isinstance(labels, dict):
            label = str(labels.get(field) or '')
        result.append((field, label or defaults.get(field, field)))
    return tuple(result)


def _rule_baseline():
    """整组精确基线：(项数, 随机期望)。优先 hitrate_baseline.RULE_BASELINE，未登记时按规格兜底。"""
    rule = ''
    try:
        rule = str(getattr(kill2_tail, 'RULE_VERSION', '') or '')
        from hitrate_baseline import RULE_BASELINE
        base = RULE_BASELINE.get(('kill2_tail', rule)) if rule else None
        if isinstance(base, (tuple, list)) and len(base) >= 2:
            return int(base[0]), float(base[1])
    except Exception:
        pass
    if rule == REGISTERED_RULE:
        return FALLBACK_BASELINE
    return None


def _actual_text(value):
    if isinstance(value, (list, tuple)):
        return '/'.join(str(item) for item in value)
    return str(value)


def _counted_keys(seal, positions):
    raw = seal.get('counted')
    if isinstance(raw, (list, tuple)):
        return [str(key) for key in raw if str(key) in positions]
    return [key for key, _label, _index in _positions() if key in positions]


def _unselected_keys(seal, counted_keys):
    raw = seal.get('unselected')
    if isinstance(raw, (list, tuple)) and raw:
        return [str(key) for key in raw]
    used = set(counted_keys)
    return [key for key, _label, _index in _positions() if key not in used]


def _stats_html(stats):
    total = stats.get('total') or 0
    pending = stats.get('pending') or 0
    late = stats.get('late') or 0
    counted_positions = getattr(kill2_tail, 'COUNTED_POSITIONS', 2)
    if total:
        positions = stats.get('positions') or {}
        tail_stats = stats.get('tail') or {}
        tail_ci = _wilson(tail_stats.get('hits'), tail_stats.get('bets'))
        tail_ci_text = (f'，95%CI {tail_ci[0] * 100:.1f}–{tail_ci[1] * 100:.1f}%'
                        if tail_ci else '')
        patterns = stats.get('patterns') or {}
        parity = patterns.get('reverse_parity') or {}
        size = patterns.get('reverse_size') or {}
        he2 = stats.get('he2_keep') or {}
        he3 = stats.get('he3_keep') or {}
        base = _rule_baseline()
        base_text = ('，精确基线 %s（%d 项）' % (_pct(base[1]), base[0]) if base else '')
        join = ' · '.join([
            f'整组命中率（7 项全中：2 位置 + 合尾 + 二数合 + 三数合 + 2 形态{base_text}） '
            f'<b>{_pct(stats.get("group_rate"))}</b>'
            f'（{stats.get("group_hits")}/{total} 期）',
            f'位置杀码（{counted_positions} 注/期，随机基线90%） '
            f'<b>{_pct(positions.get("rate"))}</b>'
            f'（{positions.get("hits")}/{positions.get("bets")} 注）',
            f'合尾杀码（随机基线90%） <b>{_pct(tail_stats.get("rate"))}</b>'
            f'（{tail_stats.get("hits")}/{tail_stats.get("bets")} 期{tail_ci_text}）',
            f'二数合保留4码（随机基线98.08%） <b>{_pct(he2.get("rate"))}</b>'
            f'（{he2.get("hits")}/{he2.get("bets")} 期）',
            f'三数合保留5码（随机基线93.75%） <b>{_pct(he3.get("rate"))}</b>'
            f'（{he3.get("hits")}/{he3.get("bets")} 期）',
            f'单双反向（随机基线93.75%） <b>{_pct(parity.get("rate"))}</b>'
            f'（{parity.get("hits")}/{total} 期）',
            f'大小反向（随机基线93.75%） <b>{_pct(size.get("rate"))}</b>'
            f'（{size.get("hits")}/{total} 期）',
            f'待核验 <b>{pending}</b> 期',
        ])
    else:
        join = (f'今日暂无已核验样本 · 待核验 <b>{pending}</b> 期 · '
                f'最早一期开奖后核验（北京时间 05:00 清零）')
    if late:
        join += f' · 未计入（发布晚于开奖）{late} 期'
    legacy = stats.get('legacy') or {}
    if legacy.get('total'):
        join += (f' · 旧口径（其他 revision）{legacy.get("total")} 期'
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
    return f'<div class="k2-stats"><span>{join}</span></div>'


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '已发布，等待目标期开奖核验。'
    positions = settlement.get('positions') or {}
    counted = settlement.get('counted') or list(positions)
    hit_positions = sum(1 for key in counted
                        if (positions.get(key) or {}).get('hit') is True)
    tail = settlement.get('tail') or {}
    tail_hit = tail.get('hit') is True
    tail_label = tail.get('label') or _tail_label_by_key(tail.get('key'))
    group = settlement.get('group_hit') is True
    actual = settlement.get('actual') or []
    parts = ['目标期已开奖：' + ''.join(str(value) for value in actual),
             f'位置 {hit_positions}/{len(counted)} 命中',
             f'{tail_label}合尾 {"命中" if tail_hit else "未中"}（实际 {tail.get("actual", "-")}）']
    for field, label in (('he2_keep', '二数合'), ('he3_keep', '三数合')):
        entry = settlement.get(field) or {}
        if isinstance(entry, dict) and entry:
            parts.append(f'{label}保留 {entry.get("keep", "-")}'
                         f'{"命中" if entry.get("hit") else "未中"}'
                         f'（实际 {_actual_text(entry.get("actual", "-"))}）')
    patterns = settlement.get('patterns') or {}
    for field, label in _reverse_fields():
        entry = patterns.get(field) or {}
        if entry:
            parts.append(f'{label}{"命中" if entry.get("hit") else "未中"}（实际 {entry.get("actual", "-")}）')
    if settlement.get('verified') is not True:
        parts.append('发布晚于开奖，未计入统计')
    else:
        parts.append('整组 ' + ('<b style="color:#15803d">命中</b>' if group else '<b style="color:#b91c1c">未中</b>'))
    return '；'.join(parts) + '。'


def _reverse_attributes(digit):
    """杀码的单双/大小反面，与七项独立框架同口径。"""
    return ('双' if digit % 2 == 1 else '单'), ('小' if digit >= 5 else '大')


def _form_derivation(seal):
    """展示两项反向形态如何由被杀码取反（未选位置字符自选）。"""
    positions = seal.get('positions') or {}
    if not isinstance(positions, dict):
        return ''
    counted = _counted_keys(seal, positions)
    labels = {key: label for key, label, _index in _positions()}
    parts = []
    for key in counted:
        if key not in positions:
            continue
        try:
            digit = int(positions[key])
        except (TypeError, ValueError):
            return ''
        parity, size = _reverse_attributes(digit)
        parts.append(f'{labels.get(key, key)}{positions[key]}→{parity}/{size}')
    if not parts:
        return ''
    unselected = _unselected_keys(seal, counted)
    unselected_text = '、'.join(labels.get(key, key) for key in unselected) or '-'
    return ('形态取反（按 ' + str(len(parts)) + ' 个被杀位置）：' + ' · '.join(parts)
            + f'；未选位置{unselected_text}字符自选')


def _version_html():
    """口径追溯行：当前 revision、自哪期起、本版期数、精确基线（只读）。"""
    try:
        info = kill2_tail.version_info()
    except Exception:
        return ''
    try:
        rule = str(info.get('rule_version') or '')
        first = info.get('first_ref')
        count = int(info.get('count') or 0)
        base = _rule_baseline()
        parts = ['口径 ' + escape(rule)]
        if first:
            parts.append('自 ref ' + escape(str(first)) + ' 起（本版 ' + str(count) + ' 期）')
        if base:
            parts.append('整组精确基线 %.2f%%（%d 项）' % (base[1] * 100.0, base[0]))
        parts.append('口径变更须先影子验证（7 项构成；基线为 10^4 精确枚举，非相乘）')
        return '<div class="k2-drop">' + ' · '.join(parts) + '</div>'
    except Exception:
        return ''


def _card(css_class, head, value, sub):
    return (f'<div class="k2-card {css_class}"><div class="k2-pos">{escape(str(head))}</div>'
            f'<div class="k2-kill">{escape(str(value))}</div>'
            f'<div class="k2-sub">{escape(str(sub))}</div></div>')


def _form_card(label, form, sub):
    return (f'<div class="k2-card k2-pattern"><div class="k2-pos">{escape(str(label))}</div>'
            f'<div class="k2-form">{escape(str(form))}</div>'
            f'<div class="k2-sub">{escape(str(sub))}</div></div>')


def _policy_html(seal):
    """尾码稳定化 + 位置连用限制的只读说明。"""
    notes = []
    tail_policy = seal.get('tail_policy') or {}
    if isinstance(tail_policy, dict) and tail_policy.get('policy'):
        note = str(tail_policy.get('note') or '')
        model_tail = tail_policy.get('model_tail') or {}
        candidate = ''
        if isinstance(model_tail, dict) and model_tail.get('key'):
            candidate = ('（AI 候选：' + _tail_label_by_key(model_tail.get('key'))
                         + ' 杀 ' + str(model_tail.get('kill')) + '）')
        notes.append('尾码稳定化：' + escape(note) + escape(candidate))
    position_limit = seal.get('position_restriction') or {}
    if isinstance(position_limit, dict):
        limit = getattr(kill2_tail, 'MAX_CONSECUTIVE_PERIODS', 3)
        labels = {key: label for key, label, _index in _positions()}
        parts = []
        for key, digit in (position_limit.get('blocked_digits') or {}).items():
            parts.append(
                f'{labels.get(key, key)}杀码 {digit} 已连用 {limit} 期'
                f'（含中间未选该位置）→ 本期不得再用')
        blocked_drop = position_limit.get('blocked_drop')
        if blocked_drop:
            parts.append(
                f'未选位置限制：{labels.get(blocked_drop, blocked_drop)} '
                f'已连用 {limit} 期 → 本期必须更换')
        if parts:
            notes.append('位置限制：' + escape('；'.join(parts)))
    return ''.join(f'<div class="k2-drop">{note}</div>' for note in notes)


def render_block():
    try:
        data = kill2_tail.current_summary()
        stats = data.get('stats') or {}
        seal = data.get('seal')
        if not isinstance(seal, dict):
            body = '<div class="k2-empty">本期 AI 建议尚未发布（等待开奖后计算，或本轮调用失败）。</div>'
            previous = data.get('previous')
            if isinstance(previous, dict):
                body += (f'<div class="k2-meta">最近一次：参考期 {escape(str(previous.get("ref_period", "")))}'
                         f' · 目标期 {escape(str(previous.get("target_period", "")))}</div>')
            return (f'{START}<section id="kill-two-ai"><style>{STYLE}</style>'
                    f'<h3>{TITLE}</h3>{body}{_stats_html(stats)}{_version_html()}'
                    f'{_foot_html()}</section>{END}')
        positions = seal.get('positions') or {}
        rows = _positions()
        counted_keys = _counted_keys(seal, positions)
        counted_set = set(counted_keys)
        cards = []
        for key, label, _index in rows:
            if key not in positions or key not in counted_set:
                continue
            cards.append(_card('', label, positions.get(key), '排除 1 码（计入整组）'))
        tail = seal.get('tail') or {}
        if isinstance(tail, dict) and tail:
            cards.append(_card('k2-tail', _tail_label(tail) + '合尾',
                               tail.get('kill', '-'), '排除 1 个和尾'))
        for field, label, sub in (
                ('he2_keep', '二数合保留', '保留 4 码（任取 2 位 6 组合）'),
                ('he3_keep', '三数合保留', '保留 5 码（任取 3 位 4 组合）')):
            keep = seal.get(field)
            if isinstance(keep, str) and keep:
                cards.append(_card('k2-keep', label, keep, sub))
        for field, label in _reverse_fields():
            form = seal.get(field)
            if isinstance(form, str) and len(form) == 4:
                cards.append(_form_card(label, form, '排除该四位形态（被杀位取反）'))
        labels = {key: label for key, label, _index in _positions()}
        unselected = _unselected_keys(seal, counted_keys)
        unselected_text = '、'.join(labels.get(key, key) for key in unselected) or '-'
        reason = escape(str(seal.get('reason') or '')) or '（无）'
        seal_tail_policy = seal.get('tail_policy') or {}
        if (isinstance(seal_tail_policy, dict)
                and seal_tail_policy.get('reused_previous') is True
                and '尾码按稳定化沿用上期' not in str(seal.get('reason') or '')):
            reason += ('<span style="color:#92400e;">（尾码已按稳定化沿用上期，'
                       '模型理由中的合尾为换码候选）</span>')
        derivation = _form_derivation(seal)
        derive_html = (f'<div class="k2-drop">{escape(derivation)}</div>'
                       if derivation else '')
        try:
            model = seal.get('model_label') or kill2_tail.model_label()
        except Exception:
            model = seal.get('model_label') or ''
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
        return (f'{START}<section id="kill-two-ai" data-as-of="{escape(str(seal.get("ref_period", "")))}">'
                f'<style>{STYLE}</style>'
                f'<h3>{TITLE}</h3>'
                f'<div class="k2-meta">{meta}</div>'
                f'<div class="k2-grid">{"".join(cards)}</div>'
                f'<div class="k2-drop">未选位置：{escape(unselected_text)}'
                f'（不参与定位项，攻击语保留 10 码；{len(counted_keys)} 个被杀位置全部计入整组）</div>'
                f'{_policy_html(seal)}{derive_html}'
                f'<div class="k2-reason">理由：{reason}</div>'
                f'{_stats_html(stats)}{_version_html()}'
                f'<div class="k2-verify">{verify}</div>'
                f'{_attack_html(seal)}'
                f'{_foot_html()}</section>{END}')
    except Exception:
        return ''


def _attack_html(seal):
    """与七项独立框架同格式的攻击语 + 复制按钮（可被全局攻击语编辑框接管）。"""
    if not isinstance(seal, dict) or not seal.get('published_at'):
        return ''
    try:
        text = kill2_tail.build_attack(seal)
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
        "var b=document.getElementById('kill2-attack-text');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('kill2-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    return ('<div class="k2-attack">'
            '<div class="k2-attack-title">攻击语（复制到平台，与七项独立框架同格式）</div>'
            '<textarea id="kill2-attack-text" readonly rows="3" '
            'aria-label="AI 二位置+一合尾+二数合/三数合攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #c7d2fe;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea>'
            '<button id="kill2-attack-copy" type="button" onclick="' + copy_action + '" '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#4f46e5;color:#fff;cursor:pointer;">'
            '复制攻击语</button>'
            '<span id="kill2-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            '<script id="kill2-attack-data" type="application/json">' + payload + '</script>')


def _foot_html():
    hold = getattr(kill2_tail, 'TAIL_HOLD_MAX', 3)
    limit = getattr(kill2_tail, 'MAX_CONSECUTIVE_PERIODS', 3)
    counted = getattr(kill2_tail, 'COUNTED_POSITIONS', 2)
    return ('<div class="k2-foot">'
            f'由 DeepSeek 决定：从千/百/十/个中选 {counted} 个位置各排除 1 码，其余位置不参与定位项'
            '（攻击语保留 10 码）；'
            '合尾从 5 种中选 1 种排除 1 个和尾数字（稳定化：命中自动沿用、最多自动沿用 '
            + str(hold) + ' 期；失手或满 ' + str(hold) + ' 期后由模型重选，重选可与上期同码；'
            'AI 尾项为换码候选），并给出二数合保留 4 码、三数合保留 5 码与两项四位反向形态。<br>'
            '同一位置同一杀码最多连用 ' + str(limit) + ' 期（中间未选该位置不清零），满后下一期必须更换。<br>'
            '命中 = 该位置实际开奖号<b>不在</b>杀码上（2 个位置全部计入）；'
            '合尾命中 = 该合尾实际和值个位<b>不在</b>排除码上；'
            '二数合命中 = 前 4 球任取 2 位的 6 种组合求和取个位，任一落在保留 4 码内；'
            '三数合命中 = 前 4 球任取 3 位的 4 种组合求和取个位，任一落在保留 5 码内；'
            '反向形态命中 = 实际四位单双/大小形态<b>不等于</b>封存形态'
            '（2 个被杀位置都取杀码形态反面；未选 2 位字符自选）。<br>'
            '2 个位置 + 1 个合尾 + 二数合 + 三数合 + 2 项反向形态共 7 项同时命中才算整组命中'
            '（整组精确基线 57.08%，10^4 精确枚举，非相乘）；'
            '只统计开奖前发布、开奖后核验的记录；北京时间 05:00 清零；只使用前 4 球（千/百/十/个），'
            '第 5 球不参与；平台提前 70 秒关盘。'
            '</div>')


def apply_kill_two_block(content, report=None):
    """原地替换/插入 AI 二位置+二数合/三数合区块；任何异常原样返回 content。"""
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
        for anchor in ('<!-- KILL_THREE_END -->', '<!-- COLD_HIT_STATS_END -->',
                       '<!-- POSITION_COLD_END -->'):
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
