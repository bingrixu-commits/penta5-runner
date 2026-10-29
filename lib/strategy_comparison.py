"""Read-only, exact-input comparison of independent strategy publications.

This is derived presentation. It never selects numbers, calls providers, changes
source seals, or joins different policies into one attractive historical rate.
"""
from datetime import datetime,timedelta
from hashlib import sha256
from html import escape
from pathlib import Path
import json,re

from ai_verified_stats import atomic_json,next_period,publication_evidence_error,prediction_outcome
from stats_history import TZ,business_start,parse_period,parse_time,period_slot,valid_draw_record,validate_conflicting_periods
from strategy_features import (build_strategy,STRATEGY_LABELS,KILLER_STRATEGY,FRAMEWORK_STRATEGY,PRO4_STRATEGY,SELECTED_STRATEGY,SIX_STRATEGY,
    LEGACY_KILLER_STRATEGY,LEGACY_FRAMEWORK_STRATEGY,LEGACY_PRO4_STRATEGY,LEGACY_SELECTED_STRATEGY,LEGACY_SIX_STRATEGY,
    ISOLATED_SELECTED_STRATEGY)
from operational_availability import load_delivery, observation, index_delivery

ROOT=Path('/root/penta5-runner')
ORIGINAL_MODELS=('killer','framework','pro4','selected')
MODELS=ORIGINAL_MODELS+('six',)
DISPLAY_MODELS=tuple(model for model in MODELS if model!='selected')
LABELS={'killer':'Flash 智能深排','framework':'Flash 固定八码框架','pro4':'Pro 四位独立单排','selected':'Pro 自选七项','six':'Pro 自选八项（独立）'}
STRATEGIES=dict(zip(MODELS,(KILLER_STRATEGY,FRAMEWORK_STRATEGY,PRO4_STRATEGY,SELECTED_STRATEGY,SIX_STRATEGY)))
LEGACY_STRATEGIES=dict(zip(MODELS,(LEGACY_KILLER_STRATEGY,LEGACY_FRAMEWORK_STRATEGY,LEGACY_PRO4_STRATEGY,LEGACY_SELECTED_STRATEGY,LEGACY_SIX_STRATEGY)))
SUPPORTED_STRATEGIES={model:frozenset((STRATEGIES[model],LEGACY_STRATEGIES[model])) for model in MODELS}
SUPPORTED_STRATEGIES['selected']=SUPPORTED_STRATEGIES['selected']|{ISOLATED_SELECTED_STRATEGY}
FILES={'killer':'r1_stats.json','framework':'r1_framework_seals.json','selected':'selected_framework_seals.json','six':'selected_six_seals.json'}
POSITIONS=('q','b','s','g');POSITION_LABELS=('千位','百位','十位','个位')
START='<!-- STRATEGY_BOARD_DERIVED_START -->';END='<!-- STRATEGY_BOARD_DERIVED_END -->'

def _canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def _read(path):
    with path.open('rb') as stream:raw=stream.read(128*1024*1024+1)
    if len(raw)>128*1024*1024:raise ValueError('oversized-strategy-source')
    value=json.loads(raw)
    if not isinstance(value,dict):raise ValueError('invalid-strategy-source')
    return value
def _strategy(seal,model):
    if model in ('killer','framework'):
        audit=seal.get('model_context_audit');body=audit.get('body') if isinstance(audit,dict) else None
        return body.get('strategy') if isinstance(body,dict) else None
    document=seal.get('input_doc')
    return document.get('strategy') if isinstance(document,dict) else None
