"""Bind new simulated rounds to committed manual-on publication intervals."""
from copy import deepcopy
from datetime import datetime
from framework_coverage import build_coverage

REVISION='manual-call-periods-v1'

def _stamp(value):
    if not isinstance(value,str):return None
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        return result if result.tzinfo is not None and result.utcoffset() is not None else None
    except ValueError:return None

def check_history_prefix(previous,current):
    if not isinstance(current,dict) or current.get('mode')!='manual' or not _stamp(current.get('introduced_at')):
        raise ValueError('pnl-control-not-initialized')
    if previous is None:return
    if (previous.get('introduced_at')!=current['introduced_at']
            or current['transitions'][:len(previous['transitions'])]!=previous['transitions']):
        raise ValueError('pnl-control-history-changed')

def enabled_generation(history,published):
    enabled=False;generation=None
    for row in history['transitions']:
        if _stamp(row['at'])>published:break
        enabled=row['enabled'];generation=row['generation']
    return generation if enabled else None

def filter_events(model,events,previous_events,history):
    introduced=_stamp(history['introduced_at'])
    old={event['target_period']:event for event in previous_events}
    result=[]
    for raw in events:
        row=deepcopy(raw);prior=old.get(row['target_period']);published=_stamp(row.get('published_at'))
        prior_placed=isinstance(prior,dict) and prior.get('status') in ('settled','pending')
        accepted=prior.get('manual_eligibility') if isinstance(prior,dict) else None
        if prior_placed and accepted is not None:
            eligibility=deepcopy(accepted)
        elif prior_placed:
            prior_published=_stamp(prior.get('published_at'))
            if prior_published is None or prior_published>=introduced:
                raise ValueError('pnl-unclassified-existing-round')
            eligibility={'revision':REVISION,'kind':'retained-before-control','generation':None,
                         'introduced_at':history['introduced_at']}
        else:
            generation=enabled_generation(history,published) if published is not None and published>=introduced else None
            eligibility={'revision':REVISION,'kind':'enabled' if generation is not None else 'not-enabled',
                         'generation':generation,'introduced_at':history['introduced_at']}
        row['manual_eligibility']=eligibility
        if eligibility['kind']=='not-enabled':
            row['status']='skip';row['reason']='manual-api-not-enabled'
            row.pop('hit',None);row.pop('source_status',None)
        elif (row.get('status')=='blocked' and row.get('reason')=='earlier-unresolved-evidence'
              and row.get('publication') is not None and row.get('coverage_sha256') is not None):
            # A pause gap has no simulated stake. Native evidence for this row
            # was already validated before the builder propagated that gap.
            actual=row.get('actual')
            if actual is None:
                row['status']='pending';row['reason']='waiting-for-exact-actual'
            else:
                proof=row['publication'];coverage=build_coverage(model,proof['response'],proof['revision'])
                row['status']='settled';row.pop('reason',None)
                row['hit']=coverage.covers_actual(actual['numbers'])
        result.append(row)
    return result
