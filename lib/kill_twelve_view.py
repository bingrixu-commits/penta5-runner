"""AI 单位置杀码 + 单双反向形态 + 一合尾 建议面板区块（kill12 独立框架，v4：1 定位 + 1 单双反向形态 + 1 合尾）。

数据源 data/kill12_tail/{seals,settlements}.json，由 scripts/run_kill12_tail.py 每期生成。
与既有框架完全独立：独立 AI 请求 / 封存 / 结算 / 统计 / 面板区块 / 攻击语。

命中口径（写死，面板脚注同步展示）：
- 位置命中 = 该位置实际开奖号 != 排除码（恰好 1 个位置，1 注/期）；
- 合尾命中 = 该合尾实际和值个位 != 排除码（5 种合尾选 1）；
- 单双反向形态命中 = 目标期实际四位单双形态串 != 封存形态串（反向排除项，4 字符）；
- 整组命中 = 1 位置 + 1 项单双反向形态 + 1 合尾同时命中（共 3 项）；
- 形态推导：被杀位置字符取杀码数字单双的反面（奇→双、偶→单），其余 3 位 AI 自选；
- 无位置/杀码连用限制；合尾 hold3-until-miss 稳定化；
- 只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；
- 只使用前 4 球（千/百/十/个），第 5 球不参与。
"""
from datetime import datetime
from html import escape
import json
import math
from pathlib import Path

import kill12_tail

START = '<!-- KILL_TWELVE_START -->'
END = '<!-- KILL_TWELVE_END -->'

# 整组随机基线兜底：(项数, 精确期望命中率)。
# 3 项 = 1 个定位杀码 + 1 项单双反向形态 + 1 个合尾（整组 3 项）。
# KILL12_ENUM_PLACEHOLDER: 正式精确枚举值待 enum 队友产出后由 Lead 登记到
# hitrate_baseline.RULE_BASELINE[('kill12_tail', 'kill12-tail-v4-one-position-one-form')]；
# 未登记前回退本兜底值（预期 ≈0.7538）。
FALLBACK_BASELINE = (3, 0.7538)

_TITLE = '🤖 AI 单位置杀码 + 单双形态 + 一合尾建议'

STYLE = """
#kill-twelve-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-twelve-ai *{box-sizing:border-box}
#kill-twelve-ai h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#kill-twelve-ai .k1-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#kill-twelve-ai .k1-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr));gap:10px;min-width:0;margin-bottom:10px}
#kill-twelve-ai .k1-card{border:1px solid #c7d2fe;border-radius:8px;background:#fff;padding:10px 12px;text-align:center;min-width:0}
#kill-twelve-ai .k1-card.k1-tail{border-color:#fecaca;background:#fff7f7}
#kill-twelve-ai .k1-card.k1-pattern{border-color:#ddd6fe;background:#faf8ff}
#kill-twelve-ai .k1-pos{font-size:13px;color:#475569}
#kill-twelve-ai .k1-kill{font-size:32px;font-weight:700;color:#1d4ed8;line-height:1.2}
#kill-twelve-ai .k1-form{font-size:22px;font-weight:700;color:#7c3aed;letter-spacing:2px;line-height:1.35}
#kill-twelve-ai .k1-card.k1-tail .k1-kill{color:#b91c1c}
#kill-twelve-ai .k1-sub{font-size:12px;color:#64748b}
#kill-twelve-ai .k1-drop{font-size:13px;color:#94a3b8;margin-bottom:6px}
#kill-twelve-ai .k1-reason{font-size:13px;color:#475569;margin-bottom:8px}
#kill-twelve-ai .k1-stats{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-twelve-ai .k1-stats b{font-size:16px;color:#1e3a8a}
#kill-twelve-ai .k1-verify{margin-top:6px;font-size:13px;color:#334155}
#kill-twelve-ai .k1-attack{margin-top:8px;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-twelve-ai .k1-attack-title{font-size:13px;font-weight:600;color:#3730a3}
#kill-twelve-ai .k1-foot{margin-top:8px;font-size:12px;color:#94a3b8}
#kill-twelve-ai .k1-empty{padding:6px 2px;font-size:14px;color:#475569}
#kill-twelve-ai .k1-stale{margin:8px 0;padding:9px 12px;border:1px solid #fca5a5;border-radius:8px;background:#fef2f2;color:#991b1b;font-size:13px;line-height:1.65;font-weight:600}
#kill-twelve-ai .k1-basis{font-size:12px;color:#475569;margin-bottom:6px}
#kill-twelve-ai .k1-attack button[disabled]{opacity:.55;cursor:not-allowed}
"""


