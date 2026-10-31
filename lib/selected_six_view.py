"""Compact independent six-item cards and separately derived real statistics.

Saved cards never change numbers. A passive controller updates only derived
text, with fixed summary height; no page reload, animation, or scroll operation.
"""
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
import json
import re

from ai_verified_stats import next_period
from stats_history import (TZ, beijing_now, business_start, load_snapshot,
                           parse_period, period_slot)
from selected_six_contract import (MARKER, MODEL, REVISION, V2_REVISION, V3_REVISION, V4_REVISION, FORM_REVISIONS,
    SUPPORTED_REVISIONS, POSITION_KEYS, POSITION_LABELS, TAIL_POSITIONS, TAIL_LABELS,
    METRIC_KEYS, REVERSE_LABELS, reverse_form, validate_response)

ROOT = Path('/root/penta5-runner')
STATS_MARKER = 'SELECTED_SIX_INDEPENDENT_STATS'
ATTACK_MARKER = 'SELECTED_SIX_INDEPENDENT_ATTACK'
START, END = '<!-- ' + MARKER + '_START -->', '<!-- ' + MARKER + '_END -->'
STATS_START, STATS_END = '<!-- ' + STATS_MARKER + '_START -->', '<!-- ' + STATS_MARKER + '_END -->'
ATTACK_START, ATTACK_END = '<!-- ' + ATTACK_MARKER + '_START -->', '<!-- ' + ATTACK_MARKER + '_END -->'
PROTECTED = ('R1_FRAMEWORK', 'R1_KILLER', 'R1_PRO_COMPARE', 'STAT50_PREDICTION',
             'HE3_REFINE', 'R1_FRAMEWORK_STATS', 'R1_KILLER_STATS',
             'MODEL_COMPARE_STATS', 'STAT50_COMPARE_STATS', 'STAT50_POSITION_STATS',
             'HE3_REFINE_STATS', 'PENTA5_STATS_PANEL', 'SELECTED_FRAMEWORK',
             'SELECTED_FRAMEWORK_STATS', 'SELECTED_FRAMEWORK_ATTACK')
STYLE = ('width:calc(100% - 24px);max-width:1050px;margin:12px auto;padding:14px;'
         'box-sizing:border-box;min-width:0;overflow-wrap:anywhere;border:2px solid #0891b2;'
         'border-radius:12px;background:#fff;color:#1e293b;font:14px/1.7 sans-serif;')
METRIC_LABELS = {**{'position_' + k: POSITION_LABELS[k] + '定位排除' for k in POSITION_KEYS},
                 **{'tail_' + k: TAIL_LABELS[k] + '排除' for k in TAIL_POSITIONS},
                 'he2_keep': '二数合保留4码', 'he3_keep': '三数合保留5码',
                 **REVERSE_LABELS}


def _reference(value):
    parsed = parse_period(value)
    if not isinstance(value, str) or not parsed or value != parsed[0]:
        raise ValueError('invalid-independent-six-reference')
    return value


def _generated(value):
    if isinstance(value, datetime):
        value = value.isoformat() if value.tzinfo is not None else None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else None
    except ValueError:
        result = None
    if result is None or result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('invalid-independent-six-generation')
    return result.astimezone(TZ)


def _numbers(response, *, revision=REVISION):
    response = validate_response(response, revision=revision)
    numbers = {**{'position_' + k: str(response['selected_positions'][k]) for k in POSITION_KEYS if k in response['selected_positions']},
               **{'tail_' + k: str(response['selected_tails'][k]) for k in TAIL_POSITIONS if k in response['selected_tails']},
               'he2_keep': response['he2_keep'], 'he3_keep': response['he3_keep']}
    if revision == V3_REVISION:
        numbers.update(reverse_form(response, revision=revision))
    elif revision in FORM_REVISIONS:
        numbers.update({'reverse_parity': response['reverse_parity'],
                        'reverse_size': response['reverse_size']})
    return numbers


def _previous_card_fragment(reference, response, generated, target, deadline, revision):
    """Byte-identical card for sealed v1/v2 rules; never re-rendered as v3."""
    rows = []
    for index, (key, number) in enumerate(_numbers(response, revision=revision).items(), 1):
        keep = key in ('he2_keep', 'he3_keep')
        rows.append('<div data-six-independent-row="' + key + '" style="padding:10px;min-width:0;border:1px solid #cffafe;border-radius:8px;background:#f8fdff;">'
                    '<b>' + str(index) + '. ' + escape(METRIC_LABELS[key]) + '：</b> '
                    '<strong data-six-independent-number="' + key + '" style="font-size:22px;color:' + ('#2563eb' if keep else '#dc2626') + ';letter-spacing:1px;">' + number + '</strong>'
                    '<div data-six-independent-rate-key="' + key + '" data-penta5-live-derived="1" style="font-size:12px;color:#475569;min-height:1.7em;">今日战绩等待核验</div></div>')
    return (START + '\n<section id="six-independent-card" data-six-independent-reference="' + reference
            + '" data-six-independent-target="' + target + '" data-six-independent-deadline="' + deadline.isoformat()
            + '" data-six-independent-revision="' + revision + '" style="' + STYLE + '">'
            '<h3 style="margin:0 0 5px;color:#0e7490;font-size:20px;">DeepSeek Pro · 六项独立框架</h3>'
            '<div style="font-size:12px;color:#64748b;">独立请求、独立封存、独立统计；不从七项结果删去一项。</div>'
            '<div style="font-size:12px;color:#64748b;">参考期：' + reference + ' ｜ 目标期：' + target
            + '<br>计算时间：' + generated.strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）</div>'
            '<div id="six-independent-validity" data-penta5-live-derived="1" role="status" style="margin:7px 0;padding:5px 8px;border-radius:6px;background:#dcfce7;color:#166534;min-height:1.7em;">已封存 · 目标期 ' + target + '</div>'
            '<div id="six-independent-summary-slot"></div>'
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:8px;">'
            + ''.join(rows) + '</div><details style="margin-top:6px;font-size:12px;color:#64748b;">'
            '<summary>思路与规则</summary><div>' + escape(response['reason']) + '</div>'
            '<div>自主选两位置与两合尾各排1码；二数合从02468留4码、三数合留5码。'
            '只用最新连续50期前4球；未选位置保留全部数字。封存后号码不变，历史号码保留核对。</div></details></section>\n' + END)


