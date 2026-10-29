"""Durable derived simulation, separate from model seals and real accounts."""
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
import fcntl,json

from ai_verified_stats import atomic_json
from stats_history import TZ,business_start,parse_time
from framework_fixed_pnl import CONFIG,CONFIG_SHA256,simulate_day
from framework_pnl_evidence import build_events
from ai_call_control import read_control_history
from ai_pnl_eligibility import check_history_prefix,filter_events
from framework_fixed_pnl_view import render_pnl,apply_pnl

ROOT=Path('/root/penta5-runner')
REVISION='framework-pnl-v2-fixed5000'
MODELS=('selected','six')
SOURCES={'selected':('selected_framework_seals.json','selected_framework_hit_stats.json'),
         'six':('selected_six_seals.json','selected_six_hit_stats.json')}
MAX_BYTES=256*1024*1024

def _canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def _checksum(value):
    return sha256(_canonical({key:item for key,item in value.items() if key!='checksum'}).encode('utf-8')).hexdigest()

def _read(path,*,missing=False):
    try:
        with Path(path).open('rb') as stream:raw=stream.read(MAX_BYTES+1)
    except FileNotFoundError:
        if missing:return None
        raise
    if len(raw)>MAX_BYTES:raise ValueError('pnl-state-too-large')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('pnl-invalid-file')
    return value

def _new_state():
    return {'version':1,'revision':REVISION,'config':deepcopy(CONFIG),'config_sha256':CONFIG_SHA256,
            'days':{},'archive':{},'updated_at':None}

def _read_state(path):
    value=_read(path,missing=True)
    if value is None:return _new_state()
    if (type(value.get('version')) is not int or value['version']!=1
            or value.get('revision')!=REVISION or value.get('config')!=CONFIG
            or value.get('config_sha256')!=CONFIG_SHA256
            or not isinstance(value.get('days'),dict) or not isinstance(value.get('archive'),dict)
            or value.get('checksum')!=_checksum(value)):
        raise ValueError('pnl-ledger-integrity')
    for day,entry in value['days'].items():
        _start(day)
        if (not isinstance(entry,dict) or set(entry.get('inputs',{}))!=set(MODELS)
                or set(entry.get('models',{}))!=set(MODELS)):
            raise ValueError('pnl-invalid-day-state')
        if any(not isinstance(entry['inputs'][model],list) for model in MODELS):
            raise ValueError('pnl-invalid-input-events')
        as_of=entry.get('as_of')
        if not isinstance(as_of,str):raise ValueError('pnl-invalid-saved-clock')
        clock=datetime.fromisoformat(as_of)
        if clock.tzinfo is None:raise ValueError('pnl-invalid-saved-clock')
        for model in MODELS:
            saved=entry['models'][model]
            if not isinstance(saved,dict) or type(saved.get('stats_error')) is not bool:
                raise ValueError('pnl-invalid-saved-result')
            if not saved['stats_error']:
                expected=simulate_day(model,entry['inputs'][model],_clock(day,clock.astimezone(TZ)))
                if _canonical(saved)!=_canonical(expected):raise ValueError('pnl-saved-accounting-changed')
    for day,entry in value['archive'].items():
        _start(day)
        if (not isinstance(entry,dict) or entry.get('biz_date')!=day or entry.get('complete') is not True
                or entry.get('config_sha256')!=CONFIG_SHA256
                or set(entry.get('models',{}))!=set(MODELS)
                or set(entry.get('audit_rows',{}))!=set(MODELS)):
            raise ValueError('pnl-invalid-archive')
        rows=entry['audit_rows']
        if entry.get('audit_sha256')!=sha256(_canonical(rows).encode('utf-8')).hexdigest():
            raise ValueError('pnl-archive-audit-changed')
        clock=datetime.fromisoformat(entry['as_of'])
        if clock.tzinfo is None:raise ValueError('pnl-invalid-archive-clock')
        for model in MODELS:
            expected=simulate_day(model,rows[model],_clock(day,clock.astimezone(TZ)))
            if (_canonical(expected['totals'])!=_canonical(entry['models'][model])
                    or _canonical(expected['events'])!=_canonical(rows[model])):
                raise ValueError('pnl-archive-accounting-changed')
    return value

