"""Observe local AI jobs and publish a separate, verified status banner."""
from contextlib import contextmanager
from datetime import timedelta
from hashlib import sha256
from html import escape
from pathlib import Path
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None

from app_secrets import safe_error
from stats_history import beijing_now, load_snapshot, parse_period, parse_time, period_slot
from ai_verified_stats import next_period

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT_SECONDS = 270
JOBS = {
    'framework': {'script': 'lib/auto_r1_framework.py', 'marker': 'R1_FRAMEWORK',
                  'seals': 'r1_framework_seals.json', 'label': '杀码框架', 'copy': 'r1_copy_btn'},
    'killer': {'script': 'lib/auto_r1_killer.py', 'marker': 'R1_KILLER',
               'seals': 'r1_stats.json', 'label': '智能排除', 'copy': 'r1_killer_copy_btn'},
}


def clean_error(value):
    text = safe_error(value)
    text = re.sub(r'sk-[A-Za-z0-9_-]+', '[REDACTED]', text)
    text = re.sub(r'(?i)((?:api[_-]?key|password|token|secret)\s*["\x27]?\s*[:=]\s*)[^\s,;]+',
                  r'\1[REDACTED]', text)
    for name, secret in os.environ.items():
        if re.search(r'(?i)(key|password|token|secret)', name) and len(secret) >= 8:
            text = text.replace(secret, '[REDACTED]')
    return text


