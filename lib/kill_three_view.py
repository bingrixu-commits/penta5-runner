"""AI 自选 2~4 位置杀码 + 一合尾 + 两反向形态 + 三数共现建议面板区块（v17 起替代固定三位置口径）。

数据源 data/kill3_tail/{seals,settlements}.json，由 scripts/run_kill3_tail.py 每期生成。

命中口径（写死，面板脚注同步展示）：
- 位置命中 = 该位置实际开奖号 != 杀码；
- 合尾命中 = 该合尾实际和值个位 != 排除码；
- 反向形态命中 = 目标期实际四位单双/大小形态 != 封存形态（反向排除项）；
- 整组命中 = AI 自选 N 个位置（N=2~4，全部计入）+ 1 个合尾 + 2 项反向形态 + 三数共现避开 3 组同时命中（共 N+6 项）；
- 三数共现避开 1/2/3 组由系统按目标期给定（data/state.json extraKills），每组算 1 项，
  单项命中 = 该组 3 个数字未全部出现在 4 球开奖号中，单项基线 97.96%；
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
from pathlib import Path
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


def _latest_seal_baseline(rule=None):
    """最近一期同 rule 封存的期望口径（position_count/expected_items/expected_group_rate）。

    v17 起整组项数与期望基线随每期自选 N 变化，面板从这里取动态值；v16 旧封存没有
    expected_* 字段时按位置数补齐，历史渲染保持可读。
    """
    try:
        seals = kill3_tail.load_seals()
    except Exception:
        return None
    rule = rule or str(getattr(kill3_tail, 'RULE_VERSION', '') or '')
    for seal in reversed(seals if isinstance(seals, list) else []):
        if not isinstance(seal, dict):
            continue
        if str(seal.get('rule_version') or '') != rule:
            continue
        try:
            count = int(seal.get('position_count')) if seal.get('position_count') is not None else kill3_tail.seal_position_count(seal)
        except Exception:
            continue
        items = seal.get('expected_items')
        if items is None:
            try:
                items = kill3_tail.expected_items(count)
            except Exception:
                items = count + 6
        rate = seal.get('expected_group_rate')
        if rate is None:
            try:
                rate = kill3_tail.expected_group_rate(count)
            except Exception:
                rate = None
        return {'items': int(items), 'count': count, 'rate': rate}
    return None

def _stats_html(stats, seal=None):
    total = stats.get('total') or 0
    pending = stats.get('pending') or 0
    late = stats.get('late') or 0
    baseline = _latest_seal_baseline() or {}
    position_count = None
    if isinstance(seal, dict):
        position_count = kill3_tail.seal_position_count(seal) or None
    position_count = position_count or baseline.get('count')
    group_items = baseline.get('items') or (kill3_tail.expected_items(position_count) if position_count else None)
    items_text = ('%d 项' % group_items) if group_items else 'N+6 项'
    count_text = ('%d' % position_count) if position_count else 'N'
    if total:
        tail_stats = stats.get('tail') or {}
        tail_ci = _wilson(tail_stats.get('hits'), tail_stats.get('bets'))
        tail_ci_text = (f'，95%CI {tail_ci[0] * 100:.1f}–{tail_ci[1] * 100:.1f}%' if tail_ci else '')
        patterns = stats.get('patterns') or {}
        parity = patterns.get('reverse_parity') or {}
        size = patterns.get('reverse_size') or {}
        merge_avoid = (stats.get('three_avoid') or {}).get('merged') or {}
        per_avoid = (stats.get('three_avoid') or {}).get('per_group') or {}
        per_avoid_text = ' / '.join(
            '%s %s' % (_pct((per_avoid.get(key) or {}).get('rate')),
                       (per_avoid.get(key) or {}).get('label') or key)
            for key in getattr(kill3_tail, 'THREE_AVOID_KEYS', ()))
        join = ' · '.join([
            f'整组命中率（{items_text}全中：{count_text} 位置 + 合尾 + 2 形态 + 三数共现 3 组；N=2~4 每期自选） <b>{_pct(stats.get("group_rate"))}</b>'
            f'（{stats.get("group_hits")}/{total} 期）',
            f'位置杀码（每期 N 注、N=2~4，随机基线90%） <b>{_pct(stats.get("positions", {}).get("rate"))}</b>'
            f'（{stats.get("positions", {}).get("hits")}/{stats.get("positions", {}).get("bets")} 注）',
            f'合尾杀码（随机基线90%） <b>{_pct(tail_stats.get("rate"))}</b>'
            f'（{tail_stats.get("hits")}/{tail_stats.get("bets")} 期{tail_ci_text}）',
            f'单双反向（随机基线93.75%） <b>{_pct(parity.get("rate"))}</b>（{parity.get("hits")}/{total} 期）',
            f'大小反向（随机基线93.75%） <b>{_pct(size.get("rate"))}</b>（{size.get("hits")}/{total} 期）',
            f'三数共现避开 3 组（单项基线97.96%） <b>{_pct(merge_avoid.get("rate"))}</b>'
            f'（{merge_avoid.get("hits")}/{merge_avoid.get("bets")} 期{("；" + per_avoid_text) if per_avoid_text else ""}）',
            f'待核验 <b>{pending}</b> 期',
        ])
    else:
        join = (f'今日暂无已核验样本 · 待核验 <b>{pending}</b> 期 · '
                f'最早一期开奖后核验（北京时间 05:00 清零）')
    if late:
        join += f' · 未计入（发布晚于开奖）{late} 期'
    legacy = stats.get('legacy') or {}
    if legacy.get('total'):
        join += (f' · 旧口径（历史版本）{legacy.get("total")} 期'
                 f'（整组 {legacy.get("group_hits")} 中）已归档不计入')
    win, loss = stats.get('current_win_streak') or 0, stats.get('current_loss_streak') or 0
    if win:
        join += f' · 当前 <b style="color:#15803d">连中 {win} 期</b>'
    elif loss:
        join += f' · 当前 <b style="color:#b91c1c">连败 {loss} 期</b>'
    join += f' · 累计四手连败 <b>{stats.get("four_loss_cycles_total") or 0}</b> 次（自本版本上线起，跨天不重叠计数）'
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
    avoid_entries = [entry for entry in (settlement.get('three_avoid') or [])
                     if isinstance(entry, dict)]
    if avoid_entries:
        parts.append('三数共现避开 ' + '/'.join(
            '%s%s' % (entry.get('value'), '命中' if entry.get('hit') else '未中')
            for entry in avoid_entries))
    if settlement.get('verified') is not True:
        parts.append('发布晚于开奖，未计入统计')
    else:
        parts.append('整组 ' + ('<b style="color:#15803d">命中</b>' if group else '<b style="color:#b91c1c">未中</b>'))
    return '；'.join(parts) + '。'


def _form_derivation(seal):
    """Show how the two reverse forms were derived (killed digits 反向 + 弃位冷热)."""
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
    dropped_value = seal.get('dropped')
    if isinstance(dropped_value, list):
        dropped_keys = dropped_value
    elif dropped_value:
        dropped_keys = [dropped_value]
    else:
        dropped_keys = []
    cold = seal.get('dropped_form_cold') if isinstance(seal.get('dropped_form_cold'), dict) else None
    if cold and isinstance(dropped_keys, list) and all(isinstance(cold.get(key), dict) for key in dropped_keys) and dropped_keys:
        notes = []
        for key in dropped_keys:
            label = kill3_tail.POSITION_LABELS.get(key, key)
            form = cold.get(key) or {}
            notes.append(f'弃位{label}按该位置冷热取{form.get("parity", "-")}/{form.get("size", "-")}')
        dropped_note = '；' + '、'.join(notes) + '（v17 起，v16 旧封存同机制）'
    elif cold and cold.get('parity') and cold.get('size'):
        label = kill3_tail.POSITION_LABELS.get(dropped_keys[0], dropped_keys[0]) if dropped_keys else '-'
        dropped_note = f'；弃位{label}按该位置冷热取{cold["parity"]}/{cold["size"]}（v16 起）'
    else:
        dropped_note = ''
    count = len(positions)
    return ('形态取反（按被杀的 %d 个位置）：' % count + ' · '.join(parts) + dropped_note)

def _version_html():
    """口径追溯行：当前 revision、自哪期起、本版期数、按每期 N 的动态期望基线（只读）。"""
    try:
        info = kill3_tail.version_info()
    except Exception:
        return ''
    try:
        rule = str(info.get('rule_version') or '')
        first = info.get('first_ref')
        count = int(info.get('count') or 0)
        baseline = _latest_seal_baseline(rule) or {}
        base = None
        try:
            from hitrate_baseline import RULE_BASELINE
            base = RULE_BASELINE.get(('kill3_tail', rule))
        except Exception:
            base = None
        parts = ['口径 ' + escape(rule)]
        if first:
            parts.append('自 ref ' + escape(str(first)) + ' 起（本版 ' + str(count) + ' 期）')
        if baseline.get('rate') is not None and baseline.get('items'):
            parts.append('最新一期整组期望 %.2f%%（%d 项，N=%s）'
                         % (float(baseline['rate']) * 100.0, int(baseline['items']),
                            baseline.get('count')))
            parts.append('按 N 精确基线：N=2 %.2f%% / N=3 %.2f%% / N=4 %.2f%%'
                         % (0.5863 * 100, 0.5168 * 100, 0.4557 * 100))
        elif base:
            parts.append('整组精确基线 %.2f%%（%d 项）' % (base[1] * 100.0, base[0]))
        parts.append('口径已冻结：变更走影子验证（ops/kill3-stability-plan.md）')
        return '<div class="k3-drop">' + ' · '.join(parts) + '</div>'
    except Exception:
        return ''

def _cumulative_html():
    """只读：本版累计命中率 + 回撤参照 + 项级命中（数据源 data/hitrate_stability.json）。

    与 _stats_html 的今日口径并列展示、不替代；只统计开奖前已发布、开奖后核验的记录，
    整组 = N+6 项同时命中（N 每期自选 2~4）。载荷缺失/版本不符/异常时返回空串，页面保持原样。
    """
    try:
        path = Path('/root/penta5-runner/data/hitrate_stability.json')
        report = json.loads(path.read_text(encoding='utf-8'))
        rows = report.get('rows') if isinstance(report, dict) else None
        if not isinstance(rows, list):
            return ''
        rule = str(getattr(kill3_tail, 'RULE_VERSION', '') or '')
        row = next((item for item in rows if isinstance(item, dict)
                    and item.get('key') == 'kill3_tail'), None)
        if not row or str(row.get('rule') or '') != rule:
            return ''
        n = row.get('n')
        hits = row.get('hits')
        rate = row.get('rate')
        expected = row.get('expected')
        if not isinstance(n, int) or n < 1 or not isinstance(hits, int) or rate is None:
            return ''
        ci = row.get('ci95')
        ci_text = ('，95%%CI %.1f%%–%.1f%%' % (ci[0] * 100.0, ci[1] * 100.0)
                   if isinstance(ci, (list, tuple)) and len(ci) >= 2 else '')
        parts = ['<b>本版累计（跨天不清零）</b>',
                 '整组 <b>%.2f%%</b>（%d/%d 期%s）' % (float(rate) * 100.0, hits, n, ci_text)]
        if expected is not None:
            parts.append('精确基线 %.2f%%' % (float(expected) * 100.0))
        delta = row.get('delta')
        p = row.get('p')
        if delta is not None:
            parts.append('偏差 %+.2fpp' % (float(delta) * 100.0))
        if p is not None:
            parts.append('二项 p=%.3f' % float(p))
        drawdown = row.get('drawdown') if isinstance(row.get('drawdown'), dict) else {}
        random_ref = drawdown.get('random') if isinstance(drawdown.get('random'), dict) else {}
        if drawdown.get('n') == n and drawdown.get('current_dd_pp') is not None:
            text = ('自峰值回撤 %.1fpp（峰值 %.1f%% 在 n=%s）'
                    % (float(drawdown['current_dd_pp']),
                       float(drawdown.get('peak_rate') or 0) * 100.0,
                       drawdown.get('peak_n')))
            if random_ref.get('median_pp') is not None:
                text += ('；同 n 纯随机序列最大回撤中位 %.1fpp / p90 %.1fpp，P≥该回撤 %.0f%%'
                         % (float(random_ref['median_pp']), float(random_ref['p90_pp']),
                            float(random_ref.get('p_ge_observed') or 0) * 100.0))
            parts.append(text)
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
                         '（基线 90.0%%，%d 期，均 %.2f 项/期）'
                         % (int(core['item_hits']), int(core['item_total']),
                            float(core['item_rate']) * 100.0,
                            int(core.get('periods') or 0),
                            float(core.get('avg_items') or 0.0)))
            if segments:
                segment_text = ' · '.join(
                    '%s %d期 %.1f%%'
                    % (str(segment.get('rule') or '').replace('kill3-tail-', ''),
                       int(segment.get('n') or 0),
                       float(segment.get('core_rate') or 0.0) * 100.0)
                    for segment in segments[-5:])
                parts.append('分段（近 5 版）：%s' % segment_text)
        if n < 200:
            parts.append('样本不足（n&lt;200），仅作参考')
        parts.append('数据每 15 分钟刷新，最多滞后约 4 期（约 20 分钟；今日行按业务日实时）')
        return ('<div class="k3-acc" style="margin-top:6px;padding-top:6px;'
                'border-top:1px dashed #c7d2fe;font-size:13px;color:#475569;line-height:1.7">'
                + ' · '.join(parts) + '</div>')
    except Exception:
        return ''


DATA_DIR = Path('/root/penta5-runner/data')
GATE_PATH = DATA_DIR / 'kill3_v16_guard.json'
V16_RULE = 'kill3-tail-v16-hot-cold-repeat-neighbor-hold-shadow-two-counted-three-avoid-cold-drop'
GATE_BASELINE = 0.5863
GATE_N = 150
GATE_Z = -2.5
GATE_RATE_FLOOR = 0.50


def _num(value):
    return ('%.2f' % float(value)) if isinstance(value, (int, float)) else '-'


V17_GATE_PATH = DATA_DIR / 'kill3_v17_guard.json'
V17_RULE = 'kill3-tail-v17-flexible-two-four-position-one-tail-two-form-three-avoid'
V17_GATE_BASELINE = {2: 0.5863, 3: 0.5168, 4: 0.4557}
V16_RULE = 'kill3-tail-v16-hot-cold-repeat-neighbor-hold-shadow-two-counted-three-avoid-cold-drop'


def _gate_html():
    """只读：kill3 v17 预登记门禁进度（data/kill3_v17_guard.json）+ v16 历史门禁停用行。

    门禁计算在 lib/kill3_v17_guard.py，由 scripts/ops_health_check.py 周期性写入该 JSON；
    本函数只展示。触发条件见 ops/kill3-stability-plan.md §9（v17 段）。
    """
    try:
        rule = str(getattr(kill3_tail, 'RULE_VERSION', '') or '')
        parts = []
        report = None
        try:
            report = json.loads(V17_GATE_PATH.read_text(encoding='utf-8'))
        except Exception:
            report = None
        if isinstance(report, dict) and str(report.get('rule_version') or '') == rule:
            n = report.get('n') or 0
            hits = report.get('hits') or 0
            rate = report.get('rate')
            expected = report.get('expected')
            verdict = report.get('verdict') or report.get('status') or '-'
            thresholds = report.get('thresholds') or {}
            parts.append('<b>v17 门禁</b> 样本 %s 期 · 命中 %s · 本版 %s vs 期望 %s · z=%s · 判定「%s」'
                         '（预登记 n≥%s、z&lt;%s 或 命中率&lt;%s）'
                         % (n, hits, _pct(rate), _pct(expected), _num(report.get('z')),
                            escape(str(verdict)),
                            thresholds.get('n_trigger', '-'),
                            thresholds.get('z_trigger', '-'),
                            _pct(thresholds.get('rate_floor'))))
        elif isinstance(report, dict):
            parts.append('v17 门禁报告 rule 不匹配（报告 %s），等待本轮写入'
                         % escape(str(report.get('rule_version') or '-')))
        else:
            parts.append('v17 门禁报告未生成（data/kill3_v17_guard.json 缺失）')
        try:
            old = json.loads(GATE_PATH.read_text(encoding='utf-8'))
            if isinstance(old, dict):
                parts.append('v16 门禁停用（现行口径 %s；历史 v16 样本 %s 期，判定「%s」）'
                             % (escape(rule), old.get('n'), escape(str(old.get('verdict') or '-'))))
        except Exception:
            pass
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
                    f'<h3>🤖 AI 自选 2~4 位置杀码 + 一合尾建议</h3>{body}{_stats_html(stats)}{_cumulative_html()}{_gate_html()}{_version_html()}'
                    f'{_foot_html()}</section>{END}')
        positions = seal.get('positions') or {}
        position_count = kill3_tail.seal_position_count(seal) or len(positions)
        display_only = seal.get('display_only')
        if display_only:
            counted_note = '其中 2 个计入整组、1 个仅展示（v16 旧封存）'
        else:
            counted_note = f'全部 {position_count} 个计入整组'
        cards = []
        for key, label, _index in kill3_tail.POSITIONS:
            if key not in positions:
                continue
            sub = ('排除 1 码（仅展示，不计入整组）' if key == display_only else '排除 1 码（计入整组）')
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
        for group in (seal.get('three_avoid') or []):
            if not isinstance(group, dict):
                continue
            code = str(group.get('value') or '')
            if not code:
                continue
            cards.append(
                f'<div class="k3-card k3-pattern">'
                f'<div class="k3-pos">三数共现 {escape(str(group.get("label") or ""))}</div>'
                f'<div class="k3-form">{escape(code)}</div>'
                f'<div class="k3-sub">排除该组三数共现</div></div>')
        dropped_value = seal.get('dropped')
        if isinstance(dropped_value, list):
            dropped_labels = [kill3_tail.POSITION_LABELS.get(key, key) for key in dropped_value]
            dropped_text = '、'.join(dropped_labels) if dropped_labels else '无（4 个位置全部选中）'
            drop_line = (f'放弃位置：{escape(dropped_text)}'
                         f'（AI 本期选 {position_count} 个位置，{counted_note}）')
        else:
            dropped = kill3_tail.POSITION_LABELS.get(dropped_value, dropped_value or '-')
            drop_line = f'放弃位置：{escape(str(dropped))}（AI 选 {position_count} 个位置，{counted_note}）'
        reason = escape(str(seal.get('reason') or '')) or '（无）'
        seal_tail_policy = seal.get('tail_policy') or {}
        if (seal_tail_policy.get('reused_previous') is True
                and '尾码按稳定化沿用上期' not in str(seal.get('reason') or '')):
            reason += ('<span style="color:#92400e;">（尾码已按稳定化沿用上期，'
                       '模型理由中的合尾为换码候选）</span>')
        derivation = _form_derivation(seal)
        derive_html = (f'<div class="k3-drop">{escape(derivation)}</div>' if derivation else '')
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
                f'<h3>🤖 AI 自选 2~4 位置杀码 + 一合尾建议</h3>'
                f'<div class="k3-meta">{meta}</div>'
                f'<div class="k3-grid">{"".join(cards)}</div>'
                f'<div class="k3-drop">{drop_line}</div>'
                f'{restrict_html}{position_html}{derive_html}'
                f'<div class="k3-reason">理由：{reason}</div>'
                f'{_stats_html(stats, seal)}{_cumulative_html()}{_gate_html()}{_version_html()}'
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
            '<div class="k3-attack-title">攻击语（复制到平台，与整组同格式）</div>'
            '<textarea id="kill3-attack-text" readonly rows="3" aria-label="AI 自选位置+一合尾攻击语" '
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
            '由 DeepSeek 决定：从千/百/十/个中自选 2~4 个不同位置、每个位置排除 1 码，所选项全部计入整组与攻击语；'
            '未选位置为放弃位，其形态字符由系统按该位置近 50 期冷热确定（v17 起）。'
            '合尾从 5 种中选 1 种排除 1 个和尾数字（合尾由模型自主决定，按 hold3-until-miss 稳定化沿用），并给出两项四位反向形态。<br>'
            '位置杀码、放弃位置与合尾均无轮换限制，每期由模型自主决定。<br>'
            '命中 = 该位置实际开奖号<b>不在</b>杀码上（统计本期所选 N 个位置）；'
            '合尾命中 = 该合尾实际和值个位<b>不在</b>排除码上；'
            '反向形态命中 = 实际四位单双/大小形态<b>不等于</b>封存形态'
            '（被杀位置都取杀码形态反面；弃位字符按该位置冷热取）。<br>'
            '三数共现避开 1/2/3 组由系统按目标期给定、各算 1 项（单项基线 97.96%：'
            '该组 3 个数字<b>未全部</b>出现在 4 球开奖号中即命中）；<br>'
            '整组命中 = N 个所选位置 + 1 个合尾 + 2 项反向形态 + 三数共现 3 组，共 N+6 项同时命中（N 每期由 AI 自选 2~4，'
            '期望基线按每期实际 N 分别取 N=2 58.63% / N=3 51.68% / N=4 45.57%）；'
            '旧口径（v1–v16）的封存记录保留但不计入本统计；'
            '只统计开奖前发布、开奖后核验的记录；累计四手连败自本版本上线起跨天叠加：连续 4 期整组未全中记 1 次、8 期记 2 次，赢一次清零，不随 05:00 清零；北京时间 05:00 清零（用于当日命中率等当日统计）；只使用前 4 球（千/百/十/个），第 5 球不参与；'
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

