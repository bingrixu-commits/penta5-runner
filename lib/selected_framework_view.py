"""Immutable selected-six prediction cards and honest daily statistics.

Expired sealed numbers remain visible with their own target and an explicit
historical label. Statistics are separate derived presentation and never alter
the saved prediction or convert an old reference into a new prediction.
"""
from datetime import date, datetime, timedelta
from html import escape
from math import ceil
from pathlib import Path
import json
import re

from ai_verified_stats import next_period
from stats_history import (TZ, beijing_now, business_start, load_snapshot,
                           parse_period, period_slot)
from selected_framework_contract import (MARKER, MODEL, REVISION, ISOLATED_REVISION, FIVE_REVISION, SEVEN_REVISION, LEGACY_REVISION, SUPPORTED_REVISIONS, he3_count, item_count, extra_metric_key, POSITION_KEYS, POSITION_LABELS,
                                         TAIL_POSITIONS, TAIL_LABELS,
                                         BASE_METRIC_KEYS, EXTRA_METRIC_KEYS, METRIC_KEYS, validate_response)
from selected_framework_attack import build_attack
from selected_single_view import render_observation

ROOT = Path('/root/penta5-runner')
START, END = '<!-- ' + MARKER + '_START -->', '<!-- ' + MARKER + '_END -->'
STATS_MARKER = 'SELECTED_FRAMEWORK_STATS'
STATS_START, STATS_END = '<!-- ' + STATS_MARKER + '_START -->', '<!-- ' + STATS_MARKER + '_END -->'
ATTACK_MARKER = 'SELECTED_FRAMEWORK_ATTACK'
ATTACK_START, ATTACK_END = '<!-- ' + ATTACK_MARKER + '_START -->', '<!-- ' + ATTACK_MARKER + '_END -->'
PROTECTED = ('R1_FRAMEWORK', 'R1_KILLER', 'R1_PRO_COMPARE', 'STAT50_PREDICTION',
             'HE3_REFINE', 'R1_FRAMEWORK_STATS', 'R1_KILLER_STATS', 'MODEL_COMPARE_STATS', 'STAT50_COMPARE_STATS',
             'STAT50_POSITION_STATS', 'HE3_REFINE_STATS', 'PENTA5_STATS_PANEL')
STYLE = ('width:calc(100% - 24px);max-width:1050px;margin:16px auto;padding:18px;'
         'box-sizing:border-box;min-width:0;overflow-wrap:anywhere;border:2px solid #6366f1;'
         'border-radius:12px;background:#fff;color:#1e293b;font:14px/1.8 sans-serif;')
METRIC_LABELS = {**{'position_' + key: POSITION_LABELS[key] + '定位排除' for key in POSITION_KEYS},
                 **{'tail_' + key: TAIL_LABELS[key] + '排除' for key in TAIL_POSITIONS},
                 'he2_keep': '二数合保留4码', 'he3_keep': '三数合保留5码',
                 **{'extra_position_' + key: '额外' + POSITION_LABELS[key] + '定位排除' for key in POSITION_KEYS},
                 **{'extra_tail_' + key: '额外' + TAIL_LABELS[key] + '排除' for key in TAIL_POSITIONS}}


def _reference(value):
    parsed = parse_period(value)
    if not isinstance(value, str) or not parsed or value != parsed[0]:
        raise ValueError('invalid-selected-framework-reference')
    return value


def _generated(value):
    if isinstance(value, datetime):
        value = value.isoformat() if value.tzinfo is not None else None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('invalid-selected-framework-generation-time')
    return parsed.astimezone(TZ)


def _card_row(key, label, value, index, keep=False):
    prefix = '保留：' if keep else '排除：'
    return ('<div data-selected6-row="' + key + '" style="padding:10px 12px;min-width:0;'
            'border:1px solid #dbeafe;border-radius:8px;background:#f8faff;overflow-wrap:anywhere;">'
            '<b>' + str(index) + '. ' + escape(label) + ' ' + prefix + '</b>'
            '<strong data-selected6-number="' + key + '" style="display:inline-block;margin-left:5px;'
            'color:' + ('#2563eb' if keep else '#dc2626') + ';font-size:20px;letter-spacing:1px;">'
            + escape(str(value)) + '</strong>'
            '<div data-selected6-rate-key="' + key + '" style="font-size:12px;color:#475569;">'
            '今日战绩等待核验</div></div>')


def card_fragment(reference, response, generated_at, *, revision=REVISION):
    """Canonical saved card. No rate, historical fallback, or mutable state."""
    reference, generated = _reference(reference), _generated(generated_at)
    response = validate_response(response, revision=revision)
    keep_label = '三数合保留%d码' % he3_count(revision)
    rule_attr = '' if revision == LEGACY_REVISION else ' data-selected6-revision="' + revision + '"'
    deadline = period_slot(next_period(reference))
    if not period_slot(reference) <= generated < deadline:
        raise ValueError('selected-framework-generation-outside-window')
    rows = []
    for key in POSITION_KEYS:
        if key in response['selected_positions']:
            rows.append(_card_row('position_' + key, POSITION_LABELS[key],
                                  response['selected_positions'][key], len(rows) + 1))
    for key in TAIL_POSITIONS:
        if key in response['selected_tails']:
            rows.append(_card_row('tail_' + key, TAIL_LABELS[key],
                                  response['selected_tails'][key], len(rows) + 1))
    rows.append(_card_row('he2_keep', '二数合保留4码（02468中选4码）', response['he2_keep'], len(rows) + 1, True))
    rows.append(_card_row('he3_keep', keep_label, response['he3_keep'], len(rows) + 1, True))
    extra = response.get('extra_exclusion')
    if extra is not None:
        label = (POSITION_LABELS if extra['kind'] == 'position' else TAIL_LABELS)[extra['key']]
        rows.append(_card_row(extra_metric_key(response, revision=revision), '额外' + label, extra['digit'], 7))
    title = ('三定位七项框架' if revision == REVISION else
             '自选七项框架' if item_count(revision) == 7 else '自选六项框架')
    suffix = ' ＋ AI额外自选1项排除' if extra is not None else ''
    position_rule = '四位选三位各排1码（AI自主选择）' if revision == REVISION else '四位选两位各排1码'
    return (START + '\n<section id="selected6-card" aria-label="' + title + '" style="' + STYLE + '"'
            ' data-selected6-reference="' + reference + '" data-selected6-target="' + next_period(reference)
            + '" data-selected6-deadline="' + deadline.isoformat() + '"' + rule_attr + '>'
            '<h3 style="font-size:19px;margin:0 0 6px;color:#4338ca;">DeepSeek Pro · ' + title + '</h3>'
            '<div>' + position_rule + ' ＋ 五种合尾选两种各排1码 ＋ 二数合保留4码 ＋ ' + keep_label + suffix + '</div>'
            '<div style="font-size:12px;color:#64748b;">参考期：' + reference + ' ｜ 预测目标期：'
            + next_period(reference) + '<br>AI计算时间：' + generated.strftime('%Y-%m-%d %H:%M:%S')
            + ' ｜ 参考截止：' + deadline.strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）</div>'
            '<div id="selected6-validity" role="status" style="margin:8px 0;padding:5px 9px;'
            'background:#fef3c7;color:#92400e;border-radius:6px;">事前封存结果；请核对目标期与参考截止时间。</div>'
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,260px),1fr));'
            'gap:10px;min-width:0;">' + ''.join(rows) + '</div>'
            '<div style="margin-top:8px;font-size:12px;color:#64748b;">简短理由：' + escape(response['reason']) + '</div>'
            '<div style="font-size:12px;color:#64748b;">仅使用真实连续50期前4球；未选位置不排除。'
            '二数合按6个两位组合、三数合按4个三位组合计算合尾，任一合尾落入保留集才算该项命中。'
            '封存结果不改号；历史结果保留号码并单独标记状态。</div></section>\n' + END)


def _placeholder(reference):
    label = reference if isinstance(reference, str) and parse_period(reference) else '--'
    return (START + '\n<section aria-label="三定位七项框架" style="' + STYLE + '">'
            '<h3 style="margin:0;color:#4338ca;">DeepSeek Pro · 三定位七项框架</h3>'
            '<p role="status">当前参考期：' + escape(label) + '；等待首个合格的事前封存结果。</p>'
            '<div>新框架单独记录真实战绩，从发布后的目标期开奖开始核验。</div></section>\n' + END)