@contextmanager
def file_lock(path, nonblocking=False):
    if fcntl is None:
        raise RuntimeError('AI job observation requires local file locking')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
        fcntl.flock(handle, flags)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def atomic_status(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            temporary = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _read_status_unlocked(root):
    path = Path(root) / 'data' / 'ai_job_status.json'
    if not path.exists():
        return {'version': 1, 'jobs': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('jobs'), dict):
        raise ValueError('Invalid AI job status; existing status was preserved')
    return data


def read_status(root=ROOT):
    with file_lock(Path(root) / 'data' / 'ai_job_status.lock'):
        return _read_status_unlocked(root)


def update_status(kind, patch, root=ROOT, clock=beijing_now):
    if kind not in JOBS:
        raise ValueError('Unknown AI job')
    with file_lock(Path(root) / 'data' / 'ai_job_status.lock'):
        data = _read_status_unlocked(root)
        previous = data['jobs'].get(kind, {})
        if not isinstance(previous, dict):
            raise ValueError('Invalid existing job status')
        data['jobs'][kind] = {**previous, **patch}
        data['updated_at'] = clock().isoformat()
        atomic_status(Path(root) / 'data' / 'ai_job_status.json', data)
        return data['jobs'][kind]


def _marker_fragment(content, marker):
    start, end = '<!-- ' + marker + '_START -->', '<!-- ' + marker + '_END -->'
    if content.count(start) != 1 or content.count(end) != 1:
        return None
    found = re.search(re.escape(start) + r'.*?' + re.escape(end), content, re.S)
    return found.group(0) if found else None


def inspect_publication(kind, current_ref, root=ROOT, now=None):
    """Trust both the real card and durable publication evidence, never process text."""
    config = JOBS[kind]
    result = {'valid': False, 'current': False, 'ref': '', 'error': 'unverified-publication'}
    try:
        content = (Path(root) / 'panel.html').read_text(encoding='utf-8')
        card = _marker_fragment(content, config['marker'])
        if card is None:
            result['error'] = 'missing-or-ambiguous-card'
            return result
        text = re.sub(r'<[^>]*>', ' ', card)
        match = re.search(r'(?:最新)?参考期号\s*[:：]\s*([0-9]{11})', text)
        if not match or not parse_period(match.group(1)):
            result['error'] = 'missing-card-reference'
            return result
        reference = match.group(1)
        result['ref'] = reference
        stats = json.loads((Path(root) / 'data' / config['seals']).read_text(encoding='utf-8'))
        seals = stats.get('prediction_seals', {}) if isinstance(stats, dict) else {}
        seal = seals.get(reference) if isinstance(seals, dict) else None
        if not isinstance(seal, dict):
            result['error'] = 'missing-durable-publication'
            return result
        target = next_period(reference)
        generated, published = parse_time(seal.get('generated_at')), parse_time(seal.get('published_at'))
        reference_time = parse_time(seal.get('reference_open_time'))
        deadline = period_slot(target)
        current = now or beijing_now()
        reference_slot = period_slot(reference)
        if (seal.get('evidence_version') != 2 or seal.get('publication_state') != 'published'
                or seal.get('ref_period') != reference or seal.get('target_period') != target
                or not generated or not published or not reference_time or not deadline
                or not reference_slot <= reference_time < reference_slot + timedelta(minutes=1)
                or not reference_time <= generated <= published <= current
                or not published < deadline
                or str(seal.get('card_html', '')).strip() != card.strip()):
            result['error'] = 'invalid-or-mismatched-publication'
            return result
        result.update(valid=True, current=reference == str(current_ref) and current < deadline - timedelta(seconds=70),
                      published_at=published.isoformat(), deadline=deadline.isoformat(),
                      fingerprint=sha256((reference + published.isoformat() + card).encode('utf-8')).hexdigest(),
                      error='' if reference == str(current_ref) and current < deadline - timedelta(seconds=70) else 'stale-reference')
        return result
    except (OSError, ValueError, TypeError, AttributeError) as error:
        result['error'] = clean_error(error)[:180]
        return result


def latest_reference(root=ROOT, now=None):
    snapshot = load_snapshot(state_path=Path(root) / 'data' / 'state.json',
                             cache_path=Path(root) / 'data' / 'stats_draw_history.json',
                             now=now or beijing_now(), persist=False)
    reference = str((snapshot.get('latest') or {}).get('period') or '')
    if not parse_period(reference):
        raise ValueError('No validated current draw reference')
    return reference


STATUS_TEXT = {
    'running': '正在更新', 'success': '已发布当前结果', 'current': '当前结果已封存',
    'failed': '更新失败', 'timeout': '更新超时', 'stale': '结果已过期，请勿使用',
    'unverified': '发布未核实，请勿使用',
}


def friendly_error(state, detail=''):
    if re.search(r'(?i)\bHTTP\s*402\b|余额不足', str(detail)):
        return 'DeepSeek账户余额不足（HTTP 402），本轮未更新；请补充余额后等待下一轮。'
    if state == 'timeout':
        return '本轮更新超时，将在下轮重试；请勿使用过期结果。'
    if state == 'stale':
        return '最新结果尚未更新，请勿使用旧结果；将于下轮重试。'
    if state == 'unverified':
        return '本轮尚未核实最新结果，请勿使用旧结果；将于下轮重试。'
    if re.search(r'(?i)valueerror|schema|invalid.*response|keep set|he2|he3|json|数据格式', str(detail)):
        return '返回数据格式不合规，本轮未更新，将在下轮重试。'
    if re.search(r'(?i)http|request|connection|timeout|urllib|接口', str(detail)):
        return '模型接口暂不可用，本轮未更新，将在下轮重试。'
    return '本轮未更新，将在下轮重试；请核对最新成功参考期。'


def banner_html(data):
    rows = []
    for kind, config in JOBS.items():
        status = data.get('jobs', {}).get(kind, {})
        state = str(status.get('status') or 'unverified')
        message = STATUS_TEXT.get(state, '尚未运行')
        reference = str(status.get('last_success_ref') or '--')
        verified_ref = str(status.get('verified_ref') or '')
        deadline = str(status.get('target_deadline') or '')
        try:
            target = next_period(verified_ref) if parse_period(verified_ref) else ''
            if not target or parse_time(deadline) != period_slot(target):
                target = ''
        except (ValueError, TypeError, OverflowError):
            target = ''
        error = friendly_error(state, status.get('error')) if status.get('error') else ''
        rows.append(
            '<div class="ai-job-row" data-ai-kind="' + kind + '" data-ai-status="' + escape(state, quote=True)
            + '" data-ai-ref="' + escape(verified_ref, quote=True) + '" data-ai-deadline="' + escape(deadline, quote=True)
            + '" data-ai-target="' + escape(target, quote=True) + '">'
            + '<b>' + config['label'] + '</b>：<span class="ai-job-state">' + escape(message) + '</span>'
            + ' ｜ 最新成功参考期：<span>' + escape(reference) + '</span>'
            + ('<div style="font-size:12px;color:#991b1b;">' + escape(error) + '</div>' if error else '')
            + '<div class="ai-job-warning" style="color:#b91c1c;font-weight:700;"></div></div>')
    return ('<!-- AI_JOB_STATUS_START -->\n'
            '<div id="ai_job_status" style="max-width:1050px;margin:12px auto;padding:12px 16px;border:1px solid #f59e0b;border-radius:8px;background:#fffbeb;line-height:1.9;">'
            '<b>AI 更新状态</b>' + ''.join(rows) + '</div>\n' + BANNER_SCRIPT + '\n' + TARGET_SCRIPT
            + '\n<!-- AI_JOB_STATUS_END -->')


BANNER_SCRIPT = """<script id="ai-job-status-control">
(function(){
  var installed=window.__PENTA5_AI_COPY_CONTROL__;
  if(installed&&installed.version==='http-copy-v2-platform-close'&&typeof installed.check==='function'){installed.check();return;}
  var pending={framework:false,killer:false};
  function availability(row,current,now){
    var ref=row?row.getAttribute('data-ai-ref')||'':'';
    var deadline=Date.parse(row?row.getAttribute('data-ai-deadline')||'':'');
    var state=row?row.getAttribute('data-ai-status')||'':'';
    var closed=/^[0-9]{11}$/.test(current)&&ref===current&&Number.isFinite(deadline)&&Number.isFinite(now)&&now>=deadline-70000;
    var expired=!/^[0-9]{11}$/.test(current)||ref!==current||!Number.isFinite(deadline)||!Number.isFinite(now)||now>=deadline-70000;
    return {closed:closed,expired:expired,unavailable:expired||['success','current','running'].indexOf(state)<0,state:state};
  }
  function reference(){return ((document.getElementById('disp_period')||{}).textContent||'').trim();}
  function check(){
    var current=reference(),now=Date.now();
    document.querySelectorAll('#ai_job_status .ai-job-row').forEach(function(row){
      var result=availability(row,current,now),expired=result.expired,state=result.state;
      var label=row.querySelector('.ai-job-state');
      if(label){var base=label.getAttribute('data-base-text')||label.textContent;label.setAttribute('data-base-text',base);label.textContent=result.closed?'已关盘，仅供核对':expired&&['success','current'].indexOf(state)>=0?'等待本期更新':base;}
      var unavailable=result.unavailable;
      var warning=row.querySelector('.ai-job-warning');
      if(warning)warning.textContent=result.closed?'平台已在开奖前70秒关盘，号码保留供核对，原封存复制已停止。':expired?'此结果未对应最新开奖或已过期，请勿使用。':(unavailable?'本次更新未核实，请勿使用旧结果。':'');
      var id=row.getAttribute('data-ai-kind')==='framework'?'r1_copy_btn':'r1_killer_copy_btn';
      document.querySelectorAll('button[id="'+id+'"]').forEach(function(button){
        button.disabled=unavailable||pending[row.getAttribute('data-ai-kind')]===true;
        button.title=unavailable?'结果未更新或已过期，暂不可复制':'';
      });
    });
  }
  function copySource(kind){
    var id=kind==='framework'?'r1_framework_attack_text':'r1_killer_attack_text';
    var nodes=document.querySelectorAll('[id="'+id+'"]');
    return nodes.length===1?String(nodes[0].innerText==null?nodes[0].textContent:nodes[0].innerText).trim():'';
  }
  function currentToken(kind,text){
    var rows=document.querySelectorAll('#ai_job_status .ai-job-row[data-ai-kind="'+kind+'"]');
    if(rows.length!==1||availability(rows[0],reference(),Date.now()).unavailable||!text||copySource(kind)!==text)return null;
    return {kind:kind,text:text,ref:rows[0].getAttribute('data-ai-ref'),deadline:rows[0].getAttribute('data-ai-deadline')};
  }
  function tokenStillCurrent(token){
    var latest=currentToken(token.kind,token.text);
    return latest&&latest.ref===token.ref&&latest.deadline===token.deadline;
  }
  function legacyCopy(token){
    if(!tokenStillCurrent(token))return 'stale';
    var area=document.createElement('textarea'),active=document.activeElement,ok=false;
    area.value=token.text;area.readOnly=true;area.setAttribute('aria-hidden','true');
    area.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;pointer-events:none;';
    document.body.appendChild(area);
    try{
      area.focus({preventScroll:true});area.select();area.setSelectionRange(0,area.value.length);
      ok=typeof document.execCommand==='function'&&document.execCommand('copy')===true;
    }catch(error){ok=false;}
    finally{
      area.remove();
      try{if(active&&typeof active.focus==='function')active.focus({preventScroll:true});}catch(error){}
    }
    return ok?'copied':'failed';
  }
  function copyCurrent(token){
    if(!tokenStillCurrent(token))return Promise.resolve('stale');
    try{
      if(navigator.clipboard&&typeof navigator.clipboard.writeText==='function'){
        return Promise.resolve(navigator.clipboard.writeText(token.text)).then(function(){return 'copied';},function(){return legacyCopy(token);});
      }
    }catch(error){}
    return Promise.resolve(legacyCopy(token));
  }
  function copyResult(kind,result){
    pending[kind]=false;check();
    if(result==='failed'){window.alert('自动复制失败，请手动选中攻击语复制。');return;}
    if(result!=='copied')return;
    var id=kind==='framework'?'r1_copy_btn':'r1_killer_copy_btn';
    document.querySelectorAll('button[id="'+id+'"]').forEach(function(button){
      var label=button.getAttribute('data-ai-copy-label')||button.textContent;
      button.setAttribute('data-ai-copy-label',label);button.textContent='✅ 复制成功';
      setTimeout(function(){button.textContent=label;},1500);
    });
  }
  // Check at the actual click too: background tabs can delay periodic timers.
  document.addEventListener('click',function(event){
    var button=event.target;
    while(button&&button!==document&&!(button.tagName==='BUTTON'&&['r1_copy_btn','r1_killer_copy_btn'].indexOf(button.id)>=0))button=button.parentNode;
    if(!button||button===document)return;
    var kind=button.id==='r1_copy_btn'?'framework':'killer';
    var token=currentToken(kind,copySource(kind));
    event.preventDefault();event.stopImmediatePropagation();
    check();
    if(!token||pending[kind])return;
    pending[kind]=true;check();
    copyCurrent(token).then(function(result){copyResult(kind,result);},function(){copyResult(kind,'failed');});
  },true);
  window.__PENTA5_AI_COPY_CONTROL__={version:'http-copy-v2-platform-close',check:check};
  check();
  window.addEventListener('load',check);
  window.addEventListener('pageshow',check);
  document.addEventListener('visibilitychange',check);
  setInterval(check,1000);
})();
</script>"""


TARGET_SCRIPT = """<script id="ai-target-period-control">
(function(){
  var version='target-period-v1';
  var definitions={framework:{marker:'R1_FRAMEWORK',copy:'r1_copy_btn'},killer:{marker:'R1_KILLER',copy:'r1_killer_copy_btn'}};
  function pad(value,width){return String(value).padStart(width||2,'0');}
  function parsePeriod(value){
    if(typeof value!=='string'||!/^[0-9]{11}$/.test(value))return null;
    var year=Number(value.slice(0,4)),month=Number(value.slice(4,6)),day=Number(value.slice(6,8)),index=Number(value.slice(8));
    var date=new Date(0);date.setUTCFullYear(year,month-1,day);date.setUTCHours(0,0,0,0);
    if(year<1||index<1||index>288||date.getUTCFullYear()!==year||date.getUTCMonth()!==month-1||date.getUTCDate()!==day)return null;
    return {day:date.getTime(),index:index,slot:date.getTime()-8*3600000+index*300000};
  }
  function nextPeriod(reference,parsed){
    if(parsed.index<288)return reference.slice(0,8)+pad(parsed.index+1,3);
    var day=new Date(parsed.day+86400000);
    return pad(day.getUTCFullYear(),4)+pad(day.getUTCMonth()+1)+pad(day.getUTCDate())+'001';
  }
  function stamp(value){
    var day=new Date(value+8*3600000);
    return pad(day.getUTCFullYear(),4)+'-'+pad(day.getUTCMonth()+1)+'-'+pad(day.getUTCDate())+' '+pad(day.getUTCHours())+':'+pad(day.getUTCMinutes())+':'+pad(day.getUTCSeconds());
  }
  function cardFor(kind,reference){
    if(typeof NodeFilter==='undefined'||!document.createTreeWalker)return null;
    var definition=definitions[kind],walker=document.createTreeWalker(document.body||document,NodeFilter.SHOW_COMMENT),node;
    var starts=[],ends=[];
    while((node=walker.nextNode())){
      var name=String(node.data||'').trim();
      if(name===definition.marker+'_START')starts.push(node);
      if(name===definition.marker+'_END')ends.push(node);
    }
    if(starts.length!==1||ends.length!==1||starts[0].parentNode!==ends[0].parentNode)return null;
    var cursor=starts[0].nextSibling,card=null;
    for(;cursor&&cursor!==ends[0];cursor=cursor.nextSibling){
      if(cursor.nodeType===1&&cursor.tagName==='DIV'){if(card)return null;card=cursor;}
      else if(cursor.nodeType===1&&cursor.tagName!=='SCRIPT')return null;
      else if(cursor.nodeType===3&&String(cursor.textContent||'').trim())return null;
    }
    if(!cursor||!card||!card.firstElementChild)return null;
    var copies=card.querySelectorAll('button[id="'+definition.copy+'"]');
    if(copies.length!==1||document.querySelectorAll('button[id="'+definition.copy+'"]').length!==1)return null;
    var header=card.firstElementChild,headings=Array.prototype.filter.call(header.querySelectorAll('span'),function(item){
      var match=String(item.textContent||'').match(/(?:最新)?参考期号\\s*[:：]\\s*([0-9]{11})/);
      return match&&match[1]===reference;
    });
    return headings.length===1?{card:card,header:header}:null;
  }
  function check(){
    var currentNodes=document.querySelectorAll('[id="disp_period"]');
    var current=currentNodes.length===1?String(currentNodes[0].textContent||'').trim():'';
    var now=Date.now();
    document.querySelectorAll('[data-ai-target-badge]').forEach(function(badge){
      if(!Object.prototype.hasOwnProperty.call(definitions,badge.getAttribute('data-ai-target-badge')))badge.remove();
    });
    Object.keys(definitions).forEach(function(kind){
      var rows=document.querySelectorAll('#ai_job_status .ai-job-row[data-ai-kind="'+kind+'"]');
      var row=rows.length===1?rows[0]:null;
      var reference=row?row.getAttribute('data-ai-ref')||'':'';
      var parsed=parsePeriod(reference),target=parsed?nextPeriod(reference,parsed):'';
      var targetParsed=parsePeriod(target),deadline=row?Date.parse(row.getAttribute('data-ai-deadline')||''):NaN;
      var state=row?row.getAttribute('data-ai-status')||'':'';
      var valid=parsed&&targetParsed&&reference===current&&row.getAttribute('data-ai-target')===target
        &&Number.isFinite(now)&&Number.isFinite(deadline)&&deadline===targetParsed.slot&&now>=parsed.slot&&now<deadline
        &&['success','current','running'].indexOf(state)>=0;
      var host=valid?cardFor(kind,reference):null,badge=null;
      document.querySelectorAll('[data-ai-target-badge="'+kind+'"]').forEach(function(item){
        if(host&&item.tagName==='DIV'&&item.parentNode===host.card&&!badge)badge=item;else item.remove();
      });
      if(!host)return;
      if(!badge){badge=document.createElement('div');badge.setAttribute('data-ai-target-badge',kind);}
      badge.setAttribute('data-ai-target-reference',reference);badge.setAttribute('data-ai-target-period',target);
      badge.setAttribute('data-ai-target-deadline',row.getAttribute('data-ai-deadline'));
      badge.setAttribute('role','note');
      badge.style.cssText='display:block;box-sizing:border-box;max-width:100%;margin:8px 0;padding:8px 10px;border:1px solid #93c5fd;border-radius:6px;background:#eff6ff;color:#1e3a8a;font-size:14px;font-weight:700;line-height:1.8;white-space:normal;overflow-wrap:anywhere;';
      badge.textContent='预测目标期：'+target+' ｜ 参考期：'+reference+' ｜ 参考截止：'+stamp(deadline)+'（北京时间）';
      host.card.insertBefore(badge,host.header.nextSibling);
    });
  }
  function attach(state){
    window.addEventListener('load',state.check);window.addEventListener('pageshow',state.check);document.addEventListener('visibilitychange',state.check);
    if(state.timer!=null)clearInterval(state.timer);state.timer=setInterval(state.check,1000);state.check();
  }
  var prior=window.__PENTA5_AI_TARGET_PERIOD__;
  if(prior&&prior.version===version&&typeof prior.check==='function'){attach(prior);return;}
  if(prior){if(prior.timer!=null)clearInterval(prior.timer);if(typeof prior.check==='function'){
    window.removeEventListener('load',prior.check);window.removeEventListener('pageshow',prior.check);document.removeEventListener('visibilitychange',prior.check);
  }}
  var state={version:version,check:check,timer:null};window.__PENTA5_AI_TARGET_PERIOD__=state;attach(state);
})();
</script>"""


def refresh_banner(root=ROOT, panel_writer=None):
    if panel_writer is None:
        from panel_io import update_panel
        panel_writer = update_panel

    def transform(content):
        block = banner_html(read_status(root))
        pattern = r'<!-- AI_JOB_STATUS_START -->.*?<!-- AI_JOB_STATUS_END -->'
        if re.search(pattern, content, re.S):
            return re.sub(pattern, lambda _: block, content, count=1, flags=re.S)
        if '</body>' not in content:
            raise ValueError('Panel body missing; status banner was not written')
        return content.replace('</body>', block + '\n</body>', 1)

    return panel_writer(transform, path=Path(root) / 'panel.html')


def append_log(kind, run_id, output, root=ROOT):
    path = Path(root) / 'data' / ('ai_job_' + kind + '.log')
    path.parent.mkdir(parents=True, exist_ok=True)
    text = clean_error(output)
    if len(text) > 65536:
        text = '[output truncated]\n' + text[-65536:]
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, 'a', encoding='utf-8') as handle:
        os.chmod(path, 0o600)
        handle.write('[' + run_id + '] ' + text + '\n')


