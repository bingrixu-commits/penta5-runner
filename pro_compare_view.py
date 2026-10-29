"""Render an independent, paired Flash / Pro comparison without altering seals."""
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
import json
import re

ROOT = Path('/root/penta5-runner')
START = '<!-- MODEL_COMPARE_STATS_START -->'
END = '<!-- MODEL_COMPARE_STATS_END -->'
CARDS = (('<!-- R1_FRAMEWORK_START -->', '<!-- R1_FRAMEWORK_END -->'),
         ('<!-- R1_KILLER_START -->', '<!-- R1_KILLER_END -->'))
METRICS = (('q_kill1', '千位定位排除'), ('b_kill1', '百位定位排除'),
           ('all_positions', '千位、百位同时通过'))
STATUSES = {'waiting': '等待下一期对照', 'requesting': 'Pro 请求中',
            'published': 'Pro 本期已封存，等待开奖核验',
            'preserved': 'Pro 既有封存已保留',
            'rejected': 'Pro 本期结果未通过校验，未计入',
            'failed': 'Pro 本期请求失败，未计入'}
BJ = timezone(timedelta(hours=8))


def _count(value):
    return value if type(value) is int and 0 <= value <= 9007199254740991 else None


def _day(value):
    try:
        return date.fromisoformat(value).isoformat() if isinstance(value, str) else '--'
    except ValueError:
        return '--'


def _period(value):
    return value if isinstance(value, str) and re.fullmatch(r'[0-9]{11}', value) else '--'


def _summary(summary):
    raw = summary if isinstance(summary, dict) else {}
    metrics = {}
    valid = bool(raw) and type(raw.get('version')) is int and raw.get('version') == 1
    count = _count(raw.get('matched_count'))
    raw_metrics = raw.get('metrics') if isinstance(raw.get('metrics'), dict) else {}
    for key, _ in METRICS:
        metric = raw_metrics.get(key) if isinstance(raw_metrics.get(key), dict) else {}
        total = _count(metric.get('total'))
        flash, pro = _count(metric.get('flash_hits')), _count(metric.get('pro_hits'))
        correct = (total is not None and flash is not None and pro is not None
                   and count == total and flash <= total and pro <= total)
        valid = valid and correct
        metrics[key] = {'total': total if correct else 0,
                        'flash_hits': flash if correct else 0,
                        'pro_hits': pro if correct else 0}
    counters = {}
    for key in ('pending_count', 'unpaired_count', 'rejected_count'):
        counters[key] = _count(raw.get(key))
        valid = valid and counters[key] is not None
    day = _day(raw.get('biz_date'))
    valid = valid and day != '--'
    raw_targets = raw.get('matched_targets')
    targets = frozenset(value for value in raw_targets if _period(value) != '--') if isinstance(raw_targets, (list, tuple)) else frozenset()
    return {'biz_date': day, 'matched_count': count if count is not None else 0,
            'metrics': metrics, 'stats_error': raw.get('stats_error') is True or (bool(raw) and not valid),
            'latest_settled_period': _period(raw.get('latest_settled_period')),
            'matched_targets': targets,
            **{key: value if value is not None else 0 for key, value in counters.items()}}


def _status(status, data):
    raw = status if isinstance(status, dict) else {}
    state = raw.get('status')
    label = STATUSES.get(state, '尚未开始，等待真实对照样本') if isinstance(state, str) else '尚未开始，等待真实对照样本'
    target = _period(raw.get('target_period'))
    if state == 'failed' and raw.get('error_code') == 'provider-insufficient-balance':
        label = 'DeepSeek账户余额不足（HTTP 402），未发布；补充余额后等待下一轮'
    if state == 'rejected' and raw.get('error_code') == 'inference-already-attempted':
        label = '本期已尝试但未发布，不重复请求'
    if state in ('published', 'preserved') and target != '--' and not data['stats_error']:
        latest = data['latest_settled_period']
        if target in data['matched_targets'] or (data['matched_count'] > 0 and target == latest):
            label = 'Pro 该期已核验'
        elif raw.get('_view_deadline_reached') is True or (latest != '--' and target < latest):
            label = '结果已到截止，等待配对核验'
    updated = '--'
    try:
        stamp = datetime.fromisoformat(raw.get('updated_at'))
        if stamp.tzinfo is not None:
            updated = stamp.astimezone(BJ).strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError, OverflowError):
        pass
    return label, target, updated