ATTACK_SCRIPT = """<script id="selected6-attack-control">
(function(){
  var version='selected-seven-attack-v7-http-hidden-source',rule='selected-seven-v6-three-positions';
  function one(id){var nodes=document.querySelectorAll('[id="'+id+'"]');return nodes.length===1?nodes[0]:null;}

  function identity(ref){
    if(typeof ref!=='string'||!/^[0-9]{11}$/.test(ref))return null;
    var y=+ref.slice(0,4),m=+ref.slice(4,6),d=+ref.slice(6,8),n=+ref.slice(8),day=new Date(Date.UTC(y,m-1,d));
    if(y<100||day.getUTCFullYear()!==y||day.getUTCMonth()!==m-1||day.getUTCDate()!==d||n<1||n>288)return null;
    return {target:n===288?new Date(day.getTime()+86400000).toISOString().slice(0,10).replace(/-/g,'')+'001':ref.slice(0,8)+String(n+1).padStart(3,'0'),deadline:day.getTime()-8*3600000+(n+1)*300000};
  }
  function state(){
    var source=one('selected6-attack-data'),box=one('selected6-attack-text'),button=one('selected6-attack-copy'),card=one('selected6-card'),data=null;
    try{if(source)data=JSON.parse(source.textContent);}catch(e){data=null;}
    var ref=data&&data.reference_period,expected=identity(ref),now=Date.now();
    var valid=!!(source&&box&&button&&card&&data&&data.version===1&&data.rule_revision===rule&&
      card.getAttribute('data-selected6-revision')===rule&&expected&&data.target_period===expected.target&&
      typeof data.attack==='string'&&data.attack.length>0&&data.attack.length<=512&&box.value===data.attack&&
      Number.isFinite(now)&&now>=Date.parse(data.published_at)&&now<expected.deadline-70000&&
      Date.parse(data.deadline)===expected.deadline&&card.getAttribute('data-selected6-reference')===ref&&
      card.getAttribute('data-selected6-target')===expected.target&&Date.parse(card.getAttribute('data-selected6-deadline'))===expected.deadline&&
      String((one('disp_period')||{}).textContent||'').trim()===ref);
    var numbers=data&&data.numbers,allowed=['position_q','position_b','position_s','position_g','tail_all','tail_qbs','tail_qbg','tail_qsg','tail_bsg','he2_keep','he3_keep'];
    if(valid){
      var keys=numbers&&typeof numbers==='object'&&!Array.isArray(numbers)?Object.keys(numbers):[];
      valid=keys.length===7&&keys.every(function(k){return allowed.indexOf(k)>=0&&typeof numbers[k]==='string';})&&
        keys.filter(function(k){return k.indexOf('position_')===0;}).length===3&&keys.filter(function(k){return k.indexOf('tail_')===0;}).length===2&&
        keys.indexOf('he2_keep')>=0&&keys.indexOf('he3_keep')>=0&&
        /^[02468]{4}$/.test(numbers.he2_keep)&&new Set(numbers.he2_keep).size===4&&
        /^[0-9]{5}$/.test(numbers.he3_keep)&&new Set(numbers.he3_keep).size===5&&
        keys.filter(function(k){return k.indexOf('position_')===0||k.indexOf('tail_')===0;}).every(function(k){return /^[0-9]$/.test(numbers[k]);})&&
        card.querySelectorAll('[data-selected6-number]').length===7&&
        keys.every(function(k){var nodes=card.querySelectorAll('[data-selected6-number="'+k+'"]');return nodes.length===1&&nodes[0].textContent.trim()===numbers[k];});
    }
    return {valid:!!valid,data:data,box:box,button:button};
  }
  function notice(message){var node=one('selected6-attack-copy-status');if(node&&node.textContent!==message)node.textContent=message;}
  function check(){
    var s=state();if(s.button&&s.button.disabled===s.valid)s.button.disabled=!s.valid;
    var label=one('selected6-attack-validity');
    if(label){var target=s.data&&s.data.target_period||'--',message=s.valid?'本期七项规则攻击语，适用目标期：'+target:(s.data&&s.data.rule_revision!==rule?'旧策略历史参考，目标期：':'历史参考或本期结果尚未匹配，目标期：')+target+'；保留文字供核对，暂停一键复制。';if(label.textContent!==message)label.textContent=message;}
    if(!s.valid)notice('请核对目标期，等待本期更新。');
  }
  function same(snapshot){
    var current=state();
    return current.valid&&snapshot&&snapshot.valid&&current.data.reference_period===snapshot.data.reference_period&&
      current.data.target_period===snapshot.data.target_period&&current.data.attack===snapshot.data.attack&&current.box.value===snapshot.data.attack;
  }
  function fallback(snapshot){
    if(!same(snapshot)){check();return false;}
    var active=document.activeElement,x=window.scrollX,y=window.scrollY;
    var selection=active&&typeof active.selectionStart==='number'?{start:active.selectionStart,end:active.selectionEnd,direction:active.selectionDirection}:null;
    var area=document.createElement('textarea'),copied=false;
    area.value=snapshot.data.attack;area.readOnly=true;area.setAttribute('aria-hidden','true');
    area.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;pointer-events:none;';
    document.body.appendChild(area);
    try{area.focus({preventScroll:true});area.select();if(same(snapshot))copied=typeof document.execCommand==='function'&&document.execCommand('copy')===true;}
    catch(e){copied=false;}
    finally{area.remove();if(active&&active.isConnected){try{active.focus({preventScroll:true});if(selection&&active.setSelectionRange)active.setSelectionRange(selection.start,selection.end,selection.direction);}catch(e){}}
      if(window.scrollX!==x||window.scrollY!==y)window.scrollTo(x,y);}
    if(copied)notice('已复制攻击语');
    else notice('自动复制未完成，请在可编辑攻击语中选择文字手动复制。');
    return copied;
  }
  async function click(event){
    var button=one('selected6-attack-copy');if(!button||event.target!==button)return;
    event.preventDefault();var s=state();if(!s.valid){check();return;}
    try{
      if(navigator.clipboard&&typeof navigator.clipboard.writeText==='function'){
        await navigator.clipboard.writeText(s.box.value);notice(same(s)?'已复制攻击语':'已复制先前攻击语，请核对当前目标期。');return;
      }
    }catch(e){ /* HTTP/IP or denied browser permission: use the selected text. */ }
    fallback(s);
  }
  var old=window.__PENTA5_SELECTED_ATTACK__;
  if(old){if(old.timer!=null)clearInterval(old.timer);if(old.check){window.removeEventListener('pageshow',old.check);document.removeEventListener('visibilitychange',old.check);document.removeEventListener('penta5:panel-updated',old.check);}if(old.click)document.removeEventListener('click',old.click);}
  var active={version:version,check:check,click:click,timer:setInterval(check,1000)};
  window.__PENTA5_SELECTED_ATTACK__=active;window.addEventListener('pageshow',check);document.addEventListener('visibilitychange',check);document.addEventListener('penta5:panel-updated',check);document.addEventListener('click',click);check();
})();
</script>"""


def render_attack_block(proof):
    """Derived representation of one validated published seal; never a new tip."""
    if not isinstance(proof, dict) or proof.get('publication_state') != 'published':
        raise ValueError('selected-attack-requires-published-seal')
    reference = _reference(proof.get('ref_period'))
    target = next_period(reference)
    if proof.get('target_period') != target:
        raise ValueError('selected-attack-target-mismatch')
    published = _generated(proof.get('published_at'))
    deadline = period_slot(target)
    if not period_slot(reference) <= published < deadline:
        raise ValueError('selected-attack-publication-outside-window')
    revision = proof.get('revision')
    response = validate_response(proof.get('response'), revision=revision)
    text = build_attack(response, revision=revision)
    numbers = {'position_' + key: str(value) for key, value in response['selected_positions'].items()}
    numbers.update({'tail_' + key: str(value) for key, value in response['selected_tails'].items()})
    numbers.update(he2_keep=response['he2_keep'], he3_keep=response['he3_keep'])
    if 'extra_exclusion' in response:
        numbers[extra_metric_key(response, revision=revision)] = str(response['extra_exclusion']['digit'])
    data = {'version': 1, 'rule_revision': revision, 'reference_period': reference, 'target_period': target,
            'deadline': deadline.isoformat(), 'published_at': published.isoformat(),
            'attack': text, 'numbers': numbers}
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, new in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        encoded = encoded.replace(old, new)
    rule_notice = '三定位七项规则事前封存结果。' if revision == REVISION else '旧策略历史参考；等待三定位七项规则更新。'
    title = ('三定位七项框架' if revision == REVISION else
             '自选七项框架' if item_count(revision) == 7 else '自选六项框架')
    return (ATTACK_START + '\n<section aria-label="' + title + '攻击语" style="' + STYLE + 'border-width:1px;">'
            '<b>' + title + '攻击语：</b><div id="selected6-attack-validity" style="font-size:12px;color:#64748b;">'
            + rule_notice + '参考期：' + reference + ' ｜ 目标期：' + target + '</div>'
            '<textarea id="selected6-attack-text" readonly rows="3" aria-label="' + title + '攻击语" '
            'style="display:block;width:100%;max-width:100%;min-width:0;box-sizing:border-box;resize:vertical;'
            'margin:7px 0;padding:10px;background:#f1f5f9;border:1px solid #dbeafe;border-radius:7px;'
            'color:#0f172a;font:15px/1.8 serif;overflow-wrap:anywhere;">' + escape(text) + '</textarea>'
            '<button type="button" id="selected6-attack-copy" disabled style="padding:8px 16px;border:0;'
            'border-radius:7px;background:#4f46e5;color:#fff;cursor:pointer;">复制攻击语</button>'
            '<span id="selected6-attack-copy-status" role="status" aria-live="polite" '
            'style="margin-left:9px;font-size:12px;color:#475569;"></span>'
            '<div style="font-size:12px;color:#64748b;">攻击语由本期封存的全部项目直接生成，未选位置保留全部数字。</div>'
            '</section>\n<script type="application/json" id="selected6-attack-data">' + encoded + '</script>\n'
            + ATTACK_SCRIPT + '\n' + ATTACK_END)