def _v3_card_fragment(reference, response, generated, target, deadline):
    rows = []
    for index, (key, number) in enumerate(_numbers(response, revision=V3_REVISION).items(), 1):
        keep = key in ('he2_keep', 'he3_keep')
        reverse = key in REVERSE_LABELS
        color = '#2563eb' if keep else ('#16a34a' if reverse else '#dc2626')
        rows.append('<div data-six-independent-row="' + key + '" style="padding:10px;min-width:0;border:1px solid #cffafe;border-radius:8px;background:#f8fdff;">'
                    '<b>' + str(index) + '. ' + escape(METRIC_LABELS[key]) + '：</b> '
                    '<strong data-six-independent-number="' + key + '" style="font-size:22px;color:' + color + ';letter-spacing:1px;">' + number + '</strong>'
                    '<div data-six-independent-rate-key="' + key + '" data-penta5-live-derived="1" style="font-size:12px;color:#475569;min-height:1.7em;">今日战绩等待核验</div></div>')
    return (START + '\n<section id="six-independent-card" data-six-independent-reference="' + reference
            + '" data-six-independent-target="' + target + '" data-six-independent-deadline="' + deadline.isoformat()
            + '" data-six-independent-revision="' + V3_REVISION + '" style="' + STYLE + '">'
            '<h3 style="margin:0 0 5px;color:#0e7490;font-size:20px;">DeepSeek Pro · 八项独立框架</h3>'
            '<div style="font-size:12px;color:#64748b;">独立请求、独立封存、独立统计；六项自选 + 两项自动反向形态（取定位排除码的反面）。</div>'
            '<div style="font-size:12px;color:#64748b;">参考期：' + reference + ' ｜ 目标期：' + target
            + '<br>计算时间：' + generated.strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）</div>'
            '<div id="six-independent-validity" data-penta5-live-derived="1" role="status" style="margin:7px 0;padding:5px 8px;border-radius:6px;background:#dcfce7;color:#166534;min-height:1.7em;">已封存 · 目标期 ' + target + '</div>'
            '<div id="six-independent-summary-slot"></div>'
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:8px;">'
            + ''.join(rows) + '</div><details style="margin-top:6px;font-size:12px;color:#64748b;">'
            '<summary>思路与规则</summary><div>' + escape(response['reason']) + '</div>'
            '<div>自主选两位置与两合尾各排1码；二数合从02468留4码；三数合留5码且固定2奇3偶或3奇2偶。'
            '单双反向形态取两个定位排除码单双的反面，大小反向形态取其大小的反面；整组八项全部命中才计入。'
            '只用最新连续50期前4球；未选位置保留全部数字。封存后号码不变，历史号码保留核对。</div></details></section>\n' + END)


def _forms_card_fragment(reference, response, generated, target, deadline, *, revision=REVISION):
    rows = []
    for index, (key, number) in enumerate(_numbers(response, revision=revision).items(), 1):
        keep = key in ('he2_keep', 'he3_keep')
        reverse = key in REVERSE_LABELS
        color = '#2563eb' if keep else ('#16a34a' if reverse else '#dc2626')
        rows.append('<div data-six-independent-row="' + key + '" style="padding:10px;min-width:0;border:1px solid #cffafe;border-radius:8px;background:#f8fdff;">'
                    '<b>' + str(index) + '. ' + escape(METRIC_LABELS[key]) + '：</b> '
                    '<strong data-six-independent-number="' + key + '" style="font-size:22px;color:' + color + ';letter-spacing:1px;">' + number + '</strong>'
                    '<div data-six-independent-rate-key="' + key + '" data-penta5-live-derived="1" style="font-size:12px;color:#475569;min-height:1.7em;">今日战绩等待核验</div></div>')
    return (START + '\n<section id="six-independent-card" data-six-independent-reference="' + reference
            + '" data-six-independent-target="' + target + '" data-six-independent-deadline="' + deadline.isoformat()
            + '" data-six-independent-revision="' + revision + '" style="' + STYLE + '">'
            '<h3 style="margin:0 0 5px;color:#0e7490;font-size:20px;">DeepSeek Pro · 八项独立框架</h3>'
            '<div style="font-size:12px;color:#64748b;">独立请求、独立封存、独立统计；六项自选 + 两项四位形态排除（除）。</div>'
            '<div style="font-size:12px;color:#64748b;">参考期：' + reference + ' ｜ 目标期：' + target
            + '<br>计算时间：' + generated.strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）</div>'
            '<div id="six-independent-validity" data-penta5-live-derived="1" role="status" style="margin:7px 0;padding:5px 8px;border-radius:6px;background:#dcfce7;color:#166534;min-height:1.7em;">已封存 · 目标期 ' + target + '</div>'
            '<div id="six-independent-summary-slot"></div>'
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:8px;">'
            + ''.join(rows) + '</div><details style="margin-top:6px;font-size:12px;color:#64748b;">'
            '<summary>思路与规则</summary><div>' + escape(response['reason']) + '</div>'
            '<div>自主选两位置与两合尾各排1码；二数合从02468留4码；三数合留5码且固定2奇3偶或3奇2偶。'
            '单双/大小反向形态为四位完整形态（千百十个）：被排除定位的字符取排除码反面，其余位置按五段minimax自选；'
            '两项为反向排除项，攻击语用「除」排除该形态，实际形态不等于它才算命中。整组八项全部通过才计入。'
            '只用最新连续50期前4球；未选位置保留全部数字。封存后号码不变，历史号码保留核对。</div></details></section>\n' + END)


