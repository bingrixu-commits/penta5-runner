"""Static presentation of prospective 50-draw statistical positioning controls."""
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path
import json
import re

ROOT = Path('/root/penta5-runner')
BJ = timezone(timedelta(hours=8))
START = '<!-- STAT50_COMPARE_STATS_START -->'
END = '<!-- STAT50_COMPARE_STATS_END -->'
CARDS = (('<!-- R1_FRAMEWORK_START -->', '<!-- R1_FRAMEWORK_END -->'),
         ('<!-- R1_KILLER_START -->', '<!-- R1_KILLER_END -->'),
         ('<!-- R1_PRO_COMPARE_START -->', '<!-- R1_PRO_COMPARE_END -->'),
         ('<!-- STAT50_PREDICTION_START -->', '<!-- STAT50_PREDICTION_END -->'))
OTHER_BLOCKS = (('<!-- MODEL_COMPARE_STATS_START -->', '<!-- MODEL_COMPARE_STATS_END -->'),
                ('<!-- R1_FRAMEWORK_STATS_START -->', '<!-- R1_FRAMEWORK_STATS_END -->'),
                ('<!-- R1_KILLER_STATS_START -->', '<!-- R1_KILLER_STATS_END -->'))
GROUPS = {
    'framework': {
        'title': '正式杀码框架 · 千位 / 百位定位对照',
        'models': ('frequency', 'markov', 'framework'),
        'metrics': (('q_kill1', '千位定位排除'), ('b_kill1', '百位定位排除')),
    },
    'killer': {
        'title': '正式深度排除 · 四位定位对照',
        'models': ('frequency', 'markov', 'killer'),
        'metrics': (('k1', '第1位（千位）'), ('k2', '第2位（百位）'),
                    ('k3', '第3位（十位）'), ('k4', '第4位（个位）')),
    },
}
MODEL_LABELS = {'frequency': '频率候选', 'markov': '转移候选',
                'framework': '正式框架', 'killer': '正式深度 AI'}


def _count(value):
    return value if type(value) is int and 0 <= value <= 9007199254740991 else None