def apply_selected_attack_block(html, fragment):
    from model_retirement import is_retired,strip_selected7_panel
    if is_retired('selected7'):return strip_selected7_panel(html)
    before = _inspect(html)
    if before[MARKER] is None:
        raise ValueError('selected-attack-requires-card')
    if fragment is not None:
        fragment = _fragment(fragment, ATTACK_MARKER)
    current = before[ATTACK_MARKER]
    if current:
        updated = html[:current[0]] + (fragment or '') + html[current[1]:]
    elif fragment is not None:
        position = before[MARKER][1]
        updated = html[:position] + '\n' + fragment + '\n' + html[position:]
    else:
        updated = html
    updated = _move_stats(updated); after = _inspect(updated)
    if (_protected(before) != _protected(after) or before[MARKER][2] != after[MARKER][2]
            or (before[STATS_MARKER][2] if before[STATS_MARKER] else None) != (after[STATS_MARKER][2] if after[STATS_MARKER] else None)):
        raise ValueError('selected-attack-changed-other-block')
    return updated


def _count(value):
    return value if type(value) is int and 0 <= value <= 9007199254740991 else None


def _metric(value):
    if not isinstance(value, dict):
        return None
    names = ('hits', 'total', 'max_win_streak', 'max_loss_streak',
             'current_win_streak', 'current_loss_streak')
    result = {name: _count(value.get(name)) for name in names}
    if any(item is None for item in result.values()) or type(value.get('current_streak_interrupted')) is not bool:
        return None
    h, n, mw, ml, cw, cl = (result[key] for key in names)
    if (h > n or mw > h or ml > n - h or bool(mw) != bool(h) or bool(ml) != bool(n - h)
            or cw > mw or cl > ml or (cw and cl)
            or (n and not cw and not cl and not value['current_streak_interrupted'])
            or (value['current_streak_interrupted'] and (cw or cl))):
        return None
    return {**result, 'current_streak_interrupted': value['current_streak_interrupted']}


def _summary(summary):
    raw = summary if isinstance(summary, dict) else {}
    day = raw.get('biz_date')
    try:
        if not isinstance(day, str) or not re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}', day):
            raise ValueError()
        day = date.fromisoformat(day).isoformat()
    except (ValueError, TypeError):
        day = '--'
    source = raw.get('metrics') if isinstance(raw.get('metrics'), dict) else {}
    metrics = {key: _metric(source.get(key)) for key in BASE_METRIC_KEYS}
    overall = _metric(raw.get('overall'))
    counters = {key: _count(raw.get(key)) for key in ('pending_count', 'overdue_count', 'conflict_count')}
    latest = raw.get('latest_settled_period')
    latest = latest if isinstance(latest, str) and parse_period(latest) and latest == parse_period(latest)[0] else None
    error = (raw.get('rule_revision') != REVISION or raw.get('stats_error') is not False or day == '--' or overall is None
             or any(value is None for value in metrics.values()) or any(value is None for value in counters.values()))
    if not error:
        n, h = overall['total'], overall['hits']
        if (counters['overdue_count'] > counters['pending_count'] or (n > 0 and latest is None)
                or any(value['total'] > n or value['total'] - value['hits'] > n - h for value in metrics.values())
                or metrics['he2_keep']['total'] != n or metrics['he3_keep']['total'] != n
                or any(metrics[key]['hits'] < h for key in ('he2_keep', 'he3_keep'))
                or sum(metrics['position_' + key]['total'] for key in POSITION_KEYS) != 3 * n
                or sum(metrics['tail_' + key]['total'] for key in TAIL_POSITIONS) != 2 * n):
            error = True
    if error:
        metrics = {key: None for key in BASE_METRIC_KEYS}
        overall = None
    return {'rule_revision': REVISION, 'biz_date': day, 'metrics': metrics, 'overall': overall, 'stats_error': error,
            'latest_settled_period': latest or '--', **counters}


def _rate(item, error, prefix):
    if error or item is None:
        return '统计核验失败，暂停显示'
    if not item['total']:
        return '暂无已验证数据'
    return prefix + ' %.2f%%（%d/%d期）' % (100 * item['hits'] / item['total'], item['hits'], item['total'])


def _streak(item, error):
    if error or item is None:
        return '统计核验失败，暂停显示'
    if not item['total']:
        return '最高连中：— ｜ 最高连败：— ｜ 当前连中：— ｜ 当前连败：—（暂无样本）'
    current = ('当前连中：%d手 ｜ 当前连败：%d手' %
               (item['current_win_streak'], item['current_loss_streak']))
    if item['current_streak_interrupted']:
        current += '（连续记录已中断）'
    return '最高连中：%d手 ｜ 最高连败：%d手 ｜ %s' % (item['max_win_streak'], item['max_loss_streak'], current)


def _current_streak(item, error):
    if error or item is None:
        return '统计核验失败，暂停显示'
    if not item['total']:
        return '当前连中：— ｜ 当前连败：—（暂无样本）'
    text = '当前连中：%d手 ｜ 当前连败：%d手' % (item['current_win_streak'], item['current_loss_streak'])
    return text + ('（连续记录已中断）' if item['current_streak_interrupted'] else '')


def _max_streak(item, error):
    if error or item is None:
        return '统计核验失败，暂停显示'
    return ('最高连中：%d手 ｜ 最高连败：%d手' % (item['max_win_streak'], item['max_loss_streak'])
            if item['total'] else '最高连中：— ｜ 最高连败：—')


def _job_summary(raw):
    """Whitelist operational state only; never expose provider text or secrets."""
    if not isinstance(raw, dict) or raw.get('revision') != REVISION or raw.get('requested_model') != MODEL:
        return None
    status = raw.get('status')
    if status not in ('waiting', 'requesting', 'published', 'preserved', 'rejected', 'failed'):
        return None
    try:
        reference = _reference(raw.get('reference_period'))
        target = next_period(reference)
        updated = _generated(raw.get('updated_at'))
        if raw.get('target_period') != target or updated < period_slot(reference):
            return None
    except (ValueError, TypeError, KeyError, OverflowError):
        return None
    # Only four public explanations. Arbitrary backend error strings are never rendered.
    code, cause = raw.get('error_code'), raw.get('cause_code')
    timeout_codes = ('publication-deadline-passed', 'selected-six-reference-advanced',
                     'selected-seven-platform-close-passed', 'selected-seven-insufficient-close-budget',
                     'provider-wall-clock-deadline-exhausted', 'reference-period-advanced',
                     'target-already-opened', 'insufficient-time-for-killer-request')
    operation_codes = ('selected-seven-operation-window-passed', 'selected-seven-insufficient-operation-budget')
    category = ('balance' if code == 'provider-insufficient-balance' else
                'model' if code == 'returned-model-not-pro' else
                'operation-window' if code in operation_codes or cause in operation_codes else
                'timeout' if code in timeout_codes or cause in timeout_codes else 'other')
    return {'status': status, 'reference_period': reference, 'target_period': target,
            'updated_at': updated.isoformat(), 'deadline': period_slot(target).isoformat(),
            'category': category}


def _job_text(job, reference, now):
    if not parse_period(reference):
        return 'AI运行状态：等待开奖数据同步。'
    if job is None:
        return 'AI运行状态：本期状态暂不可用，等待服务器更新。'
    if job['reference_period'] != reference:
        return 'AI运行状态：等待本期任务启动；上一轮参考期：' + job['reference_period'] + '。'
    updated, deadline = _generated(job['updated_at']), _generated(job['deadline'])
    if updated > now:
        return 'AI运行状态：时间尚未核对，等待服务器更新。'
    if now >= deadline:
        return 'AI运行状态：本轮已截止，等待下一期任务；历史号码请核对目标期。'
    target, status = job['target_period'], job['status']
    if status == 'requesting':
        return 'AI运行状态：正在生成，目标期：%s（已用时%d秒）；下方号码请核对目标期。' % (target, max(0, int((now-updated).total_seconds())))
    if status == 'waiting':
        return 'AI运行状态：等待本期真实数据校验，目标期：' + target + '。'
    if status in ('published', 'preserved'):
        return 'AI运行状态：本轮结果已完成，目标期：' + target + '；请核对号码框架的目标期。'
    reason = {'balance': 'API余额不足，充值后后续任务会自动继续',
              'model': '返回模型不匹配，本轮未采用',
              'timeout': '本轮超时，结果未发布', 'operation-window': '操作时间不足，本轮未采用',
              'other': '本轮调用或发布核验未通过'}[job['category']]
    return 'AI运行状态：' + reason + '；等待下一期任务。'


def _coverage_summary(raw):
    if not isinstance(raw, dict) or raw.get('rule_revision') != REVISION or raw.get('coverage_error') is not False:
        return None
    try:
        day = raw.get('biz_date')
        if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
            return None
    except (ValueError, TypeError):
        return None
    names = ('attempted_count', 'published_count', 'in_progress_count', 'unpublished_count',
             'closed_unpublished_count', 'failed_before_deadline_count', 'completed_count')
    counts = {key: _count(raw.get(key)) for key in names}
    failures = raw.get('failure_counts')
    failures = {key: _count(failures.get(key)) for key in ('timeout', 'balance', 'other', 'unknown')} if isinstance(failures, dict) else {}
    operation_count = _count(raw.get('operation_window_count', 0))
    if (len(failures) != 4 or any(value is None for value in (*counts.values(), *failures.values()))
            or counts['attempted_count'] != counts['published_count'] + counts['in_progress_count'] + counts['unpublished_count']
            or counts['completed_count'] != counts['published_count'] + counts['unpublished_count']
            or counts['unpublished_count'] != counts['closed_unpublished_count'] + counts['failed_before_deadline_count']
            or sum(failures.values()) != counts['unpublished_count']
            or operation_count is None or operation_count > failures['other']):
        return None
    result = {'rule_revision': REVISION, 'biz_date': day, **counts, 'failure_counts': failures}
    if 'operation_window_count' in raw:
        result['operation_window_count'] = operation_count
    return result