def card_fragment(reference, response, generated_at, *, revision=REVISION):
    if revision not in SUPPORTED_REVISIONS:
        raise ValueError('unknown-independent-six-revision')
    reference, generated = _reference(reference), _generated(generated_at)
    response = validate_response(response, revision=revision)
    target, deadline = next_period(reference), period_slot(next_period(reference))
    if not period_slot(reference) <= generated < deadline:
        raise ValueError('independent-six-generation-outside-window')
    if revision in FORM_REVISIONS:
        return _forms_card_fragment(reference, response, generated, target, deadline, revision=revision)
    if revision == V3_REVISION:
        return _v3_card_fragment(reference, response, generated, target, deadline)
    return _previous_card_fragment(reference, response, generated, target, deadline, revision)


def _placeholder(reference):
    label = reference if isinstance(reference, str) and parse_period(reference) else '--'
    return (START + '\n<section style="' + STYLE + '"><h3 style="margin:0;color:#0e7490;">DeepSeek Pro · 八项独立框架</h3>'
            '<p>当前参考期：' + escape(label) + '；等待首次独立事前封存。</p>'
            '<div>六项自选与两项反向形态从本次上线后的真实发布开始累计。</div></section>\n' + END)


def build_attack(response, *, revision=REVISION):
    response = validate_response(response, revision=revision)
    order = '1234567890'
    remaining = lambda digit: ''.join(x for x in order if x != str(digit))
    labels = dict(zip(POSITION_KEYS, ('千', '百', '十', '个')))
    tails = {'all': '千百十个合', 'qbs': '千百十合', 'qbg': '千百个合', 'qsg': '千十个合', 'bsg': '百十个合'}
    if revision == V3_REVISION:
        # v3 narrowed a selected position to the intersection of the mirrored
        # parity and size digit classes (the kill digit is never inside it).
        from selected_six_contract import reverse_keep_digits
        parts = [labels[k] + (reverse_keep_digits(response['selected_positions'][k])
                              if k in response['selected_positions'] else order)
                 for k in POSITION_KEYS]
    else:
        parts = [labels[k] + (remaining(response['selected_positions'][k]) if k in response['selected_positions'] else order) for k in POSITION_KEYS]
    parts.append('四定')
    parts.extend(tails[k] + remaining(response['selected_tails'][k]) for k in TAIL_POSITIONS if k in response['selected_tails'])
    text = ''.join(parts)
    if revision == REVISION:
        # v4: the two four-position forms are excluded explicitly in the attack.
        text += '除' + response['reverse_parity'] + '除' + response['reverse_size']
    return text + '二数合' + response['he2_keep'] + '三数合' + response['he3_keep'] + '各'


def _encoded(data):
    text = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, new in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        text = text.replace(old, new)
    return text


def render_attack_block(proof):
    if not isinstance(proof, dict) or proof.get('publication_state') != 'published' or proof.get('revision') != REVISION:
        raise ValueError('independent-six-attack-requires-publication')
    reference, published = _reference(proof.get('ref_period')), _generated(proof.get('published_at'))
    target = next_period(reference)
    if proof.get('target_period') != target or not period_slot(reference) <= published < period_slot(target):
        raise ValueError('independent-six-attack-publication-mismatch')
    response = validate_response(proof.get('response'), revision=REVISION)
    text = build_attack(response)
    data = {'version': 1, 'rule_revision': REVISION, 'reference_period': reference,
            'target_period': target, 'published_at': published.isoformat(),
            'deadline': period_slot(target).isoformat(), 'numbers': _numbers(response, revision=REVISION), 'attack': text}
    return (ATTACK_START + '\n<section id="six-independent-attack" style="' + STYLE + 'border-width:1px;">'
            '<b>八项独立框架攻击语</b><div id="six-independent-attack-validity" style="font-size:12px;color:#64748b;min-height:1.7em;">目标期：' + target + '；等待页面核对。</div>'
            '<textarea id="six-independent-attack-text" readonly rows="3" aria-label="八项独立框架攻击语" '
            'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;border:1px solid #bae6fd;border-radius:7px;background:#f8fdff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
            + escape(text) + '</textarea><button id="six-independent-attack-copy" type="button" disabled '
            'style="padding:7px 15px;border:0;border-radius:7px;background:#0891b2;color:#fff;">复制攻击语</button>'
            '<span id="six-independent-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></section>'
            '<script id="six-independent-attack-data" type="application/json">' + _encoded(data) + '</script>\n' + ATTACK_END)


def _count(value):
    return value if type(value) is int and 0 <= value <= 9007199254740991 else None


def _metric(raw):
    if not isinstance(raw, dict):
        return None
    fields = ('hits', 'total', 'max_win_streak', 'max_loss_streak', 'current_win_streak', 'current_loss_streak')
    value = {k: _count(raw.get(k)) for k in fields}
    if any(v is None for v in value.values()) or type(raw.get('current_streak_interrupted')) is not bool:
        return None
    h, n, mw, ml, cw, cl = (value[k] for k in fields)
    interrupted = raw['current_streak_interrupted']
    if (h > n or mw > h or ml > n-h or bool(mw) != bool(h) or bool(ml) != bool(n-h)
            or cw > mw or cl > ml or (cw and cl) or (n and not cw and not cl and not interrupted)
            or (interrupted and (cw or cl))):
        return None
    return {**value, 'current_streak_interrupted': interrupted}