def _period_at(at):
    if at.hour==at.minute==0:return (at-timedelta(days=1)).strftime('%Y%m%d')+'288'
    return at.strftime('%Y%m%d')+str((at.hour*60+at.minute)//5).zfill(3)
def _history(reference,actuals):
    end=period_slot(reference)
    return [actuals[_period_at(end-timedelta(minutes=5*offset))] for offset in range(49,-1,-1)]
def _metadata(seal,reference,model):
    value=seal.get('model_metadata');family='flash' if model in ('killer','framework') else 'pro'
    pattern=re.compile(r'deepseek-(?:v4(?:\.1)?-)?'+family+r'(?:-[0-9]{4})?')
    if (not isinstance(value,dict) or value.get('ref')!=reference or value.get('finish_reason')!='stop'
            or not all(isinstance(value.get(key),str) and pattern.fullmatch(value[key]) for key in ('requested_model','returned_model'))):
        raise ValueError('unverified-strategy-model')
    return value

def _publication(seal,model,now,actuals,root):
    reference=seal['ref_period'];strategy=_strategy(seal,model)
    if model in ('killer','framework'):
        from stat50_compare import _archive,_killer_proof
        from framework_hit_stats import _publication as framework_publication
        proof=_killer_proof(seal,now) if model=='killer' else framework_publication(seal,now)
        history=_history(reference,actuals)
        _archive(root,model,reference,seal.get('model_context_audit'),history,parse_time(proof['generated_at']),now)
        if seal['model_context_audit']['body'].get('cross_model_advice')!='disabled' or seal['model_context_audit']['body'].get('sources')!=[]:
            raise ValueError('shared-advice-not-independent')
    else:
        from publication_validation_cache import validate_publication as cached_publication
        cache_model={'pro4':'pro4','six':'selected6','selected':'selected7'}[model]
        cached_publication(cache_model,seal,reference,now,root=root)
        proof=seal;history=seal['history']
    identity=strategy.get('strategy_id') if isinstance(strategy,dict) else None
    if identity not in SUPPORTED_STRATEGIES[model]:
        raise ValueError('unsupported-model-strategy')
    # Reconstruct the version that was actually archived. A new default must
    # never turn a valid historical seal into a different-strategy rejection.
    expected=build_strategy(history,reference,identity)
    if any(actuals.get(row['period'])!=row for row in history):raise ValueError('strategy-source-history-unavailable-or-changed')
    if _canonical(strategy)!=_canonical(expected):raise ValueError('strategy-feature-mismatch')
    metadata=_metadata(seal,reference,model)
    response=proof['response']
    if model=='killer':positions={key:response['k'+str(i+1)][0] for i,key in enumerate(POSITIONS)}
    elif model=='framework':positions={'q':response['q_kill1'],'b':response['b_kill1']}
    elif model=='pro4':positions={key:response['k'+str(i+1)][0] for i,key in enumerate(POSITIONS)}
    elif model=='six':positions=dict(response['selected_positions'])
    else:
        positions=dict(response['selected_positions']);extra=response.get('extra_exclusion')
        if extra and extra['kind']=='position':positions[extra['key']]=extra['digit']
    return {'reference':reference,'target':proof['target_period'],'proof':proof,'raw':seal,'positions':positions,
            'history_sha256':expected['history_sha256'],'published_at':proof['published_at'],
            'seconds_before_close':round((period_slot(proof['target_period'])-parse_time(proof['published_at'])).total_seconds()-70,3),
            'model':metadata['returned_model'],'elapsed_seconds':metadata.get('elapsed_seconds'),
            'version_identity':_version_identity(seal,metadata,model)}


def _version_identity(seal,metadata,model):
    """Only explicitly archived policy/transport information defines a cohort."""
    strategy=_strategy(seal,model)
    token=re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}')
    def known(value):return isinstance(value,str) and token.fullmatch(value) is not None
    strategy_id=strategy.get('strategy_id') if isinstance(strategy,dict) else None
    revision=seal.get('revision')
    if model in ('killer','framework'):
        audit=seal.get('model_context_audit')
        revision=audit.get('version') if isinstance(audit,dict) else None
    policy=metadata.get('request_policy_version')
    transport=metadata.get('request_transport')
    transport_version=transport.get('version') if isinstance(transport,dict) else policy
    gate=metadata.get('strategy_validation')
    gate_version=gate.get('version') if isinstance(gate,dict) and gate.get('checked') is True else None
    thinking,effort,tokens=metadata.get('thinking'),metadata.get('reasoning_effort'),metadata.get('max_tokens')
    if (not all(known(value) for value in (strategy_id,revision,policy,transport_version,gate_version,
                                           metadata.get('requested_model'),metadata.get('returned_model')))
            or thinking not in ('enabled','disabled') or type(tokens) is not int or not 1<=tokens<=32768
            or (thinking=='enabled' and effort not in ('low','high','max'))
            or (thinking=='disabled' and effort is not None)):
        return None
    return {'strategy_id':strategy_id,'revision':revision,'request_policy_version':policy,
            'transport_version':transport_version,'validation_version':gate_version,
            'requested_model':metadata['requested_model'],'returned_model':metadata['returned_model'],
            'thinking':thinking,'reasoning_effort':effort,'max_tokens':tokens}


