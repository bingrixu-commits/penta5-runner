"""Independent AI-selected cold framework view; no provider calls or writes to evidence."""
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
import json
import math
import re

from cold_observation_view import _inspect
from stats_history import TZ, parse_period, period_slot

ROOT = Path('/root/penta5-runner')
LEGACY_REVISION = 'cold50-framework-v1-50-pos3-tail3'
FIVE_REVISION = 'cold50-framework-v2-50-pos3-tail2'
REVISION = 'cold50-framework-v3-50-pos3-tail2-he2x4-he3x5'
TAIL_COUNTS = {LEGACY_REVISION: 3, FIVE_REVISION: 2, REVISION: 2}
MARKER, ANCHOR = 'COLD50_FRAMEWORK', 'COLD_FRAMEWORK'
START, END = '<!-- COLD50_FRAMEWORK_START -->', '<!-- COLD50_FRAMEWORK_END -->'
POSITIONS = (('q', '千位', '千'), ('b', '百位', '百'), ('s', '十位', '十'), ('g', '个位', '个'))
TAILS = (('all', '千百十个合尾', '千百十个合'), ('qbs', '千百十合尾', '千百十合'),
         ('qbg', '千百个合尾', '千百个合'), ('qsg', '千十个合尾', '千十个合'),
         ('bsg', '百十个合尾', '百十个合'))
BASE_METRIC_KEYS = tuple('position_' + row[0] for row in POSITIONS) + tuple('tail_' + row[0] for row in TAILS)
KEEP_SPECS = (('he2_keep', '二数合', 4, '02468'), ('he3_keep', '三数合', 5, '0123456789'))
METRIC_KEYS = BASE_METRIC_KEYS + tuple(row[0] for row in KEEP_SPECS)
_OWN_ID = re.compile(r'\bid\s*=\s*[\'"]cold50-framework[\'"]', re.I)
_HASH = re.compile('[0-9a-f]{64}')


def _count(value, maximum=1000000000):
    return type(value) is int and 0 <= value <= maximum


def metric_keys(revision):
    return METRIC_KEYS if revision == REVISION else BASE_METRIC_KEYS


def _keep(value, count, allowed):
    return isinstance(value, str) and len(value) == count and len(set(value)) == count and set(value) <= set(allowed)


def _period(value):
    parsed = parse_period(value) if isinstance(value, str) else None
    return value if parsed and parsed[0] == value else None


def _stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return stamp.astimezone(TZ) if stamp.tzinfo is not None and stamp.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _header(summary):
    if not isinstance(summary, dict):
        return None
    beginning, updated = _stamp(summary.get('business_start')), _stamp(summary.get('updated_at'))
    day = summary.get('biz_date')
    try:
        day_valid = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
    except (ValueError, TypeError):
        day_valid = False
    if (type(summary.get('version')) is not int or summary['version'] != 1
            or not isinstance(summary.get('revision'), str)
            or summary['revision'] not in TAIL_COUNTS or not day_valid
            or type(summary.get('stats_error')) is not bool or beginning is None or updated is None
            or beginning.strftime('%Y-%m-%d') != day or beginning.time().isoformat() != '05:00:00'
            or not beginning <= updated < beginning + timedelta(days=1)):
        return None
    return {'biz_date': day, 'business_start': beginning.isoformat(), 'updated_at': updated.isoformat(),
            'revision': summary['revision'], 'tail_count': TAIL_COUNTS[summary['revision']],
            'item_count': 7 if summary['revision'] == REVISION else 3 + TAIL_COUNTS[summary['revision']]}


def _metric(raw):
    fields = ('settled_count', 'win_count', 'loss_count', 'max_win_streak', 'max_loss_streak',
              'current_win_streak', 'current_loss_streak', 'four_loss_cycles')
    if not isinstance(raw, dict) or any(not _count(raw.get(k)) for k in fields):
        return None
    n, w, l, mw, ml, cw, cl, cycles = (raw[k] for k in fields)
    broken, rate = raw.get('streak_broken'), raw.get('hit_rate')
    if (type(broken) is not bool or n != w + l or mw > w or ml > l
            or bool(mw) != bool(w) or bool(ml) != bool(l) or cw > mw or cl > ml or (cw and cl)
            or (broken and (cw or cl)) or (n and not cw and not cl and not broken)
            or cycles > l // 4 or cycles < ml // 4):
        return None
    if n:
        if (isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate)
                or rate != round(100 * w / n, 2)):
            return None
    elif rate is not None:
        return None
    return {**{k: raw[k] for k in fields}, 'hit_rate': rate, 'streak_broken': broken}