def _previous_rules(raw):
    """Validate preserved previous-rule cumulative lines for display only."""
    rules = raw if isinstance(raw, dict) else {}
    result = []
    for revision, label in ((V3_REVISION, '旧八项规则（v3·两位反向）'),
                            (V2_REVISION, '旧六项规则')):
        item = rules.get(revision)
        if not isinstance(item, dict) or item.get('rule_revision') != revision:
            continue
        overall, cumulative = _metric(item.get('overall')), _metric(item.get('cumulative_overall'))
        if overall is None or cumulative is None:
            continue
        result.append({'rule_revision': revision, 'label': label, 'overall': overall,
                       'cumulative_overall': cumulative})
    return result


def _summary(raw):
    raw = raw if isinstance(raw, dict) else {}
    day = raw.get('biz_date')
    try:
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            raise ValueError('bad-day')
    except (ValueError, TypeError):
        day = '--'
    metrics = {k: _metric((raw.get('metrics') or {}).get(k)) for k in METRIC_KEYS} if isinstance(raw.get('metrics'), dict) else {}
    overall = _metric(raw.get('overall'))
    counters = {k: _count(raw.get(k)) for k in ('pending_count', 'overdue_count', 'conflict_count', 'rejected_count')}
    latest = raw.get('latest_settled_period')
    latest = latest if isinstance(latest, str) and parse_period(latest) and latest == parse_period(latest)[0] else None
    error = (raw.get('stats_error') is not False or raw.get('rule_revision') != REVISION or day == '--'
             or overall is None or len(metrics) != len(METRIC_KEYS) or any(v is None for v in metrics.values())
             or any(v is None for v in counters.values()))
    if not error:
        n, h = overall['total'], overall['hits']
        error = (counters['overdue_count'] > counters['pending_count'] or (n and not latest)
                 or any(v['total'] > n or v['total']-v['hits'] > n-h for v in metrics.values())
                 or any(metrics[k]['total'] != n for k in ('he2_keep', 'he3_keep', 'reverse_parity', 'reverse_size'))
                 or any(metrics[k]['hits'] < h for k in ('he2_keep', 'he3_keep', 'reverse_parity', 'reverse_size'))
                 or sum(metrics['position_'+k]['total'] for k in POSITION_KEYS) != 2*n
                 or sum(metrics['tail_'+k]['total'] for k in TAIL_POSITIONS) != 2*n)
    return {'rule_revision': REVISION, 'biz_date': day, 'stats_error': bool(error),
            'overall': None if error else overall, 'metrics': {} if error else metrics,
            'previous_rules': _previous_rules(raw.get('historical_rules')),
            'latest_settled_period': latest, **counters}


def _rate(item, error=False, prefix='整组8项全部命中率'):
    if error or item is None:
        return '统计核验失败，暂停显示'
    return (prefix + ' %.2f%%（%d/%d期）' % (100*item['hits']/item['total'], item['hits'], item['total'])
            if item['total'] else prefix + '：暂无已验证数据')


def _streak(item, error=False, current=False):
    if error or item is None:
        return '统计核验失败，暂停显示'
    names = ('current_win_streak', 'current_loss_streak') if current else ('max_win_streak', 'max_loss_streak')
    labels = ('当前连中', '当前连败') if current else ('最高连中', '最高连败')
    if not item['total']:
        return labels[0] + '：— ｜ ' + labels[1] + '：—（暂无样本）'
    result = '%s：%d手 ｜ %s：%d手' % (labels[0], item[names[0]], labels[1], item[names[1]])
    return result + ('（连续记录已中断）' if current and item['current_streak_interrupted'] else '')


