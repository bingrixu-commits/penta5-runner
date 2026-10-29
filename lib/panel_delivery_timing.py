"""Derived observations of durably committed native panel delivery.

This file never changes a prediction or controls an API. Timestamps are captured
after panel fsync while its lock is held, then saved outside that lock. They are
observations of a usable representation, not a claim about browser receipt.
The first persisted observation wins; racing writers may persist a later commit
first. This remains a conservative upper bound, never an earliest-time claim.
"""
from datetime import datetime, timedelta
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import fcntl
import json
import os
import re
import tempfile

from ai_verified_stats import next_period
from stats_history import TZ, beijing_now, parse_period, period_slot

MODELS = {
    'killer': ('R1_KILLER', 'r1_killer_attack_text', None),
    'framework': ('R1_FRAMEWORK', 'r1_framework_attack_text', None),
    'selected7': ('SELECTED_FRAMEWORK', 'selected6-attack-text', 'selected6-attack-data'),
    'selected6': ('SELECTED_SIX_INDEPENDENT', 'six-independent-attack-text', 'six-independent-attack-data'),
    'he3_refine': ('HE3_REFINE', None, 'he3-refine-active'),
    'cold': ('COLD_FRAMEWORK', 'cold-framework-attack', 'cold-framework-data'),
    'cold_fixed': ('COLD_FIXED_FRAMEWORK', 'cold-fixed-framework-attack', 'cold-fixed-framework-data'),
    'cold50': ('COLD50_FRAMEWORK', 'cold50-framework-attack', 'cold50-framework-data'),
}
COLD_FAMILIES = {
    'cold': ('cold-framework', {'cold-framework-v1-30-pos3-tail3': 3,
                              'cold-framework-v2-30-pos3-tail2': 2,
                              'cold-framework-v3-30-pos3-tail2-he2x4-he3x5': 2}),
    'cold_fixed': ('cold-fixed-framework', {'cold-fixed-framework-v1-30-fixed-qbs-all-qbs-he2x4-he3x5': 2}),
    'cold50': ('cold50-framework', {'cold50-framework-v3-50-pos3-tail2-he2x4-he3x5': 2}),
}
FIXED_COLD_SPECS = {
    'cold-fixed-framework-v1-30-fixed-qbs-all-qbs-he2x4-he3x5': (frozenset(('q','b','s')),frozenset(('all','qbs'))),
}
FIELDS = ('model','reference_period','target_period','source_card_sha256','attack_sha256','scope')
MAX_EVENTS = 4096

def _packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()

def _hash(value): return sha256(value).hexdigest()