def _stats(summary, header):
    if header is None or summary.get('stats_error') is not False:
        return None
    overall = _metric(summary.get('overall'))
    raw_metrics = summary.get('metrics')
    keys = metric_keys(header['revision'])
    counts = ('pending_count', 'missing_count', 'conflict_count', 'rejected_count')
    if (overall is None or not isinstance(raw_metrics, dict) or set(raw_metrics) != set(keys)
            or any(not _count(summary.get(k)) for k in counts)):
        return None
    metrics = {k: _metric(raw_metrics[k]) for k in keys}
    if any(row is None for row in metrics.values()):
        return None
    n, losses = overall['settled_count'], overall['loss_count']
    latest = _period(summary.get('latest_settled_period'))
    if ((n and latest is None) or (summary.get('latest_settled_period') is not None and latest is None)
            or any(row['settled_count'] > n or row['loss_count'] > losses for row in metrics.values())
            or sum(metrics['position_' + row[0]]['settled_count'] for row in POSITIONS) != 3 * n
            or sum(metrics['tail_' + row[0]]['settled_count'] for row in TAILS) != header['tail_count'] * n
            or (header['revision'] == REVISION and any(metrics[key]['settled_count'] != n for key, *_ in KEEP_SPECS))):
        return None
    return {'overall': overall, 'metrics': metrics, 'latest_settled_period': latest,
            **{k: summary[k] for k in counts}}


def _prediction(summary, header):
    raw = summary.get('prediction') if isinstance(summary, dict) else None
    if (header is None or not isinstance(raw, dict)
            or summary.get('prediction_revision') != header['revision']):
        return None
    ref, target, first, last = (_period(raw.get(k)) for k in
                                ('reference_period', 'target_period', 'first_period', 'last_period'))
    count = raw.get('sample_count')
    seal, published, deadline = (_stamp(raw.get(k)) for k in ('sealed_at', 'published_at', 'deadline'))
    updated = _stamp(header['updated_at'])
    position, tail = raw.get('selected_positions'), raw.get('selected_tails')
    state = raw.get('publication_state')
    if (None in (ref, target, first, last) or type(count) is not int or count != 50
            or last != ref or period_slot(target) - period_slot(ref) != timedelta(minutes=5)
            or period_slot(last) - period_slot(first) != timedelta(minutes=5 * (count - 1))
            or seal is None or deadline is None or deadline != period_slot(target) - timedelta(seconds=70)
            or not period_slot(ref) <= seal < period_slot(target) or seal > updated
            or any(not isinstance(raw.get(k), str) or not _HASH.fullmatch(raw[k])
                   for k in ('history_sha256', 'input_sha256', 'seal_sha256'))
            or state not in ('prepared', 'published', 'late')
            or not isinstance(position, dict) or len(position) != 3
            or not set(position) <= {row[0] for row in POSITIONS}
            or not isinstance(tail, dict) or len(tail) != header['tail_count']
            or not set(tail) <= {row[0] for row in TAILS}
            or any(type(v) is not int or not 0 <= v <= 9 for v in (*position.values(), *tail.values()))):
        return None
    if state == 'published':
        if published is None or not seal <= published <= updated or published >= deadline:
            return None
    elif raw.get('published_at') is not None:
        return None
    keeps = {}
    if header['revision'] == REVISION:
        if any(not _keep(raw.get(key), length, allowed) for key, _, length, allowed in KEEP_SPECS):
            return None
        keeps = {key: raw[key] for key, *_ in KEEP_SPECS}
    return {**{k: raw[k] for k in ('reference_period', 'target_period', 'sample_count', 'first_period',
                                  'last_period', 'history_sha256', 'input_sha256', 'seal_sha256',
                                  'publication_state')},
            'sealed_at': seal.isoformat(), 'published_at': published.isoformat() if published else None,
            'deadline': deadline.isoformat(),
            'selected_positions': {row[0]: position[row[0]] for row in POSITIONS if row[0] in position},
            'selected_tails': {row[0]: tail[row[0]] for row in TAILS if row[0] in tail}, **keeps}