def _start(day):
    value=datetime.strptime(day,'%Y-%m-%d').replace(tzinfo=TZ,hour=5)
    if value.strftime('%Y-%m-%d')!=day:raise ValueError('pnl-invalid-day')
    return value

def _clock(day,now):
    return min(now,_start(day)+timedelta(days=1,microseconds=-1))

def _prefix_unchanged(previous,current):
    """Never revise a settled cost, or an already assigned pending stake."""
    fresh={row['target_period']:row for row in current['events']}
    settled_fields=('status','number_count','proof_sha256','coverage_sha256','hit','actual_open_time',
                    'step','stake_cents','cost_cents','return_cents','net_cents','cumulative_net_cents','reset_reason')
    assigned_fields=('number_count','proof_sha256','coverage_sha256','step','stake_cents')
    for old in previous['events']:
        row=fresh.get(old['target_period'])
        if row is None:raise ValueError('pnl-prior-event-disappeared')
        if old['status']=='settled':
            if any(row.get(key)!=old.get(key) for key in settled_fields):
                raise ValueError('pnl-settled-event-changed')
        elif old['status']=='pending' and old.get('step') is not None:
            if row['status'] not in ('pending','settled') or any(row.get(key)!=old.get(key) for key in assigned_fields):
                raise ValueError('pnl-assigned-stake-changed')
        elif old['status']=='skip' and row['status']!='skip':
            raise ValueError('pnl-skipped-event-changed')

def _error(model,day,code):
    return {'model':model,'biz_date':day,'stats_error':True,'error_code':code,
            'events':[],'totals':{},'pending_count':0,'skipped_count':0,'blocked_count':0,
            'current_step':None,'current_stake_cents':None,'latest_settled_period':None}

def _day_complete(day,models):
    last=(_start(day)+timedelta(days=1,minutes=-5)).strftime('%Y%m%d')+'059'
    return all(not result.get('stats_error') and not result['pending_count'] and not result['blocked_count']
               and result['events'] and result['events'][-1]['target_period']==last
               for result in models.values())

def _archive(day,entry):
    # Full native publication evidence remains in the original hit/seal ledgers.
    # Retain every money row and proof digest here without duplicating its large
    # 50-draw prompt after this day is completely settled.
    rows={model:[{key:value for key,value in row.items() if key not in ('publication','actual')}
                 for row in entry['models'][model]['events']] for model in MODELS}
    return {'biz_date':day,'as_of':entry['as_of'],'config_sha256':CONFIG_SHA256,
            'models':{model:entry['models'][model]['totals'] for model in MODELS},
            'audit_rows':rows,'audit_sha256':sha256(_canonical(rows).encode('utf-8')).hexdigest(),
            'complete':True}

def _summary(state,now,models,*,error=False):
    start=business_start(now);day=start.strftime('%Y-%m-%d')
    history=[]
    for date,entry in sorted(state.get('archive',{}).items(),reverse=True):
        history.append({key:value for key,value in entry.items() if key!='audit_rows'})
    for date,entry in sorted(state.get('days',{}).items(),reverse=True):
        if date!=day:
            history.append({'biz_date':date,'as_of':entry.get('as_of'),'config_sha256':CONFIG_SHA256,
                            'complete':False,'models':{model:entry.get('models',{}).get(model,{}).get('totals',{})
                                                     for model in MODELS}})
    return {'version':1,'revision':REVISION,'biz_date':day,'business_start':start.isoformat(),
            'updated_at':now.isoformat(),'config':deepcopy(CONFIG),'config_sha256':CONFIG_SHA256,
            'models':models,'history':history,'stats_error':error}