SCRIPT = r'''<script id="selected-six-independent-control">
(function(){
  var version='eight-independent-view-v2-forms',rule='__REVISION__';
  var keys=['position_q','position_b','position_s','position_g','tail_all','tail_qbs','tail_qbg','tail_qsg','tail_bsg','he2_keep','he3_keep','reverse_parity','reverse_size'];
  function one(id){var a=document.querySelectorAll('[id="'+id+'"]');return a.length===1?a[0]:null;}
  function put(n,v){if(n&&n.textContent!==v)n.textContent=v;}
  function json(id){try{var n=one(id);return n?JSON.parse(n.textContent):null;}catch(e){return null;}}
  function aware(value){
    if(typeof value!=='string'||!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.test(value))return NaN;
    var suffix=value.match(/([+-])(\d{2}):(\d{2})$/),offset=suffix?(+suffix[2]*60+(+suffix[3]))*(suffix[1]==='+'?1:-1):0,stamp=Date.parse(value);
    if(suffix&&(+suffix[2]>23||+suffix[3]>59)||!Number.isFinite(stamp))return NaN;
    return new Date(stamp+offset*60000).toISOString().slice(0,19)===value.slice(0,19)?stamp:NaN;
  }
  function identity(ref){
    if(typeof ref!=='string'||!/^\d{11}$/.test(ref))return null;
    var y=+ref.slice(0,4),m=+ref.slice(4,6),d=+ref.slice(6,8),n=+ref.slice(8),day=new Date(Date.UTC(y,m-1,d));
    if(y<100||day.getUTCFullYear()!==y||day.getUTCMonth()!==m-1||day.getUTCDate()!==d||n<1||n>288)return null;
    return {target:n===288?new Date(day.getTime()+86400000).toISOString().slice(0,10).replace(/-/g,'')+'001':ref.slice(0,8)+String(n+1).padStart(3,'0'),deadline:day.getTime()-28800000+(n+1)*300000};
  }
  function metric(x){
    if(!x||typeof x!=='object'||Array.isArray(x)||typeof x.current_streak_interrupted!=='boolean')return false;
    if(!['hits','total','max_win_streak','max_loss_streak','current_win_streak','current_loss_streak'].every(function(k){return Number.isSafeInteger(x[k])&&x[k]>=0;}))return false;
    return x.hits<=x.total&&x.max_win_streak<=x.hits&&x.max_loss_streak<=x.total-x.hits&&!!x.max_win_streak===!!x.hits&&!!x.max_loss_streak===!!(x.total-x.hits)&&x.current_win_streak<=x.max_win_streak&&x.current_loss_streak<=x.max_loss_streak&&!(x.current_win_streak&&x.current_loss_streak)&&(!x.total||x.current_win_streak||x.current_loss_streak||x.current_streak_interrupted)&&(!x.current_streak_interrupted||(!x.current_win_streak&&!x.current_loss_streak));
  }
  function validStats(d){
    if(!d||d.rule_revision!==rule||d.stats_error!==false||typeof d.biz_date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(d.biz_date)||!metric(d.overall)||!d.metrics)return false;
    if(!keys.every(function(k){return metric(d.metrics[k]);})||!['pending_count','overdue_count','conflict_count','rejected_count'].every(function(k){return Number.isSafeInteger(d[k])&&d[k]>=0;})||d.overdue_count>d.pending_count)return false;
    var n=d.overall.total,h=d.overall.hits;
    return keys.every(function(k){var v=d.metrics[k];return v.total<=n&&v.total-v.hits<=n-h;})&&d.metrics.he2_keep.total===n&&d.metrics.he3_keep.total===n&&d.metrics.reverse_parity.total===n&&d.metrics.reverse_size.total===n&&d.metrics.he2_keep.hits>=h&&d.metrics.he3_keep.hits>=h&&d.metrics.reverse_parity.hits>=h&&d.metrics.reverse_size.hits>=h&&keys.slice(0,4).reduce(function(v,k){return v+d.metrics[k].total;},0)===2*n&&keys.slice(4,9).reduce(function(v,k){return v+d.metrics[k].total;},0)===2*n;
  }
  function attackState(){
    var d=json('six-independent-attack-data'),c=one('six-independent-card'),box=one('six-independent-attack-text'),cur=one('disp_period'),now=Date.now(),i=d&&identity(d.reference_period);
    var numbers=d&&d.numbers,names=numbers&&typeof numbers==='object'&&!Array.isArray(numbers)?Object.keys(numbers):[];
    var good=!!d&&!!c&&!!box&&!!cur&&d.version===1&&d.rule_revision===rule&&i&&d.target_period===i.target&&aware(d.deadline)===i.deadline&&c.getAttribute('data-six-independent-revision')===rule&&c.getAttribute('data-six-independent-reference')===d.reference_period&&c.getAttribute('data-six-independent-target')===i.target&&aware(c.getAttribute('data-six-independent-deadline'))===i.deadline&&typeof d.attack==='string'&&d.attack.length>0&&d.attack.length<=512&&box.defaultValue===d.attack&&Number.isFinite(aware(d.published_at))&&aware(d.published_at)>=i.deadline-300000&&aware(d.published_at)<=now&&aware(d.published_at)<i.deadline-70000&&names.length===8;
    if(good)good=names.every(function(k){return keys.indexOf(k)>=0&&typeof numbers[k]==='string';})&&names.filter(function(k){return k.indexOf('position_')===0;}).length===2&&names.filter(function(k){return k.indexOf('tail_')===0;}).length===2&&names.filter(function(k){return k==='reverse_parity'||k==='reverse_size';}).length===2&&/^[02468]{4}$/.test(numbers.he2_keep)&&new Set(numbers.he2_keep).size===4&&/^[0-9]{5}$/.test(numbers.he3_keep)&&new Set(numbers.he3_keep).size===5&&/^[单双]{4}$/.test(numbers.reverse_parity)&&/^[大小]{4}$/.test(numbers.reverse_size)&&names.filter(function(k){return k!=='he2_keep'&&k!=='he3_keep'&&k!=='reverse_parity'&&k!=='reverse_size';}).every(function(k){return /^[0-9]$/.test(numbers[k]);})&&c.querySelectorAll('[data-six-independent-number]').length===8&&names.every(function(k){var a=c.querySelectorAll('[data-six-independent-number="'+k+'"]');return a.length===1&&a[0].textContent.trim()===numbers[k];});
    var text=box&&typeof box.value==='string'?box.value:'',custom=!!d&&typeof d.attack==='string'&&text!==d.attack;
    var originalGood=!!good&&String(cur.textContent||'').trim()===d.reference_period;
    var originalCopy=originalGood&&Number.isFinite(now)&&now<i.deadline-70000;
    return {data:d,good:originalGood,originalCopy:originalCopy,custom:custom,text:text,
      copy:custom?!!good&&Number.isFinite(now)&&text.trim().length>0&&text.length<=4096:originalCopy};
  }
  function check(){
    var data=json('six-independent-stats-data'),good=validStats(data),now=Date.now(),day=Number.isFinite(now)?new Date(now+10800000).toISOString().slice(0,10):null,today=good&&day===data.biz_date;
    function rate(m,p){return !good?'统计核验失败，暂停显示':!today?'北京时间05:00已切换，等待本周期数据':!m.total?p+'：暂无已验证数据':p+' '+(100*m.hits/m.total).toFixed(2)+'%（'+m.hits+'/'+m.total+'期）';}
    function streak(m,current){if(!good)return '统计核验失败，暂停显示';if(!today)return '北京时间05:00已切换，等待本周期数据';var a=current?'当前':'最高';if(!m.total)return a+'连中：— ｜ '+a+'连败：—（暂无样本）';return a+'连中：'+m[current?'current_win_streak':'max_win_streak']+'手 ｜ '+a+'连败：'+m[current?'current_loss_streak':'max_loss_streak']+'手'+(current&&m.current_streak_interrupted?'（连续记录已中断）':'');}
    var summary=one('six-independent-overall-summary'),slot=one('six-independent-summary-slot');
    if(summary&&slot&&summary.parentNode!==slot)slot.appendChild(summary);
    put(one('six-independent-overall-rate'),rate(good&&data.overall,'整组8项全部命中率'));
    put(one('six-independent-overall-max'),streak(good&&data.overall,false));put(one('six-independent-overall-current'),streak(good&&data.overall,true));
    document.querySelectorAll('[data-six-independent-rate-key]').forEach(function(n){var k=n.getAttribute('data-six-independent-rate-key');put(n,keys.indexOf(k)<0?'统计项目不匹配':rate(good&&data.metrics[k],k==='he2_keep'||k==='he3_keep'?'命中率':'排除成功率'));});
    var c=one('six-independent-card'),ref=c&&c.getAttribute('data-six-independent-reference'),id=identity(ref),cur=one('disp_period'),valid=!!c&&c.getAttribute('data-six-independent-revision')===rule&&!!id&&c.getAttribute('data-six-independent-target')===id.target&&aware(c.getAttribute('data-six-independent-deadline'))===id.deadline&&cur&&String(cur.textContent||'').trim()===ref&&now>=id.deadline-300000&&now<id.deadline;
    put(one('six-independent-validity'),valid?'已封存 · 目标期 '+id.target:'历史封存 · 目标期 '+(c?c.getAttribute('data-six-independent-target'):'—')+'（等待本期更新）');
    var box=one('six-independent-attack-text');if(box&&box.readOnly)box.readOnly=false;
    var a=attackState(),button=one('six-independent-attack-copy');if(button&&button.disabled===a.copy)button.disabled=!a.copy;
    put(button,a.custom?'复制编辑内容':'复制攻击语');
    put(one('six-independent-attack-validity'),a.originalCopy?'目标期：'+a.data.target_period+'；已封存，关盘前可复制。':a.good?'本期已关盘，号码保留供核对。':'历史参考或尚未核对，号码保留；等待本期更新。');
    var note=one('six-independent-attack-edit-note');
    if(box&&!note){note=document.createElement('div');note.id='six-independent-attack-edit-note';note.setAttribute('data-penta5-live-derived','1');note.style.cssText='font-size:12px;line-height:1.7;min-height:1.7em;color:#64748b;';box.insertAdjacentElement('afterend',note);}
    put(note,a.custom?'手动编辑文字，不改变封存/统计；刷新时恢复页面最新攻击语。':'可手动输入；刷新时恢复页面最新攻击语。');
  }
  function copy(e){if(!e.target||e.target.id!=='six-independent-attack-copy')return;var a=attackState(),status=one('six-independent-copy-status');if(!a.copy){check();return;}
    function fallback(){var again=attackState();if(!again.copy||again.text!==a.text||again.custom!==a.custom||again.data.attack!==a.data.attack||again.data.target_period!==a.data.target_period){check();return;}
      var active=document.activeElement,x=scrollX,y=scrollY,selection=active&&typeof active.selectionStart==='number'?{start:active.selectionStart,end:active.selectionEnd}:null,t=document.createElement('textarea'),ok=false;t.value=a.text;t.style.cssText='position:fixed;left:-9999px;top:0;opacity:0';document.body.appendChild(t);
      try{t.focus({preventScroll:true});t.select();ok=document.execCommand('copy')===true;}catch(e){}finally{t.remove();if(active&&active.isConnected){active.focus({preventScroll:true});if(selection&&active.setSelectionRange)active.setSelectionRange(selection.start,selection.end);}if(scrollX!==x||scrollY!==y)scrollTo(x,y);}put(status,ok?'已复制':'复制未完成，请手动选择文字。');}
    if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(a.text).then(function(){var again=attackState();put(status,again.text===a.text&&again.data&&again.data.target_period===a.data.target_period?'已复制':'已复制先前文字，请核对当前内容。');},fallback);else fallback();}
  function input(e){if(e.target&&e.target.id==='six-independent-attack-text')check();}
  var old=window.__PENTA5_SELECTED_SIX_INDEPENDENT__;if(old){if(old.timer!=null)clearInterval(old.timer);if(old.check){window.removeEventListener('pageshow',old.check);document.removeEventListener('visibilitychange',old.check);document.removeEventListener('penta5:panel-updated',old.check);}if(old.copy)document.removeEventListener('click',old.copy);if(old.input)document.removeEventListener('input',old.input);}
  var state={version:version,check:check,copy:copy,input:input,timer:setInterval(check,1000)};window.__PENTA5_SELECTED_SIX_INDEPENDENT__=state;window.addEventListener('pageshow',check);document.addEventListener('visibilitychange',check);document.addEventListener('penta5:panel-updated',check);document.addEventListener('click',copy);document.addEventListener('input',input);check();
})();
</script>'''