def _coverage_text(data, kind):
    if data is None:
        return '出号情况核验暂不可用，等待服务器更新。'
    if kind == 'main':
        return '任务尝试：%d期 ｜ 已事前发布：%d期 ｜ 生成中：%d期 ｜ 未发布：%d期' % tuple(
            data[key] for key in ('attempted_count', 'published_count', 'in_progress_count', 'unpublished_count'))
    if kind == 'rate':
        n, p = data['completed_count'], data['published_count']
        return ('已完成任务发布率：%.2f%%（%d/%d期）；生成中不计入分母。' % (100*p/n, p, n)
                if n else '已完成任务发布率：—（暂无已完成任务）')
    failures = data['failure_counts']
    operation_count = data.get('operation_window_count', 0)
    operation_text = '（操作时间不足%d期）' % operation_count if operation_count else ''
    return '未发布原因：超时%d期 ｜ 余额不足%d期 ｜ 其他%d期%s ｜ 原因未记录%d期' % (
        failures['timeout'], failures['balance'], failures['other'], operation_text, failures['unknown'])


def _market_text(reference, now):
    """User-provided platform cutoff; a reminder, never a publication gate."""
    try:
        reference = _reference(reference)
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError('invalid-market-clock')
        now = now.astimezone(TZ)
        target = next_period(reference)
        close = period_slot(target) - timedelta(seconds=70)
        if now < period_slot(reference):
            return '平台关盘信息：时间尚未核对，等待页面更新。'
        state = ('距关盘%d秒' % max(0, ceil((close-now).total_seconds())) if now < close
                 else '已关盘，结果仅供核对')
        return ('平台关盘：目标期：%s ｜ 北京时间%s ｜ %s；本期发布时间等待页面核验。' %
                (target, close.strftime('%Y-%m-%d %H:%M:%S'), state))
    except (ValueError, TypeError, KeyError, OverflowError):
        return '平台关盘信息：等待本期参考期和时间核对。'