def _day(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        return '--'
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return '--'


def _period(value):
    return value if isinstance(value, str) and re.fullmatch(r'[0-9]{11}', value) else '--'


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
        return parsed.astimezone(BJ) if parsed is not None and parsed.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _group(raw, definition, top_valid):
    source = raw if isinstance(raw, dict) else {}
    expected_models = list(definition['models'])
    valid = top_valid and source.get('models') == expected_models
    count = _count(source.get('matched_count'))
    raw_targets = source.get('matched_targets')
    targets = (raw_targets if isinstance(raw_targets, list)
               and all(_period(item) != '--' for item in raw_targets) else [])
    valid = (valid and count is not None and isinstance(raw_targets, list)
             and len(targets) == count and targets == sorted(set(targets)))
    latest = _period(source.get('latest_settled_period'))
    valid = valid and (latest == targets[-1] if targets else source.get('latest_settled_period') is None)
    counters = {key: _count(source.get(key))
                for key in ('pending_count', 'unpaired_count', 'rejected_count')}
    valid = valid and all(value is not None for value in counters.values())
    expected_keys = {key for key, _ in definition['metrics']}
    raw_metrics = source.get('metrics') if isinstance(source.get('metrics'), dict) else {}
    valid = valid and set(raw_metrics) == expected_keys
    metrics = {}
    for key, _ in definition['metrics']:
        metric = raw_metrics.get(key) if isinstance(raw_metrics.get(key), dict) else {}
        total = _count(metric.get('total'))
        raw_hits = metric.get('hits') if isinstance(metric.get('hits'), dict) else {}
        hits = {model: _count(raw_hits.get(model)) for model in expected_models}
        correct = (total is not None and total == count and set(raw_hits) == set(expected_models)
                   and all(value is not None and value <= total for value in hits.values()))
        valid = valid and correct
        metrics[key] = {'total': total if correct else 0,
                        'hits': hits if correct else {model: 0 for model in expected_models}}
    valid = valid and source.get('stats_error') is not True
    return {'valid': bool(valid), 'matched_count': count if count is not None else 0,
            'matched_targets': frozenset(targets), 'latest_settled_period': latest,
            'metrics': metrics,
            **{key: value if value is not None else 0 for key, value in counters.items()}}


def _summary(summary):
    source = summary if isinstance(summary, dict) else {}
    day = _day(source.get('biz_date'))
    beginning = _stamp(source.get('business_start'))
    updated = _stamp(source.get('updated_at'))
    groups = source.get('groups') if isinstance(source.get('groups'), dict) else {}
    valid = (type(source.get('version')) is int and source.get('version') == 1
             and source.get('experiment_id') == 'stat50-v1' and day != '--'
             and beginning is not None and beginning.strftime('%Y-%m-%d') == day
             and beginning.time().isoformat() == '05:00:00' and updated is not None
             and set(groups) == set(GROUPS) and source.get('stats_error') is not True)
    return {'biz_date': day, 'updated_at': updated.strftime('%Y-%m-%d %H:%M:%S') if updated else '--',
            'groups': {key: _group(groups.get(key), definition, valid)
                       for key, definition in GROUPS.items()}}


def _status(status, group):
    raw = status if isinstance(status, dict) else {}
    state = raw.get('status')
    labels = {'waiting': '等待下一期实验封存', 'published': '本期实验已封存，等待开奖核验',
              'preserved': '本期既有实验封存已保留，等待开奖核验',
              'rejected': '本期实验未通过校验，未计入', 'failed': '本期实验失败，未计入'}
    target = _period(raw.get('target_period'))
    label = labels.get(state, '实验状态不可用') if isinstance(state, str) else '等待首期实验封存'
    if raw and (type(raw.get('version')) is not int or raw.get('version') != 1):
        label = '实验状态不可用'
    elif state in ('published', 'preserved') and target == '--':
        label = '实验状态不可用'
    elif state in ('published', 'preserved'):
        if group['valid'] and target in group['matched_targets']:
            label = '本期三方已共同核验'
        elif raw.get('_view_deadline_reached') is True or (
                group['valid'] and group['latest_settled_period'] != '--'
                and target < group['latest_settled_period']):
            label = '本期已到开奖截止，等待三方配对核验'
    stamp = _stamp(raw.get('updated_at'))
    return label, target, stamp.strftime('%Y-%m-%d %H:%M:%S') if stamp else '--'


def render_stat50_block(summary, status=None):
    """Render fixed labels only; rates are always recomputed from verified counts."""
    data = _summary(summary)
    sections = []
    for name, definition in GROUPS.items():
        group = data['groups'][name]
        label, target, _ = _status(status, group)
        sample = ('共同已验证样本：%d期 · 三方同分母' % group['matched_count']
                  if group['valid'] else '统计核验失败，对照结果暂不可用')
        rows = []
        for key, title in definition['metrics']:
            metric = group['metrics'][key]
            cells = []
            for model in definition['models']:
                total, hits = metric['total'], metric['hits'][model]
                rate = ('统计不可用' if not group['valid'] else
                        ('%.2f%%（%d/%d期）' % (100 * hits / total, hits, total)
                         if total else '暂无共同已验证数据'))
                cells.append('<div data-stat50-model="' + model + '" style="min-width:0;overflow-wrap:anywhere;">'
                             '<span style="color:#64748b;">' + MODEL_LABELS[model] + '</span><br>'
                             '<b style="color:#1d4ed8;">' + rate + '</b></div>')
            rows.append('<div data-stat50-metric="' + key + '" style="border-top:1px solid #dbeafe;'
                        'padding:9px 0;min-width:0;"><b>' + title + ' · 排除成功率</b>'
                        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));'
                        'gap:5px 16px;margin-top:4px;">' + ''.join(cells) + '</div></div>')
        counters = ('待核验：%d期 ｜ 未配对：%d期 ｜ 拒绝计入：%d期' %
                    (group['pending_count'], group['unpaired_count'], group['rejected_count'])
                    if group['valid'] else '样本计数暂不可用')
        sections.append('<section data-stat50-group="' + name + '" style="margin-top:12px;'
                        'padding:12px;background:white;border:1px solid #dbeafe;border-radius:7px;min-width:0;">'
                        '<h4 style="font-size:15px;color:#1e3a8a;margin:0 0 3px;">' + definition['title'] + '</h4>'
                        '<div style="color:#475569;margin-bottom:6px;">' + sample + '</div>' + ''.join(rows)
                        + '<div style="border-top:1px solid #dbeafe;padding-top:7px;color:#64748b;">' + counters
                        + '<br>最近共同核验期：' + (escape(group['latest_settled_period']) if group['valid'] else '--')
                        + '<br>' + escape(label) + ' ｜ 目标期：' + escape(target) + '</div></section>')
    return (START + '\n<section id="stat50_compare_stats" data-stats-business-date="'
            + escape(data['biz_date'], quote=True) + '" style="width:calc(100% - 24px);max-width:1000px;'
            'box-sizing:border-box;margin:12px auto 18px;padding:14px;border:1px solid #93c5fd;'
            'border-radius:9px;background:#f5f9ff;color:#334155;font:13px/1.7 sans-serif;'
            'overflow-wrap:anywhere;min-width:0;">'
            '<h3 style="font-size:17px;color:#1e40af;margin:0 0 5px;">近50期统计候选 · 定位排除实验对照</h3>'
            '<div>每次仅用近50期真实开奖前4球；' + escape(data['biz_date']) + ' 05:00起（北京时间）。'
            '<br>只计三方同目标、同50期开奖前封存结果；只比定位排除，不含合尾、形态或整组8项通过。</div>'
            + ''.join(sections) + '<div style="margin-top:10px;color:#64748b;">统计更新时间：'
            + escape(data['updated_at']) + '（北京时间）<br><b>候选尚未证明优于正式模型；正式AI继续运行。</b>'
            '</div></section>\n' + END)