def _version_cohorts(publications,models,actuals,conflicts,delivery,now):
    """Same targets/input/positions, separated by the complete version vector.

    This diagnostic never ranks cohorts and never changes the older totals.
    Missing observations cannot be backfilled from a publication timestamp.
    """
    union=set().union(*(set(publications[model]) for model in models))
    excluded={key:0 for key in ('pending_actual','missing_publication','history_mismatch',
                                'version_unknown','availability_unknown','card_only',
                                'availability_late','no_common_position')}
    groups={}
    for target in sorted(union):
        if target not in actuals or target in conflicts:
            excluded['pending_actual']+=1;continue
        if not all(target in publications[model] for model in models):
            excluded['missing_publication']+=1;continue
        rows=[publications[model][target] for model in models]
        if len({row['history_sha256'] for row in rows})!=1:
            excluded['history_mismatch']+=1;continue
        if any(row.get('version_identity') is None for row in rows):
            excluded['version_unknown']+=1;continue
        observations=[observation(model,publications[model][target]['raw'],delivery,now) for model in models]
        if any(item['status']=='card-only' for item in observations):
            excluded['card_only']+=1;continue
        if any(item['status']!='verified' for item in observations):
            excluded['availability_unknown']+=1;continue
        if not all(item['before_close'] for item in observations):
            excluded['availability_late']+=1;continue
        common=set.intersection(*(set(row['positions']) for row in rows))
        if not common:
            excluded['no_common_position']+=1;continue
        versions={model:publications[model][target]['version_identity'] for model in models}
        identity=sha256(_canonical(versions).encode()).hexdigest()
        group=groups.setdefault(identity,{'version_vector_sha256':identity,'versions':versions,'targets':[],
            'target_count':0,'position_tests':0,'models':{model:{'hits':0,'total':0}for model in models}})
        group['targets'].append(target);group['target_count']+=1;group['position_tests']+=len(common)
        for model in models:
            for position in common:
                hit=actuals[target]['numbers'][POSITIONS.index(position)]!=publications[model][target]['positions'][position]
                group['models'][model]['hits']+=int(hit);group['models'][model]['total']+=1
    ordered=sorted(groups.values(),key=lambda item:(item['targets'][0],item['version_vector_sha256']))
    return {'models':list(models),'candidate_target_count':len(union),'excluded':excluded,'groups':ordered,
            'matched_target_count':sum(group['target_count'] for group in ordered),
            'basis':'same-target-same-50-draw-input-same-position-version-vector-server-attack-observed-before-close'}

def _outcomes(entry,model,digits):
    if model=='killer':return prediction_outcome(entry['raw'],digits)
    if model=='framework':
        from framework_hit_stats import _outcomes as outcomes
        return outcomes(entry['proof']['response'],digits)
    if model=='pro4':return {key:digits[i]!=entry['positions'][key] for i,key in enumerate(POSITIONS)}
    if model=='six':
        from selected_six_contract import outcomes
        return outcomes(entry['proof']['response'],digits,revision=entry['proof']['revision'])
    from selected_framework_contract import outcomes
    return outcomes(entry['proof']['response'],digits,revision=entry['proof']['revision'])