# The browser-side validator must gate on the live contract revision.
# A revision bump that leaves this literal stale freezes the card at
# waiting-for-this-period and pauses all client-side statistics.
if SCRIPT.count("rule='__REVISION__'") != 1:
    raise ValueError('selected-six-view-rule-placeholder-mismatch')
SCRIPT = SCRIPT.replace("rule='__REVISION__'", "rule='" + REVISION + "'", 1)


def render_stats_block(summary, job_status=None, *, reference=None, now=None, coverage=None):
    data = _summary(summary)
    error = data['stats_error']
    previous_rules = data.get('previous_rules') or []
    previous_text = ''.join(
        '<div>' + escape(rule['label']) + '历史（独立保留，已停止累计）：'
        + escape(' ｜ '.join(segment for segment in (
            _rate(rule[key], False, label)
            for key, label in (('overall', '今日'), ('cumulative_overall', '累计'))
            if rule[key]['total']))) + '</div>'
        for rule in previous_rules) or '<div>旧规则历史（独立保留）：暂无已验证数据。</div>'
    return (STATS_START + '\n<section id="six-independent-stats" style="' + STYLE + 'border-width:1px;background:#f8fdff;">'
            '<div id="six-independent-overall-summary" data-penta5-live-derived="1" style="margin:8px 0;padding:10px;border:1px solid #a5f3fc;border-radius:8px;background:#ecfeff;min-height:114px;box-sizing:border-box;font-variant-numeric:tabular-nums;">'
            '<div style="font-size:12px;color:#64748b;">八项独立 · 今日整组战绩 · ' + escape(data['biz_date']) + ' 05:00起</div>'
            '<div id="six-independent-overall-rate" style="font-size:18px;font-weight:bold;color:#0e7490;min-height:1.7em;">' + escape(_rate(data['overall'], error)) + '</div>'
            '<div id="six-independent-overall-max" style="min-height:1.7em;">' + escape(_streak(data['overall'], error)) + '</div>'
            '<div id="six-independent-overall-current" style="font-size:16px;font-weight:bold;min-height:1.7em;">' + escape(_streak(data['overall'], error, True)) + '</div></div>'
            '<details><summary>八项独立统计规则与明细</summary><div style="font-size:12px;color:#64748b;">'
            '整组必须八项同时通过：两项定位排除、两项合尾排除、二数合保留4码、三数合保留5码，'
            '以及两项四位反向形态排除。'
            '仅按真实开奖核验。北京时间05:00划分统计日；'
            '未选类型、缺预测、冲突都会打断连续记录，不补造胜负；零样本不显示百分比。'
            '二数合按6个两位组合、三数合按4个三位组合，任一合尾落入保留集才算该项命中；'
            '三数合保留5码固定2奇3偶或3奇2偶。反向形态为千百十个四位完整形态（单双/大小）：'
            '被排除定位的字符取排除码反面，其余位置按五段minimax自选；'
            '两项为反向排除项，目标期实际四位形态不等于该形态才算该项命中，攻击语用「除」排除该形态。'
            '历史证据保留，成绩不能保证下一期命中。平台提前70秒关盘。</div>'
            + previous_text +
            '<div style="font-size:12px;">最近核验期：' + escape(data['latest_settled_period'] or '—')
            + ' ｜ 待核验：' + str(data['pending_count'] if data['pending_count'] is not None else '—')
            + ' ｜ 超期：' + str(data['overdue_count'] if data['overdue_count'] is not None else '—')
            + ' ｜ 冲突：' + str(data['conflict_count'] if data['conflict_count'] is not None else '—') + '</div></details></section>'
            '<script id="six-independent-stats-data" type="application/json">' + _encoded(data) + '</script>\n'
            + SCRIPT + '\n' + STATS_END)

