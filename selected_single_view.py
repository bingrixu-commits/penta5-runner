"""Hidden observation of the seven choices already sealed by selected Pro.

No selection, inference, ranking, or new prediction is performed here. This
derived view reads the matching canonical card and its published attack data.
"""
from datetime import date, datetime
from html import escape
import json
import re

from selected_framework_contract import (REVISION, BASE_METRIC_KEYS, POSITION_KEYS,
                                         POSITION_LABELS, TAIL_LABELS)
from stats_history import parse_period

LABELS = {**{'position_' + key: POSITION_LABELS[key] + '排除' for key in POSITION_KEYS},
          **{'tail_' + key: label + '排除' for key, label in TAIL_LABELS.items()},
          'he2_keep': '二数合保留4码', 'he3_keep': '三数合保留5码',
          **{'extra_position_' + key: '额外' + POSITION_LABELS[key] + '排除' for key in POSITION_KEYS},
          **{'extra_tail_' + key: '额外' + label + '排除' for key, label in TAIL_LABELS.items()}}


def _metric(raw):
    if not isinstance(raw, dict):
        return None
    names = ('hits', 'total', 'max_win_streak', 'max_loss_streak',
             'current_win_streak', 'current_loss_streak')
    if (any(type(raw.get(key)) is not int or not 0 <= raw[key] <= 9007199254740991 for key in names)
            or type(raw.get('current_streak_interrupted')) is not bool):
        return None
    h, n, mw, ml, cw, cl = (raw[key] for key in names)
    if (h > n or mw > h or ml > n-h or bool(mw) != bool(h) or bool(ml) != bool(n-h)
            or cw > mw or cl > ml or (cw and cl)
            or (n and not cw and not cl and not raw['current_streak_interrupted'])
            or (raw['current_streak_interrupted'] and (cw or cl))):
        return None
    return {**{key: raw[key] for key in names},
            'current_streak_interrupted': raw['current_streak_interrupted']}


def _period(value):
    parsed = parse_period(value)
    return value if isinstance(value, str) and parsed and value == parsed[0] else None