def _at(value):
    parsed = datetime.fromisoformat(value.replace('Z','+00:00')) if isinstance(value,str) else value
    if not isinstance(parsed,datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('delivery-invalid-clock')
    return parsed.astimezone(TZ)

class _Nodes(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.nodes={}; self.stack=[]; self.job_rows=[]; self.cold_numbers=[]
    def handle_starttag(self,tag,attrs):
        attributes=dict(attrs); key=attributes.get('id')
        names=[name for name,_ in attrs]
        if 'ai-job-row' in str(attributes.get('class') or '').split():
            self.job_rows.append({'attrs':attributes,'ambiguous_attributes':len(names)!=len(set(names))})
        cold_number=any('data-'+prefix+'-number' in attributes or 'data-'+prefix+'-keep' in attributes
                        for prefix,_ in COLD_FAMILIES.values())
        node={'attrs':attributes,'tag':tag,'text':[],
              'ambiguous_attributes':len(names)!=len(set(names))} if key or cold_number else None
        if key: self.nodes.setdefault(key,[]).append(node)
        if cold_number: self.cold_numbers.append(node)
        self.stack.append((tag,node))
        if tag in ('br','img','input','meta','link','hr','source','wbr','area','base','embed','param','track','col'):
            self.stack.pop()
    def handle_endtag(self,tag):
        for i in range(len(self.stack)-1,-1,-1):
            if self.stack[i][0]==tag:
                del self.stack[i:];break
    def handle_data(self,data):
        for _,node in self.stack:
            if node is not None: node['text'].append(data)
    def one(self,key):
        values=self.nodes.get(key,[])
        return values[0] if len(values)==1 and not values[0]['ambiguous_attributes'] else None


def _cold_public_identity(prediction, revision, model='cold'):
    """Bind cold delivery to its immutable seal hash, never refreshed stats HTML."""
    family = COLD_FAMILIES.get(model)
    if family is None:
        return None
    _, revisions = family
    if (not isinstance(prediction,dict) or not isinstance(revision,str) or revision not in revisions
            or prediction.get('publication_state')!='published'):
        return None
    reference,target=prediction.get('reference_period'),prediction.get('target_period')
    if (not isinstance(reference,str) or not parse_period(reference) or parse_period(reference)[0]!=reference
            or target!=next_period(reference)):
        return None
    if model in ('cold50',):
        first,last=prediction.get('first_period'),prediction.get('last_period')
        if (type(prediction.get('sample_count')) is not int or prediction['sample_count']!=50
                or not isinstance(first,str) or not parse_period(first) or parse_period(first)[0]!=first
                or last!=reference or period_slot(last)-period_slot(first)!=timedelta(minutes=245)):
            return None
    try:
        sealed,published,deadline=(_at(prediction.get(key)) for key in ('sealed_at','published_at','deadline'))
    except (ValueError,TypeError,OverflowError):
        return None
    if not period_slot(reference)<=sealed<=published<deadline or deadline!=period_slot(target)-timedelta(seconds=70):
        return None
    if any(not isinstance(prediction.get(key),str) or not re.fullmatch('[0-9a-f]{64}',prediction[key])
           for key in ('seal_sha256','history_sha256','input_sha256')):
        return None
    positions,tails=prediction.get('selected_positions'),prediction.get('selected_tails')
    position_keys=('q','b','s','g')
    tail_labels={'all':'千百十个合','qbs':'千百十合','qbg':'千百个合','qsg':'千十个合','bsg':'百十个合'}
    for values,allowed,count in ((positions,position_keys,3),(tails,tail_labels,revisions[revision])):
        if (not isinstance(values,dict) or len(values)!=count or not set(values)<=set(allowed)
                or any(type(value) is not int or not 0<=value<=9 for value in values.values())):
            return None
    if model in ('cold_fixed',) and (frozenset(positions),frozenset(tails))!=FIXED_COLD_SPECS[revision]:
        return None
    digits='1234567890'
    remaining=lambda value: ''.join(digit for digit in digits if digit!=str(value))
    attack=''.join(label+(remaining(positions[key]) if key in positions else digits)
                   for key,label in zip(position_keys,('千','百','十','个')))+'四定'
    attack+=''.join(label+remaining(tails[key]) for key,label in tail_labels.items() if key in tails)
    keeps={}
    if revision in ('cold-framework-v3-30-pos3-tail2-he2x4-he3x5',
                    'cold-fixed-framework-v1-30-fixed-qbs-all-qbs-he2x4-he3x5',
                    'cold50-fixed-framework-v1-50-fixed-qbs-all-qbs-he2x4-he3x5',
                    'cold50-fixed-framework-v2-50-fixed-qbg-qbg-bsg-he2x4-he3x5',
                    'cold50-framework-v3-50-pos3-tail2-he2x4-he3x5'):
        for key,label,count,allowed in (('he2_keep','二数合',4,'02468'),('he3_keep','三数合',5,'0123456789')):
            value=prediction.get(key)
            if not isinstance(value,str) or len(value)!=count or len(set(value))!=count or not set(value)<=set(allowed):
                return None
            keeps[key]=value
            attack+=label+value
    attack+='各'
    identity={'model':model,'reference_period':reference,'target_period':target,
              'source_card_sha256':prediction['seal_sha256'],
              'attack_sha256':_hash(attack.encode()),'scope':'attack'}
    identity['event_id']=_hash(_packed(identity))
    numbers={'position_'+key:value for key,value in positions.items()}
    numbers.update({'tail_'+key:value for key,value in tails.items()})
    return identity,attack,numbers,keeps,published


def cold_seal_identity(seal, model='cold'):
    """Public cold projection identity without modifying its publication evidence."""
    result=_cold_public_identity(seal,seal.get('revision') if isinstance(seal,dict) else None,model=model)
    return result[0] if result else None


def _cold_panel_identity(parser, *, model='cold', require_available=False, observed_at=None):
    prefix=COLD_FAMILIES[model][0]
    data,card,attack=(parser.one(prefix+suffix) for suffix in ('-data','-card','-attack'))
    try:
        payload=json.loads(''.join(data['text'])) if data else None
    except (ValueError,TypeError):
        return None
    if not isinstance(payload,dict) or type(payload.get('version')) is not int or payload['version']!=1:
        return None
    revision=payload.get('revision')
    if payload.get('prediction_revision',revision)!=revision:
        return None
    result=_cold_public_identity(payload.get('prediction'),revision,model=model)
    if result is None or card is None or attack is None:
        return None
    identity,expected,numbers,keeps,published=result
    if (payload.get('attack')!=expected or ''.join(attack['text'])!=expected
            or card['attrs'].get('data-'+prefix+'-reference')!=identity['reference_period']
            or card['attrs'].get('data-'+prefix+'-target')!=identity['target_period']
            or card['attrs'].get('data-'+prefix+'-seal')!=identity['source_card_sha256']):
        return None
    observed_numbers,observed_keeps={},{}
    for node in parser.cold_numbers:
        attributes=node['attrs']
        if not any('data-'+prefix+suffix in attributes for suffix in ('-number','-keep')):
            continue
        if any('data-'+other+suffix in attributes for other,_ in COLD_FAMILIES.values()
               if other!=prefix for suffix in ('-number','-keep')):
            return None
        if node['ambiguous_attributes']:
            return None
        number,keep=attributes.get('data-'+prefix+'-number'),attributes.get('data-'+prefix+'-keep')
        if (number is not None)==(keep is not None):
            return None
        destination,key=(observed_numbers,number) if number is not None else (observed_keeps,keep)
        if key in destination:
            return None
        destination[key]=''.join(node['text']).strip()
    if observed_numbers!={key:str(value) for key,value in numbers.items()} or observed_keeps!=keeps:
        return None
    if require_available:
        current=parser.one('disp_period')
        if current is None or ''.join(current['text']).strip()!=identity['reference_period']:
            return None
    if observed_at is not None and _at(observed_at)<published:
        return None
    return identity


def extract_identity(model, html, *, _parsed=None, _require_available=False, _observed_at=None):
    """Return original card/attack hashes; rejects duplicate or mismatched IDs.

    Native proof validation belongs to the consumer. Hashes standardize only
    exterior card whitespace: sha256(seal['card_html'].strip().encode()).
    """
    if model not in MODELS or not isinstance(html,str): return None
    marker,attack_id,data_id=MODELS[model]
    start,end='<!-- '+marker+'_START -->','<!-- '+marker+'_END -->'
    if html.count(start)!=1 or html.count(end)!=1: return None
    left,right=html.index(start),html.index(end)+len(end)
    if right<=left: return None
    card=html[left:right]
    parser=_parsed
    if parser is None:
        parser=_Nodes();parser.feed(html)
    if model in COLD_FAMILIES:
        return _cold_panel_identity(parser,model=model,require_available=_require_available,observed_at=_observed_at)
    if model in ('killer','framework'):
        native=_Nodes();native.feed(card)
        match=re.search(r'(?:最新)?参考期号\s*[:：]\s*([0-9]{11})',' '.join(
            ''.join(node['text']) for items in native.nodes.values() for node in items))
        # Some legacy cards put the reference in text outside an id'd node.
        if not match:
            match=re.search(r'(?:最新)?参考期号\s*[:：]\s*([0-9]{11})',re.sub('<[^>]+>',' ',card))
        reference=match.group(1) if match else None
    elif model in ('selected7','selected6'):
        node=parser.one(data_id)
        try: payload=json.loads(''.join(node['text'])) if node else None
        except (ValueError,TypeError): return None
        if not isinstance(payload,dict): return None
        reference=payload.get('reference_period')
        if (not isinstance(reference,str) or not parse_period(reference)
                or payload.get('target_period')!=next_period(reference)):
            return None
    else:
        node=parser.one(data_id)
        reference=node['attrs'].get('data-reference') if node else None
    if not isinstance(reference,str) or not parse_period(reference) or parse_period(reference)[0]!=reference:
        return None
    if _require_available:
        current=parser.one('disp_period')
        if current is None or ''.join(current['text']).strip()!=reference:
            return None
        if model in ('killer','framework'):
            rows=[row for row in parser.job_rows if row['attrs'].get('data-ai-kind')==model]
            if len(rows)!=1 or rows[0]['ambiguous_attributes']:
                return None
            row=rows[0]['attrs']
            try: deadline=_at(row.get('data-ai-deadline'))
            except (ValueError,TypeError,OverflowError): return None
            if (row.get('data-ai-ref')!=reference or row.get('data-ai-target')!=next_period(reference)
                    or row.get('data-ai-status') not in ('success','current','running')
                    or deadline!=period_slot(next_period(reference))):
                return None
    attack=None
    if attack_id:
        node=parser.one(attack_id)
        if not node: return None
        attack=''.join(node['text']).strip()
        if not attack: return None
        if model in ('selected7','selected6') and payload.get('attack')!=attack: return None
    identity={'model':model,'reference_period':reference,'target_period':next_period(reference),
              'source_card_sha256':_hash(card.strip().encode()),
              'attack_sha256':_hash(attack.encode()) if attack is not None else None,
              'scope':'attack' if attack is not None else 'card-only'}
    identity['event_id']=_hash(_packed(identity))
    return identity

def _read(path):
    if not path.exists(): return {'version':1,'events':[]}
    if path.stat().st_size>8*1024*1024: raise ValueError('delivery-oversize')
    value=json.loads(path.read_text(encoding='utf8'))
    if not isinstance(value,dict): raise ValueError('delivery-invalid-data')
    checksum=value.get('checksum'); body={k:v for k,v in value.items() if k!='checksum'}
    if checksum!=_hash(_packed(body)) or body.get('version')!=1 or not isinstance(body.get('events'),list):
        raise ValueError('delivery-invalid-checksum')
    if len(body['events'])>MAX_EVENTS: raise ValueError('delivery-too-many-events')
    seen=set()
    for event in body['events']:
        if not isinstance(event,dict):
            raise ValueError('delivery-invalid-event')
        identity={key:event.get(key) for key in FIELDS}
        identifier=_hash(_packed(identity))
        if (event.get('event_id')!=identifier or identifier in seen or identity['model'] not in MODELS
                or type(event.get('first_transition')) is not bool
                or event.get('timing_kind')!='server-commit-observed'
                or identity['scope'] not in ('attack','card-only')
                or not re.fullmatch('[a-f0-9]{64}',identity.get('source_card_sha256') or '')
                or (identity['scope']=='attack' and not re.fullmatch('[a-f0-9]{64}',identity.get('attack_sha256') or ''))
                or (identity['scope']=='card-only' and identity['attack_sha256'] is not None)
                or not isinstance(identity['reference_period'],str) or not parse_period(identity['reference_period'])
                or identity['target_period']!=next_period(identity['reference_period'])):
            raise ValueError('delivery-invalid-event')
        _at(event.get('server_ready_at'));seen.add(identifier)
    return body

def load_delivery(root, now=None):
    """Missing or corrupt measurement never invents an earlier ready time."""
    try:
        value=_read(Path(root)/'data/ai_delivery_timing.json')
        if now is not None:
            current=_at(now)
            if any(_at(row['server_ready_at'])>current for row in value['events']):
                raise ValueError('delivery-future-observation')
        return {**value,'measurement_error':False}
    except (OSError,ValueError,TypeError,KeyError,OverflowError):
        return {'version':1,'events':[],'measurement_error':True}

def record_delivery(path, before, committed, committed_at):
    """Called after releasing panel.lock. Never reads a model ledger or secrets."""
    path=Path(path); at=_at(committed_at);root=path.parent
    records=[]
    old_parser=_Nodes();old_parser.feed(before)
    new_parser=_Nodes();new_parser.feed(committed)
    for model in MODELS:
        identity=extract_identity(model,committed,_parsed=new_parser,_require_available=True,_observed_at=at)
        if identity is None: continue
        previous=extract_identity(model,before,_parsed=old_parser,_require_available=True)
        records.append({**identity,'server_ready_at':at.isoformat(),
            'timing_kind':'server-commit-observed','first_transition':previous!=identity})
    if not records: return
    destination=root/'data/ai_delivery_timing.json'
    destination.parent.mkdir(exist_ok=True)
    with Path(str(destination)+'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        old=_read(destination); events={row['event_id']:row for row in old['events']}
        for record in records: events.setdefault(record['event_id'],record)
        cutoff=at-timedelta(days=3)
        rows=sorted((row for row in events.values() if _at(row['server_ready_at'])>=cutoff),
                    key=lambda row:(row['server_ready_at'],row['event_id']))[-MAX_EVENTS:]
        body={'version':1,'events':rows}; value={**body,'checksum':_hash(_packed(body))}
        if value=={**old,'checksum':_hash(_packed(old))}: return
        temporary=None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent,delete=False) as stream:
                temporary=Path(stream.name);os.fchmod(stream.fileno(),0o600)
                stream.write(_packed(value)+b'\n');stream.flush();os.fsync(stream.fileno())
            os.replace(temporary,destination)
            directory=os.open(destination.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            if temporary is not None and temporary.exists(): temporary.unlink()
