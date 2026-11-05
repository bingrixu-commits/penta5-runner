"""Compact, derived operational evidence; never edits sealed model results."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import json
import math
import re

from panel_markers import _inspect

ROOT = Path('/root/penta5-runner')
REVISION = 'runtime-observability-v1'
MARKER = 'RUNTIME_OBSERVABILITY'
START, END = '<!-- RUNTIME_OBSERVABILITY_START -->', '<!-- RUNTIME_OBSERVABILITY_END -->'
TZ = timezone(timedelta(hours=8))
MODELS = (
    ('killer', 'Flash 杀码'), ('framework', 'Flash 框架'),
    ('he3_refine', '三合五尾'),
)
STATUSES = {
    'current': '本轮已完成', 'pending': '本轮进行中', 'manual_off': '手动停止',
    'await_rule_start': '等待新规则起始期',
    'historical': '历史结果', 'late': '发布超时', 'missing': '缺少本期结果',
    'failed': '本轮异常', 'unknown': '暂无证据',
}
PHASES = {'expected': '等待本轮', 'source': '等待取数', 'input': '输入已冻结',
          'request': '请求已启动', 'response': '响应已完成', 'published': '已发布', 'panel': '已核对页面',
          'rule_start': '等待新规则生效'}
_OWN_ID = re.compile(r'\bid\s*=\s*[\'"]runtime-observability[\'"]', re.I)

STYLE = '''<style>
#runtime-observability{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:8px auto;color:#334155;font:13px/1.6 sans-serif;min-width:0}
#runtime-observability *{box-sizing:border-box}#runtime-observability details{min-width:0;border:1px solid #cbd5e1;border-radius:8px;background:#f8fafc}
#runtime-observability summary{height:42px;min-height:42px;padding:0 10px;display:flex;align-items:center;gap:7px;white-space:nowrap;overflow:hidden;cursor:pointer;list-style:none}
#runtime-observability summary::-webkit-details-marker{display:none}#runtime-observability summary:before{content:"▸";color:#64748b;flex:none}#runtime-observability details[open]>summary:before{content:"▾"}
#runtime-observability .rt-summary-text{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:600}
#runtime-observability .rt-stale{flex:none;font-size:11px;color:#92400e;background:#fff7ed;padding:0 3px;border-radius:3px}
#runtime-observability .rt-body{border-top:1px solid #e2e8f0;padding:9px 10px 10px;min-width:0;overflow-wrap:anywhere}
#runtime-observability .rt-meta,#runtime-observability .rt-note{color:#64748b;font-size:12px}
#runtime-observability .rt-table-scroll{max-width:100%;overflow:auto;margin:7px 0;-webkit-overflow-scrolling:touch}
#runtime-observability table{width:100%;min-width:1010px;border-collapse:collapse;text-align:left;font-size:12px;line-height:1.6}
#runtime-observability th{font-weight:600;background:#f1f5f9;color:#475569}#runtime-observability th,#runtime-observability td{vertical-align:top;padding:7px 8px;border-bottom:1px solid #e2e8f0}
#runtime-observability td:first-child{width:140px}#runtime-observability .rt-stage{max-width:245px}#runtime-observability .rt-mini{font-size:11px;color:#64748b;font-weight:400}
#runtime-observability .rt-warning{color:#92400e}#runtime-observability .rt-ok{color:#166534}#runtime-observability [hidden]{display:none!important}
@media(max-width:430px){#runtime-observability{width:calc(100% - 16px)}#runtime-observability summary{padding:0 8px;gap:5px;font-size:12px}#runtime-observability .rt-body{padding:8px}}
</style>'''

SCRIPT = r'''<script id="runtime-observability-control">
(function(){
  'use strict';
  var version='runtime-observability-view-v3-clock-aging',old=window.__PENTA5_RUNTIME_OBSERVABILITY__;
  if(old&&old.version===version){old.check();return;}
  if(old&&typeof old.check==='function'){
    document.removeEventListener('penta5:panel-updated',old.check);
    document.removeEventListener('penta5:panel-received',old.check);
    window.removeEventListener('pageshow',old.check);
  }
  function one(id){var all=document.querySelectorAll('[id="'+id+'"]');return all.length===1?all[0]:null;}
  function check(){
    var card=one('runtime-observability'),notice=one('runtime-observability-freshness');
    if(!card||!notice)return;
    var raw=card.getAttribute('data-runtime-updated')||'',stamp=Date.parse(raw),now=Date.now();
    var text=!raw||!Number.isFinite(stamp)?'等待有效运行摘要。':!Number.isFinite(now)||stamp>now+60000?'摘要时间尚未核对。':now-stamp>150000?'摘要已超过2分30秒未更新，以下仅为最近快照。':'摘要随页面静默更新。';
    if(notice.textContent!==text)notice.textContent=text;
    var stale=!raw||!Number.isFinite(stamp)||!Number.isFinite(now)||now-stamp>150000||stamp>now+60000;
    var badge=one('runtime-observability-stale');
    if(badge&&badge.hidden!==!stale)badge.hidden=!stale;
    var summary=one('runtime-observability-line');
    if(summary)summary.setAttribute('title',stale?'运行快照待更新；展开查看最近证据。':'展开运行明细');
    var evidence=one('runtime-observability-data'),publicData=null,receipt=window.__PENTA5_CLIENT_DELIVERY__;
    try{if(evidence)publicData=JSON.parse(evidence.textContent);}catch(_){}
    document.querySelectorAll('[data-runtime-client-model]').forEach(function(node){
      var key=node.getAttribute('data-runtime-client-model'),row=publicData&&publicData.models&&publicData.models[key];
      var received=receipt&&receipt.version===1&&receipt.models&&receipt.models[key],message='本浏览器本次收到：未采集。';
      var time=receipt&&Date.parse(receipt.received_at),ready=Date.parse(node.getAttribute('data-runtime-ready-at')||'');
      if(row&&received&&row.availability&&row.availability.event_id&&
         received.event_id===row.availability.event_id&&received.reference_period===row.reference_period&&received.target_period===row.target_period&&Number.isFinite(time)){
        message=Number.isFinite(now)&&time<=now+60000&&(!Number.isFinite(ready)||time>=ready)?
          '本浏览器本次收到：'+new Date(time+8*3600000).toISOString().slice(11,19)+'（本机时间，仅诊断）。':
          '本浏览器本次收到：本机与服务器时间尚未核对。';
      }
      if(node.textContent!==message)node.textContent=message;
    });
  }
  window.__PENTA5_RUNTIME_OBSERVABILITY__={version:version,check:check};
  document.addEventListener('penta5:panel-updated',check);
  document.addEventListener('penta5:panel-received',check);
  window.addEventListener('pageshow',check);
  check();
})();
</script>'''


def _integer(value, maximum=9007199254740991):
    return value if type(value) is int and 0 <= value <= maximum else None


def _number(value):
    return float(value) if type(value) in (int, float) and -9007199254740991 <= value <= 9007199254740991 and math.isfinite(value) else None


def _stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.astimezone(TZ) if result.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _period(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{11}', value):
        return None
    try:
        day = date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None
    return value if day.strftime('%Y%m%d') == value[:8] and 1 <= int(value[8:]) <= 288 else None


def _header(summary):
    if not isinstance(summary, dict) or type(summary.get('version')) is not int or summary['version'] != 1 or summary.get('revision') != REVISION:
        return None
    updated, start = _stamp(summary.get('updated_at')), _stamp(summary.get('business_start'))
    if (updated is None or start is None or start.hour != 5 or start.minute or start.second or start.microsecond
            or not start <= updated < start + timedelta(days=1)
            or summary.get('biz_date') != start.strftime('%Y-%m-%d')
            or type(summary.get('stats_error')) is not bool):
        return None
    return updated, start


def _seconds(value, *, signed=False):
    number = _number(value)
    if number is None or (number < 0 and not signed):
        return '—'
    return ('+' if signed and number > 0 else '') + f'{number:.1f}秒'


def _count(value):
    result = _integer(value)
    return str(result) if result is not None else '—'


def _label(mapping, value, fallback):
    return mapping.get(value, fallback) if isinstance(value, str) else fallback


def _gib(value):
    count = _integer(value)
    return f'{count / 1073741824:.1f}GiB' if count is not None else '—'


def _resources(data, updated):
    resources = data.get('resources') if isinstance(data.get('resources'), dict) else {}
    backup = data.get('backup') if isinstance(data.get('backup'), dict) else {}
    level = _label({'ok': '资源正常', 'warning': '资源告警', 'critical': '资源紧张'}, resources.get('level'), '资源：—')
    used = _number(resources.get('disk_used_percent'))
    disk = _gib(resources.get('disk_free_bytes')) + ' / ' + _gib(resources.get('disk_total_bytes'))
    memory = _gib(resources.get('memory_available_bytes')) + ' / ' + _gib(resources.get('memory_total_bytes'))
    stamp = _stamp(backup.get('last_verified_at'))
    verified = '—'
    if stamp is not None and updated is not None and timedelta() <= updated - stamp:
        minutes = int((updated - stamp).total_seconds() // 60)
        age = str(minutes) + '分钟前' if minutes < 60 else f'{minutes // 60}小时{minutes % 60}分钟前'
        verified = stamp.strftime('%Y-%m-%d %H:%M') + '（' + age + '）'
    receipt = _stamp(backup.get('offsite_receipt_at'))
    receipt_text = receipt.strftime('%Y-%m-%d %H:%M') if receipt is not None else '—'
    return ('<div class="rt-note">' + level + '；磁盘可用/总量 ' + disk
            + ('（已用' + f'{used:.1f}%' + '）' if used is not None and 0 <= used <= 100 else '')
            + '；内存可用/总量 ' + memory + '<br>最近验证备份：' + verified
            + '；异地回执：' + receipt_text + '。</div>')


def _quality(value):
    """Only verified finite counts can produce a percentage."""
    if not isinstance(value, dict):
        return None
    eligible = _integer(value.get('closed_eligible_count'))
    on_time = _integer(value.get('on_time_count'))
    if eligible is None or on_time is None or on_time > eligible:
        return None
    return value, eligible, on_time


def _rate(value):
    quality = _quality(value)
    if quality is None or quality[1] == 0:
        return '—'
    _, total, on_time = quality
    return f'{100 * on_time / total:.1f}%（{on_time}/{total}）'


def _latency_quantiles(recent):
    recent = recent if isinstance(recent, dict) else {}
    durations = recent.get('durations') if isinstance(recent.get('durations'), dict) else {}
    quantiles = durations.get('source_to_publish') if isinstance(durations.get('source_to_publish'), dict) else {}
    count = _integer(quantiles.get('sample_count'), 50)
    p50, p95 = _number(quantiles.get('p50')), _number(quantiles.get('p95'))
    if count is None or count == 0 or p50 is None or p95 is None or not 0 <= p50 <= p95:
        return '近50期 P50 — / P95 —（样本—）'
    return '近50期 P50 ' + _seconds(p50) + ' / P95 ' + _seconds(p95) + '（样本' + str(count) + '）'


def _stages(model):
    stages = model.get('stages')
    if not isinstance(stages, dict):
        return '阶段证据：—'
    labels = (('source_opened_at', '开奖'), ('input_frozen_at', '输入'),
              ('request_started_at', '请求'), ('response_completed_at', '响应'), ('published_at', '发布'))
    result = []
    for key, label in labels:
        stamp = _stamp(stages.get(key))
        title = stamp.strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）' if stamp else '无证据'
        result.append('<span title="' + title + '">' + label + ('✓' if stamp else '—') + '</span>')
    return ' · '.join(result)


def _row(key, label, raw, day):
    model = raw if isinstance(raw, dict) else {}
    status = _label(STATUSES, model.get('status'), '暂无证据')
    ref, target = _period(model.get('reference_period')), _period(model.get('target_period'))
    times = model.get('durations_seconds') if isinstance(model.get('durations_seconds'), dict) else {}
    quality = model.get('quality') if isinstance(model.get('quality'), dict) else {}
    today = quality.get('today')
    counters = today if isinstance(today, dict) else {}
    mismatch = '；页面目标待核对' if model.get('panel_matches') is False else ''
    diagnostic = '；操作时间不足' if model.get('error_code') == 'operation-window' else ''
    unavailable = model.get('error_code') == 'evidence-unavailable'
    unreadable = '；数据源不可读' if unavailable else ''
    phase = _label(PHASES, model.get('phase'), '阶段：—')
    if model.get('status')=='await_rule_start':
        active_start=_period(model.get('active_rule_start_target'))
        if active_start:phase+='（目标'+active_start+'起）'
    elapsed = _seconds(times.get('source_to_publish'))
    api_elapsed = _seconds(times.get('provider_elapsed'))
    return ('<tr data-runtime-model="' + key + '"><td><b>' + label + '</b>'
            '<div class="rt-mini">参考 ' + (ref or '—') + '<br>目标 ' + (target or '—') + '</div></td>'
            '<td class="rt-stage">' + status + ' / ' + phase + mismatch + diagnostic + unreadable + '<div class="rt-mini">' + _stages(model) + '</div></td>'
            '<td>' + elapsed + '<div class="rt-mini">API ' + api_elapsed + '<br>'
            + _latency_quantiles(quality.get('recent50')) + '</div></td>'
            '<td>封存 ' + _seconds(model.get('close_remaining_seconds'), signed=True) + _availability(key,model) + '</td>'
            '<td data-stats-business-date="' + day + '">' + ('—' if unavailable else _rate(today))
            + '<div class="rt-mini">重试 ' + ('—' if unavailable else _count(counters.get('retry_count')))
            + ' / 关盘余量&lt;90秒 ' + ('—' if unavailable else _count(counters.get('margin_under90_count'))) + '</div></td>'
            '<td>' + ('—' if unavailable else _rate(quality.get('recent50'))) + '</td>'
            '<td data-stats-business-date="' + day + '">' + ('— / — / —' if unavailable else ' / '.join(_count(counters.get(name)) for name in
                ('missing_count', 'api_timeout_count', 'strategy_rejected_count',
                 'other_rejected_count'))) + '</td></tr>')


def _availability(key,model):
    data=model.get('availability') if isinstance(model.get('availability'),dict) else {}
    ready=_stamp(data.get('server_ready_at'))
    status=data.get('status')
    if status not in ('verified','card-only') or ready is None:
        text='服务端攻击语就绪：未观测'
    elif status=='card-only':
        text='仅卡片已观测 '+ready.strftime('%H:%M:%S')+'，无可操作攻击语证据'
    else:
        text='攻击语提交已观测 '+ready.strftime('%H:%M:%S')+' / 余'+_seconds(data.get('server_ready_margin_seconds'),signed=True)
    return ('<div class="rt-mini">'+text+'</div><div class="rt-mini" data-penta5-live-derived="1" '
            'data-runtime-client-model="'+key+'" data-runtime-ready-at="'+(ready.isoformat() if ready else '')+
            '">本浏览器本次收到：未采集。</div>')


def _delivery_json(models):
    value={'version':1,'models':{}}
    for key,_ in MODELS:
        raw=models.get(key) if isinstance(models.get(key),dict) else {}
        availability=raw.get('availability') if isinstance(raw.get('availability'),dict) else {}
        identity=availability.get('event_id')
        identity=identity if isinstance(identity,str) and re.fullmatch('[0-9a-f]{64}',identity) else None
        value['models'][key]={'reference_period':_period(raw.get('reference_period')),
                             'target_period':_period(raw.get('target_period')),
                             'availability':{'event_id':identity}}
    return json.dumps(value,ensure_ascii=True,separators=(',',':'))


def _version_appendix(value):
    try:
        from strategy_comparison import render_version_comparison
        return render_version_comparison(value,identity='runtime-version-comparison')
    except (ValueError,TypeError,KeyError,IndexError,OverflowError):
        return '<div class="rt-note">共同版本样本：暂不可用，等待核验。</div>'


def render(summary):
    """Render one constant-height closed summary and optional evidence details."""
    header = _header(summary)
    data = summary if header else {}
    api = data.get('api') if isinstance(data.get('api'), dict) else {}
    source = data.get('source') if isinstance(data.get('source'), dict) else {}
    models = data.get('models') if isinstance(data.get('models'), dict) else {}
    round_data = data.get('round') if isinstance(data.get('round'), dict) else {}
    updated, start = header if header else (None, None)
    day = start.strftime('%Y-%m-%d') if start else '--'
    latest = _period(source.get('latest_reference'))
    enabled = api.get('enabled') if type(api.get('enabled')) is bool else None
    api_text = 'API启动' if enabled is True else 'API停止' if enabled is False else 'API—'
    total_count = round_data.get('total_count')
    active_count = total_count if type(total_count) is int and total_count > 0 else len(MODELS)
    completed = _integer(round_data.get('completed_count'), active_count)
    errors = _integer(round_data.get('error_count'), active_count)
    complete_text = str(completed) if completed is not None else '—'
    error_text = str(errors) if errors is not None else '—'
    line = api_text + ' · 数据至' + (latest[-3:] + '期' if latest else '—') + ' · 完成' + complete_text + '/' + str(active_count) + ' · 异常' + error_text
    unavailable = data.get('stats_error') is not False
    warning = '<div class="rt-warning">运行摘要暂不可用；没有将缺少的证据计作零次异常或成功。</div>' if unavailable else ''
    source_note = ('数据尚未就绪，等待最新连续真实开奖。' if source.get('source_ready') is False else
                   '数据就绪。' if source.get('source_ready') is True else '数据就绪状态：—。')
    meta = ('更新：' + (updated.strftime('%Y-%m-%d %H:%M:%S') if updated else '—') + '（北京时间）'
            '；完整数据期：' + (latest or '—') + '；本轮应到参考期：' + (_period(source.get('expected_reference')) or '—'))
    def row_label(key, fallback):
        value = models.get(key)
        dynamic = value.get('label') if isinstance(value, dict) else None
        return dynamic if isinstance(dynamic, str) and dynamic else fallback
    rows = ''.join(_row(key, row_label(key, label), models.get(key), day) for key, label in MODELS)
    return (START + '\n<section id="runtime-observability" data-runtime-updated="'
            + (updated.isoformat() if updated else '') + '">' + STYLE
            + '<details id="runtime-observability-details"><summary id="runtime-observability-line" aria-label="运行摘要，展开查看明细">'
            '<span id="runtime-observability-stale" class="rt-stale" data-penta5-live-derived="1" title="运行摘要待更新" hidden>旧</span>'
            '<span class="rt-summary-text">' + line + '</span></summary><div class="rt-body">'
            + warning + '<div class="rt-meta">' + meta + '<br>' + source_note + '</div>'
            '<div id="runtime-observability-freshness" class="rt-note" data-penta5-live-derived="1">摘要随页面静默更新。</div>'
            + _resources(data, updated)
            +
            '<div class="rt-table-scroll" tabindex="0" aria-label="各模型运行明细，可左右滚动"><table>'
            '<thead><tr><th>模型 / 期号</th><th>状态 / 阶段证据</th><th>开奖至发布耗时</th><th>发布距关盘</th>'
            '<th>今日按时率</th><th>最近50期按时率</th><th>今日缺期 / 超时 / 策略拒绝 / 其它拒绝</th></tr></thead>'
            '<tbody>' + rows + '</tbody></table></div>'
            +_version_appendix(data.get('version_comparison'))+
            '<div class="rt-note">手动停止不计失败；—表示暂无可核验证据。按时率只衡量发布时效，不是号码命中率。'
            '尚未关盘期次不进按时率分母；“开奖”时间不代表抓取到达时间。'
            '策略拒绝列只统计策略校验类拒绝；其它异常拒绝单列统计（也计入重试）。'
            '封存时间、服务端提交观测和本浏览器本次收到分别记录；观测不等于精确首次就绪，本机时间不参与正式样本。'
            '统计日按北京时间05:00起；详情可左右滚动。'
            '号码口径与全站一致：只用前4球(千/百/十/个)，第5球不参与。</div></div></details>'
            '<script type="application/json" id="runtime-observability-data">'+_delivery_json(models)+'</script>'
            + SCRIPT + '</section>\n' + END)


def apply(content, fragment):
    """Own one top-level range; preserve every other named fragment byte."""
    before, incoming = _inspect(content), _inspect(fragment)
    body = re.search(r'<body\b[^>]*>', content, re.I)
    if (body is None or set(incoming) != {MARKER} or incoming[MARKER][2] != fragment.strip()
            or len(_OWN_ID.findall(fragment)) != 1):
        raise ValueError('Invalid runtime observability fragment')
    own = before.get(MARKER)
    if own is None:
        if _OWN_ID.search(content):
            raise ValueError('Runtime observability id without markers')
        anchor = before.get('AI_API_CONTROL')
        if anchor is not None and anchor[3] != 0:
            raise ValueError('Runtime observability anchor must be top-level')
        position = anchor[1] if anchor else body.end()
        updated = content[:position] + '\n' + fragment + '\n' + content[position:]
    else:
        if own[3] != 0 or len(_OWN_ID.findall(content)) != 1:
            raise ValueError('Invalid runtime observability placement')
        updated = content[:own[0]] + fragment + content[own[1]:]
    after = _inspect(updated)
    if ({name: row[2] for name, row in before.items() if name != MARKER}
            != {name: row[2] for name, row in after.items() if name != MARKER}):
        raise ValueError('Runtime observability changed another named fragment')
    return updated


def publish(summary, *, root=ROOT):
    """Atomic shared panel writer; no provider calls or evidence-ledger writes."""
    from panel_io import update_panel
    fragment = render(summary)
    return update_panel(lambda content: apply(content, fragment), path=Path(root) / 'panel.html')