def _attack(prediction):
    order = '1234567890'
    remaining = lambda number: ''.join(digit for digit in order if digit != str(number))
    positions, tails = prediction['selected_positions'], prediction['selected_tails']
    parts = [label + (remaining(positions[key]) if key in positions else order) for key, _, label in POSITIONS]
    parts.append('四定')
    parts.extend(label + remaining(tails[key]) for key, _, label in TAILS if key in tails)
    if 'he2_keep' in prediction and 'he3_keep' in prediction:
        parts.extend(('二数合' + prediction['he2_keep'], '三数合' + prediction['he3_keep']))
    return ''.join(parts) + '各'


def _encoded(data):
    text = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, new in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        text = text.replace(old, new)
    return text


STYLE = '''<style>
#cold50-framework{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:2px solid #0f766e;border-radius:12px;background:#fff;color:#1e293b;font:14px/1.7 sans-serif;min-width:0;overflow-wrap:anywhere}
#cold50-framework *{box-sizing:border-box}
#cold50-framework h3{font-size:20px;color:#0f766e;margin:0 0 4px}
#cold50-framework .cf-meta,#cold50-framework .cf-note{font-size:12px;color:#64748b}
#cold50-framework .cf-status{margin:8px 0;padding:6px 9px;border-radius:7px;background:#f1f5f9;color:#475569;min-height:2.4em}
#cold50-framework .cf-stats{margin:8px 0;padding:9px 11px;border:1px solid #99f6e4;border-radius:8px;background:#f0fdfa}
#cold50-framework .cf-rate{font-size:18px;font-weight:700;color:#115e59}
#cold50-framework .cf-current{font-size:17px;font-weight:700}
#cold50-framework .cf-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;min-width:0}
#cold50-framework .cf-item{min-width:0;padding:9px 10px;border:1px solid #ccfbf1;border-radius:8px;background:#f8fdfc}
#cold50-framework .cf-number{font-size:23px;color:#dc2626;letter-spacing:1px}
#cold50-framework .cf-item-rate{font-size:12px;color:#475569}
#cold50-framework .cf-attack{margin-top:10px;padding-top:8px;border-top:1px solid #e2e8f0}
#cold50-framework .cf-legacy-attack{white-space:pre-wrap;overflow-wrap:anywhere;padding:9px;border:1px solid #ccfbf1;border-radius:7px;background:#f8fdfc}
#cold50-framework textarea{display:block;width:100%;min-width:0;margin:7px 0;padding:9px;resize:vertical;border:1px solid #99f6e4;border-radius:7px;background:#f8fdfc;font:15px/1.8 serif;overflow-wrap:anywhere}
#cold50-framework button{padding:7px 15px;border:0;border-radius:7px;background:#0f766e;color:#fff;cursor:pointer;font:14px/1.5 sans-serif}
#cold50-framework button:disabled{opacity:.5;cursor:default}
#cold50-framework details{margin-top:7px;font-size:12px;color:#64748b}
#cold50-framework summary{cursor:pointer}
@media(max-width:700px){#cold50-framework .cf-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(max-width:430px){#cold50-framework{padding:11px;width:calc(100% - 16px)}#cold50-framework h3{font-size:18px}#cold50-framework .cf-grid{grid-template-columns:minmax(0,1fr)}#cold50-framework .cf-rate{font-size:17px}}
</style>'''