def _stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else None
        return parsed.isoformat() if parsed and parsed.tzinfo is not None and parsed.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _summary(raw, cumulative=False):
    if not isinstance(raw, dict) or raw.get('rule_revision') != REVISION or raw.get('stats_error') is not False:
        return None
    source = raw.get('metrics')
    if not isinstance(source, dict):
        return None
    metrics = {key: _metric(source.get(key)) for key in BASE_METRIC_KEYS}
    overall = _metric(raw.get('overall'))
    if overall is None or any(value is None for value in metrics.values()):
        return None
    n, h = overall['total'], overall['hits']
    latest, closed = _period(raw.get('latest_settled_period')), _period(raw.get('latest_known_closed_period'))
    if ((n and latest is None) or (raw.get('latest_settled_period') is not None and latest is None)
            or (raw.get('latest_known_closed_period') is not None and closed is None)
            or (closed and latest and latest > closed)
            or any(value['total'] > n or value['total']-value['hits'] > n-h for value in metrics.values())
            or metrics['he2_keep']['total'] != n or metrics['he3_keep']['total'] != n
            or metrics['he2_keep']['hits'] < h or metrics['he3_keep']['hits'] < h
            or sum(metrics[key]['total'] for key in BASE_METRIC_KEYS[:4]) != 3*n
            or sum(metrics[key]['total'] for key in BASE_METRIC_KEYS[4:9]) != 2*n):
        return None
    result = {'rule_revision': REVISION, 'stats_error': False, 'metrics': metrics, 'overall': overall,
              'latest_settled_period': latest, 'latest_known_closed_period': closed}
    if cumulative:
        result['as_of'] = _stamp(raw.get('as_of'))
        return result if result['as_of'] else None
    day = raw.get('biz_date')
    try:
        if not isinstance(day, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', day) or date.fromisoformat(day).isoformat() != day:
            return None
    except (ValueError, TypeError):
        return None
    result['biz_date'] = day
    return result


SCRIPT = r'''<script id="selected6-single-control">
(function(){
  'use strict';
  var version='selected-single-observation-v5-three-positions',rule='selected-seven-v6-three-positions';
  var keys=['position_q','position_b','position_s','position_g','tail_all','tail_qbs','tail_qbg','tail_qsg','tail_bsg','he2_keep','he3_keep'];
  var labels={position_q:'千位排除',position_b:'百位排除',position_s:'十位排除',position_g:'个位排除',tail_all:'千百十个合尾排除',tail_qbs:'千百十合尾排除',tail_qbg:'千百个合尾排除',tail_qsg:'千十个合尾排除',tail_bsg:'百十个合尾排除',he2_keep:'二数合保留4码',he3_keep:'三数合保留5码',extra_position_q:'额外千位排除',extra_position_b:'额外百位排除',extra_position_s:'额外十位排除',extra_position_g:'额外个位排除',extra_tail_all:'额外千百十个合尾排除',extra_tail_qbs:'额外千百十合尾排除',extra_tail_qbg:'额外千百个合尾排除',extra_tail_qsg:'额外千十个合尾排除',extra_tail_bsg:'额外百十个合尾排除'};
  function one(id){var found=document.querySelectorAll('[id="'+id+'"]');return found.length===1?found[0]:null;}
  function put(node,text){if(node&&node.textContent!==text)node.textContent=text;}
  function hidden(node,value){if(node&&node.hidden!==value)node.hidden=value;}

  function identity(ref){
    if(typeof ref!=='string'||!/^[0-9]{11}$/.test(ref))return null;
    var y=+ref.slice(0,4),m=+ref.slice(4,6),d=+ref.slice(6,8),n=+ref.slice(8),day=new Date(Date.UTC(y,m-1,d));
    if(y<100||day.getUTCFullYear()!==y||day.getUTCMonth()!==m-1||day.getUTCDate()!==d||n<1||n>288)return null;
    return {target:n===288?new Date(day.getTime()+86400000).toISOString().slice(0,10).replace(/-/g,'')+'001':ref.slice(0,8)+String(n+1).padStart(3,'0'),deadline:day.getTime()-8*3600000+(n+1)*300000};
  }
  function stamp(value){
    if(typeof value!=='string')return NaN;
    var m=/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/.exec(value);
    if(!m||+m[1]<100||+m[4]>23||+m[5]>59||+m[6]>59)return NaN;
    var day=new Date(Date.UTC(+m[1],+m[2]-1,+m[3]));
    if(day.getUTCFullYear()!==+m[1]||day.getUTCMonth()!==+m[2]-1||day.getUTCDate()!==+m[3])return NaN;
    return Date.parse(value);
  }
  function data(id){var source=one(id);try{return source?JSON.parse(source.textContent):null;}catch(e){return null;}}
  function metric(x){
    if(!x||typeof x!=='object'||Array.isArray(x))return false;
    var names=['hits','total','max_win_streak','max_loss_streak','current_win_streak','current_loss_streak'];
    if(!names.every(function(k){return Number.isSafeInteger(x[k])&&x[k]>=0;})||typeof x.current_streak_interrupted!=='boolean')return false;
    return x.hits<=x.total&&x.max_win_streak<=x.hits&&x.max_loss_streak<=x.total-x.hits&&!!x.max_win_streak===!!x.hits&&!!x.max_loss_streak===!!(x.total-x.hits)&&x.current_win_streak<=x.max_win_streak&&x.current_loss_streak<=x.max_loss_streak&&!(x.current_win_streak&&x.current_loss_streak)&&(!x.total||x.current_win_streak||x.current_loss_streak||x.current_streak_interrupted)&&(!x.current_streak_interrupted||(!x.current_win_streak&&!x.current_loss_streak));
  }
  function statistics(x,cumulative){
    if(!x||typeof x!=='object'||Array.isArray(x)||x.rule_revision!==rule||x.stats_error!==false||!metric(x.overall)||!x.metrics||!keys.every(function(k){return metric(x.metrics[k]);}))return false;
    var n=x.overall.total,h=x.overall.hits;
    if((n&&!identity(x.latest_settled_period))||(x.latest_settled_period!==null&&!identity(x.latest_settled_period))||(x.latest_known_closed_period!==null&&!identity(x.latest_known_closed_period))||(x.latest_settled_period&&x.latest_known_closed_period&&x.latest_settled_period>x.latest_known_closed_period))return false;
    if(!keys.every(function(k){return x.metrics[k].total<=n&&x.metrics[k].total-x.metrics[k].hits<=n-h;})||x.metrics.he2_keep.total!==n||x.metrics.he3_keep.total!==n||x.metrics.he2_keep.hits<h||x.metrics.he3_keep.hits<h||keys.slice(0,4).reduce(function(a,k){return a+x.metrics[k].total;},0)!==3*n||keys.slice(4,9).reduce(function(a,k){return a+x.metrics[k].total;},0)!==2*n)return false;
    if(cumulative)return Number.isFinite(stamp(x.as_of))&&stamp(x.as_of)<=Date.now();
    if(typeof x.biz_date!=='string'||!/^\d{4}-\d{2}-\d{2}$/.test(x.biz_date))return false;
    var date=new Date(x.biz_date+'T00:00:00Z');return Number.isFinite(date.getTime())&&date.toISOString().slice(0,10)===x.biz_date;
  }
  function state(){
    var card=one('selected6-card'),d=data('selected6-attack-data'),box=one('selected6-attack-text'),period=one('disp_period'),expected=d&&identity(d.reference_period),now=Date.now();
    var valid=!!(card&&d&&box&&period&&d.version===1&&d.rule_revision===rule&&card.getAttribute('data-selected6-revision')===rule&&expected&&d.target_period===expected.target&&stamp(d.deadline)===expected.deadline&&Number.isFinite(stamp(d.published_at))&&stamp(d.published_at)>=expected.deadline-300000&&stamp(d.published_at)<expected.deadline&&stamp(d.published_at)<=now&&Number.isFinite(now)&&card.getAttribute('data-selected6-reference')===d.reference_period&&card.getAttribute('data-selected6-target')===d.target_period&&stamp(card.getAttribute('data-selected6-deadline'))===expected.deadline&&typeof d.attack==='string'&&d.attack.length>0&&d.attack.length<=512&&box.value===d.attack);
    var numbers=d&&d.numbers,selected=numbers&&typeof numbers==='object'&&!Array.isArray(numbers)?Object.keys(numbers):[];
    valid=valid&&selected.length===7&&selected.every(function(k){return keys.indexOf(k)>=0&&typeof numbers[k]==='string';})&&selected.filter(function(k){return k.indexOf('position_')===0;}).length===3&&selected.filter(function(k){return k.indexOf('tail_')===0;}).length===2&&/^[02468]{4}$/.test(numbers.he2_keep)&&new Set(numbers.he2_keep).size===4&&/^[0-9]{5}$/.test(numbers.he3_keep)&&new Set(numbers.he3_keep).size===5&&selected.filter(function(k){return /^(position_|tail_)/.test(k);}).every(function(k){return /^[0-9]$/.test(numbers[k]);});
    var nodes=card?Array.from(card.querySelectorAll('[data-selected6-number]')):[];
    valid=valid&&nodes.length===7&&nodes.every(function(node){var k=node.getAttribute('data-selected6-number');return selected.indexOf(k)>=0&&card.querySelectorAll('[data-selected6-number="'+k+'"]').length===1&&node.textContent.trim()===numbers[k];});
    var current=period?period.textContent.trim():'',same=valid&&current===d.reference_period&&!!identity(current),close=expected?expected.deadline-70000:NaN;
    return {valid:!!valid,available:!!(same&&now<close),same:!!same,closed:!!(same&&now>=close),data:d,selected:valid?nodes.map(function(n){return n.getAttribute('data-selected6-number');}):[],now:now,close:close};
  }
  function rate(m){return m.total?(100*m.hits/m.total).toFixed(2)+'%（'+m.hits+'/'+m.total+'期）':'—（暂无已核验样本）';}
  function streak(m){return m.total?'最高连中：'+m.max_win_streak+'手 ｜ 最高连败：'+m.max_loss_streak+'手 ｜ 当前连中：'+m.current_win_streak+'手 ｜ 当前连败：'+m.current_loss_streak+'手'+(m.current_streak_interrupted?'（连续记录已中断）':''):'最高连中：— ｜ 最高连败：— ｜ 当前连中：— ｜ 当前连败：—（暂无样本）';}
  function notice(text){put(one('selected6-single-copy-status'),text);}
  function check(){
    var root=one('selected6-single-observation');if(!root)return;
    var s=state(),payload=data('selected6-single-data'),good=!!payload&&payload.version===1&&payload.rule_revision===rule;
    var daily=good&&statistics(payload.daily,false)?payload.daily:null,cumulative=good&&statistics(payload.cumulative,true)?payload.cumulative:null;
    if(daily&&cumulative&&!keys.every(function(k){var a=daily.metrics[k],b=cumulative.metrics[k];return a.total<=b.total&&a.hits<=b.hits&&a.total-a.hits<=b.total-b.hits;})){daily=null;cumulative=null;}
    var today=Number.isFinite(s.now)?new Date(s.now+3*3600000).toISOString().slice(0,10):null,dayReady=!!daily&&daily.biz_date===today;
    var target=s.data&&identity(s.data.reference_period)&&s.data.target_period===identity(s.data.reference_period).target?s.data.target_period:'--';
    put(one('selected6-single-status'),s.available?'本期封存参考，目标期：'+target:s.valid?(s.closed?'本期已关盘，仅供核对，目标期：':'历史参考，等待本期结果；原目标期：')+target:'本期号码来源尚未通过核验，暂停单项展示与复制。');
    put(one('selected6-single-margin'),s.valid?'发布时距离平台关盘：'+Math.floor((s.close-stamp(s.data.published_at))/1000)+'秒；关盘为开奖前70秒。':'发布时间等待核验。');
    put(one('selected6-single-asof'),cumulative?'当前规则累计，统计截至：'+cumulative.as_of.replace('T',' ')+'；最近核验期：'+(cumulative.latest_settled_period||'—'):'累计统计暂未通过核验。');
    keys.forEach(function(k){
      var row=one('selected6-single-'+k),selected=s.valid&&s.selected.indexOf(k)>=0;hidden(row,!selected);
      var button=one('selected6-single-copy-'+k);if(button&&button.disabled===!!(selected&&s.available))button.disabled=!(selected&&s.available);
      if(!selected)return;
      if(row&&row.style.order!==String(s.selected.indexOf(k)))row.style.order=String(s.selected.indexOf(k));
      put(one('selected6-single-number-'+k),s.data.numbers[k]);
      put(one('selected6-single-daily-'+k),!daily?'今日：统计核验暂不可用':!dayReady?'今日：北京时间05:00已切换，等待本周期统计':'今日已核验：'+rate(daily.metrics[k]));
      put(one('selected6-single-total-'+k),cumulative?'本规则累计：'+rate(cumulative.metrics[k]):'本规则累计：统计核验暂不可用');
      put(one('selected6-single-streak-'+k),cumulative?streak(cumulative.metrics[k]):'累计连中连败等待核验。');
    });
    if(!s.available)notice(s.valid?'号码保留供核对，当前暂停复制。':'等待本期匹配的封存结果。');
    else if(one('selected6-single-copy-status')&&['号码保留供核对，当前暂停复制。','等待本期匹配的封存结果。'].indexOf(one('selected6-single-copy-status').textContent)>=0)notice('');
  }
  async function click(event){
    var button=event.target.closest&&event.target.closest('[data-selected6-single-copy]');
    if(!button||!one('selected6-single-observation')||!one('selected6-single-observation').contains(button))return;
    event.preventDefault();var k=button.getAttribute('data-selected6-single-copy'),s=state();
    if(!s.available||s.selected.indexOf(k)<0||one('selected6-single-copy-'+k)!==button){check();return;}
    var text='目标期：'+s.data.target_period+'；'+labels[k]+'：'+s.data.numbers[k]+'；参考期：'+s.data.reference_period;
    function stillCurrent(){var live=state();return live.available&&live.selected.indexOf(k)>=0&&live.data.reference_period===s.data.reference_period&&live.data.target_period===s.data.target_period&&live.data.numbers[k]===s.data.numbers[k];}
    try{if(navigator.clipboard&&typeof navigator.clipboard.writeText==='function'){await navigator.clipboard.writeText(text);if(!stillCurrent()){check();return;}notice('已复制本项参考；请核对目标期：'+s.data.target_period);return;}}catch(e){}
    if(!stillCurrent()){check();return;}
    // A plain text fallback contains no platform command or invented syntax.
    var box=document.createElement('textarea'),focused=document.activeElement,x=scrollX,y=scrollY,selection=focused&&typeof focused.selectionStart==='number'?{start:focused.selectionStart,end:focused.selectionEnd,direction:focused.selectionDirection}:null;box.value=text;box.readOnly=true;box.style.cssText='position:fixed;left:-9999px;top:0;';document.body.appendChild(box);box.focus({preventScroll:true});box.select();var copied=false;try{copied=document.execCommand('copy')===true;}catch(e){}box.remove();if(focused&&focused.isConnected){focused.focus({preventScroll:true});if(selection&&typeof focused.setSelectionRange==='function')try{focused.setSelectionRange(selection.start,selection.end,selection.direction);}catch(e){}}if(scrollX!==x||scrollY!==y)scrollTo(x,y);notice(copied?'已复制本项参考；请核对目标期：'+s.data.target_period:'自动复制未完成，请核对上方号码。');
  }
  var old=window.__PENTA5_SELECTED_SINGLE__;
  if(old&&old.version===version){old.check();return;}
  if(old){if(old.timer!=null)clearInterval(old.timer);window.removeEventListener('pageshow',old.check);document.removeEventListener('visibilitychange',old.check);document.removeEventListener('penta5:panel-updated',old.check);document.removeEventListener('click',old.click);}
  window.__PENTA5_SELECTED_SINGLE__={version:version,check:check,click:click,timer:setInterval(check,1000)};
  window.addEventListener('pageshow',check);document.addEventListener('visibilitychange',check);document.addEventListener('penta5:panel-updated',check);document.addEventListener('click',click);check();
})();
</script>'''


def render_observation(summary):
    """Render a passive projection inside the existing selected stats marker."""
    raw = summary if isinstance(summary, dict) else {}
    payload = {'version': 1, 'rule_revision': REVISION,
               'daily': _summary(raw), 'cumulative': _summary(raw.get('cumulative'), cumulative=True)}
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    for old, new in (('&', '\\u0026'), ('<', '\\u003c'), ('>', '\\u003e'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')):
        encoded = encoded.replace(old, new)
    rows = []
    for key in BASE_METRIC_KEYS:
        rows.append('<article id="selected6-single-' + key + '" hidden style="min-width:0;padding:10px;border:1px solid #dbeafe;border-radius:8px;background:#fff;">'
                    '<b>' + escape(LABELS[key]) + '：<strong id="selected6-single-number-' + key + '" style="color:#2563eb;letter-spacing:1px;font-size:18px;">—</strong></b>'
                    '<div id="selected6-single-daily-' + key + '" style="font-size:12px;">今日等待核验</div>'
                    '<div id="selected6-single-total-' + key + '" style="font-size:12px;">累计等待核验</div>'
                    '<details id="selected6-single-details-' + key + '" style="font-size:12px;"><summary>累计连中连败</summary>'
                    '<div id="selected6-single-streak-' + key + '">等待核验</div></details>'
                    '<button type="button" id="selected6-single-copy-' + key + '" data-selected6-single-copy="' + key + '" disabled '
                    'style="margin-top:5px;padding:4px 8px;max-width:100%;border:1px solid #c7d2fe;border-radius:5px;background:#eef2ff;color:#3730a3;">复制本项参考</button></article>')
    return ('<section id="selected6-single-observation" hidden aria-label="本期单项独立观察" style="margin-top:12px;padding:12px;min-width:0;border:1px solid #c7d2fe;border-radius:9px;background:#f8faff;overflow-wrap:anywhere;">'
            '<h4 style="margin:0 0 4px;font-size:16px;">本期单项独立观察</h4>'
            '<div id="selected6-single-status" role="status" style="font-size:12px;">等待本期封存来源核验。</div>'
            '<div id="selected6-single-margin" style="font-size:12px;color:#64748b;">发布时间等待核验。</div>'
            '<p style="margin:5px 0;font-size:12px;color:#475569;">本区拆分展示同一三定位七项规则封存结果。固定三个定位、两个合尾和两个保留集，未选位置不补排。只统计该项目实际被选中的期数，非本数字胜率；不自动挑选历史最高项目。</p>'
            '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,250px),1fr));gap:8px;min-width:0;">' + ''.join(rows) + '</div>'
            '<div id="selected6-single-asof" style="margin-top:6px;font-size:12px;color:#64748b;">累计统计等待核验。</div>'
            '<div id="selected6-single-copy-status" role="status" aria-live="polite" style="font-size:12px;color:#475569;"></div>'
            '</section>\n<script type="application/json" id="selected6-single-data">' + encoded + '</script>\n' + SCRIPT)