SCRIPT = """<script id="selected6-control">
(function(){
  var version='selected-seven-view-v12-operation-window',rule='selected-seven-v6-three-positions',keys=['position_q','position_b','position_s','position_g','tail_all','tail_qbs','tail_qbg','tail_qsg','tail_bsg','he2_keep','he3_keep'];
  function put(node,text){if(node&&node.textContent!==text)node.textContent=text;}
  function attr(node,key,value){if(node&&node.getAttribute(key)!==value)node.setAttribute(key,value);}
  function dateAttr(node,value){if(value)attr(node,'data-stats-business-date',value);else if(node&&node.hasAttribute('data-stats-business-date'))node.removeAttribute('data-stats-business-date');}
  var colorValues={},colorProbe=document.createElement('i');
  function color(node,key,value){var token=key+value;if(!colorValues[token]){colorProbe.style[key]=value;colorValues[token]=colorProbe.style[key];}if(node&&node.style[key]!==colorValues[token])node.style[key]=value;}
  function compact(){
    var cards=document.querySelectorAll('[id="selected6-card"]'),summaries=document.querySelectorAll('[id="selected6-compact-summary"]');
    if(cards.length!==1||summaries.length!==1)return;
    var card=cards[0],summary=summaries[0],badge=card.querySelector('#selected6-validity');
    // Presentation only. Canonical saved card bytes and every number stay intact.
    if(card.getAttribute('data-selected6-revision')===rule){
      if(summary.parentNode!==card&&badge)badge.insertAdjacentElement('afterend',summary);
      attr(card,'data-selected6-compact','true');
      var rows=card.querySelector('[data-selected6-row]');if(rows)attr(rows.parentNode,'data-selected6-grid','true');
    }else{
      attr(card,'data-selected6-compact','false');
      var notice=document.getElementById('selected6-stats-notice');if(notice&&summary.parentNode!==notice)notice.insertBefore(summary,notice.firstChild);
    }
  }
  function day(){var now=Date.now();return Number.isFinite(now)?new Date(now+3*3600000).toISOString().slice(0,10):null;}

  function identity(ref){
    if(typeof ref!=='string'||!/^[0-9]{11}$/.test(ref))return null;
    var year=+ref.slice(0,4),month=+ref.slice(4,6),date=+ref.slice(6,8),serial=+ref.slice(8),d=new Date(Date.UTC(year,month-1,date));
    if(year<100||d.getUTCFullYear()!==year||d.getUTCMonth()!==month-1||d.getUTCDate()!==date||serial<1||serial>288)return null;
    var next=serial===288?(new Date(d.getTime()+86400000).toISOString().slice(0,10).replace(/-/g,'')+'001'):ref.slice(0,8)+String(serial+1).padStart(3,'0');
    return {target:next,deadline:d.getTime()-8*3600000+(serial+1)*300000};
  }
  function metric(x){
    if(!x||typeof x!=='object'||Array.isArray(x))return false;
    var names=['hits','total','max_win_streak','max_loss_streak','current_win_streak','current_loss_streak'];
    if(!names.every(function(k){return Number.isSafeInteger(x[k])&&x[k]>=0;})||typeof x.current_streak_interrupted!=='boolean')return false;
    return x.hits<=x.total&&x.max_win_streak<=x.hits&&x.max_loss_streak<=x.total-x.hits&&!!x.max_win_streak===!!x.hits&&!!x.max_loss_streak===!!(x.total-x.hits)&&x.current_win_streak<=x.max_win_streak&&x.current_loss_streak<=x.max_loss_streak&&!(x.current_win_streak&&x.current_loss_streak)&&(!x.total||x.current_win_streak||x.current_loss_streak||x.current_streak_interrupted)&&(!x.current_streak_interrupted||(!x.current_win_streak&&!x.current_loss_streak));
  }
  function normalized(d){
    if(!d||typeof d!=='object'||Array.isArray(d)||d.rule_revision!==rule||d.stats_error!==false||!metric(d.overall)||!d.metrics||typeof d.biz_date!=='string'||!/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(d.biz_date))return false;
    if(!['pending_count','overdue_count','conflict_count'].every(function(k){return Number.isSafeInteger(d[k])&&d[k]>=0;})||d.overdue_count>d.pending_count)return false;
    if(!keys.every(function(k){return metric(d.metrics[k])&&d.metrics[k].total<=d.overall.total&&d.metrics[k].total-d.metrics[k].hits<=d.overall.total-d.overall.hits;}))return false;
    var n=d.overall.total,h=d.overall.hits;
    return d.metrics.he2_keep.total===n&&d.metrics.he3_keep.total===n&&d.metrics.he2_keep.hits>=h&&d.metrics.he3_keep.hits>=h&&keys.slice(0,4).reduce(function(v,k){return v+d.metrics[k].total;},0)===3*n&&keys.slice(4,9).reduce(function(v,k){return v+d.metrics[k].total;},0)===2*n;
  }
  function currentCard(card){
    if(!card||card.getAttribute('data-selected6-revision')!==rule)return false;
    var nodes=Array.from(card.querySelectorAll('[data-selected6-number]')),names=nodes.map(function(node){return node.getAttribute('data-selected6-number');});
    return nodes.length===7&&new Set(names).size===7&&names.filter(function(k){return k.indexOf('position_')===0;}).length===3&&names.filter(function(k){return k.indexOf('tail_')===0;}).length===2&&names.indexOf('he2_keep')>=0&&names.indexOf('he3_keep')>=0&&nodes.every(function(node){
      var key=node.getAttribute('data-selected6-number'),value=node.textContent.trim();
      return keys.indexOf(key)>=0&&(key==='he2_keep'?/^[02468]{4}$/.test(value)&&new Set(value).size===4:key==='he3_keep'?/^[0-9]{5}$/.test(value)&&new Set(value).size===5:/^[0-9]$/.test(value));
    });
  }
  function jobStatus(){
    var labels=document.querySelectorAll('[id="selected6-job-status"]'),sources=document.querySelectorAll('[id="selected6-job-data"]');
    if(labels.length!==1)return;var label=labels[0],job=null,now=Date.now(),current=String((document.getElementById('disp_period')||{}).textContent||'').trim();
    function show(text){put(label,'AI运行状态：'+text);}
    if(!identity(current)){show('等待开奖数据同步。');return;}
    try{if(sources.length===1)job=JSON.parse(sources[0].textContent);}catch(e){job=null;}
    var expected=job&&identity(job.reference_period),updated=job&&Date.parse(job.updated_at);
    if(!job||!expected||job.target_period!==expected.target||Date.parse(job.deadline)!==expected.deadline||
      !Number.isFinite(updated)||updated<expected.deadline-300000||
      ['waiting','requesting','published','preserved','rejected','failed'].indexOf(job.status)<0||
      ['balance','model','timeout','other','operation-window'].indexOf(job.category)<0){show('本期状态暂不可用，等待服务器更新。');return;}
    if(job.reference_period!==current){show('等待本期任务启动；上一轮参考期：'+job.reference_period+'。');return;}
    if(!Number.isFinite(now)||updated>now){show('时间尚未核对，等待服务器更新。');return;}
    if(now>=expected.deadline){show('本轮已截止，等待下一期任务；历史号码请核对目标期。');return;}
    if(job.status==='requesting'){show('正在生成，目标期：'+job.target_period+'（已用时'+Math.max(0,Math.floor((now-updated)/1000))+'秒）；下方号码请核对目标期。');return;}
    if(job.status==='waiting'){show('等待本期真实数据校验，目标期：'+job.target_period+'。');return;}
    if(job.status==='published'||job.status==='preserved'){show('本轮结果已完成，目标期：'+job.target_period+'；请核对号码框架的目标期。');return;}
    show(({balance:'API余额不足，充值后后续任务会自动继续',model:'返回模型不匹配，本轮未采用',timeout:'本轮超时，结果未发布','operation-window':'操作时间不足，本轮未采用',other:'本轮调用或发布核验未通过'})[job.category]+'；等待下一期任务。');
  }
  function coverageCheck(){
    var sources=document.querySelectorAll('[id="selected6-coverage-data"]'),data=null;
    try{if(sources.length===1)data=JSON.parse(sources[0].textContent);}catch(e){data=null;}
    var names=['attempted_count','published_count','in_progress_count','unpublished_count','closed_unpublished_count','failed_before_deadline_count','completed_count'];
    var f=data&&data.failure_counts,operationCount=data&&Object.prototype.hasOwnProperty.call(data,'operation_window_count')?data.operation_window_count:0;
    var good=!!data&&data.rule_revision===rule&&typeof data.biz_date==='string'&&/^[0-9]{4}-[0-9]{2}-[0-9]{2}$/.test(data.biz_date)&&
      names.every(function(k){return Number.isSafeInteger(data[k])&&data[k]>=0;})&&!!f&&
      ['timeout','balance','other','unknown'].every(function(k){return Number.isSafeInteger(f[k])&&f[k]>=0;})&&
      data.attempted_count===data.published_count+data.in_progress_count+data.unpublished_count&&
      data.completed_count===data.published_count+data.unpublished_count&&
      data.unpublished_count===data.closed_unpublished_count+data.failed_before_deadline_count&&
      f.timeout+f.balance+f.other+f.unknown===data.unpublished_count&&
      Number.isSafeInteger(operationCount)&&operationCount>=0&&operationCount<=f.other;
    var today=good&&data.biz_date===day(),texts=good?{
      main:'任务尝试：'+data.attempted_count+'期 ｜ 已事前发布：'+data.published_count+'期 ｜ 生成中：'+data.in_progress_count+'期 ｜ 未发布：'+data.unpublished_count+'期',
      rate:data.completed_count?'已完成任务发布率：'+(100*data.published_count/data.completed_count).toFixed(2)+'%（'+data.published_count+'/'+data.completed_count+'期）；生成中不计入分母。':'已完成任务发布率：—（暂无已完成任务）',
      failures:'未发布原因：超时'+f.timeout+'期 ｜ 余额不足'+f.balance+'期 ｜ 其他'+f.other+'期'+(operationCount?'（操作时间不足'+operationCount+'期）':'')+' ｜ 原因未记录'+f.unknown+'期'}:{};
    ['main','rate','failures'].forEach(function(k){var nodes=document.querySelectorAll('[id="selected6-coverage-'+k+'"]');if(nodes.length===1){put(nodes[0],!good?'出号情况核验暂不可用，等待服务器更新。':!today?'北京时间05:00已切换，等待本周期出号情况':texts[k]);dateAttr(nodes[0],good?data.biz_date:null);}});
  }
  function marketStatus(){
    var labels=document.querySelectorAll('[id="selected6-market-status"]');
    if(labels.length!==1){labels.forEach(function(node){put(node,'平台关盘信息：页面项目重复，暂不能核验本期。');});return;}
    var label=labels[0],now=Date.now(),periods=document.querySelectorAll('[id="disp_period"]');
    var current=periods.length===1?String(periods[0].textContent||'').trim():'',expected=identity(current);
    function one(id){var nodes=document.querySelectorAll('[id="'+id+'"]');return nodes.length===1?nodes[0]:null;}
    function aware(value){
      if(typeof value!=='string'||!/^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$/.test(value))return NaN;
      var suffix=value.match(/([+-])([0-9]{2}):([0-9]{2})$/),offset=suffix?(+suffix[2]*60+(+suffix[3]))*(suffix[1]==='+'?1:-1):0,stamp=Date.parse(value);
      if(suffix&&(+suffix[2]>23||+suffix[3]>59)||!Number.isFinite(stamp))return NaN;
      return new Date(stamp+offset*60000).toISOString().slice(0,19)===value.slice(0,19)?stamp:NaN;
    }
    if(!expected){put(label,'平台关盘信息：等待本期参考期和时间核对。');return;}
    if(!Number.isFinite(now)||now<expected.deadline-300000){put(label,'平台关盘信息：时间尚未核对，等待页面更新。');return;}
    var close=expected.deadline-70000,closed=now>=close;
    var prefix='平台关盘：目标期：'+expected.target+' ｜ 北京时间'+new Date(close+8*3600000).toISOString().slice(0,19).replace('T',' ')+' ｜ '+
      (closed?'已关盘，结果仅供核对':'距关盘'+Math.max(0,Math.ceil((close-now)/1000))+'秒');
    var card=one('selected6-card'),source=one('selected6-attack-data'),box=one('selected6-attack-text'),data=null;
    try{if(source)data=JSON.parse(source.textContent);}catch(e){data=null;}
    var cardMatches=!!card&&card.getAttribute('data-selected6-revision')===rule&&card.getAttribute('data-selected6-reference')===current&&card.getAttribute('data-selected6-target')===expected.target&&
      aware(card.getAttribute('data-selected6-deadline'))===expected.deadline;
    var numbers=data&&data.numbers,names=numbers&&typeof numbers==='object'&&!Array.isArray(numbers)?Object.keys(numbers):[];
    var published=data&&aware(data.published_at),metadata=cardMatches&&!!data&&data.version===1&&data.rule_revision===rule&&data.reference_period===current&&data.target_period===expected.target&&
      aware(data.deadline)===expected.deadline&&Number.isFinite(published)&&published>=expected.deadline-300000&&published<expected.deadline&&published<=now&&
      !!box&&typeof data.attack==='string'&&data.attack.length>0&&data.attack.length<=512&&box.value===data.attack&&
      names.length===7&&names.every(function(k){return keys.indexOf(k)>=0&&typeof numbers[k]==='string'&&/^[0-9]+$/.test(numbers[k]);})&&
      names.filter(function(k){return k.indexOf('position_')===0&&numbers[k].length===1;}).length===3&&
      names.filter(function(k){return k.indexOf('tail_')===0&&numbers[k].length===1;}).length===2&&
      typeof numbers.he2_keep==='string'&&numbers.he2_keep.length===4&&new Set(numbers.he2_keep).size===4&&/^[02468]{4}$/.test(numbers.he2_keep)&&
      typeof numbers.he3_keep==='string'&&numbers.he3_keep.length===5&&new Set(numbers.he3_keep).size===5&&
      card.querySelectorAll('[data-selected6-number]').length===7&&names.every(function(k){var nodes=card.querySelectorAll('[data-selected6-number="'+k+'"]');return nodes.length===1&&nodes[0].textContent.trim()===numbers[k];});
    var detail=!cardMatches?'等待本期七项规则结果；下方旧号码保留供核对':!metadata?'本期发布时间尚未核验，请核对号码目标期':
      published<close?'本期已在关盘前发布（北京时间'+new Date(published+8*3600000).toISOString().slice(11,19)+'）':
      '本期结果晚于关盘发布（北京时间'+new Date(published+8*3600000).toISOString().slice(11,19)+'），仅供核对';
    put(label,prefix+' ｜ '+detail+'。');
    color(label,'background',closed?'#fef2f2':metadata&&published<close?'#ecfdf5':'#fff7ed');
    color(label,'color',closed?'#991b1b':metadata&&published<close?'#166534':'#92400e');
  }
  function check(){
    compact();
    jobStatus();
    coverageCheck();
    marketStatus();
    var sources=document.querySelectorAll('[id="selected6-stats-data"]'),data=null;
    if(sources.length===1){try{data=JSON.parse(sources[0].textContent);}catch(e){data=null;}}
    var good=normalized(data),today=good&&data.biz_date===day();
    function rate(item,prefix){return !good?'统计核验失败，暂停显示':!today?'北京时间05:00已切换，等待本周期数据':!item.total?'暂无已验证数据':prefix+' '+(100*item.hits/item.total).toFixed(2)+'%（'+item.hits+'/'+item.total+'期）';}
    function streak(item){
      if(!good)return '统计核验失败，暂停显示';if(!today)return '北京时间05:00已切换，等待本周期数据';
      return maximum(item)+' ｜ '+currentCounts(item);
    }
    function maximum(item){
      if(!good)return '统计核验失败，暂停显示';if(!today)return '北京时间05:00已切换，等待本周期数据';
      return item.total?'最高连中：'+item.max_win_streak+'手 ｜ 最高连败：'+item.max_loss_streak+'手':'最高连中：— ｜ 最高连败：—';
    }
    function currentCounts(item){
      if(!good)return '统计核验失败，暂停显示';if(!today)return '北京时间05:00已切换，等待本周期数据';
      if(!item.total)return '当前连中：— ｜ 当前连败：—（暂无样本）';
      return '当前连中：'+item.current_win_streak+'手 ｜ 当前连败：'+item.current_loss_streak+'手'+(item.current_streak_interrupted?'（连续记录已中断）':'');
    }
    document.querySelectorAll('[data-selected6-rate-key]').forEach(function(node){
      var owner=node.closest('#selected6-card');
      if(owner&&owner.getAttribute('data-selected6-revision')!==rule){put(node,'旧策略历史结果；三定位七项规则独立统计');return;}
      var key=node.getAttribute('data-selected6-rate-key'),item=good&&data.metrics[key];
      if(keys.indexOf(key)<0){put(node,'统计项目不匹配，暂停显示');return;}
      put(node,rate(item,key==='he2_keep'||key==='he3_keep'?'命中率':'排除成功率')+(!owner&&good&&today?' ｜ '+streak(item):''));
      dateAttr(node,good?data.biz_date:null);
    });
    var overall=document.getElementById('selected6-overall-rate'),runs=document.getElementById('selected6-overall-streaks');
    if(overall){put(overall,rate(good&&data.overall,'整组7项全部命中率'));dateAttr(overall,good?data.biz_date:null);}
    var maxNode=document.getElementById('selected6-overall-max'),currentNode=document.getElementById('selected6-overall-current');
    if(maxNode&&currentNode){put(maxNode,maximum(good&&data.overall));put(currentNode,currentCounts(good&&data.overall));dateAttr(maxNode,good?data.biz_date:null);dateAttr(currentNode,good?data.biz_date:null);}
    else if(runs)put(runs,streak(good&&data.overall));
    var cards=document.querySelectorAll('[id="selected6-card"]'),status=document.getElementById('selected6-validity');
    if(status){
      var card=cards.length===1?cards[0]:null,current=String((document.getElementById('disp_period')||{}).textContent||'').trim();
      var ref=card?card.getAttribute('data-selected6-reference'):'',target=card?card.getAttribute('data-selected6-target'):'',deadline=card?Date.parse(card.getAttribute('data-selected6-deadline')||''):NaN;
      var expected=identity(ref),currentRule=currentCard(card),valid=currentRule&&!!expected&&expected.target===target&&ref===current&&Number.isFinite(deadline)&&deadline===expected.deadline&&Number.isFinite(Date.now())&&Date.now()<deadline;
      put(status,valid?'已封存 · 目标期 '+target:!currentRule?'旧策略历史 · 目标期 '+target:'历史参考 · 目标期 '+target+'（等待本期更新）');
      color(status,'background',valid?'#dcfce7':'#fef3c7');color(status,'color',valid?'#166534':'#92400e');
      if(card)attr(card,'data-selected6-current',valid?'true':'false');
    }
  }
  var old=window.__PENTA5_SELECTED_SIX__;
  if(old){if(old.timer!=null)clearInterval(old.timer);if(typeof old.check==='function'){window.removeEventListener('pageshow',old.check);document.removeEventListener('visibilitychange',old.check);document.removeEventListener('penta5:panel-updated',old.check);}}
  var state={version:version,check:check,timer:null};window.__PENTA5_SELECTED_SIX__=state;
  window.addEventListener('pageshow',check);document.addEventListener('visibilitychange',check);document.addEventListener('penta5:panel-updated',check);state.timer=setInterval(check,1000);check();
})();
</script>"""