# This script is deliberately identical for placeholders, failures and published
# predictions. Quiet refresh never executes fetched script text.
SCRIPT = r'''<script id="cold50-framework-control">
/* cold50-framework-control-v4 */
(function(){
'use strict';
const previous=window.__PENTA5_COLD50_FRAMEWORK__;
if(previous&&previous.destroy)previous.destroy();
const revision='cold50-framework-v3-50-pos3-tail2-he2x4-he3x5',digits='1234567890';
const position=[['q','千'],['b','百'],['s','十'],['g','个']];
const tails=[['all','千百十个合'],['qbs','千百十合'],['qbg','千百个合'],['qsg','千十个合'],['bsg','百十个合']];
function byId(id){return document.getElementById(id);}
function period(value){
 if(typeof value!=='string'||!(/^[0-9]{11}$/).test(value))return null;
 const year=+value.slice(0,4),month=+value.slice(4,6),day=+value.slice(6,8),index=+value.slice(8);
 const base=Date.UTC(year,month-1,day),d=new Date(base);
 if(index<1||index>288||d.getUTCFullYear()!==year||d.getUTCMonth()!==month-1||d.getUTCDate()!==day)return null;
 return base-28800000+index*300000;
}
function timestamp(value){return typeof value==='string'&&/(Z|[+-][0-9]{2}:[0-9]{2})$/.test(value)?Date.parse(value):NaN;}
function remaining(number){return Array.from(digits).filter(x=>x!==String(number)).join('');}
function expected(p){
 return position.map(x=>x[1]+(Object.prototype.hasOwnProperty.call(p.selected_positions,x[0])?remaining(p.selected_positions[x[0]]):digits)).join('')+
 '四定'+tails.filter(x=>Object.prototype.hasOwnProperty.call(p.selected_tails,x[0])).map(x=>x[1]+remaining(p.selected_tails[x[0]])).join('')+
 '二数合'+p.he2_keep+'三数合'+p.he3_keep+'各';
}
function keep(raw,count,allowed){return typeof raw==='string'&&raw.length===count&&new Set(raw).size===count&&Array.from(raw).every(x=>allowed.includes(x));}
function selection(raw,allowed,count){
 return raw&&typeof raw==='object'&&!Array.isArray(raw)&&Object.keys(raw).length===count&&
 Object.keys(raw).every(k=>allowed.some(x=>x[0]===k)&&Number.isInteger(raw[k])&&raw[k]>=0&&raw[k]<=9);
}
function state(){
 const box=byId('cold50-framework-attack'),node=byId('cold50-framework-data'),card=byId('cold50-framework-card');
 if(!box||!node||!card)return {ok:false,message:'等待框架更新。'};
 let d;try{d=JSON.parse(node.textContent);}catch(e){return {ok:false,message:'框架数据核对失败，等待刷新。'};}
 const p=d&&d.prediction;
 if(!d||d.version!==1||d.revision!==revision||d.prediction_revision!==revision||!p)return {ok:false,message:'等待首次事前封存，号码生成后在此显示。'};
 const ref=period(p.reference_period),deadline=timestamp(p.deadline),target=period(p.target_period);
 const published=timestamp(p.published_at),sealed=timestamp(p.sealed_at),now=Date.now();
 if(ref===null||target===null||target-ref!==300000||deadline!==target-70000||p.sample_count!==50||
    !selection(p.selected_positions,position,3)||!selection(p.selected_tails,tails,2)||
    !keep(p.he2_keep,4,'02468')||!keep(p.he3_keep,5,'0123456789')||
    ![p.input_sha256,p.history_sha256,p.seal_sha256].every(x=>typeof x==='string'&&(/^[0-9a-f]{64}$/).test(x))||
    !Number.isFinite(sealed)||sealed<ref||sealed>=target||sealed>now||
    card.dataset.cold50FrameworkReference!==p.reference_period||card.dataset.cold50FrameworkTarget!==p.target_period||
    card.dataset.cold50FrameworkSeal!==p.seal_sha256)
   return {ok:false,message:'框架号码或目标期核对失败；保留原文，等待刷新。'};
 const numbers={};
 position.forEach(x=>{if(Object.prototype.hasOwnProperty.call(p.selected_positions,x[0]))numbers['position_'+x[0]]=p.selected_positions[x[0]];});
 tails.forEach(x=>{if(Object.prototype.hasOwnProperty.call(p.selected_tails,x[0]))numbers['tail_'+x[0]]=p.selected_tails[x[0]];});
 const nodes=card.querySelectorAll('[data-cold50-framework-number]');
 const keepNodes=card.querySelectorAll('[data-cold50-framework-keep]');
 if(nodes.length!==5||new Set(Array.from(nodes).map(x=>x.dataset.cold50FrameworkNumber)).size!==5||
    Array.from(nodes).some(x=>!(x.dataset.cold50FrameworkNumber in numbers)||x.textContent.trim()!==String(numbers[x.dataset.cold50FrameworkNumber]))||
    keepNodes.length!==2||new Set(Array.from(keepNodes).map(x=>x.dataset.cold50FrameworkKeep)).size!==2||
    Array.from(keepNodes).some(x=>!['he2_keep','he3_keep'].includes(x.dataset.cold50FrameworkKeep)||x.textContent.trim()!==p[x.dataset.cold50FrameworkKeep])||
    typeof d.attack!=='string'||expected(p)!==d.attack||box.defaultValue!==d.attack||box.value!==d.attack)
   return {ok:false,message:'攻击语与封存号码不一致；保留原文，等待刷新。'};
 if(p.publication_state!=='published'||!Number.isFinite(published)||published<sealed||published>now||published>=deadline)
   return {ok:false,message:p.publication_state==='late'?'本期未能在关盘前完成发布，仅保留核对。':'正在封存与发布；号码保留，暂不可复制。'};
 const latest=byId('disp_period');
 if(!latest||latest.textContent.trim()!==p.reference_period)
   return {ok:false,message:'历史参考 · 目标期 '+p.target_period+'；等待本期更新。'};
 if(now>=deadline)return {ok:false,message:'本期已关盘，仅供核对 · 目标期 '+p.target_period};
 return {ok:true,text:d.attack,identity:p.target_period+':'+p.seal_sha256,message:'已事前封存 · 目标期 '+p.target_period+' · 开奖前70秒停止复制'};
}
function check(){
 const s=state(),button=byId('cold50-framework-copy'),label=byId('cold50-framework-validity');
 if(button&&button.disabled!==!s.ok)button.disabled=!s.ok;
 if(label&&label.textContent!==s.message)label.textContent=s.message;
 return s;
}
function same(snapshot){const s=check();return s.ok&&s.identity===snapshot.identity&&s.text===snapshot.text;}
function fallback(snapshot){
 if(!same(snapshot))return false;
 const active=document.activeElement,selection=active&&typeof active.selectionStart==='number'?[active.selectionStart,active.selectionEnd]:null;
 const x=window.scrollX,y=window.scrollY,temp=document.createElement('textarea');
 temp.value=snapshot.text;temp.readOnly=true;temp.style.cssText='position:fixed;left:-10000px;top:0;opacity:0;';
 document.body.appendChild(temp);temp.select();let ok=false;
 try{if(same(snapshot))ok=document.execCommand('copy');}catch(e){}finally{
  temp.remove();if(active&&active.isConnected){try{active.focus({preventScroll:true});if(selection)active.setSelectionRange(selection[0],selection[1]);}catch(e){}}
  if(window.scrollX!==x||window.scrollY!==y)window.scrollTo(x,y);
 }
 return ok;
}
async function click(event){
 const button=event.target.closest&&event.target.closest('#cold50-framework-copy');if(!button)return;
 const snapshot=check(),status=byId('cold50-framework-copy-status');if(!snapshot.ok)return;
 button.disabled=true;let copied=false;
 try{
  if(navigator.clipboard&&window.isSecureContext){await navigator.clipboard.writeText(snapshot.text);copied=true;}
  else copied=fallback(snapshot);
 }catch(e){copied=fallback(snapshot);}
 const current=same(snapshot);
 if(status)status.textContent=copied?(current?'已复制本期封存攻击语。':'复制期间页面已更新，请核对目标期后重新复制。'):'复制失败，可选中文本手动复制。';
 check();
}
function visible(){if(!document.hidden)check();}
document.addEventListener('click',click);document.addEventListener('visibilitychange',visible);
document.addEventListener('penta5:panel-updated',check);window.addEventListener('pageshow',check);
window.__PENTA5_COLD50_FRAMEWORK__={check:check,destroy:function(){
 document.removeEventListener('click',click);document.removeEventListener('visibilitychange',visible);
 document.removeEventListener('penta5:panel-updated',check);window.removeEventListener('pageshow',check);
}};
check();
})();
</script>'''