def render_compare_block(summary, status=None):
    """Return escaped, static HTML; never render provider text or supplied rates."""
    data = _summary(summary)
    label, target, updated = _status(status, data)
    rows = []
    for key, title in METRICS:
        metric = data['metrics'][key]
        rates = []
        for model, column in (('Flash', 'flash_hits'), ('Pro', 'pro_hits')):
            total, hits = metric['total'], metric[column]
            rate = ('统计核验失败，暂停显示' if data['stats_error'] else
                    ('%.2f%%（%d/%d期）' % (hits / total * 100, hits, total)
                     if total else '暂无共同已验证数据'))
            rates.append('<div data-compare-model="' + model.lower() + '" '
                         'style="min-width:0;overflow-wrap:anywhere;"><b>' + model
                         + '：</b>' + rate + '</div>')
        rows.append('<div data-compare-key="' + key + '" style="padding:9px 0;'
                    'border-top:1px solid #dbeafe;min-width:0;">'
                    '<b>' + title + '</b><div style="display:grid;'
                    'grid-template-columns:repeat(auto-fit,minmax(min(100%,220px),1fr));'
                    'gap:5px 16px;margin-top:4px;">' + ''.join(rates) + '</div></div>')
    title = ('统计核验失败，暂停显示对照结果' if data['stats_error'] else
             '共同已验证样本：' + str(data['matched_count']) + '期')
    return (START + '\n<section id="model_compare_stats" data-stats-business-date="'
            + escape(data['biz_date'], quote=True) + '" style="width:calc(100% - 24px);'
            'max-width:1000px;margin:12px auto 18px;padding:14px;box-sizing:border-box;'
            'border:1px solid #a5b4fc;border-radius:8px;background:#f8faff;color:#334155;'
            'font:13px/1.8 sans-serif;overflow-wrap:anywhere;min-width:0;">'
            '<h3 style="margin:0 0 4px;font-size:16px;color:#3730a3;">Flash / Pro 相同初始输入定位对照</h3>'
            '<div>共享近50期开奖作为相同初始输入，只使用前4球；' + escape(data['biz_date'])
            + ' 05:00起（北京时间）。<br><b>' + title + '</b></div>'
            + ''.join(rows) + '<div style="padding-top:7px;border-top:1px solid #dbeafe;">'
            '待核验：' + str(data['pending_count']) + '期 ｜ 未配对：'
            + str(data['unpaired_count']) + '期 ｜ 拒绝计入：' + str(data['rejected_count'])
            + '期<br>最近共同核验期：' + escape(data['latest_settled_period'])
            + '<br>Pro 状态：' + escape(label) + ' ｜ 目标期：' + escape(target)
            + '<br>状态时间：' + escape(updated) + '（北京时间）'
            '<br>仅计入两模型使用相同初始输入、开奖前封存且开奖后核验的共同目标期；'
            '未配对、冲突或校验失败不计入成功率。后续纠正重试消息可能不同。'
            '<br><b>Pro 只作对照，正式 Flash 继续运行；待真实结果积累后再判断是否替换。</b>'
            '</div></section>\n' + END)


def _sealed_cards(html):
    cards = []
    for start, end in CARDS:
        if html.count(start) != 1 or html.count(end) != 1:
            raise ValueError('Missing or ambiguous sealed AI card')
        left, right = html.index(start), html.index(end) + len(end)
        if right <= left:
            raise ValueError('Reversed sealed AI card markers')
        cards.append((left, right, html[left:right]))
    first, second = sorted(cards)
    if first[1] > second[0]:
        raise ValueError('Overlapping sealed AI cards')
    return cards


def apply_compare_block(html, summary, status=None):
    """Update one independent block under the caller's shared panel lock."""
    if not isinstance(html, str):
        raise TypeError('Panel HTML must be text')
    cards = _sealed_cards(html)
    starts, ends = html.count(START), html.count(END)
    if starts != ends or starts > 1:
        raise ValueError('Ambiguous comparison markers')
    content = html
    if starts:
        left, right = html.index(START), html.index(END) + len(END)
        if right <= left or any(left < stop and right > begin for begin, stop, _ in cards):
            raise ValueError('Comparison block overlaps a sealed card')
        # Remove only the exact owned block and at most its preceding newline.
        left = left - 1 if left > 0 and html[left - 1] == '\n' else left
        if left > 0 and html[left - 1] == '\r':
            left -= 1
        content = html[:left] + html[right:]
    anchor = CARDS[0][1]
    position = content.index(anchor) + len(anchor)
    stats_end = '<!-- R1_FRAMEWORK_STATS_END -->'
    if content.count(stats_end) == 1:
        candidate = content.index(stats_end) + len(stats_end)
        if candidate > position and not any(left < candidate < right
                                            for left, right, _ in _sealed_cards(content)):
            position = candidate
    updated = content[:position] + '\n' + render_compare_block(summary, status) + content[position:]
    before = [card for _, _, card in cards]
    after = [card for _, _, card in _sealed_cards(updated)]
    if before != after or updated.count(START) != 1 or updated.count(END) != 1:
        raise ValueError('Comparison presentation changed sealed cards')
    return updated


def _read_status(root):
    try:
        status = json.loads((root / 'data' / 'pro_compare_status.json').read_text(encoding='utf-8'))
        return status if isinstance(status, dict) else {'status': 'failed'}
    except FileNotFoundError:
        return None
    except (ValueError, TypeError, OSError):
        return {'status': 'failed'}


def refresh_comparison_panel(snapshot=None, root=ROOT):
    """Refresh derived paired statistics, then compose the panel under its lock."""
    from panel_io import update_panel
    from pro_model_compare import refresh_comparison_stats
    root = Path(root)
    now = snapshot.get('now') if isinstance(snapshot, dict) else None
    now = now if isinstance(now, datetime) and now.tzinfo is not None else datetime.now(BJ)
    status = _read_status(root)
    try:
        summary = refresh_comparison_stats(snapshot=snapshot, root=root)
    except (ValueError, TypeError, KeyError, OSError):
        day = (now.astimezone(BJ) - timedelta(hours=5)).date().isoformat()
        failure = {'biz_date': day, 'stats_error': True}
        update_panel(lambda html: apply_compare_block(html, failure, status), path=root / 'panel.html')
        raise
    if isinstance(status, dict) and status.get('status') in ('published', 'preserved'):
        from stats_history import period_slot
        deadline = period_slot(status.get('target_period'))
        status = dict(status, _view_deadline_reached=deadline is not None and now >= deadline)
    update_panel(lambda html: apply_compare_block(html, summary, status), path=root / 'panel.html')
    return summary