def _decision_audit(entry,model,strategy,history):
    """Recompute sample diagnostics without changing the sealed prediction."""
    features=strategy['features'];response=entry['proof']['response']
    slots=dict(entry['positions'])
    if model=='killer':slots.update(all=response['he_all'],qbs=response['he_q3'],bsg=response['he_h3'])
    elif model=='framework':slots.update(qbs=response['he_q3_kill'],bsg=response['he_h3_kill'])
    elif model in ('selected','six'):
        slots.update(response['selected_tails'])
        extra=response.get('extra_exclusion')
        if extra and extra['kind']=='tail':slots[extra['key']]=extra['digit']
    checks=[]
    for slot,digit in slots.items():
        if model=='selected':continue
        item=features[slot]
        if model=='killer':scores=item['smoothed_descriptive_share']
        elif model=='framework':scores=item['counts']
        elif model=='pro4':scores=item['weighted_counts']
        else:
            scores=list(zip(item['maximum_single_block_failure_count'],
                            item['across_block_count_variance'],item['total_counts']))
        check={'slot':slot,'digit':digit,'selected_score':scores[digit],
               'best_score':min(scores),'meets_local_objective':scores[digit]==min(scores)}
        if model=='killer':
            check.update(conditional_n=item['conditional_n'],sparse=item['sparse'],
                         prior_weight=round(10/(item['conditional_n']+10),6))
        checks.append(check)
    for field in ('he2_keep','he3_keep'):
        if field not in response or model not in ('framework','six'):continue
        if model=='framework':
            score={keep:-covered for keep,covered in features['keep_coverage'][field]}
        else:
            score={row[0]:tuple(row[1:4]) for row in features['keep_failures'][field]}
        selected=score[''.join(sorted(response[field]))]
        checks.append({'slot':field,'keep':response[field],'selected_score':selected,
                       'best_score':min(score.values()),'meets_local_objective':selected==min(score.values())})
    failures=[not all(_outcomes(entry,model,row['numbers']).values()) for row in history]
    return {'strategy_id':strategy['strategy_id'],'source':'program-derived-from-saved-input-and-response',
            'local_checks':checks,'local_objective_misses':sum(not item['meets_local_objective'] for item in checks),
            'historical_group_failures':sum(failures),
            'historical_group_block_failures':[sum(failures[start:start+10]) for start in range(0,50,10)],
            'window_size':50,'sample_only':True,'global_optimality_claim':False,
            'changes_prediction':False,'uses_provider_reasoning':False}


def _metric(series,latest=None):
    hits=wins=losses=mw=ml=0;previous=None
    for target,hit in sorted(series):
        if previous is not None and next_period(previous)!=target:wins=losses=0
        hits+=int(hit)
        if hit:wins+=1;losses=0;mw=max(mw,wins)
        else:losses+=1;wins=0;ml=max(ml,losses)
        previous=target
    interrupted=bool(series and latest and previous!=latest)
    if interrupted:wins=losses=0
    return {'hits':hits,'total':len(series),'percent':round(100*hits/len(series),4) if series else None,
            'max_win_streak':mw,'max_loss_streak':ml,'current_win_streak':wins,'current_loss_streak':losses,'interrupted':interrupted}