def _rate(metric, *, keep=False):
    if metric is None:
        return '战绩核验暂不可用'
    if not metric['settled_count']:
        return '暂无已验证数据'
    return (('命中率 ' if keep else '排除成功率 ') + format(metric['hit_rate'], '.2f') + '%（' + str(metric['win_count'])
            + '/' + str(metric['settled_count']) + '期）')


def _stats_html(data, header, *, prefix='cold50-framework'):
    day = ' data-stats-business-date="' + header['biz_date'] + '"' if header else ''
    if data is None:
        return ('<div id="' + prefix + '-stats" class="cf-stats"' + day
                + ' role="status">战绩核验暂不可用，等待下次刷新；暂不显示百分比与连中连败。</div>')
    overall = data['overall']
    n, w = overall['settled_count'], overall['win_count']
    items = str(header['item_count'])
    rate = ('整组' + items + '项全部通过率 ' + format(overall['hit_rate'], '.2f') + '%（' + str(w) + '/' + str(n) + '期）'
            if n else '整组' + items + '项：暂无已验证数据')
    broken = '（连续记录已中断）' if overall['streak_broken'] else ''
    return ('<div id="' + prefix + '-stats" class="cf-stats"' + day + '><div class="cf-meta">今日真实已验证战绩 · '
            + header['biz_date'] + ' 05:00起（北京时间）</div><div id="' + prefix + '-overall-rate" class="cf-rate">'
            + rate + '</div><div id="' + prefix + '-overall-max">最高连中：' + str(overall['max_win_streak'])
            + '手 ｜ 最高连败：' + str(overall['max_loss_streak']) + '手</div>'
            '<div id="' + prefix + '-overall-current" class="cf-current">当前连中：'
            + str(overall['current_win_streak']) + '手 ｜ 当前连败：' + str(overall['current_loss_streak']) + '手' + broken
            + '</div><div id="' + prefix + '-overall-four-loss">今日四手连败次数：'
            + str(overall['four_loss_cycles']) + '次（不重叠计数）</div><div class="cf-meta">已中 '
            + str(w) + '期 · 未中 ' + str(overall['loss_count']) + '期 · 待开奖 '
            + str(data['pending_count']) + '期 · 缺预测 ' + str(data['missing_count'])
            + '期 · 冲突 ' + str(data['conflict_count']) + '期 · 拒绝 '
            + str(data['rejected_count']) + '条<br>最近核验期：' + escape(data['latest_settled_period'] or '—')
            + '</div></div>')