def refresh_pnl(snapshot,*,root=ROOT,writer=None):
    """Caller owns stats refresh. Never opens an API, account or seal for writing."""
    root=Path(root);now=snapshot.get('now') if isinstance(snapshot,dict) else None
    if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('pnl-invalid-clock')
    now=now.astimezone(TZ);day=business_start(now).strftime('%Y-%m-%d')
    # The v1 per-number scenario remains an untouched historical artifact.
    # A different financial assumption starts a separate derived simulation.
    path=root/'data/framework_pnl_v2_ledger.json';path.parent.mkdir(parents=True,exist_ok=True)
    if writer is None:
        from panel_io import update_panel
        writer=update_panel
    with Path(str(path)+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            previous=_read_state(path)
        except (ValueError,TypeError,KeyError,OSError,OverflowError):
            summary=_summary(_new_state(),now,{model:_error(model,day,'pnl-ledger-integrity') for model in MODELS},error=True)
            # Preserve damaged raw state; an integrity failure never becomes a
            # clean empty history or a newly attractive P&L record.
            atomic_json(root/'data/framework_pnl_v2_summary.json',summary)
            writer(lambda page:apply_pnl(page,render_pnl(summary)),path=root/'panel.html')
            return summary
        state=deepcopy(previous);sources={};source_errors=set()
        try:
            control=read_control_history(root=root)
            check_history_prefix(previous.get('control_history'),control)
            state['control_history']=control
        except (ValueError,TypeError,KeyError,OSError,OverflowError):
            summary=_summary(previous,now,{model:_error(model,day,'pnl-control-integrity') for model in MODELS},error=True)
            atomic_json(root/'data/framework_pnl_v2_summary.json',summary)
            writer(lambda page:apply_pnl(page,render_pnl(summary)),path=root/'panel.html')
            return summary
        for model,(seal_file,hit_file) in SOURCES.items():
            try:
                # Native publishers write seals atomically. The hit ledger has
                # its own lock; take it one model at a time, never under panel IO.
                with Path(str(root/'data'/hit_file)+'.lock').open('a') as source_lock:
                    fcntl.flock(source_lock,getattr(fcntl,'LOCK_SH',fcntl.LOCK_EX))
                    sources[model]=(_read(root/'data'/seal_file),_read(root/'data'/hit_file))
            except (ValueError,TypeError,KeyError,OSError,OverflowError):source_errors.add(model)
        for date in sorted(set(state['days'])|{day}):
            old=previous['days'].get(date,{'inputs':{model:[] for model in MODELS},'models':{},'as_of':now.isoformat()})
            entry={'inputs':deepcopy(old['inputs']),'models':{},'as_of':now.isoformat()}
            for model in MODELS:
                try:
                    if model in source_errors:raise ValueError('pnl-source-unavailable')
                    events=build_events(model,snapshot,*sources[model],previous_events=old['inputs'][model],biz_date=date)
                    events=filter_events(model,events,old['inputs'][model],control)
                    result=simulate_day(model,events,_clock(date,now))
                    if old['inputs'][model]:
                        baseline=simulate_day(model,old['inputs'][model],_clock(date,now))
                        _prefix_unchanged(baseline,result)
                    entry['inputs'][model]=events;entry['models'][model]=result
                except (ValueError,TypeError,KeyError,OSError,OverflowError):
                    entry['models'][model]=_error(model,date,'pnl-evidence-or-accounting-integrity')
            state['days'][date]=entry
            if date!=day and _day_complete(date,entry['models']):
                state['archive'][date]=_archive(date,entry);del state['days'][date]
        state['updated_at']=now.isoformat();state['checksum']=_checksum(state)
        if state!=previous:atomic_json(path,state)
        models=state['days'][day]['models'];summary=_summary(state,now,models)
        atomic_json(root/'data/framework_pnl_v2_summary.json',summary)
        writer(lambda page:apply_pnl(page,render_pnl(summary)),path=root/'panel.html')
        return summary