def _sealed_cards(html):
    cards = []
    for index, (start, end) in enumerate(CARDS):
        starts, ends = html.count(start), html.count(end)
        if index >= 2 and starts == 0 and ends == 0:
            continue
        if starts != 1 or ends != 1:
            raise ValueError('Missing or ambiguous sealed card markers')
        left, right = html.index(start), html.index(end) + len(end)
        if right <= left:
            raise ValueError('Reversed sealed card markers')
        cards.append((left, right, html[left:right]))
    ordered = sorted(cards)
    if any(first[1] > second[0] for first, second in zip(ordered, ordered[1:])):
        raise ValueError('Overlapping sealed cards')
    return cards


def _protected_blocks(html):
    blocks = _sealed_cards(html)
    for start, end in OTHER_BLOCKS:
        starts, ends = html.count(start), html.count(end)
        if starts == 0 and ends == 0:
            continue
        if starts != 1 or ends != 1:
            raise ValueError('Ambiguous existing statistics markers')
        left, right = html.index(start), html.index(end) + len(end)
        if right <= left:
            raise ValueError('Reversed existing statistics markers')
        blocks.append((left, right, html[left:right]))
    ordered = sorted(blocks)
    if any(first[1] > second[0] for first, second in zip(ordered, ordered[1:])):
        raise ValueError('Overlapping protected panel fragments')
    return blocks


def apply_stat50_block(html, summary, status=None):
    """Replace only the owned derived block; immutable publication bytes survive."""
    if not isinstance(html, str):
        raise TypeError('Panel must be text')
    protected = _protected_blocks(html)
    starts, ends = html.count(START), html.count(END)
    if starts != ends or starts > 1:
        raise ValueError('Ambiguous statistics comparison markers')
    content = html
    if starts:
        left, right = html.index(START), html.index(END) + len(END)
        if right <= left or any(left < stop and right > begin for begin, stop, _ in protected):
            raise ValueError('Statistics block overlaps a sealed card')
        left = left - 1 if left > 0 and html[left - 1] == '\n' else left
        if left > 0 and html[left - 1] == '\r':
            left -= 1
        content = html[:left] + html[right:]
    content_cards = _sealed_cards(content)
    position = content.index(CARDS[0][1]) + len(CARDS[0][1])
    for begin, end in (('<!-- MODEL_COMPARE_STATS_START -->', '<!-- MODEL_COMPARE_STATS_END -->'),
                       ('<!-- R1_FRAMEWORK_STATS_START -->', '<!-- R1_FRAMEWORK_STATS_END -->')):
        start_count, end_count = content.count(begin), content.count(end)
        if start_count == 0 and end_count == 0:
            continue
        if start_count != 1 or end_count != 1:
            raise ValueError('Ambiguous statistics insertion anchor')
        anchor_left, anchor_right = content.index(begin), content.index(end) + len(end)
        if anchor_right <= anchor_left or any(anchor_left < stop and anchor_right > left
                                               for left, stop, _ in content_cards):
            raise ValueError('Statistics anchor overlaps a sealed card')
        position = anchor_right
        break
    updated = content[:position] + '\n' + render_stat50_block(summary, status) + content[position:]
    if ([block for _, _, block in protected] != [block for _, _, block in _protected_blocks(updated)]
            or updated.count(START) != 1 or updated.count(END) != 1):
        raise ValueError('Statistics composition changed immutable cards')
    return updated


def _read_status(root):
    try:
        status = json.loads((root / 'data' / 'stat50_compare_status.json').read_text(encoding='utf-8'))
        return status if isinstance(status, dict) else {'version': 1, 'status': 'failed'}
    except FileNotFoundError:
        return None
    except (ValueError, TypeError, OSError):
        return {'version': 1, 'status': 'failed'}


def refresh_stat50_panel(snapshot=None, root=ROOT):
    """Refresh statistics and compose under the shared server panel writer lock."""
    from panel_io import update_panel
    from stat50_compare import refresh_stat50_stats
    root = Path(root)
    now = snapshot.get('now') if isinstance(snapshot, dict) else None
    now = now if isinstance(now, datetime) and now.tzinfo is not None else datetime.now(BJ)
    status = _read_status(root)
    try:
        summary = refresh_stat50_stats(snapshot=snapshot, root=root)
    except (ValueError, TypeError, KeyError, OSError):
        failure = {'biz_date': (now.astimezone(BJ) - timedelta(hours=5)).date().isoformat(),
                   'stats_error': True}
        update_panel(lambda html: apply_stat50_block(html, failure, status), path=root / 'panel.html')
        raise
    if isinstance(status, dict) and status.get('status') in ('published', 'preserved'):
        from stats_history import period_slot
        deadline = period_slot(status.get('target_period'))
        status = dict(status, _view_deadline_reached=deadline is not None and now >= deadline)
    update_panel(lambda html: apply_stat50_block(html, summary, status), path=root / 'panel.html')
    return summary