def _section_open(extra=''):
    """section 开头（id 字面量全文件仅在此处出现一次，便于 grep 计数）。"""
    return f'<section id="kill-twelve-ai"{extra}>'


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
    positions = stats.get('positions') or {}
    tail_stats = stats.get('tail') or {}
    form_stats = stats.get('form') or {}
    if total:
        join = ' · '.join([
            '整组命中率（3 项全中：1 位置 + 1 单双形态 + 1 合尾） <b>%s</b>（%s/%s 期）'
            % (_pct(stats.get('group_rate')), stats.get('group_hits'), total),
            '位置杀码（1 注/期，随机基线90%%） <b>%s</b>（%s/%s 注）'
            % (_pct(positions.get('rate')), positions.get('hits'), positions.get('bets')),
            '单双反向形态（随机基线93.75%%） <b>%s</b>（%s/%s 期）'
            % (_pct(form_stats.get('rate')), form_stats.get('hits'), form_stats.get('bets')),
            '合尾杀码（随机基线90%%） <b>%s</b>（%s/%s 期）'
            % (_pct(tail_stats.get('rate')), tail_stats.get('hits'), tail_stats.get('bets')),
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
    join += (' · 今日四手连败 <b>%s</b> 次 / 累计四手连败 <b>%s</b> 次'
             '（自本版本上线起，跨天不重叠计数）'
             % (stats.get('four_loss_cycles') or 0,
                stats.get('four_loss_cycles_total') or 0))
    bets = positions.get('bets') or 0
    if bets and bets < 200:
        sigma = 100.0 * math.sqrt(0.9 * 0.1 / bets)
        join += (' · 位置单项样本 n=%s（&lt;200 属小样本：90%% 项 1σ≈%.1fpp、2σ≈%.1fpp），'
                 '单日单项只作波动参考' % (bets, sigma, 2 * sigma))
    return '<div class="k1-stats"><span>%s</span></div>' % join


def _settlement_line(settlement, seal=None, latest=None):
    if not isinstance(settlement, dict):
        seal = seal if isinstance(seal, dict) else {}
        target = str(seal.get('target_period') or '')
        state = str(seal.get('publication_state') or '')
        if state and state != 'published':
            return '本期尚未发布（仅封存，未计入统计）；任务恢复后下一期重新发布。'
        if target and latest and target <= str(latest):
            return ('目标期 %s 已开奖（数据源最新期 %s），尚未结算；下个任务周期补结算。'
                    % (escape(target), escape(str(latest))))
        return '已发布，等待目标期开奖核验。'
    positions = settlement.get('positions') or {}
    counted = settlement.get('counted') or list(positions)
    parts = ['目标期已开奖：' + ''.join(str(value) for value in (settlement.get('actual') or []))]
    for key in counted:
        entry = positions.get(key) or {}
        if not isinstance(entry, dict):
            continue
        label = kill12_tail.POSITION_LABELS.get(key, key)
        parts.append('%s杀 %s %s（实际 %s）' % (
            label, entry.get('kill', '-'),
            '命中' if entry.get('hit') is True else '未中', entry.get('actual', '-')))
    tail = settlement.get('tail') or {}
    if isinstance(tail, dict) and tail:
        parts.append('%s合尾杀 %s %s（实际 %s）' % (
            tail.get('label') or '', tail.get('kill', '-'),
            '命中' if tail.get('hit') is True else '未中', tail.get('actual', '-')))
    form = settlement.get('form') or {}
    if isinstance(form, dict) and form.get('form'):
        parts.append('%s %s %s（实际 %s）' % (
            form.get('label') or form.get('type') or '单双反向形态', form.get('form') or '',
            '命中' if form.get('hit') is True else '未中', form.get('actual', '-')))
    if settlement.get('verified') is not True:
        parts.append('发布晚于开奖，未计入统计')
    else:
        parts.append('整组 ' + ('<b style="color:#15803d">命中</b>'
                                if settlement.get('group_hit') is True
                                else '<b style="color:#b91c1c">未中</b>'))
    return '；'.join(parts) + '。'


def _form_derivation(seal):
    """形态推导行：被杀位置取杀码单双形态反面，其余 3 个位置字符由 AI 自选。"""
    positions = seal.get('positions') or {}
    form_type = str(seal.get('form_type') or '')
    form = seal.get('form')
    if not isinstance(positions, dict) or form_type not in kill12_tail.REVERSE_LABELS:
        return ''
    if not isinstance(form, str) or len(form) != 4:
        return ''
    parts = []
    for key, label, _index in kill12_tail.POSITIONS:
        if key not in positions:
            continue
        kill_text = str(positions[key])
        try:
            kill_number = int(kill_text)
        except (TypeError, ValueError):
            return ''
        if not 0 <= kill_number <= 9:
            return ''
        reverse_parity = kill12_tail.reverse_attributes(kill_number)
        parts.append('%s杀 %s → 被杀位置必须写 %s' % (label, kill_text, reverse_parity))
    if not parts:
        return ''
    form_label = kill12_tail.REVERSE_LABELS.get(form_type, form_type)
    return ('形态推导（%s）：%s；其余 3 个位置字符由 AI 自选；封存形态 %s'
            % (form_label, ' · '.join(parts), form))


def _tail_policy_html(seal):
    """合尾 hold3 稳定化说明。"""
    policy = seal.get('tail_policy')
    if not isinstance(policy, dict) or not policy:
        return ''
    note = str(policy.get('note') or '').strip()
    bits = ['合尾稳定化：' + (note or 'hold3-until-miss（命中后最多连用 3 期（含首次封存））')]
    model_tail = policy.get('model_tail')
    if isinstance(model_tail, dict):
        key = str(model_tail.get('key') or '')
        label = str(model_tail.get('label') or (kill12_tail.TAILS.get(key) or {}).get('label') or '')
        bits.append('AI 候选 %s合尾 杀%s' % (label, model_tail.get('kill')))
    if policy.get('previous_hit') is True:
        bits.append('上期命中')
    elif policy.get('previous_contiguous') is False:
        bits.append('上期与本期不连续，未沿用')
    return '<div class="k1-drop">%s</div>' % escape(' · '.join(bits))


def _missing_version_targets(rule):
    """当前 rule 已发布封存目标期的缺槽（按每期序列推算）；仅供卡面披露，不改统计。"""
    try:
        refs = set()
        targets = set()
        for seal in kill12_tail.load_seals():
            if not isinstance(seal, dict):
                continue
            if str(seal.get('rule_version') or '') != rule:
                continue
            if seal.get('publication_state') != 'published':
                continue
            ref = str(seal.get('ref_period') or '')
            target = str(seal.get('target_period') or '')
            if len(ref) == 11:
                refs.add(ref)
            if len(target) == 11:
                targets.add(target)
        if not refs or not targets:
            return []
        cursor = kill12_tail.next_period(min(refs))
        end = kill12_tail.next_period(max(refs))
        missing = []
        guard = 0
        while cursor and end and cursor <= end and guard < 20000:
            if cursor not in targets:
                missing.append(cursor)
            cursor = kill12_tail.next_period(cursor)
            guard += 1
        return missing
    except Exception:
        return []


def _version_html(stats=None):
    """口径追溯行：rule_version、自 ref 起期数（含待核验）、缺期披露、整组精确基线（未登记回退 FALLBACK_BASELINE）。"""
    try:
        stats = stats if isinstance(stats, dict) else {}
        rule = str(getattr(kill12_tail, 'RULE_VERSION', '') or '')
        info = {}
        try:
            info = kill12_tail.version_info() or {}
            rule = str(info.get('rule_version') or rule)
        except Exception:
            info = {}
        baseline = None
        try:
            from hitrate_baseline import RULE_BASELINE
            baseline = RULE_BASELINE.get(('kill12_tail', rule))
        except Exception:
            baseline = None
        if not baseline:
            baseline = FALLBACK_BASELINE
        parts = ['口径 ' + escape(rule)]
        first = info.get('first_ref')
        if first:
            count = int(info.get('count') or 0)
            detail = '本版 %d 期' % count
            try:
                pending = int(stats.get('pending') or 0)
            except (TypeError, ValueError):
                pending = 0
            if pending:
                detail += '，含待核验 %d 期' % pending
            parts.append('自 ref ' + escape(str(first)) + ' 起（' + detail + '）')
        missing = _missing_version_targets(rule)
        published_count = int(info.get('count') or 0)
        if missing:
            shown = '；'.join(missing[:3])
            if len(missing) > 3:
                shown += '；等'
            planned = published_count + len(missing)
            coverage = (published_count / planned * 100.0) if planned else 0.0
            parts.append('规划 %d 槽 · 已发布 %d 槽（覆盖率 %.1f%%） · 未发布 %d 槽'
                         '（未发布含全局 AI 停用/数据未就绪窗口，示例 %s；'
                         '不计入累计 n、不按 0 命中）'
                         % (planned, published_count, coverage, len(missing), escape(shown)))
        elif published_count:
            parts.append('规划 %d 槽 · 已发布 %d 槽 · 无未发布槽位'
                         % (published_count, published_count))
        parts.append('整组精确基线 %.2f%%（%d 项）'
                     % (float(baseline[1]) * 100.0, int(baseline[0])))
        parts.append('kill12 独立框架：与既有框架完全独立封存 / 结算 / 统计')
        return '<div class="k1-drop">' + ' · '.join(parts) + '</div>'
    except Exception:
        return ''


def _cumulative_html():
    """只读：本版累计（与 /hitrate 同源：framework_hitrate_cumulative.json；缺失回退稳定性报告）。"""
    try:
        rule = str(getattr(kill12_tail, 'RULE_VERSION', '') or '')
        row = None
        as_of = None
        for filename in ('framework_hitrate_cumulative.json', 'hitrate_stability.json'):
            try:
                report = json.loads((Path('/root/penta5-runner/data') / filename).read_text(encoding='utf-8'))
            except Exception:
                continue
            rows = report.get('rows') if isinstance(report, dict) else None
            if not isinstance(rows, list):
                continue
            candidate = next((item for item in rows if isinstance(item, dict)
                              and item.get('key') == 'kill12_tail'), None)
            if candidate is None or str(candidate.get('rule') or '') != rule:
                continue
            row = candidate
            if filename.startswith('framework_'):
                as_of = candidate.get('last_period') or report.get('as_of_period')
            else:
                as_of = report.get('as_of_period')
            break
        if not row:
            return ''
        n = row.get('n')
        hits = row.get('hits')
        rate = row.get('rate')
        if not isinstance(n, int) or n < 1 or not isinstance(hits, int) or rate is None:
            return ''
        ci = row.get('ci95')
        ci_text = ('，95%%CI %.1f%%–%.1f%%' % (ci[0] * 100.0, ci[1] * 100.0)
                   if isinstance(ci, (list, tuple)) and len(ci) >= 2 else '')
        parts = ['<b>本版累计（跨天不清零）</b>']
        if as_of:
            parts.append('数据截至 <b>%s</b>（与 /hitrate 同源，每 15 分钟刷新，最多滞后约 4 期/约 20 分钟；'
                         '今日行按业务日实时）' % escape(str(as_of)))
        parts.append('整组 <b>%.2f%%</b>（%d/%d 期%s）'
                     % (float(rate) * 100.0, hits, n, ci_text))
        if row.get('expected') is not None:
            parts.append('精确基线 %.2f%%' % (float(row['expected']) * 100.0))
        if row.get('delta') is not None:
            parts.append('偏差 %+.2fpp' % (float(row['delta']) * 100.0))
        if row.get('p') is not None:
            parts.append('二项 p=%.3f' % float(row['p']))
        item = row.get('item_stats') if isinstance(row.get('item_stats'), dict) else {}
        if item.get('items') and item.get('avg_items') is not None:
            parts.append('平均命中 %.2f/%d 项（项级 %.1f%%）'
                         % (float(item['avg_items']), int(item['items']),
                            float(item.get('item_rate') or 0) * 100.0))
        versions = row.get('versions') if isinstance(row.get('versions'), dict) else {}
        core = versions.get('core') if isinstance(versions.get('core'), dict) else {}
        segments = versions.get('segments') if isinstance(versions.get('segments'), list) else []
        if core.get('item_total') and core.get('item_rate') is not None:
            parts.append('<b>跨版本核心杀项（切版不清零）</b> %d/%d 项 = <b>%.2f%%</b>'
                         '（基线 90.0%%，%d 期）'
                         % (int(core['item_hits']), int(core['item_total']),
                            float(core['item_rate']) * 100.0,
                            int(core.get('periods') or 0)))
            if segments:
                parts.append('分段（近 5 版）：' + ' · '.join(
                    '%s %d期 %.1f%%'
                    % (str(segment.get('rule') or '').replace('kill12-tail-', ''),
                       int(segment.get('n') or 0),
                       float(segment.get('core_rate') or 0.0) * 100.0)
                    for segment in segments[-5:]))
        if n < 200:
            parts.append('样本不足（n<200），仅作参考')
        return ('<div class="k1-acc" style="margin-top:6px;padding-top:6px;'
                'border-top:1px dashed #c7d2fe;font-size:13px;color:#475569;line-height:1.7">'
                + ' · '.join(parts) + '</div>')
    except Exception:
        return ''


def _stale_banner_html(seal, latest):
    """最新一期未发布时，明确提示当前展示的是历史推荐（防止照旧投注）。"""
    if not isinstance(seal, dict):
        return ''
    ref = str(seal.get('ref_period') or '-')
    target = str(seal.get('target_period') or '-')
    latest_text = str(latest or '-')
    return ('<div class="k1-stale">⚠ 本框架最近一期未发布：数据源最新开奖期 %s，当前展示的是'
            '参考期 %s（目标期 %s）的历史推荐，已过期。请勿照此投注；攻击语复制已禁用。'
            '可能原因：全局 AI 已停止 / 本框架已停用 / 任务失败。恢复后下一期会重新发布。</div>'
            % (escape(latest_text), escape(ref), escape(target)))


def _position_basis_html(basis):
    """定位依据留档展示（仅新封存带 position_basis 字段）。"""
    if not isinstance(basis, dict) or not basis.get('candidates'):
        return ''
    key = str(basis.get('key') or '')
    label = kill12_tail.POSITION_LABELS.get(key, key)
    top = '、'.join(str(value) for value in (basis.get('hot_top3') or [])) or '-'
    repeat = str(basis.get('repeat') if basis.get('repeat') is not None else '-')
    neighbor = '%s/%s' % (basis.get('neighbor_prev') or '-', basis.get('neighbor_next') or '-')
    ok = basis.get('in_candidates') is True
    status = ('合规（候选内%s）' % ('，热码 top3' if basis.get('in_hot_top3') else '')
              if ok else '超出候选集，请核查')
    return ('<div class="k1-basis">定位依据：%s 热码 top3 %s · 上期重码 %s · 邻码 %s；'
            '排除码 %s → %s</div>'
            % (escape(label), escape(top), escape(repeat), escape(neighbor),
               escape(str(basis.get('kill') or '-')), escape(status)))


def render_block():
    """渲染 kill12 面板区块（最新已发布封存 + 当日统计 + 攻击语）。"""
    try:
        summary = kill12_tail.current_summary()
    except Exception:
        return ''
    seal = (summary.get('seal') or summary.get('previous')) if isinstance(summary, dict) else None
    if not isinstance(seal, dict):
        return (_section_open()
                + '<h3>' + _TITLE + '</h3>'
                + '<div class="k1-empty">等待首期封存：cron 每 5 分钟检查，开奖后生成并发布。</div>'
                + '</section>')
    stats = summary.get('stats') or {}
    settlement = summary.get('settlement')
    latest = str(summary.get('latest_period') or stats.get('latest_period') or '')
    stale = bool(isinstance(summary, dict) and summary.get('seal') is None)
    reference = seal.get('ref_period')
    target = seal.get('target_period')
    positions = seal.get('positions') or {}
    counted = seal.get('counted') or list(positions)
    tail = seal.get('tail') or {}
    form_type = str(seal.get('form_type') or '')
    form = seal.get('form') if (
        form_type in kill12_tail.REVERSE_LABELS
        and isinstance(seal.get('form'), str)
        and len(seal.get('form')) == 4
    ) else ''
    html = [_section_open(' data-as-of="%s"' % escape(str(reference or '')))]
    html.append('<h3>' + _TITLE + '</h3>')
    html.append('<div class="k1-meta">参考期 %s · 目标期 %s · AI 计算 %s（北京时间） · %s</div>'
                % (escape(str(reference or '-')), escape(str(target or '-')),
                   _clock(seal.get('generated_at')),
                   escape(str(seal.get('model_label') or seal.get('model') or '-'))))
    if stale:
        html.append(_stale_banner_html(seal, latest))
    cards = []
    for key, label, _index in kill12_tail.POSITIONS:
        if key not in positions:
            continue
        kill = positions.get(key)
        counted_mark = '（计入整组）' if key in counted else '（不计入整组）'
        cards.append('<div class="k1-card"><div class="k1-pos">%s</div>'
                     '<div class="k1-kill">%s</div>'
                     '<div class="k1-sub">定位排除 1 码%s</div></div>'
                     % (escape(label), escape(str(kill)), escape(counted_mark)))
    if form:
        form_label = kill12_tail.REVERSE_LABELS.get(form_type, form_type)
        sub = '反向排除该四位单双形态（计入整组）'
        for key, label, index in kill12_tail.POSITIONS:
            if key not in positions:
                continue
            kill_text = str(positions[key])
            try:
                kill_number = int(kill_text)
            except (TypeError, ValueError):
                kill_number = None
            if kill_number is not None and 0 <= kill_number <= 9 and index < len(form):
                sub = ('被杀位置对照片：%s杀 %s → 第 %d 位取反写 %s'
                       % (label, kill_text, index + 1, form[index]))
            break
        cards.append('<div class="k1-card k1-pattern"><div class="k1-pos">%s</div>'
                     '<div class="k1-form">%s</div>'
                     '<div class="k1-sub">%s</div></div>'
                     % (escape(str(form_label)), escape(form), escape(sub)))
    if isinstance(tail, dict) and tail:
        cards.append('<div class="k1-card k1-tail"><div class="k1-pos">%s合尾</div>'
                     '<div class="k1-kill">%s</div>'
                     '<div class="k1-sub">排除 1 个和尾（计入整组）</div></div>'
                     % (escape(str(tail.get('label') or '')), escape(str(tail.get('kill', '-')))))
    html.append('<div class="k1-grid">' + ''.join(cards) + '</div>')
    reason = escape(str(seal.get('reason') or '').strip())
    if reason:
        html.append('<div class="k1-reason">理由：%s</div>' % reason)
    html.append(_tail_policy_html(seal))
    html.append(_form_derivation(seal))
    html.append(_position_basis_html(seal.get('position_basis')))
    html.append('<div class="k1-drop">整组 3 项：1 定位杀码 + 1 单双反向形态 + 1 合尾；'
                '位置与杀码不做连用限制（完全由模型每期自选）；'
                '合尾稳定化 hold3-until-miss（命中后最多连用 3 期（含首次封存），缺期不沿用）</div>')
    html.append('<div class="k1-drop">发布 %s · 窗口 %s 期 · 关盘按开奖槽位倒推 70 秒</div>'
                % (_clock(seal.get('published_at')), escape(str(seal.get('window') or '-'))))
    html.append('<div class="k1-verify">' + _settlement_line(settlement, seal, latest) + '</div>')
    html.append(_stats_html(stats))
    html.append(_cumulative_html())
    html.append(_version_html(stats))
    html.append(_attack_html(seal, stale=stale))
    html.append(_foot_html())
    html.append('</section>')
    return ''.join(html)


def _attack_html(seal, stale=False):
    """与既有框架同格式的攻击语 + 复制按钮（复制脚本沿用同款，只换元素 id）。

    stale=True（展示的是已过期的历史推荐）时禁用复制，避免照旧投注。
    """
    if not isinstance(seal, dict) or not seal.get('published_at'):
        return ''
    try:
        text = kill12_tail.build_attack(seal)
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
        "var b=document.getElementById('kill12-attack-text');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('kill12-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    title = '攻击语（复制到平台，与八项同格式）'
    if stale:
        title += ' · ⚠ 已过期，禁止复制'
        button = ('<button id="kill12-attack-copy" type="button" disabled '
                  'style="padding:7px 15px;border:0;border-radius:7px;background:#94a3b8;color:#fff;cursor:not-allowed;">'
                  '已过期，禁止复制</button>')
    else:
        button = ('<button id="kill12-attack-copy" type="button" onclick="' + copy_action + '" '
                  'style="padding:7px 15px;border:0;border-radius:7px;background:#4f46e5;color:#fff;cursor:pointer;">'
                  '复制攻击语</button>')
    return ('<div class="k1-attack">'
            '<div class="k1-attack-title">' + title + '</div>'
            '<textarea id="kill12-attack-text" readonly rows="3" aria-label="AI 单位置+单双形态+一合尾攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
            'border:1px solid #c7d2fe;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea>'
            + button +
            '<span id="kill12-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>'
            '<script id="kill12-attack-data" type="application/json">' + payload + '</script>')


def _foot_html():
    return ('<div class="k1-foot">口径（写死）：'
            '定位杀码 = 该位置实际开奖号 ≠ 排除码（4 个位置选 1，1 注/期）；'
            '单双反向形态 = 4 字符（仅单/双，按千百十个顺序），被杀位置字符必须取杀码单双反面'
            '（杀码为奇数→双、偶数→单），其余 3 位由 AI 自选；'
            '命中 = 目标期实际四位单双形态串 ≠ 封存形态串；'
            '合尾杀码 = 该合尾实际和值个位 ≠ 排除码（5 种合尾选 1；hold3-until-miss 稳定化：'
            '命中后最多连用 3 期（含首次封存）；失手、连用满 3 期或缺期时由模型重选，重选可与上期同码；AI 尾项为换码候选）；'
            '整组命中 = 1 位置 + 1 项单双反向形态 + 1 合尾同时命中（共 3 项）；'
            '定位依据：排除码须取自该位置近 50 期热码 top3、上期重码、上期邻码（±1，0↔9 循环），'
            '不得以上期冷码 / 长遗漏码为依据；位置与杀码不做连用限制（每期由模型自选）；'
            '只统计开奖前已发布、开奖后按真实开奖核验的记录；北京时间 05:00 清零；'
            '累计四手连败自本版本上线起跨天叠加：连续 4 期整组未全中记 1 次、8 期记 2 次，'
            '赢一次清零，不随 05:00 清零；只使用前 4 球（千/百/十/个），第 5 球不参与；'
            '平台提前 70 秒关盘。</div>')


def apply_kill_twelve_block(content, report=None):
    """把 kill12 区块原地写入面板 content（START/END 标记替换；缺失时按兜底锚点插入）。"""
    try:
        if not isinstance(content, str):
            return content
        block = '<style>' + STYLE + '</style>' + render_block()
        if START in content and END in content:
            head, rest = content.split(START, 1)
            _old, tail_part = rest.split(END, 1)
            return head + START + block + END + tail_part
        if '<!-- KILL_ELEVEN_END -->' in content:
            return content.replace('<!-- KILL_ELEVEN_END -->',
                                   '<!-- KILL_ELEVEN_END -->' + START + block + END, 1)
        marker = '</body>'
        if marker in content:
            return content.replace(marker, START + block + END + marker, 1)
        return content
    except Exception:
        if report is not None:
            try:
                report('kill12-block-failed')
            except Exception:
                pass
        return content