def build_comparison(snapshot,*,root=ROOT,ledgers=None,delivery=None):
    now=snapshot['now']
    if not isinstance(now,datetime) or now.tzinfo is None:raise ValueError('strategy-board-clock')
    now=now.astimezone(TZ);day=business_start(now).strftime('%Y-%m-%d')
    actuals={};conflicts=validate_conflicting_periods(snapshot.get('conflicting_periods',()))
    for raw in snapshot['records']:
        if not isinstance(raw,dict):continue
        record=valid_draw_record(raw.get('period'),raw.get('numbers'),raw.get('opentime'),now)
        if record:
            if record['period'] in actuals and actuals[record['period']]!=record:conflicts.add(record['period'])
            actuals[record['period']]=record
    for target in conflicts:actuals.pop(target,None)
    latest=max((period for period,row in actuals.items() if business_start(parse_time(row['opentime'])).strftime('%Y-%m-%d')==day),default=None)
    models={};publications={};closed_series={}
    for model in MODELS:
        info={'strategy_id':STRATEGIES[model],'label':LABELS[model],'strategy_label':STRATEGY_LABELS[STRATEGIES[model]],
              'published_count':0,'on_time_count':0,'late_count':0,'pending_count':0,'unverified_count':0,'rejected_count':0,'old_policy_count':0,'source_error':False,'latest':None}
        publications[model]={};closed_series[model]=[];positions={key:[] for key in POSITIONS}
        try:
            if model=='selected':
                # 2026-10-30: legacy 'selected' 账本已退役并归档(see /root/archive/),
                # 面板 DISPLAY_MODELS 本来就不展示它; 这里保留空索引以维持 summary
                # 形状, 避免每次刷新再读 65MB+29MB 死数据。
                source={'prediction_seals':{}}
            else:
                source=ledgers[model] if ledgers is not None else _read(Path(root)/'data'/FILES[model])
            seals=source.get('prediction_seals',{})
            if not isinstance(seals,dict):raise ValueError('invalid-strategy-index')
            for reference,seal in sorted(seals.items()):
                if not isinstance(seal,dict) or seal.get('publication_state')!='published':continue
                target=seal.get('target_period');parsed=parse_period(target)
                if not parsed or target!=parsed[0] or business_start(period_slot(target)).strftime('%Y-%m-%d')!=day:continue
                strategy=_strategy(seal,model)
                if not isinstance(strategy,dict) or strategy.get('strategy_id')!=STRATEGIES[model]:info['old_policy_count']+=1;continue
                try:
                    entry=_publication(seal,model,now,actuals,Path(root))
                    if entry['reference']!=reference or target in publications[model]:raise ValueError('ambiguous-strategy-target')
                    if target in conflicts:raise ValueError('conflicting-strategy-target')
                    info['published_count']+=1
                    if info['latest'] is None or entry['reference']>info['latest']['reference']:
                        info['latest']={key:entry[key] for key in ('reference','target','positions','published_at','seconds_before_close','model','elapsed_seconds')}
                        try:
                            history=seal.get('history') if model not in ('killer','framework') else _history(reference,actuals)
                            info['latest']['decision_audit']=_decision_audit(entry,model,strategy,history)
                        except (ValueError,TypeError,KeyError,IndexError):
                            info['latest']['decision_audit']={'status':'unavailable'}
                    if entry['seconds_before_close']<=0:info['late_count']+=1;continue
                    info['on_time_count']+=1
                    actual=actuals.get(target)
                    if actual is None:
                        info['pending_count' if period_slot(target)>now else 'unverified_count']+=1
                        publications[model][target]=entry
                        continue
                    if business_start(parse_time(actual['opentime'])).strftime('%Y-%m-%d')!=day:continue
                    if publication_evidence_error(entry['proof'],actual):raise ValueError('late-strategy-evidence')
                    result=_outcomes(entry,model,actual['numbers'])
                    if model=='six':
                        from selected_six_contract import item_count as six_item_count
                        expected_count=six_item_count(entry['proof']['revision'])
                    else:
                        expected_count=dict(killer=9,framework=8,pro4=4,selected=7)[model]
                    if not isinstance(result,dict) or len(result)!=expected_count or any(type(value) is not bool for value in result.values()):raise ValueError('invalid-strategy-outcomes')
                    publications[model][target]=entry
                    closed_series[model].append((target,all(result.values())))
                    for key,kill in entry['positions'].items():positions[key].append((target,actual['numbers'][POSITIONS.index(key)]!=kill))
                except (ValueError,TypeError,KeyError,OSError,OverflowError):info['rejected_count']+=1
        except (ValueError,TypeError,KeyError,OSError,OverflowError):
            info['source_error']=True
            publications[model]={};closed_series[model]=[];positions={key:[] for key in POSITIONS}
        info['overall']=_metric(closed_series[model],latest)
        info['positions']={key:_metric(values,latest) for key,values in positions.items()}
        info['position_hits']=sum(item['hits'] for item in info['positions'].values());info['position_total']=sum(item['total'] for item in info['positions'].values())
        models[model]=info
    common=_paired_publications(publications,ORIGINAL_MODELS,actuals,conflicts)
    split_common=_paired_publications(publications,('six','selected'),actuals,conflicts)
    # Panel commits continue after this actual-draw snapshot was frozen. Read
    # the structurally verified log without treating a later commit as damage
    # to all older evidence. observation() enforces ready <= frozen now per row.
    delivery=index_delivery(load_delivery(Path(root),None) if delivery is None else delivery)
    version_comparison={'version':1,'updated_at':now.isoformat(),'biz_date':day,
        'scope':'five-validated-position-strategies; HE3 and cold are different tasks',
        'cohorts':{name:_version_cohorts(publications,cohort,actuals,conflicts,delivery,now)
                   for name,cohort in (('six_seven',('six','selected')),('flash',('killer','framework')),
                                       ('original_four',ORIGINAL_MODELS))}}
    return {'version':1,'policy':'strategy-isolation-v2','biz_date':day,'updated_at':now.isoformat(),'models':models,
            'common':common,'six_seven_common':split_common,
            'version_comparison':version_comparison,
            'stats_error':False,'latest_known_closed_period':latest}

