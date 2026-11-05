"""Pure identity-bound delivery observations; never prediction or first-time proof."""
from datetime import datetime, timedelta
from hashlib import sha256
import json
import re

from stats_history import TZ, parse_period, period_slot

KINDS = {'killer': 'killer', 'framework': 'framework', 'pro4': 'pro4',
         'selected': 'selected7', 'selected7': 'selected7',
         'he3_refine': 'he3_refine'}


def stamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return value.astimezone(TZ) if value.tzinfo is not None and value.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def seal_identity(model, seal):
    """Derive exactly the original source card and attack, never an edited DOM."""
    kind = KINDS.get(model)
    card = seal.get('card_html') if isinstance(seal, dict) else None
    if kind is None or not isinstance(card, str) or not card:
        return None
    text = None
    try:
        if kind in ('killer', 'framework'):
            from panel_delivery_timing import extract_identity
            identity=extract_identity(kind,card)
            return ({key:identity[key] for key in ('model','source_card_sha256','attack_sha256','scope')}
                    if identity is not None else None)
        elif kind == 'selected7':
            from selected_framework_attack import build_attack
            text = build_attack(seal.get('response'), revision=seal.get('revision'))
    except (ImportError, ValueError, TypeError, KeyError, OverflowError):
        text = None
    return {'model': kind, 'source_card_sha256': sha256(card.strip().encode('utf-8')).hexdigest(),
            'attack_sha256': sha256(text.encode('utf-8')).hexdigest() if text else None,
            'scope': 'attack' if text else 'card-only'}


def load_delivery(root, now):
    """Optional diagnostics fail closed without breaking the original statistics."""
    try:
        from panel_delivery_timing import load_delivery as load
        value = load(root, now=now)
        if isinstance(value, dict) and type(value.get('version')) is int and value['version'] == 1:
            return value
    except (ImportError, OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        pass
    return {'version': 1, 'events': [], 'source_error': True}


def index_delivery(value):
    """One bounded scan shared by a complete observation/comparison pass."""
    if (not isinstance(value,dict) or type(value.get('version')) is not int or value['version']!=1
            or not isinstance(value.get('events'),list) or len(value['events'])>10000):
        return {'version':1,'events':[],'_by_identity':{}}
    indexed={}
    for event in value['events']:
        if not isinstance(event,dict):continue
        keys=tuple(event.get(key) for key in ('model','reference_period','target_period',
                                             'source_card_sha256','attack_sha256','scope'))
        if not all(item is None or isinstance(item,str) for item in keys):continue
        indexed.setdefault(keys,[]).append(event)
    return {'version':1,'events':value['events'],'_by_identity':indexed}


def observation(model, seal, delivery, now, *, identity=None):
    """An observed commit is an upper bound on readiness, never browser receipt."""
    empty = {'status': 'unobserved', 'server_ready_at': None, 'server_ready_margin_seconds': None,
             'scope': None, 'first_transition': None, 'before_close': False,
             'browser_received_at': None, 'browser_timing_kind': 'not-collected',
             'timing_kind': 'server-commit-observed'}
    if (not isinstance(now, datetime) or now.tzinfo is None or not isinstance(seal, dict)
            or seal.get('publication_state') != 'published'):
        return empty
    reference = seal.get('ref_period', seal.get('reference_period'))
    target = seal.get('target_period')
    if not isinstance(reference, str) or not parse_period(reference) or not isinstance(target, str) or not parse_period(target):
        return empty
    opened, published = period_slot(reference), stamp(seal.get('published_at'))
    cutoff = period_slot(target) - timedelta(seconds=70)
    if period_slot(target) - opened != timedelta(minutes=5) or published is None or not opened <= published <= now:
        return empty
    identity = identity or seal_identity(model, seal)
    if not isinstance(identity, dict) or identity.get('model') != KINDS.get(model):
        return empty
    if (not isinstance(identity.get('source_card_sha256'),str)
            or re.fullmatch('[0-9a-f]{64}',identity['source_card_sha256']) is None
            or identity.get('scope') not in ('attack','card-only')
            or (identity['scope']=='attack' and (not isinstance(identity.get('attack_sha256'),str)
                or re.fullmatch('[0-9a-f]{64}',identity['attack_sha256']) is None))):
        return empty
    if not isinstance(delivery, dict) or type(delivery.get('version')) is not int or delivery['version'] != 1:
        return empty
    events = delivery.get('events')
    if not isinstance(events, list) or len(events) > 10000:
        return empty
    if isinstance(delivery.get('_by_identity'),dict):
        events=delivery['_by_identity'].get((identity['model'],reference,target,
            identity['source_card_sha256'],identity.get('attack_sha256'),identity['scope']),[])
    matches = []
    for event in events:
        if not isinstance(event, dict) or event.get('model') != identity['model']:
            continue
        if event.get('reference_period') != reference or event.get('target_period') != target:
            continue
        if (event.get('source_card_sha256') != identity.get('source_card_sha256')
                or event.get('scope') != identity.get('scope')
                or event.get('attack_sha256') != identity.get('attack_sha256')
                or event.get('timing_kind') != 'server-commit-observed'
                or type(event.get('first_transition')) is not bool):
            continue
        event_id = event.get('event_id')
        ready = stamp(event.get('server_ready_at'))
        event_identity={key:event.get(key)for key in ('model','reference_period','target_period',
                        'source_card_sha256','attack_sha256','scope')}
        expected_id=sha256(json.dumps(event_identity,ensure_ascii=False,sort_keys=True,
                           separators=(',',':'),allow_nan=False).encode()).hexdigest()
        if (not isinstance(event_id, str) or re.fullmatch('[0-9a-f]{64}', event_id) is None
                or event_id!=expected_id or ready is None or not published <= ready <= now):
            continue
        matches.append((ready, event_id, event))
    if not matches:
        return empty
    ready, event_id, event = min(matches, key=lambda item: (item[0], item[1]))
    attack = identity['scope'] == 'attack'
    return {**empty, 'status': 'verified' if attack else 'card-only',
            'server_ready_at': ready.isoformat(), 'server_ready_margin_seconds': round((cutoff-ready).total_seconds(), 3),
            'scope': identity['scope'], 'first_transition': event['first_transition'],
            'before_close': bool(attack and ready < cutoff), 'event_id': event_id}
