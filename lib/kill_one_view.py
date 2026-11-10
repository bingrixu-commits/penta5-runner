"""kill1 v8 面板容器：AI 双定位杀码 + 共现避开三组 + 二数合/三数合建议（整组 7 项）。

整组固定 7 项：
  1) AI 每期从千/百/十/个自选恰好 2 个位置，各杀 1 码；
  2) 系统给定三数共现，避开 1/2/3 组（每组 3 个数字）；
  3) kill10 同目标期二数合 he2 保留 4 码；
  4) 三数合 he3 保留 5 码。
整组随机基线 69.9700%（登记 0.6997，精确 0.6997078452825）。

v7 公开 API 保持不变：START / END / STYLE / render_block / apply_kill_one_block。
模块顶层 import kill1_tail；所有 HTML 文本做 escape；异常不得冒泡。
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

import kill1_tail

START = '<!-- KILL_ONE_START -->'
END = '<!-- KILL_ONE_END -->'

FALLBACK_BASELINE = (7, 0.6997)
STABILITY_PATH = Path('/root/penta5-runner/data/hitrate_stability.json')

TITLE = '🤖 AI 双定位杀码 + 共现避开三组 + 二数合/三数合建议（整组 7 项）'

POSITION_ORDER = ('q', 'b', 's', 'g')
DEFAULT_LABELS = {'q': '千位', 'b': '百位', 's': '十位', 'g': '个位'}
STATE_TEXT = {'published': '已发布', 'pending': '待发布', 'draft': '草稿', 'closed': '已关盘'}
CUMULATIVE_EMPTY = '累计命中率：同版本尚无累计样本（自动跟随 data/hitrate_stability.json 刷新）。'

STYLE = (
    '#kill-one-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;'
    'margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;'
    'background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}'
    '#kill-one-ai *{box-sizing:border-box}'
    '#kill-one-ai .k1-title{margin:0 0 8px;font-size:17px;font-weight:700;color:#3730a3}'
    '#kill-one-ai .k1-meta{margin:0 0 10px;padding:7px 10px;border-radius:8px;background:#e0e7ff;color:#3730a3;font-size:13px}'
    '#kill-one-ai .k1-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin:10px 0}'
    '#kill-one-ai .k1-card{padding:10px 12px;border:1px solid #c7d2fe;border-radius:10px;background:#fff;box-shadow:0 1px 2px rgba(55,48,163,.06)}'
    '#kill-one-ai .k1-card-head{font-size:14px;color:#3730a3;font-weight:700}'
    '#kill-one-ai .k1-kill{margin:4px 0 2px;font-size:24px;font-weight:800;color:#b91c1c;letter-spacing:2px}'
    '#kill-one-ai .k1-keep-big{color:#065f46}'
    '#kill-one-ai .k1-basis{margin:2px 0;color:#4338ca;font-size:12.5px;font-weight:600}'
    '#kill-one-ai .k1-keep{color:#065f46;font-size:13px;font-family:ui-monospace,Consolas,monospace}'
    '#kill-one-ai .k1-note{margin:4px 0;color:#64748b;font-size:12.5px}'
    '#kill-one-ai .k1-reason{margin:8px 0;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;color:#475569;font-size:13px}'
    '#kill-one-ai .k1-verify{margin:8px 0;padding:7px 10px;border-radius:8px;background:#fff;border:1px solid #e2e8f0;color:#334155;font-size:13px}'
    '#kill-one-ai .k1-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin:10px 0}'
    '#kill-one-ai .k1-stat{padding:7px 10px;border:1px solid #c7d2fe;border-radius:9px;background:#fff}'
    '#kill-one-ai .k1-stat .l{display:block;color:#64748b;font-size:12px}'
    '#kill-one-ai .k1-stat .v{display:block;color:#3730a3;font-size:16px;font-weight:700}'
    '#kill-one-ai .k1-attack{margin:10px 0;padding:9px 10px;border:1px solid #a5b4fc;border-radius:9px;background:#fff}'
    '#kill-one-ai .k1-attack-title{font-size:13px;color:#3730a3;font-weight:700}'
    '#kill-one-ai .k1-sub{color:#64748b;font-size:12px}'
    '#kill-one-ai .k1-foot{margin-top:9px;color:#64748b;font-size:12px;line-height:1.65}'
    '#kill-one-ai .k1-empty{margin:10px 0;padding:16px;border:1px dashed #a5b4fc;border-radius:10px;background:#fff;color:#64748b}'
    '#kill-one-ai .k1-empty b{color:#3730a3}'
)

__all__ = ['START', 'END', 'STYLE', 'FALLBACK_BASELINE', 'STABILITY_PATH', 'render_block', 'apply_kill_one_block']


def _escape(value):
    try:
        if value is None:
            return '-'
        return html.escape(str(value), quote=True)
    except Exception:
        return ''


def _text(value, default='-'):
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def _pct(value, digits=2):
    try:
        if value is None or isinstance(value, bool):
            return '-'
        number = float(value)
        if number != number:
            return '-'
        return ('%.' + str(digits) + 'f%%') % (number * 100.0)
    except Exception:
        return '-'


def _rate(value, digits=2, default='-'):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _pct(value, digits)
    return default


def _maybe_rate(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _rate(value)
    return _text(value)


def _clock(value, fmt='%m-%d %H:%M:%S'):
    if not value:
        return '-'
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        return datetime.fromisoformat(text).strftime(fmt)
    except Exception:
        return _escape(value)


def _section_open(extra=''):
    return '<section id="kill-one-ai"%s>' % extra


def _note(text):
    return '<div class="k1-note">%s</div>' % text


def _position_labels():
    labels = dict(DEFAULT_LABELS)
    try:
        raw = getattr(kill1_tail, 'POSITION_LABELS', None)
        if isinstance(raw, dict):
            for key, value in raw.items():
                labels[str(key)] = str(value)
        elif isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    labels[str(item[0])] = str(item[1])
    except Exception:
        pass
    return labels


def _position_keys():
    keys = []
    try:
        raw = getattr(kill1_tail, 'POSITIONS', None)
        for item in raw or ():
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                key = str(item[0])
                if key not in keys:
                    keys.append(key)
    except Exception:
        keys = []
    for key in POSITION_ORDER:
        if key not in keys:
            keys.append(key)
    return keys


def _attack_order():
    try:
        order = getattr(kill1_tail, 'ATTACK_ORDER', '1234567890')
        if not order:
            return '1234567890'
        return str(order)
    except Exception:
        return '1234567890'


def _remaining(kill):
    try:
        kill_text = str(kill)
        return ''.join(ch for ch in _attack_order() if ch != kill_text)
    except Exception:
        return ''


def _selected_positions(seal):
    out = []
    if not isinstance(seal, dict):
        return out
    try:
        raw = seal.get('positions')
    except Exception:
        raw = None
    if isinstance(raw, dict):
        for key in _position_keys():
            value = raw.get(key)
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                for item in value:
                    out.append((key, str(item)))
            else:
                out.append((key, str(value)))
    elif isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = str(item.get('position') or item.get('key') or '')
            value = item.get('kill') if item.get('kill') is not None else item.get('value')
            if key and value is not None:
                out.append((key, str(value)))
    return out[:2]


def _basis_text(seal, key):
    basis = ''
    try:
        basis = seal.get('position_basis') if isinstance(seal, dict) else ''
        if isinstance(basis, dict):
            basis = basis.get(key) or ''
    except Exception:
        basis = ''
    text = ''
    try:
        text = str(basis).strip() if basis else ''
    except Exception:
        text = ''
    if not text:
        return '定位依据：AI 自选'
    mapping = {'hot-repeat-neighbor': '热码/重码/邻码', 'ai': 'AI 自选'}
    return '定位依据：' + mapping.get(text, text)


def _position_cards(seal):
    labels = _position_labels()
    cards = []
    for key, kill in _selected_positions(seal):
        cards.append(
            '<div class="k1-card">'
            '<div class="k1-card-head">%s（自选定位）</div>'
            '<div class="k1-kill">排除 %s</div>'
            '<div class="k1-basis">%s</div>'
            '<div class="k1-keep">保留码 %s</div>'
            '</div>' % (
                _escape(labels.get(key, key)),
                _escape(kill),
                _escape(_basis_text(seal, key)),
                _escape(' '.join(_remaining(kill))),
            )
        )
    return cards


def _avoid_items(seal):
    items = []
    if not isinstance(seal, dict):
        return items
    try:
        raw = seal.get('three_avoid')
    except Exception:
        raw = None
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if isinstance(item, dict):
                items.append(item)
            elif item is not None:
                items.append({'value': item})
    return items[:3]


def _value_text(value):
    if isinstance(value, (list, tuple)):
        return ' '.join(str(item) for item in value)
    return _text(value)


def _slash_text(value):
    if isinstance(value, (list, tuple)):
        return ' / '.join(str(item) for item in value)
    return _text(value)


def _avoid_cards(seal):
    cards = []
    for idx, item in enumerate(_avoid_items(seal)):
        label = _text(item.get('label'), '避开%d组' % (idx + 1))
        cards.append(
            '<div class="k1-card">'
            '<div class="k1-card-head">三数共现 · %s</div>'
            '<div class="k1-kill">%s</div>'
            '<div class="k1-basis">3 个数字未全部出现即命中（单项基线 97.96%%，计入整组）</div>'
            '</div>' % (_escape(label), _escape(_value_text(item.get('value'))))
        )
    return cards


def _keep_card(label, value, sub):
    return (
        '<div class="k1-card">'
        '<div class="k1-card-head">%s</div>'
        '<div class="k1-kill k1-keep-big">%s</div>'
        '<div class="k1-note">%s</div>'
        '</div>' % (_escape(label), _escape(_value_text(value)), _escape(sub))
    )


def _positions_note(seal):
    labels = _position_labels()
    selected = [key for key, _kill in _selected_positions(seal)]
    selected_text = '、'.join(labels.get(key, key) for key in selected) or '-'
    rest_text = '、'.join(labels.get(key, key) for key in _position_keys() if key not in selected) or '无'
    return _note(
        '本期自选 %s 个位置：%s；未选位置：%s（攻击语保留 10 码）' % (
            len(selected), _escape(selected_text), _escape(rest_text))
    )


def _reason_html(seal):
    reason = ''
    try:
        reason = seal.get('reason') if isinstance(seal, dict) else ''
    except Exception:
        reason = ''
    text = _text(reason, '')
    if not text:
        text = '—'
    return '<div class="k1-reason">AI 理由：%s</div>' % _escape(text)


def _policy_html():
    return _note(
        '整组固定 7 项：AI 自选 2 个位置各杀 1 码 + 3 组三数共现避开 + 二数合保留 4 码 + 三数合保留 5 码；'
        '7 项全部命中才计整组命中，整组理论基线 69.97%（0.6997）。'
    )


def _publish_html(seal):
    state = ''
    published = None
    if isinstance(seal, dict):
        try:
            state = str(seal.get('publication_state') or '').strip()
            published = seal.get('published_at')
        except Exception:
            state = ''
            published = None
    state_text = STATE_TEXT.get(state, state or '-')
    return _note('发布状态：%s · 发布 %s · 关盘：目标期开奖前 70 秒截止。' % (
        _escape(state_text), _clock(published)))


def _meta_html(seal):
    try:
        return (
            '<div class="k1-meta">参考期 %s · 目标期 %s · AI 计算时间 %s · 模型 %s · 提示词 %s · 数据源 前四球（千/百/十/个）</div>'
            % (
                _escape(seal.get('ref_period')),
                _escape(seal.get('target_period')),
                _clock(seal.get('generated_at')),
                _escape(_text(seal.get('model_label'))),
                _escape(_text(seal.get('prompt_version'))),
            )
        )
    except Exception:
        return ''


def _hit_text(flag):
    if flag is None:
        return '待核验'
    return '命中' if flag else '未中'


def _settlement_html(settlement, seal):
    if not isinstance(settlement, dict) or not settlement:
        target = '-'
        try:
            target = seal.get('target_period') if isinstance(seal, dict) else '-'
        except Exception:
            target = '-'
        return '<div class="k1-verify">结算：目标期 %s 尚未开奖，等待结算（面板每 5 分钟自动跟随）。</div>' % _escape(target)
    labels = _position_labels()
    parts = []
    actual = settlement.get('actual')
    parts.append('结算：目标期 %s 实际 %s' % (
        _escape(_text(settlement.get('target_period'))), _escape(_slash_text(actual))))
    positions = settlement.get('positions')
    if isinstance(positions, dict):
        for key in _position_keys():
            item = positions.get(key)
            if not isinstance(item, dict):
                continue
            parts.append('%s杀 %s→%s %s' % (
                labels.get(key, key),
                _escape(_text(item.get('kill'))),
                _escape(_text(item.get('actual'))),
                _hit_text(item.get('hit')),
            ))
    avoids = settlement.get('three_avoid')
    if isinstance(avoids, (list, tuple)):
        for idx, item in enumerate(avoids):
            if not isinstance(item, dict):
                continue
            parts.append('三数共现 %s %s %s' % (
                _escape(_text(item.get('label'), '避开%d组' % (idx + 1))),
                _escape(_value_text(item.get('value'))),
                _hit_text(item.get('hit')),
            ))
    for key, title in (('he2_keep', '二数合'), ('he3_keep', '三数合')):
        item = settlement.get(key)
        if not isinstance(item, dict):
            continue
        parts.append('%s 保留 %s→实际 %s %s' % (
            title,
            _escape(_value_text(item.get('keep'))),
            _escape(_text(item.get('actual'))),
            _hit_text(item.get('hit')),
        ))
    parts.append('整组 %s' % _hit_text(settlement.get('group_hit')))
    verified = settlement.get('verified')
    if verified is True:
        parts.append('已验证')
    elif verified is False:
        parts.append('待核验')
    return '<div class="k1-verify">%s</div>' % _escape(' · '.join(parts))


def _stat_cell(label, value):
    return '<div class="k1-stat"><span class="l">%s</span><span class="v">%s</span></div>' % (
        _escape(label), _escape(value))


def _per_period(bets, total):
    if isinstance(bets, int) and isinstance(total, int) and total > 0:
        value = bets / total
        if value == int(value):
            return int(value)
        return round(value, 2)
    return '-'


def _stats_html(stats):
    if not isinstance(stats, dict):
        stats = {}
    total = stats.get('total')
    positions = stats.get('positions') if isinstance(stats.get('positions'), dict) else {}
    avoid = stats.get('three_avoid') if isinstance(stats.get('three_avoid'), dict) else {}
    merged = avoid.get('merged') if isinstance(avoid.get('merged'), dict) else {}
    he2 = stats.get('he2_keep') if isinstance(stats.get('he2_keep'), dict) else {}
    he3 = stats.get('he3_keep') if isinstance(stats.get('he3_keep'), dict) else {}
    expected = stats.get('expected') if isinstance(stats.get('expected'), dict) else {}
    expected_rate = expected.get('group_rate')
    cells = [
        _stat_cell('今日整组', '%s/%s（%s）' % (
            _text(stats.get('group_hits'), '0'), _text(total, '0'), _rate(stats.get('group_rate')))),
        _stat_cell('今日位置单注', '%s 注/期' % _text(_per_period(positions.get('bets'), total))),
        _stat_cell('今日共现单注', '%s 组/期' % _text(_per_period(merged.get('bets'), total))),
        _stat_cell('今日二数合', _rate(he2.get('rate'))),
        _stat_cell('今日三数合', _rate(he3.get('rate'))),
        _stat_cell('当前连中/连败', '连中 %s / 连败 %s' % (
            _text(stats.get('current_win_streak'), '0'), _text(stats.get('current_loss_streak'), '0'))),
        _stat_cell('今日最长连败', _text(stats.get('max_loss_streak'), '0')),
        _stat_cell('待核验', _text(stats.get('pending'), '0')),
        _stat_cell('整组理论基线', _rate(expected_rate if expected_rate is not None else FALLBACK_BASELINE[1])),
    ]
    return '<div class="k1-stats">%s</div>' % ''.join(cells)


def _stability_rows():
    try:
        path = STABILITY_PATH
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return []
    if isinstance(data, dict):
        rows = data.get('rows')
    else:
        rows = data
    if isinstance(rows, dict):
        rows = list(rows.values())
    return rows if isinstance(rows, list) else []


def _cumulative_html():
    try:
        rule = getattr(kill1_tail, 'RULE_VERSION', None)
        row = None
        for item in _stability_rows():
            if not isinstance(item, dict):
                continue
            if item.get('key') != 'kill1_tail':
                continue
            if rule and item.get('rule') != rule:
                continue
            row = item
            break
        if row is None:
            return _note(CUMULATIVE_EMPTY)
        hits = row.get('hits') if row.get('hits') is not None else row.get('hit_count')
        rate = row.get('rate') if row.get('rate') is not None else row.get('hit_rate')
        diff = row.get('diff') if row.get('diff') is not None else row.get('delta')
        ci = row.get('ci95') if row.get('ci95') is not None else row.get('ci')
        p_value = row.get('p') if row.get('p') is not None else row.get('p_value')
        return _note('本版累计：n=%s · 命中 %s · 命中率 %s · 理论 %s · 差值 %s · CI95 %s · p=%s · %s' % (
            _escape(_text(row.get('n'))),
            _escape(_text(hits)),
            _escape(_rate(rate)),
            _escape(_maybe_rate(row.get('expected'))),
            _escape(_text(diff)),
            _escape(_text(ci)),
            _escape(_text(p_value)),
            _escape(_text(row.get('status'), '')),
        ))
    except Exception:
        return ''


def _version_html():
    try:
        info = kill1_tail.version_info() or {}
        if not isinstance(info, dict):
            info = {}
        return _note('口径 %s · 已发布 %s 期（%s → %s）' % (
            _escape(_text(info.get('rule_version'), 'kill1_tail')),
            _escape(_text(info.get('count'), '0')),
            _escape(_text(info.get('first_ref'), '-')),
            _escape(_text(info.get('last_ref'), '-')),
        ))
    except Exception:
        return ''


def _json_script(data):
    try:
        text = data.replace('&', chr(92) + 'u0026')
        text = text.replace('<', chr(92) + 'u003c')
        text = text.replace('>', chr(92) + 'u003e')
        return text
    except Exception:
        return ''


def _attack_html(seal):
    try:
        attack = ''
        try:
            attack = kill1_tail.build_attack(seal) or ''
        except Exception:
            attack = ''
        if not isinstance(attack, str):
            attack = str(attack)
        payload = {
            'version': 1,
            'reference_period': seal.get('ref_period') if isinstance(seal, dict) else None,
            'target_period': seal.get('target_period') if isinstance(seal, dict) else None,
            'published_at': seal.get('published_at') if isinstance(seal, dict) else None,
            'attack': attack,
        }
        try:
            data = json.dumps(payload, ensure_ascii=False)
        except Exception:
            data = '{}'
        data = _json_script(data)
        body = (
            '<div class="k1-attack">'
            '<div class="k1-attack-title">攻击语（可直接复制）</div>'
            '<textarea id="kill1-attack-text" readonly aria-label="kill1 攻击语"'
            ' style="width:100%;min-height:52px;margin-top:6px;padding:6px;border:1px solid #c7d2fe;'
            'border-radius:6px;background:#fff;font:12px/1.5 monospace;color:#334155;resize:vertical">'
            '__KILL1_ATTACK__</textarea>'
            '<div><button id="kill1-attack-copy" type="button">复制攻击语</button> '
            '<span id="kill1-copy-status" class="k1-sub"></span></div>'
            '<script id="kill1-attack-data" type="application/json">__KILL1_DATA__</script>'
            '<script>(function(){'
            'var btn=document.getElementById("kill1-attack-copy");'
            'if(!btn||btn.getAttribute("data-bound")){return}'
            'btn.setAttribute("data-bound","1");'
            'function fallback(){'
            'try{done(document.execCommand("copy"))}catch(e){done(false)}'
            '}'
            'function done(ok){'
            'var s=document.getElementById("kill1-copy-status");'
            'if(!s){return}'
            's.textContent=ok?"已复制":"复制失败，请手动选择";'
            'setTimeout(function(){s.textContent=""},1600);'
            '}'
            'btn.addEventListener("click",function(){'
            'var t=document.getElementById("kill1-attack-text");'
            'if(!t){done(false);return}'
            't.focus();t.select();'
            'if(typeof navigator!=="undefined"&&navigator.clipboard&&navigator.clipboard.writeText){'
            'navigator.clipboard.writeText(t.value).then(function(){done(true)},function(){fallback()});'
            '}else{fallback()}'
            '});'
            '})();</script>'
            '</div>'
        )
        body = body.replace('__KILL1_DATA__', data)
        return body.replace('__KILL1_ATTACK__', html.escape(attack, quote=True))
    except Exception:
        return ''


def _foot_html():
    return (
        '<div class="k1-foot">'
        '口径：整组固定 7 项 = AI 每期从千/百/十/个自选恰好 2 个位置各杀 1 码 + 系统给定三数共现避开 1/2/3 组'
        '（每组 3 个数字）+ kill10 同目标期二数合 he2 保留 4 码 + 三数合 he3 保留 5 码；'
        '整组 7 项全部命中才计整组命中。整组随机基线 69.9700%（登记 0.6997，精确 0.6997078452825）；'
        '单项基线：位置杀号 90%、三数共现避开单组 97.96%、二数合 98.08%、三数合 93.75%。'
        '本面板只读展示，每 5 分钟跟随最新封存刷新。'
        '</div>'
    )


def _render(seal, stats, settlement):
    parts = [
        _section_open(' data-as-of="%s"' % _escape(seal.get('ref_period'))),
        '<h3 class="k1-title">%s</h3>' % TITLE,
        _meta_html(seal),
    ]
    cards = []
    cards.extend(_position_cards(seal))
    cards.extend(_avoid_cards(seal))
    cards.append(_keep_card('二数合保留 4 码', seal.get('he2_keep'),
                            '保留 4 码，任一两位和值个位落入即命中（单项基线 98.08%，计入整组）'))
    cards.append(_keep_card('三数合保留 5 码', seal.get('he3_keep'),
                            '保留 5 码，任一三位和值个位落入即命中（单项基线 93.75%，计入整组）'))
    parts.append('<div class="k1-grid">%s</div>' % ''.join(cards))
    parts.append(_positions_note(seal))
    parts.append(_reason_html(seal))
    parts.append(_policy_html())
    parts.append(_publish_html(seal))
    parts.append(_settlement_html(settlement, seal))
    parts.append(_stats_html(stats))
    parts.append(_cumulative_html())
    parts.append(_version_html())
    parts.append(_attack_html(seal))
    parts.append(_foot_html())
    parts.append('</section>')
    return ''.join(parts)


def _empty_html(summary):
    latest = ''
    if isinstance(summary, dict):
        try:
            latest = summary.get('latest_period')
        except Exception:
            latest = ''
    parts = [
        _section_open(''),
        '<h3 class="k1-title">%s</h3>' % TITLE,
        '<div class="k1-empty"><b>等待首期封存</b>：cron 每 5 分钟检查，开奖后生成并发布；'
        '当前最新开奖期 %s，上期封存会自动滚动为 previous。</div>' % _escape(_text(latest, '未知')),
    ]
    stats = {}
    try:
        stats = kill1_tail.today_stats() or {}
    except Exception:
        stats = {}
    parts.append(_stats_html(stats))
    parts.append(_cumulative_html())
    parts.append(_version_html())
    parts.append(_attack_html(None))
    parts.append(_foot_html())
    parts.append('</section>')
    return ''.join(parts)


def render_block():
    try:
        summary = kill1_tail.current_summary()
        if not isinstance(summary, dict):
            summary = {}
        seal = summary.get('seal') or summary.get('previous')
        if not isinstance(seal, dict) or not seal:
            return _empty_html(summary)
        stats = summary.get('stats')
        if not isinstance(stats, dict):
            try:
                stats = kill1_tail.today_stats()
            except Exception:
                stats = {}
        settlement = summary.get('settlement')
        if not isinstance(settlement, dict) or not settlement:
            try:
                rows = kill1_tail.load_settlements() or []
                settlement = rows[-1] if rows and isinstance(rows[-1], dict) else {}
            except Exception:
                settlement = {}
        return _render(seal, stats, settlement)
    except Exception:
        return ''


def apply_kill_one_block(content, report=None):
    if not isinstance(content, str):
        return content
    try:
        block = '<style>' + STYLE + '</style>' + render_block()
        if START in content and END in content:
            head, _sep, rest = content.partition(START)
            inner, _sep2, tail = rest.partition(END)
            return head + START + block + END + tail
        if '<!-- KILL_THREE_END -->' in content:
            marker = '<!-- KILL_THREE_END -->'
            return content.replace(marker, marker + START + block + END, 1)
        if '</body>' in content:
            return content.replace('</body>', START + block + END + '</body>', 1)
        return content
    except Exception:
        try:
            if callable(report):
                report('kill1-block-failed')
        except Exception:
            pass
        return content