def _paired_publications(publications,models,actuals,conflicts):
    """Preserve the original four-policy cohort; six/seven has a separate join."""
    cohort=set().union(*(set(publications[model]) for model in models))
    matched_targets=[];matched_samples=0;no_position=0;hash_conflicts=0
    paired={model:{'hits':0,'total':0,'positions':{key:{'hits':0,'total':0} for key in POSITIONS}} for model in models}
    for target in sorted(cohort):
        if target not in actuals or target in conflicts or not all(target in publications[model] for model in models):continue
        rows=[publications[model][target] for model in models]
        if len({row['history_sha256'] for row in rows})!=1:hash_conflicts+=1;continue
        common=set.intersection(*(set(row['positions']) for row in rows))
        if not common:no_position+=1;continue
        matched_targets.append(target);matched_samples+=len(common)
        for model in models:
            for key in common:
                hit=actuals[target]['numbers'][POSITIONS.index(key)]!=publications[model][target]['positions'][key]
                paired[model]['hits']+=int(hit);paired[model]['total']+=1
                paired[model]['positions'][key]['hits']+=int(hit);paired[model]['positions'][key]['total']+=1
    return {'target_count':len(matched_targets),'position_tests':matched_samples,'latest_target':matched_targets[-1] if matched_targets else None,
            'unpaired_targets':len(cohort-set(matched_targets)),'no_common_position_targets':no_position,
            'history_mismatch_targets':hash_conflicts,'models':paired}

def _rate(h,n):return '%.2f%%（%d/%d）'%(100*h/n,h,n) if n else '—（暂无已核验样本）'
def _streak_text(metric):
    if not metric['total']:return '最高连中 — ｜ 最高连败 — ｜ 当前连中 — ｜ 当前连败 —'
    return ('最高连中 '+str(metric['max_win_streak'])+'手 ｜ 最高连败 '+str(metric['max_loss_streak'])+
            '手 ｜ 当前连中 '+str(metric['current_win_streak'])+'手 ｜ 当前连败 '+str(metric['current_loss_streak'])+
            '手'+('（连续记录中断）' if metric['interrupted'] else ''))


def render_version_comparison(value,identity='strategy-version-comparison'):
    """A compact, descriptive appendix; order is time/version, never hit rate."""
    if not _valid_version_comparison(value):
        return '<div style="font-size:12px;color:#64748b;">共同版本样本：等待可核验的关盘前攻击语就绪证据。</div>'
    pieces=[]
    # Retired-model cohorts remain valid archived data, but are no longer an
    # active comparison on either this board or the runtime appendix.
    for key,label in (('flash','Flash 两策略'),):
        cohort=value['cohorts'].get(key)
        if not isinstance(cohort,dict):continue
        groups=cohort.get('groups',[])
        exclusions=cohort.get('excluded',{})
        line=label+'：共同目标 '+str(cohort.get('matched_target_count',0))+'期 / '+str(len(groups))+'个版本组合'
        rows=[]
        for group in groups:
            versions=group['versions']
            rates=' ｜ '.join(LABELS[model]+' '+_rate(metric['hits'],metric['total']) for model,metric in group['models'].items())
            versions_text='；'.join(LABELS[model]+': '+version['strategy_id']+' / '+version['request_policy_version']+
                ' / '+version['transport_version']+' / '+version['thinking']+' / '+str(version['max_tokens'])+'tokens'
                for model,version in versions.items())
            rows.append('<div style="margin-top:5px;">'+escape(group['targets'][0]+'～'+group['targets'][-1])+
                ' ｜ '+str(group['target_count'])+'期 / '+str(group['position_tests'])+'个共同定位：'+escape(rates)+
                '<div style="font-size:11px;color:#64748b;">'+escape(versions_text)+'</div></div>')
        excluded_text=' ｜ '.join(label_+str(exclusions.get(name,0))+'期' for name,label_ in (
            ('missing_publication','缺少配对封存'),('version_unknown','版本未知'),('availability_unknown','就绪未观测'),
            ('card_only','仅卡片'),('availability_late','就绪晚于关盘'),('history_mismatch','输入不一致'),
            ('pending_actual','待真实开奖'),('no_common_position','无共同位置')))
        pieces.append('<div style="margin:5px 0;"><b>'+escape(line)+'</b><div style="font-size:12px;color:#64748b;">'+
                      escape(excluded_text)+'</div>'+''.join(rows)+'</div>')
    return ('<details id="'+escape(identity,quote=True)+'" style="margin-top:8px;font-size:12px;">'
            '<summary>共同目标 · 同版本表现（描述统计）</summary>'+''.join(pieces)+
            '<div style="color:#64748b;">仅纳入严格事前封存、真实开奖、同50期输入及关盘前已观测到匹配原攻击语就绪的共同位置。'
            '没有就绪记录的历史样本不补造时间；card-only不算可操作攻击语。版本组合分开列示，样本少不据此选赢家，'
            '不按不同项目数的整组率排名。当前显示Flash两策略共同样本；HE3和冷码任务另行核验。候选范围只含至少一套已发现封存的目标，并非全日应出次数。</div></details>')