def _prediction_meta(prediction):
    if prediction is None:
        return '等待最新连续完整50期真实开奖样本与首次事前封存。'
    return ('参考期：' + prediction['reference_period'] + ' ｜ 目标期：' + prediction['target_period']
            + '<br>样本：' + str(prediction['sample_count']) + '期（连续50期），'
            + prediction['first_period'] + ' ～ ' + prediction['last_period']
            + '<br>封存时间：' + _stamp(prediction['sealed_at']).strftime('%Y-%m-%d %H:%M:%S')
            + '（北京时间）；复制截止：'
            + _stamp(prediction['deadline']).strftime('%Y-%m-%d %H:%M:%S'))


def _cards_html(prediction, data, header, *, history=None):
    if prediction is None:
        return ''
    cards = []
    attribute = 'data-cold50-framework-' + history + '-number' if history else 'data-cold50-framework-number'
    for family, specs in (('position', POSITIONS), ('tail', TAILS)):
        chosen = prediction['selected_positions' if family == 'position' else 'selected_tails']
        for key, label, _ in specs:
            if key not in chosen:
                continue
            metric = family + '_' + key
            cards.append('<article class="cf-item"><b>' + str(len(cards) + 1) + '. ' + label
                         + '排除：</b> <strong class="cf-number" ' + attribute + '="' + metric + '">'
                         + str(chosen[key]) + '</strong><div class="cf-item-rate" data-stats-business-date="'
                         + header['biz_date'] + '">' + _rate(data['metrics'][metric] if data else None)
                         + '</div></article>')
    if header['revision'] == REVISION:
        for key, label, length, _ in KEEP_SPECS:
            cards.append('<article class="cf-item"><b>' + str(len(cards) + 1) + '. ' + label
                         + '保留：</b> <strong class="cf-number" data-cold50-framework-keep="' + key + '">'
                         + prediction[key] + '</strong><div class="cf-note">保留' + str(length) + '码</div>'
                         + '<div class="cf-item-rate" data-stats-business-date="' + header['biz_date'] + '">'
                         + _rate(data['metrics'][key] if data else None, keep=True) + '</div></article>')
    return ''.join(cards)