def _previous_six(raw):
    old = raw.get('previous_six') if isinstance(raw, dict) else None
    if not isinstance(old, dict) or old.get('rule_revision') != FIVE_REVISION or old.get('stats_error') is not False:
        return '<details><summary>旧六项规则历史战绩</summary>历史统计暂未通过核验。</details>'
    daily, cumulative = _metric(old.get('overall')), _metric(old.get('cumulative_overall'))
    day = old.get('biz_date')
    try:
        valid_day = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    except (ValueError, TypeError):
        valid_day = False
    if daily is None or cumulative is None or not valid_day or daily['total'] > cumulative['total'] or daily['hits'] > cumulative['hits']:
        return '<details><summary>旧六项规则历史战绩</summary>历史统计暂未通过核验。</details>'
    return ('<details id="selected6-previous-six"><summary>旧六项规则历史战绩（独立保留）</summary>'
            '<div data-stats-business-date="' + escape(day, quote=True) + '">'
            + escape(_rate(daily, False, '旧六项今日全部通过')) + ' ｜ ' + escape(_streak(daily, False)) + '</div>'
            '<div>' + escape(_rate(cumulative, False, '旧六项累计全部通过')) + ' ｜ ' + escape(_streak(cumulative, False)) + '</div>'
            '<div style="font-size:12px;color:#64748b;">旧六项成绩不计入新七项；新规则从事前发布后开始累计。</div></details>')

def _previous_seven(raw):
    old = raw.get('previous_seven') if isinstance(raw, dict) else None
    if not isinstance(old, dict) or old.get('rule_revision') != SEVEN_REVISION or old.get('stats_error') is not False:
        return '<details><summary>旧七项自选策略规则历史战绩</summary>历史统计暂未通过核验。</details>'
    daily, cumulative = _metric(old.get('overall')), _metric(old.get('cumulative_overall'))
    day = old.get('biz_date')
    try:
        valid_day = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    except (ValueError, TypeError):
        valid_day = False
    if daily is None or cumulative is None or not valid_day or daily['total'] > cumulative['total'] or daily['hits'] > cumulative['hits']:
        return '<details><summary>旧七项自选策略规则历史战绩</summary>历史统计暂未通过核验。</details>'
    return ('<details id="selected6-previous-seven"><summary>旧七项自选策略规则历史战绩（独立保留）</summary>'
            '<div data-stats-business-date="' + escape(day, quote=True) + '">'
            + escape(_rate(daily, False, '旧七项自选策略今日全部通过')) + ' ｜ ' + escape(_streak(daily, False)) + '</div>'
            '<div>' + escape(_rate(cumulative, False, '旧七项自选策略累计全部通过')) + ' ｜ ' + escape(_streak(cumulative, False)) + '</div>'
            '<div style="font-size:12px;color:#64748b;">旧七项自选策略成绩不计入三定位七项规则；新规则从事前发布后开始累计。</div></details>')


def _historical_rules(raw):
    """Keep previous sealed cohorts visible without activating their cards."""
    source = raw.get('historical_rules') if isinstance(raw, dict) else None
    source = source if isinstance(source, dict) else {}
    blocks = []
    for revision in sorted(SUPPORTED_REVISIONS - {REVISION, FIVE_REVISION, SEVEN_REVISION}):
        old = source.get(revision)
        label = ('旧独立七项策略' if revision == ISOLATED_REVISION else
                 '旧六项初版规则' if revision == LEGACY_REVISION else '旧七项重叠策略')
        daily = _metric(old.get('overall')) if isinstance(old, dict) else None
        cumulative = _metric(old.get('cumulative_overall')) if isinstance(old, dict) else None
        valid = (isinstance(old, dict) and old.get('rule_revision') == revision
                 and old.get('stats_error') is False and daily is not None and cumulative is not None
                 and daily['total'] <= cumulative['total'] and daily['hits'] <= cumulative['hits'])
        body = ('<div>' + escape(_rate(daily, False, label + '今日全部通过')) + ' ｜ '
                + escape(_streak(daily, False)) + '</div><div>'
                + escape(_rate(cumulative, False, label + '累计全部通过')) + ' ｜ '
                + escape(_streak(cumulative, False)) + '</div>' if valid else '历史统计暂未通过核验。')
        blocks.append('<details data-selected6-historical-rule="' + escape(revision, quote=True)
                      + '"><summary>' + label + '历史战绩（独立保留）</summary>' + body
                      + '<div style="font-size:12px;color:#64748b;">旧规则成绩不计入三定位七项规则。</div></details>')
    return ''.join(blocks)