def _inspect(html):
    if not isinstance(html, str):
        raise TypeError('independent-six-panel-must-be-text')
    result = {}
    for marker in (*PROTECTED, MARKER, STATS_MARKER, ATTACK_MARKER):
        start, end = '<!-- ' + marker + '_START -->', '<!-- ' + marker + '_END -->'
        counts = html.count(start), html.count(end)
        if counts == (0, 0):
            result[marker] = None
            continue
        if counts != (1, 1):
            raise ValueError('independent-six-ambiguous-panel-markers')
        left, right = html.index(start), html.index(end) + len(end)
        if left >= right-len(end):
            raise ValueError('independent-six-reversed-panel-markers')
        result[marker] = left, right, html[left:right]
    ordered = sorted(value[:2] for value in result.values() if value is not None)
    if any(left < last_end for (_, last_end), (left, _) in zip(ordered, ordered[1:])):
        raise ValueError('independent-six-overlapping-panel-markers')
    return result


def _protected(found):
    return {k: found[k][2] if found[k] else None for k in PROTECTED}


def _fragment(fragment, marker):
    if not isinstance(fragment, str):
        raise TypeError('independent-six-fragment-must-be-text')
    fragment = fragment.strip()
    found = _inspect(fragment)[marker]
    if found is None or found[:2] != (0, len(fragment)):
        raise ValueError('independent-six-invalid-owned-fragment')
    return fragment


def _replace(html, marker, fragment):
    if marker == ATTACK_MARKER and fragment is None:
        # Every owned quiet-refresh range exists even before the first seal.
        # A later JSON/textarea publication adds no fetched executable script.
        fragment = ATTACK_START + '\n' + ATTACK_END
    found = _inspect(html)[marker]
    if found:
        return html[:found[0]] + (fragment or '') + html[found[1]:]
    if fragment is None:
        return html
    if html.count('</body>') != 1:
        raise ValueError('independent-six-ambiguous-panel-body')
    return html.replace('</body>', fragment+'\n</body>', 1)