def _proof_patch(proof):
    result = {'verified_ref': proof.get('ref', '') if proof.get('valid') else '',
              'target_deadline': proof.get('deadline', '') if proof.get('valid') else ''}
    if proof.get('valid'):
        result.update(last_success_ref=proof['ref'], last_success_at=proof['published_at'])
    return result


def run_job(kind, root=ROOT, runner=None, clock=beijing_now, snapshot_loader=None, panel_writer=None):
    if kind not in JOBS:
        raise ValueError('Unknown AI job')
    from ai_call_control import AICallsDisabled,require_ai_enabled
    try:require_ai_enabled(root=root,clock=clock)
    except AICallsDisabled:return {'status':'paused','exit_code':0}
    root = Path(root)
    loader = snapshot_loader or (lambda: latest_reference(root, clock()))
    execute = runner or subprocess.run
    run_id = uuid.uuid4().hex
    try:
        with file_lock(root / 'data' / ('ai_job_' + kind + '.wrapper.lock'), nonblocking=True):
            try:
                reference = loader()
                if not parse_period(reference):
                    raise ValueError('Invalid current reference')
            except Exception as error:
                append_log(kind, run_id, clean_error(error), root)
                update_status(kind, {'status': 'failed', 'error': friendly_error('failed', error),
                                     'finished_at': clock().isoformat(), 'verified_ref': '', 'target_deadline': ''}, root, clock)
                refresh_banner(root, panel_writer)
                return {'status': 'failed', 'exit_code': 1}
            before = inspect_publication(kind, reference, root, clock())
            update_status(kind, {'status': 'running', 'run_id': run_id, 'started_at': clock().isoformat(),
                                 'current_ref': reference, 'error': '', **_proof_patch(before)}, root, clock)
            refresh_banner(root, panel_writer)
            timed_out, returncode, error_text = False, None, ''
            try:
                require_ai_enabled(root=root,clock=clock)
                child = execute([sys.executable, str(root / JOBS[kind]['script'])], cwd=str(root),
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=TIMEOUT_SECONDS, check=False)
                returncode = child.returncode
                output = str(child.stdout or '') + '\n' + str(child.stderr or '')
                append_log(kind, run_id, output, root)
                if returncode != 0:
                    error_text = '本地任务退出码 ' + str(returncode) + '；' + clean_error(output).strip().split('\n')[-1][:120]
            except subprocess.TimeoutExpired as error:
                timed_out = True
                append_log(kind, run_id, str(error.stdout or '') + '\n' + str(error.stderr or '') + '\njob timeout', root)
                error_text = f'任务超过{TIMEOUT_SECONDS}秒，已停止本次更新。'
            except Exception as error:
                error_text = clean_error(error)[:180]
                append_log(kind, run_id, error_text, root)
            try:
                require_ai_enabled(root=root,clock=clock)
            except AICallsDisabled:
                update_status(kind,{'status':'paused','finished_at':clock().isoformat(),'error':'',
                                    'verified_ref':'','target_deadline':''},root,clock)
                return {'status':'paused','exit_code':0}
            try:
                current_ref = loader()
                after = inspect_publication(kind, current_ref, root, clock())
            except Exception as error:
                current_ref, after = '', {'valid': False, 'current': False, 'error': clean_error(error)[:180]}
            if timed_out:
                status = 'timeout'
            elif returncode != 0:
                status = 'failed'
            elif not after.get('valid'):
                status = 'unverified'
            elif not after.get('current'):
                status = 'stale'
            else:
                status = 'current' if before.get('fingerprint') == after.get('fingerprint') else 'success'
            if not error_text and status not in ('success', 'current'):
                error_text = '未核实当前期号的有效发布：' + str(after.get('error') or 'unknown')
                append_log(kind, run_id, error_text, root)
            update_status(kind, {'status': status, 'finished_at': clock().isoformat(),
                                 'returncode': returncode, 'current_ref': current_ref,
                                 'error': friendly_error(status, error_text) if error_text else '', **_proof_patch(after)}, root, clock)
            refresh_banner(root, panel_writer)
            return {'status': status, 'exit_code': 0 if status in ('success', 'current') else 1}
    except BlockingIOError:
        return {'status': 'already-running', 'exit_code': 0}