def render_stats_block(summary, job_status=None, *, reference=None, now=None, coverage=None):
    data = _summary(summary)
    error = data['stats_error']
    job = _job_summary(job_status)
    now = now if isinstance(now, datetime) and now.tzinfo is not None else beijing_now()
    job_encoded = json.dumps(job, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    coverage = _coverage_summary(coverage)
    coverage_encoded = json.dumps(coverage, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, new in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        encoded = encoded.replace(old, new)
        job_encoded = job_encoded.replace(old, new)
        coverage_encoded = coverage_encoded.replace(old, new)
    rows = ''.join('<div style="margin:5px 0;"><b>' + escape(METRIC_LABELS[key]) + '：</b>'
                   '<span data-selected6-rate-key="' + key + '" data-stats-business-date="' + escape(data['biz_date'], quote=True) + '">'
                   + escape(_rate(data['metrics'][key], error, '命中率' if key in ('he2_keep', 'he3_keep') else '排除成功率'))
                   + ' ｜ ' + escape(_streak(data['metrics'][key], error)) + '</span></div>' for key in BASE_METRIC_KEYS)
    return (STATS_START + '\n<style id="selected6-compact-style">'
            '#selected6-card[data-selected6-compact="true"]{padding:14px!important;line-height:1.55!important;}'
            '#selected6-card[data-selected6-compact="true"]>h3{font-size:18px!important;}'
            '#selected6-card[data-selected6-compact="true"]>div:nth-last-child(-n+2){display:none;}'
            '#selected6-card [data-selected6-grid="true"]{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:8px!important;}'
            '#selected6-card[data-selected6-compact="true"] [data-selected6-row]{padding:8px!important;font-size:13px;line-height:1.55;}'
            '#selected6-card[data-selected6-compact="true"] [data-selected6-row^="extra_"]{grid-column:1/-1;}'
            '#selected6-card[data-selected6-compact="true"] [data-selected6-number]{font-size:21px!important;letter-spacing:1px!important;font-variant-numeric:tabular-nums;}'
            '#selected6-card[data-selected6-compact="true"] [data-selected6-rate-key]{font-size:12px!important;min-height:2.8em;}'
            '#selected6-validity{font-variant-numeric:tabular-nums;min-height:2.4em;box-sizing:border-box;}'
            '#selected6-compact-summary{margin:8px 0;padding:10px;border:1px solid #c7d2fe;border-radius:8px;background:#eef2ff;line-height:1.7;font-variant-numeric:tabular-nums;}'
            '#selected6-overall-rate{font-size:17px;font-weight:700;color:#3730a3;min-height:1.7em;}'
            '#selected6-overall-max{font-size:13px;min-height:1.7em;}'
            '#selected6-overall-current{font-size:16px;font-weight:700;color:#312e81;min-height:1.7em;}'
            '#selected6-market-status{font-variant-numeric:tabular-nums;min-height:4.2em;font-size:12px;}'
            '#selected6-stats-notice details>summary{cursor:pointer;font-weight:600;}'
            '#selected6-stats-notice{padding:12px 14px!important;margin-top:8px!important;}'
            '@media(min-width:680px){#selected6-card[data-selected6-compact="true"] [data-selected6-row]{font-size:14px;}#selected6-card[data-selected6-compact="true"] [data-selected6-rate-key]{min-height:1.7em;}}'
            '</style>\n<section id="selected6-stats-notice" style="' + STYLE + 'border-width:1px;background:#f8faff;">'
            '<div id="selected6-compact-summary" data-selected6-derived="true" data-penta5-live-derived="1" aria-label="整组七项命中统计">'
            '<div style="font-size:12px;color:#64748b;">今日整组战绩 · ' + escape(data['biz_date']) + ' 05:00起 · 三定位七项规则</div>'
            '<div id="selected6-overall-rate" data-stats-business-date="' + escape(data['biz_date'], quote=True) + '">'
            + escape(_rate(data['overall'], error, '整组7项全部命中率')) + '</div>'
            '<div id="selected6-overall-streaks">'
            '<div id="selected6-overall-max" data-stats-business-date="' + escape(data['biz_date'], quote=True) + '">' + escape(_max_streak(data['overall'], error)) + '</div>'
            '<div id="selected6-overall-current" data-stats-business-date="' + escape(data['biz_date'], quote=True) + '">'
            + escape(_current_streak(data['overall'], error)) + '</div></div></div>'
            + _previous_seven(summary)
            + _previous_six(summary)
            + _historical_rules(summary)
            + render_observation(summary)
            + '<details id="selected6-operation-details"><summary>运行情况与核验明细</summary>'
            '<div id="selected6-job-status" role="status" aria-live="polite" style="margin:8px 0;padding:8px 10px;'
            'border:1px solid #c7d2fe;border-radius:6px;background:#eef2ff;color:#3730a3;overflow-wrap:anywhere;">'
            + escape(_job_text(job, reference, now)) + '</div>'
            '<div id="selected6-market-status" role="status" style="margin:8px 0;padding:8px 10px;'
            'border:1px solid #fed7aa;border-radius:6px;background:#fff7ed;color:#92400e;overflow-wrap:anywhere;">'
            + escape(_market_text(reference, now)) + '</div>'
            '<div style="margin:8px 0;padding:8px 10px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;overflow-wrap:anywhere;">'
            '<b>三定位七项规则今日出号情况（与预测命中率分开）</b>'
            + ''.join('<div id="selected6-coverage-' + kind + '" data-stats-business-date="' + escape(coverage['biz_date'] if coverage else '--', quote=True) + '">' + escape(_coverage_text(coverage, kind)) + '</div>' for kind in ('main', 'rate', 'failures'))
            + '<div style="font-size:12px;color:#64748b;">只核算真实任务及有效事前发布；未发布不算预测命中，正在生成不算失败。历史原因未记录时不补猜。</div></div>'
            '<div style="font-size:12px;color:#64748b;">最近核验期：' + escape(data['latest_settled_period'])
            + ' ｜ 待核验：' + (str(data['pending_count']) if data['pending_count'] is not None else '—')
            + '期 ｜ 超期：' + (str(data['overdue_count']) if data['overdue_count'] is not None else '—')
            + '期 ｜ 冲突：' + (str(data['conflict_count']) if data['conflict_count'] is not None else '—') + '期</div></details>'
            '<details id="selected6-metric-details" style="margin-top:6px;"><summary>各项命中率、连中连败</summary>' + rows + '</details>'
            '<details id="selected6-method-details" style="margin-top:6px;"><summary>统计规则</summary>'
            '<div style="font-size:12px;color:#64748b;">按实际开奖时间划分05:00统计日；整组须7项同时通过。'
            '各位置、合尾只累计该类型实际被选中的期数；未选择、缺预测或冲突会打断连续记录，'
            '不补造胜负。三定位、两合尾及两个保留集固定为7项，无额外项目；零样本不显示百分比。三定位七项规则从新版本事前发布后独立统计，旧规则历史分别保留。只读本期50期输入，不读取其他模型输出，也不为不同而强制换号。历史证据保留，不能据历史成绩保证下一期命中。</div></details>'
            '</section>\n<script type="application/json" id="selected6-stats-data">' + encoded + '</script>\n'
            '<script type="application/json" id="selected6-job-data">' + job_encoded + '</script>\n'
            '<script type="application/json" id="selected6-coverage-data">' + coverage_encoded + '</script>\n'
            + SCRIPT + '\n' + STATS_END)


def _inspect(html):
    if not isinstance(html, str):
        raise TypeError('selected-framework-panel-must-be-text')
    result = {}
    for marker in (*PROTECTED, MARKER, ATTACK_MARKER, STATS_MARKER):
        start, end = '<!-- ' + marker + '_START -->', '<!-- ' + marker + '_END -->'
        counts = html.count(start), html.count(end)
        if counts == (0, 0):
            result[marker] = None
            continue
        if counts != (1, 1):
            raise ValueError('selected-framework-ambiguous-panel-markers')
        left, right = html.index(start), html.index(end) + len(end)
        if left >= right - len(end):
            raise ValueError('selected-framework-reversed-panel-markers')
        result[marker] = left, right, html[left:right]
    ordered = sorted(value[:2] for value in result.values() if value is not None)
    if any(left < last_end for (_, last_end), (left, _) in zip(ordered, ordered[1:])):
        raise ValueError('selected-framework-overlapping-panel-markers')
    return result


def _protected(found):
    return {key: found[key][2] if found[key] else None for key in PROTECTED}


def _move_stats(html):
    found = _inspect(html); card = found[MARKER]
    blocks = [found[key] for key in (ATTACK_MARKER, STATS_MARKER) if found[key] is not None]
    if not blocks:
        return html
    if card is None:
        raise ValueError('selected-framework-stats-require-card')
    edges = [card] + blocks
    if all(right[0] >= left[1] and not html[left[1]:right[0]].strip()
           for left, right in zip(edges, edges[1:])):
        return html
    clean = html
    for block in sorted(blocks, reverse=True):
        clean = clean[:block[0]] + clean[block[1]:]
    position = _inspect(clean)[MARKER][1]
    return clean[:position] + '\n' + '\n'.join(block[2] for block in blocks) + '\n' + clean[position:]


def compose_selected_transform(html, transform):
    from model_retirement import is_retired,strip_selected7_panel
    if is_retired('selected7'):return strip_selected7_panel(html)
    before = _inspect(html)
    if not callable(transform):
        raise TypeError('selected-framework-transform-must-be-callable')
    changed = transform(html); after = _inspect(changed)
    if _protected(before) != _protected(after):
        raise ValueError('selected-framework-publication-changed-other-block')
    if (before[STATS_MARKER][2] if before[STATS_MARKER] else None) != (after[STATS_MARKER][2] if after[STATS_MARKER] else None):
        raise ValueError('selected-framework-publication-changed-statistics')
    if after[MARKER] is None:
        raise ValueError('selected-framework-publication-did-not-create-card')
    prior_attack = before[ATTACK_MARKER][2] if before[ATTACK_MARKER] else None
    next_attack = after[ATTACK_MARKER][2] if after[ATTACK_MARKER] else None
    if prior_attack != next_attack:
        raise ValueError('selected-framework-publication-changed-attack')
    # A new saved card must not briefly inherit the previous target's attack.
    if before[MARKER] is None or before[MARKER][2] != after[MARKER][2]:
        attack = after[ATTACK_MARKER]
        if attack:
            changed = changed[:attack[0]] + changed[attack[1]:]
    return _move_stats(changed)


def _fragment(fragment, marker):
    if not isinstance(fragment, str):
        raise TypeError('selected-framework-fragment-must-be-text')
    fragment = fragment.strip(); found = _inspect(fragment)[marker]
    if found is None or found[:2] != (0, len(fragment)):
        raise ValueError('selected-framework-invalid-owned-fragment')
    return fragment


def apply_selected_block(html, fragment):
    fragment = _fragment(fragment, MARKER)
    def transform(content):
        block = _inspect(content)[MARKER]
        if block:
            return content[:block[0]] + fragment + content[block[1]:]
        if content.count('</body>') != 1:
            raise ValueError('selected-framework-ambiguous-panel-body')
        return content.replace('</body>', fragment + '\n</body>', 1)
    return compose_selected_transform(html, transform)


def apply_selected_stats_block(html, fragment):
    from model_retirement import is_retired,strip_selected7_panel
    if is_retired('selected7'):return strip_selected7_panel(html)
    fragment = _fragment(fragment, STATS_MARKER); before = _inspect(html)
    if before[MARKER] is None:
        raise ValueError('selected-framework-stats-require-card')
    current = before[STATS_MARKER]
    if current:
        updated = html[:current[0]] + fragment + html[current[1]:]
    else:
        if html.count('</body>') != 1:
            raise ValueError('selected-framework-ambiguous-panel-body')
        updated = html.replace('</body>', fragment + '\n</body>', 1)
    updated = _move_stats(updated); after = _inspect(updated)
    if _protected(before) != _protected(after) or before[MARKER][2] != after[MARKER][2]:
        raise ValueError('selected-framework-statistics-changed-prediction')
    return updated


def _latest_retained(root, snapshot):
    from ai_prediction_guard import read_stats
    from selected_framework_model import validate_publication
    ledger = read_stats(Path(root) / 'data/selected_framework_seals.json')
    index = ledger.get('prediction_seals', {})
    if not isinstance(index, dict):
        return None
    reference, now = (snapshot.get('latest') or {}).get('period'), snapshot['now']
    candidates = sorted(((ref, seal) for ref, seal in index.items()
                         if isinstance(ref, str) and parse_period(ref)
                         and isinstance(seal, dict) and seal.get('publication_state') == 'published'
                         and (not reference or ref <= reference)), reverse=True)[:50]
    for ref, seal in candidates:
        try:
            validate_publication(seal, ref, now)
            return seal
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            continue
    return None


def _card_reference(fragment):
    """Read only the owned canonical section's reference, never reason text."""
    if not isinstance(fragment, str):
        return None
    headers = re.findall(r'<section\s+id="selected6-card"\s[^>]*>', fragment)
    if len(headers) != 1:
        return None
    values = re.findall(r'\sdata-selected6-reference="([^"]*)"', headers[0])
    try:
        return _reference(values[0]) if len(values) == 1 else None
    except (ValueError, TypeError):
        return None


def _apply_selected_refresh(html, fragment, attack, statistics):
    """The panel lock's fresh content wins over an older refresh snapshot.

    Prediction publication and statistics run independently. A refresh started
    before a new seal must not roll its saved card or matching attack backward
    after waiting for the shared panel lock.
    """
    found = _inspect(html)
    existing = _card_reference(found[MARKER][2]) if found[MARKER] else None
    chosen = _card_reference(fragment)
    if existing is not None and (chosen is None or existing > chosen):
        return apply_selected_stats_block(html, statistics)
    return apply_selected_stats_block(
        apply_selected_attack_block(apply_selected_block(html, fragment), attack), statistics)


def refresh_selected_attack_panel(root=ROOT, reference=None):
    """Publish only the current seal's matching attack, without historical stats.

    The model's normal validator and live fifty-draw check provide the proof.
    A rendered card is only compared byte-for-byte, never used to invent one.
    Heavy ledger decoding happens before taking the shared panel lock.
    """
    from model_retirement import is_retired,strip_selected7_panel
    if is_retired('selected7'):
        from panel_io import update_panel
        update_panel(strip_selected7_panel,path=Path(root)/'panel.html')
        return {'retired':True,'available':False,'updated':False,'stats_error':False,'latest_settled_period':None,'pending_count':0}
    from selected_framework_model import select_current, _source
    from scripts.run_ai_ready_job import expected_reference
    from panel_io import update_panel
    root = Path(root)
    initial = load_snapshot(state_path=root / 'data/state.json',
                            cache_path=root / 'data/stats_draw_history.json', persist=False)
    initial_now = initial.get('now')
    if not isinstance(initial_now, datetime) or initial_now.tzinfo is None:
        return {'reference': reference, 'available': False, 'updated': False, 'reason': 'invalid-display-clock'}
    expected = expected_reference(initial_now)
    reference = expected if reference is None else _reference(reference)
    if reference != expected:
        return {'reference': reference, 'available': False, 'updated': False, 'reason': 'reference-advanced'}
    proof = select_current(root, initial)
    if (not isinstance(proof, dict) or proof.get('publication_state') != 'published'
            or proof.get('revision') != REVISION or proof.get('ref_period') != reference):
        return {'reference': reference, 'available': False, 'updated': False, 'reason': 'no-current-published-proof'}
    attack = render_attack_block(proof)
    result = {'reference': reference, 'available': False, 'updated': False, 'reason': 'display-deferred'}

    def transform(html):
        live = load_snapshot(state_path=root / 'data/state.json',
                             cache_path=root / 'data/stats_draw_history.json', persist=False)
        try:
            # This preserves the actual source, consecutive-50, conflict,
            # reference and seventy-second closing gates without decoding the
            # large prediction ledger again while other publishers wait.
            if _source(reference, live, live['now']) != proof['history']:
                result['reason'] = 'source-evidence-changed'
                return html
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            result['reason'] = 'source-not-current'
            return html
        found = _inspect(html)
        existing = found[MARKER]
        if existing is None or existing[2] != proof['card_html']:
            # Includes a newer publication and the prepared/acknowledged race.
            # Never roll a raw card backwards or attach another target's text.
            result['reason'] = 'current-card-mismatch'
            return html
        updated = apply_selected_attack_block(html, attack)
        result.update(available=True, reason='current-attack-ready')
        return updated

    result['updated'] = bool(update_panel(transform, path=root / 'panel.html'))
    return result


def _coverage_file_identity(path):
    """Cheap identity for an atomic ledger/status replacement, not its content."""
    try:
        value=Path(path).stat()
        return (value.st_dev,value.st_ino,value.st_size,value.st_mtime_ns,value.st_ctime_ns)
    except OSError:
        return None


def refresh_selected_panel(snapshot=None, root=ROOT):
    from model_retirement import is_retired,strip_selected7_panel
    if is_retired('selected7'):
        from panel_io import update_panel
        update_panel(strip_selected7_panel,path=Path(root)/'panel.html')
        return {'retired':True,'available':False,'updated':False,'stats_error':False,'latest_settled_period':None,'pending_count':0}
    from selected_framework_model import select_current
    from selected_framework_stats import refresh_selected_stats
    from scripts.run_ai_ready_job import expected_reference
    from panel_io import update_panel
    root = Path(root)
    if snapshot is None:
        snapshot = load_snapshot(state_path=root / 'data/state.json',
                                 cache_path=root / 'data/stats_draw_history.json', persist=False)
    try:
        summary = refresh_selected_stats(snapshot=snapshot, root=root)
    except Exception:
        now = snapshot.get('now') if isinstance(snapshot.get('now'), datetime) else beijing_now()
        summary = {'biz_date': business_start(now).strftime('%Y-%m-%d'), 'stats_error': True,
                   'latest_settled_period': None, 'pending_count': 0, 'overdue_count': 0, 'conflict_count': 0}
    # Whole-history validation is CPU-heavy and does not need panel.lock. Bind
    # its frozen result to exact atomic source/status file identities; discard
    # only this diagnostic if a publisher changes either while we wait.
    coverage_path=root/'data/selected_framework_seals.json'
    status_path=root/'data/selected_framework_status.json'
    prepared_coverage=None; coverage_identity=status_identity=None; coverage_now=None
    source=saved_status=None
    try:
        from ai_prediction_guard import read_stats
        from selected_framework_coverage import make_coverage
        coverage_identity=_coverage_file_identity(coverage_path)
        status_identity=_coverage_file_identity(status_path)
        source=read_stats(coverage_path)
        saved_status=json.loads(status_path.read_text(encoding='utf-8')) if status_identity is not None else None
        coverage_now=beijing_now()
        prepared_coverage=make_coverage(source,coverage_now,status=saved_status)
        if (coverage_identity is None or coverage_identity!=_coverage_file_identity(coverage_path)
                or status_identity!=_coverage_file_identity(status_path)):
            prepared_coverage=None
    except (OSError,ValueError,TypeError,KeyError,OverflowError):
        prepared_coverage=None
    finally:
        source=saved_status=None
    result = {}

    def transform(html):
        # Heavy statistics keep the caller's frozen input. Display selection
        # instead runs after that work, under the same lock as publication.
        live = load_snapshot(state_path=root / 'data/state.json',
                             cache_path=root / 'data/stats_draw_history.json', persist=False)
        render_now = live['now']
        reference = expected_reference(render_now)
        try:
            current = select_current(root, live)
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            current = None
        try:
            retained = _latest_retained(root, {'latest': {'period': reference}, 'now': render_now})
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            retained = None
        proof = current
        if retained is not None and (proof is None or retained['ref_period'] > proof['ref_period']):
            proof = retained
        fragment = proof['card_html'] if proof is not None else _placeholder(reference)
        try:
            job_status = json.loads((root / 'data/selected_framework_status.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            job_status = None
        coverage=prepared_coverage
        if (coverage is None or coverage_now is None or render_now<coverage_now
                or expected_reference(coverage_now)!=reference
                or business_start(coverage_now)!=business_start(render_now)
                or coverage_identity!=_coverage_file_identity(coverage_path)
                or status_identity!=_coverage_file_identity(status_path)):
            coverage=None
        statistics = render_stats_block(summary, job_status, reference=reference, now=render_now, coverage=coverage)
        try:
            attack = render_attack_block(proof) if proof is not None else None
        except (ValueError, TypeError, KeyError, OverflowError):
            attack = None
        result.update(reference=reference, available=bool(proof is not None and proof.get('revision') == REVISION
                      and proof['ref_period'] == reference and render_now < period_slot(next_period(reference))),
                      retained_reference=proof.get('ref_period') if proof is not current and proof is not None else None)
        # The existing transform also protects a newer current card that was
        # committed immediately before its independent ledger acknowledgement.
        return _apply_selected_refresh(html, fragment, attack, statistics)

    update_panel(transform, path=root / 'panel.html')
    return {**summary, **result}