def _valid_version_comparison(value):
    """Fail closed on corrupted derived counters, targets or version vectors."""
    expected={'six_seven':('six','selected'),'flash':('killer','framework'),'original_four':ORIGINAL_MODELS}
    count=lambda number,limit:type(number)is int and 0<=number<=limit
    try:
        if (not isinstance(value,dict) or type(value.get('version'))is not int or value['version']!=1
                or not isinstance(value.get('cohorts'),dict) or not set(value['cohorts'])<=set(expected)):
            return False
        for name,cohort in value['cohorts'].items():
            models=expected[name];groups=cohort['groups'];excluded=cohort['excluded'];targets=set()
            if (cohort['models']!=list(models) or not isinstance(groups,list) or len(groups)>288
                    or not count(cohort['candidate_target_count'],288) or not count(cohort['matched_target_count'],288)
                    or set(excluded)!=set(('pending_actual','missing_publication','history_mismatch','version_unknown',
                                           'availability_unknown','card_only','availability_late','no_common_position'))
                    or not all(count(number,288)for number in excluded.values())):
                return False
            for group in groups:
                versions=group['versions'];sample=group['targets'];n=group['target_count'];tests=group['position_tests']
                if (not count(n,288) or not n or not isinstance(sample,list) or len(sample)!=n
                        or sample!=sorted(set(sample)) or any(not isinstance(t,str)or not parse_period(t)or parse_period(t)[0]!=t for t in sample)
                        or targets.intersection(sample) or not count(tests,4*n) or tests<n
                        or set(versions)!=set(models) or set(group['models'])!=set(models)
                        or group['version_vector_sha256']!=sha256(_canonical(versions).encode()).hexdigest()):
                    return False
                targets.update(sample)
                for model in models:
                    metric=group['models'][model];version=versions[model]
                    if (set(metric)!=set(('hits','total')) or not count(metric['total'],4*n) or metric['total']!=tests or not count(metric['hits'],tests)
                            or not isinstance(version,dict) or version.get('strategy_id')!=STRATEGIES[model]
                            or set(version)!=set(('strategy_id','revision','request_policy_version','transport_version',
                                'validation_version','requested_model','returned_model','thinking','reasoning_effort','max_tokens'))
                            or version.get('thinking')not in ('enabled','disabled') or not count(version.get('max_tokens'),32768)
                            or (version['thinking']=='enabled' and version.get('reasoning_effort')not in ('low','high','max'))
                            or (version['thinking']=='disabled' and version.get('reasoning_effort')is not None)
                            or not version['max_tokens'] or any(not isinstance(version.get(key),str)
                                or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}',version[key])is None for key in
                                ('revision','request_policy_version','transport_version','validation_version','requested_model','returned_model'))):
                        return False
            if len(targets)!=cohort['matched_target_count'] or len(targets)+sum(excluded.values())!=cohort['candidate_target_count']:
                return False
        return True
    except (ValueError,TypeError,KeyError,AttributeError,OverflowError):
        return False