def _history_html(summary):
    header = _header(summary)
    if header is None or header['revision'] not in (LEGACY_REVISION, FIVE_REVISION):
        return ''
    legacy = header['revision'] == LEGACY_REVISION
    name = 'legacy' if legacy else 'five'
    prefix = 'cold50-framework-' + name
    items = str(header['item_count'])
    tails = '3' if legacy else '2'
    rule = '三定位三合尾' if legacy else '三定位二合尾'
    data = _stats(summary, header)
    prediction = _prediction(summary, header)
    attack = _attack(prediction) if prediction else ''
    metadata = _prediction_meta(prediction) if prediction else '本统计日暂无可显示的旧' + items + '项封存号码。'
    public = {'version': 1, 'revision': header['revision'],
              'prediction_revision': header['revision'] if prediction else None,
              'prediction': prediction, 'attack': attack}
    return ('<details id="' + prefix + '"><summary>原' + items + '项框架历史与统计（' + rule + '）</summary>'
            '<div class="cf-note">原规则按3个位置与' + tails + '种合尾共' + items + '项核验；已有封存号码与旧规则成绩保留。'
            '下列记录不计入当前7项战绩，仅供历史核对。</div>'
            + _stats_html(data, header, prefix=prefix)
            + '<div id="' + prefix + '-card"><div class="cf-meta">' + metadata + '</div>'
            '<div class="cf-grid">' + _cards_html(prediction, data, header, history=name) + '</div></div>'
            '<div class="cf-attack"><b>原' + items + '项封存攻击语（历史核对）</b>'
            '<div id="' + prefix + '-attack" class="cf-legacy-attack">'
            + escape(attack) + '</div></div>'
            '<script id="' + prefix + '-data" type="application/json">' + _encoded(public)
            + '</script></details>')


def _histories(summary, header):
    if not isinstance(summary, dict):
        return []
    if 'history_summaries' in summary:
        raw = summary['history_summaries'] if isinstance(summary['history_summaries'], list) else []
    else:
        raw = [summary.get('legacy_summary')]
    if header is not None and header['revision'] in (LEGACY_REVISION, FIVE_REVISION):
        raw = [summary, *raw]
    by_revision = {}
    for history in raw:
        parsed = _header(history)
        if (parsed is not None and parsed['revision'] in (LEGACY_REVISION, FIVE_REVISION)
                and parsed['revision'] not in by_revision):
            by_revision[parsed['revision']] = history
    return [by_revision[revision] for revision in (LEGACY_REVISION, FIVE_REVISION) if revision in by_revision]