def _move_blocks(html):
    found = _inspect(html)
    if found[MARKER] is None:
        raise ValueError('independent-six-derived-requires-card')
    blocks = [found[k] for k in (ATTACK_MARKER, STATS_MARKER) if found[k]]
    if not blocks:
        return html
    edges = [found[MARKER]]+blocks
    if all(right[0] >= left[1] and not html[left[1]:right[0]].strip() for left, right in zip(edges, edges[1:])):
        return html
    for block in sorted(blocks, reverse=True):
        html = html[:block[0]]+html[block[1]:]
    pos = _inspect(html)[MARKER][1]
    return html[:pos]+'\n'+'\n'.join(b[2] for b in blocks)+'\n'+html[pos:]


def compose_selected_transform(html, transform):
    before = _inspect(html)
    if not callable(transform):
        raise TypeError('independent-six-transform-must-be-callable')
    changed = transform(html)
    after = _inspect(changed)
    if _protected(before) != _protected(after) or after[MARKER] is None:
        raise ValueError('independent-six-publication-changed-other-block')
    for key in (STATS_MARKER, ATTACK_MARKER):
        if (before[key][2] if before[key] else None) != (after[key][2] if after[key] else None):
            raise ValueError('independent-six-publication-changed-derived-block')
    if before[MARKER] is None or before[MARKER][2] != after[MARKER][2]:
        changed = _replace(changed, ATTACK_MARKER, None)
    return _move_blocks(changed)


def apply_selected_block(html, fragment):
    fragment = _fragment(fragment, MARKER)
    return compose_selected_transform(html, lambda content: _replace(content, MARKER, fragment))


def apply_selected_stats_block(html, fragment):
    before = _inspect(html)
    updated = _move_blocks(_replace(html, STATS_MARKER, _fragment(fragment, STATS_MARKER)))
    after = _inspect(updated)
    if _protected(before) != _protected(after) or before[MARKER][2] != after[MARKER][2]:
        raise ValueError('independent-six-statistics-changed-prediction')
    return updated


def apply_selected_attack_block(html, fragment):
    before = _inspect(html)
    if before[MARKER] is None:
        raise ValueError('independent-six-attack-requires-card')
    updated = _move_blocks(_replace(html, ATTACK_MARKER, _fragment(fragment, ATTACK_MARKER) if fragment is not None else None))
    after = _inspect(updated)
    if (_protected(before) != _protected(after) or before[MARKER][2] != after[MARKER][2]
            or (before[STATS_MARKER][2] if before[STATS_MARKER] else None) != (after[STATS_MARKER][2] if after[STATS_MARKER] else None)):
        raise ValueError('independent-six-attack-changed-other-block')
    return updated


def _card_reference(fragment):
    if not isinstance(fragment, str):
        return None
    headers = re.findall(r'<section\s+id="six-independent-card"\s[^>]*>', fragment)
    if len(headers) != 1:
        return None
    refs = re.findall(r'\sdata-six-independent-reference="([^"]*)"', headers[0])
    try:
        return _reference(refs[0]) if len(refs) == 1 else None
    except (ValueError, TypeError):
        return None


def _apply_selected_refresh(html, fragment, attack, statistics):
    found = _inspect(html)
    existing = _card_reference(found[MARKER][2]) if found[MARKER] else None
    chosen = _card_reference(fragment)
    if existing is not None and (chosen is None or existing > chosen):
        return apply_selected_stats_block(html, statistics)
    return apply_selected_stats_block(apply_selected_attack_block(apply_selected_block(html, fragment), attack), statistics)


def refresh_selected_panel(snapshot=None, root=ROOT):
    from selected_six_model import select_current, validate_publication
    from selected_six_stats import refresh_selected_stats
    from ai_prediction_guard import read_stats
    from scripts.run_ai_ready_job import expected_reference
    from panel_io import update_panel
    root = Path(root)
    snapshot = snapshot if snapshot is not None else load_snapshot(state_path=root/'data/state.json', cache_path=root/'data/stats_draw_history.json', persist=False)
    summary = refresh_selected_stats(snapshot=snapshot, root=root)
    result = {}

    def transform(html):
        # Select the display from fresh durable evidence only after statistics,
        # while holding the publication lock; keep the caller's stats snapshot.
        live = load_snapshot(state_path=root/'data/state.json', cache_path=root/'data/stats_draw_history.json', persist=False)
        now = live['now']
        reference = expected_reference(now)
        try:
            proof = select_current(root, live)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            proof = None
        try:
            index = read_stats(root/'data/selected_six_seals.json').get('prediction_seals', {})
            candidates = sorted(((ref, seal) for ref, seal in index.items() if isinstance(ref, str) and parse_period(ref) and ref <= reference and isinstance(seal, dict)), reverse=True)[:50]
            for ref, seal in candidates:
                try:
                    validate_publication(seal, ref, now)
                    if proof is None or ref > proof['ref_period']:
                        proof = seal
                    break
                except (OSError, ValueError, TypeError, KeyError, OverflowError):
                    continue
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            pass
        fragment = proof['card_html'] if proof is not None else _placeholder(reference)
        statistics = render_stats_block(summary, reference=reference, now=now)
        # Old policies keep their card bytes, without a current-policy attack.
        attack = render_attack_block(proof) if proof is not None and proof.get('revision') == REVISION else None
        result.update(reference=reference, available=bool(proof and proof.get('revision') == REVISION
                      and proof['ref_period'] == reference and now < period_slot(next_period(reference))-timedelta(seconds=70)),
                      retained_reference=proof['ref_period'] if proof and proof['ref_period'] != reference else None)
        return _apply_selected_refresh(html, fragment, attack, statistics)

    update_panel(transform, path=root/'panel.html')
    return {**summary, **result}


refresh_independent_six_panel = refresh_selected_panel