def render_board(summary):
    day=summary['biz_date'];cards=[]
    for model in DISPLAY_MODELS:
        item=summary['models'][model];overall=item['overall']
        overall_label='本策略原任务整组通过'
        error=item['source_error'];latest=item['latest']
        status=('来源核验暂不可用' if error else '等待本策略首次发布' if latest is None else
                '最近目标 '+latest['target']+' ｜ 发布时距关盘 '+str(latest['seconds_before_close'])+'秒')
        detail=''.join('<div>'+label+'：'+_rate(item['positions'][key]['hits'],item['positions'][key]['total'])+'</div>' for key,label in zip(POSITIONS,POSITION_LABELS))
        cards.append('<article style="min-width:0;padding:10px;border:1px solid #c7d2fe;border-radius:8px;background:#fff;">'
            '<b>'+escape(item['label'])+'</b><div style="color:#4338ca;">策略：'+escape(item['strategy_label'])+'</div>'
            '<div style="font-size:12px;color:#64748b;">'+escape(status)+'</div>'
            '<div data-stats-business-date="'+day+'" style="margin-top:5px;font-weight:bold;color:#3730a3;">'+overall_label+'：'+('暂停显示' if error else _rate(overall['hits'],overall['total']))+'</div>'
            '<div data-stats-business-date="'+day+'" style="font-size:12px;">'+('统计暂不可用' if error else _streak_text(overall))+'</div>'
            '<details><summary>定位与发布明细</summary>'
            '<div data-stats-business-date="'+day+'">本策略定位已核验：'+('暂停显示' if error else _rate(item['position_hits'],item['position_total']))+'</div>'
            '<div data-stats-business-date="'+day+'">'+detail+'</div>'
            '<div>关盘前发布 '+str(item['on_time_count'])+'期 ｜ 关盘后 '+str(item['late_count'])+'期 ｜ 待开奖 '+str(item['pending_count'])+'期 ｜ 已到期未核验 '+str(item['unverified_count'])+'期 ｜ 拒绝 '+str(item['rejected_count'])+'期</div>'
            '<div style="font-size:12px;">旧策略 '+str(item['old_policy_count'])+'份封存保留，不计入本策略成绩。</div></details></article>')
    return (START+'\n<section id="strategy-comparison-board" style="width:calc(100% - 24px);max-width:1050px;box-sizing:border-box;margin:16px auto;padding:14px;border:1px solid #a5b4fc;border-radius:10px;background:#f8faff;color:#1e293b;font:14px/1.65 sans-serif;overflow-wrap:anywhere;">'
        '<h3 style="margin:0 0 5px;">独立策略 · 今日对照</h3>'
        '<div style="font-size:12px;">'+day+' 05:00起（北京时间）｜四套独立判断，各自使用最新连续50期；允许独立得出相同号码。</div>'
        '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,280px),1fr));gap:8px;">'+''.join(cards)+'</div>'
        +render_version_comparison(summary.get('version_comparison'))+
        '<details style="margin-top:8px;font-size:12px;"><summary>如何理解这些模型</summary>'
        '<div>各自整组项目数不同，不能按整组百分比直接排名；共同定位只在同目标、同输入及同位置的配对样本内比较。只有已发布并真实开奖的样本才计命中，零样本不显示百分比。</div>'
        '<div>六项优先检查最差分段；条件转移使用固定均匀先验，条件样本不足会明确标注。近期加权与总体频次各按固定目标判断，不共享当前号码。统计基准Markov的稀疏回退只属于该基准，不代表正式转移模型。</div>'
        '<div>方法不同不保证号码不同或提高预测效果；策略上线前的历史成绩单独保留。</div></details>'
        '<div style="font-size:12px;color:#64748b;">统计核验：'+escape(summary['updated_at'].replace('T',' '))+'</div></section>\n'+END)

def install_board(html,fragment):
    outer_start='<!-- PENTA5_STATS_PANEL_START -->';outer_end='<!-- PENTA5_STATS_PANEL_END -->'
    if html.count(outer_start)!=1 or html.count(outer_end)!=1:raise ValueError('strategy-board-owned-marker')
    left=html.index(outer_start);right=html.index(outer_end,left)
    owned=html[left:right]
    if owned.count(START)!=owned.count(END) or owned.count(START)>1:raise ValueError('strategy-board-duplicate')
    if START in owned:
        a=owned.index(START);b=owned.index(END,a)+len(END);owned=owned[:a]+fragment+owned[b:]
    else:owned+='\n'+fragment+'\n'
    return html[:left]+owned+html[right:]

def refresh_strategy_board(snapshot,*,root=ROOT,writer=None):
    summary=build_comparison(snapshot,root=root);fragment=render_board(summary)
    if writer is None:
        from panel_io import update_panel
        writer=lambda transform:update_panel(transform,path=Path(root)/'panel.html')
    writer(lambda html:install_board(html,fragment))
    atomic_json(Path(root)/'data/strategy_comparison_stats.json',summary)
    return summary


def preserve_board(current,replacement):
    """Keep this board mounted while the base stats table is being refreshed."""
    if START not in current and END not in current:return replacement
    if current.count(START)!=1 or current.count(END)!=1:raise ValueError('strategy-board-duplicate')
    left=current.index(START);right=current.index(END,left)+len(END)
    return install_board(replacement,current[left:right])