def render_framework(summary):
    """Render only sanitized public metadata and verified derived statistics."""
    header = _header(summary)
    histories = _histories(summary, header)
    if header is not None and header['revision'] != REVISION:
        header = None
    data = _stats(summary, header) if isinstance(summary, dict) else None
    prediction = _prediction(summary, header)
    attack = _attack(prediction) if prediction else ''
    enabled = summary.get('api_enabled') if isinstance(summary, dict) else None
    api_status = ('API已停止，新期等待启动；保留已有封存。' if enabled is False else
                  'API已启动，等待正常轮次完成本期独立选择。' if enabled is True else
                  'API状态尚未确认；请查看页面启动／停止开关。')
    attributes = ''
    meta = _prediction_meta(prediction)
    if prediction:
        attributes = (' data-cold50-framework-reference="' + prediction['reference_period']
                      + '" data-cold50-framework-target="' + prediction['target_period']
                      + '" data-cold50-framework-seal="' + prediction['seal_sha256'] + '"')
    public = {'version': 1, 'revision': REVISION, 'prediction_revision': REVISION if prediction else None,
              'prediction': prediction, 'attack': attack}
    validity = '等待页面核对；封存号码保留显示。' if prediction else '等待首次事前封存。'
    return (START + '\n<section id="cold50-framework">' + STYLE
            + '<h3>DeepSeek Pro · 50期冷码七项框架</h3>'
            '<div class="cf-meta">AI自主选3个位置与2种合尾，各排该项近50期第1冷码；另选二数合4码、三数合5码（2单3双或3单2双）</div>'
            '<div id="cold50-framework-api-status" class="cf-meta">' + api_status + '</div>'
            '<div id="cold50-framework-card"' + attributes + '><div class="cf-meta">' + meta + '</div>'
            '<div id="cold50-framework-validity" class="cf-status" role="status">' + validity + '</div>'
            + _stats_html(data, header) + '<div class="cf-grid">' + _cards_html(prediction, data, header) + '</div></div>'
            '<div class="cf-attack"><b>50期冷码框架攻击语</b><div class="cf-note">复制本框架5项排除、二数合4码与三数合5码；未选的位置保留全部数字。</div>'
            '<textarea id="cold50-framework-attack" readonly rows="3" aria-label="50期冷码框架攻击语">'
            + escape(attack) + '</textarea><button id="cold50-framework-copy" type="button" disabled>复制攻击语</button> '
            '<span id="cold50-framework-copy-status" class="cf-note" role="status"></span></div>'
            '<details id="cold50-framework-rules"><summary>统计与封存规则</summary>'
            '<div>只使用跨统计日最新连续完整50期真实开奖的前4球；不足50期不出码，成绩仍按北京时间05:00划分统计日。'
            '各项目按出现次数少、当前遗漏长、固定数字顺序取第1冷码；AI从4个位置选3个、5种合尾选2个，各排该项第1冷码。'
            '合尾为对应位置数字之和的个位。二数合从02468中保留4个不同数字；新封存三数合必须保留5个不同数字，单双只能2比3或3比2；历史封存按原记录保留。'
            '二数合按前4球的6种两位组合计算，三数合按前4球的4种三位组合计算；各项只要有一个组合的合尾落入对应保留码即中。'
            '整组须5项排除、二数合与三数合共7项同时通过。单项成绩只统计该项被选中的期次。'
            '<br>封存后号码不变，统计只累计真实事前发布且开奖已核验的样本；待开奖不算输，缺预测、冲突和未选项目会中断对应连续记录。'
            '今日四手连败按不重叠计数，连续8败记2次；每日05:00重新统计。'
            '<br>发布与复制均须在目标期开奖前70秒完成。历史与关盘后仍保留号码核对，暂不可复制。'
            '冷码描述历史，不构成未来命中保证。</div></details>'
            + ''.join(_history_html(history) for history in histories)
            + '<script id="cold50-framework-data" type="application/json">' + _encoded(public) + '</script>'
            + SCRIPT + '</section>\n' + END)


def apply_framework(content, block):
    """Insert one top-level owned section; every other named byte stays intact."""
    before, incoming = _inspect(content), _inspect(block)
    if (set(incoming) != {MARKER} or incoming[MARKER][2] != block.strip()
            or len(_OWN_ID.findall(block)) != 1):
        raise ValueError('Invalid cold framework block')
    if ANCHOR not in before or before[ANCHOR][3] != 0:
        raise ValueError('Top-level original cold framework anchor required')
    own = before.get(MARKER)
    if own is None:
        if _OWN_ID.search(content):
            raise ValueError('Cold framework id without owned markers')
        position = before[ANCHOR][1]
        updated = content[:position] + '\n' + block + '\n' + content[position:]
    else:
        if own[3] != 0 or len(_OWN_ID.findall(content)) != 1:
            raise ValueError('Invalid existing cold framework placement')
        if own[0] < before[ANCHOR][1]:
            raise ValueError('Cold framework precedes its anchor')
        updated = content[:own[0]] + block + content[own[1]:]
    after = _inspect(updated)
    protected = {name: row[2] for name, row in before.items() if name != MARKER}
    if protected != {name: row[2] for name, row in after.items() if name != MARKER}:
        raise ValueError('Cold framework changed another named block')
    return updated


def publish(summary, *, root=ROOT):
    """Use the panel's shared exclusive lock and atomic durable replacement."""
    from panel_io import update_panel
    block = render_framework(summary)
    return update_panel(lambda content: apply_framework(content, block), path=Path(root) / 'panel.html')
