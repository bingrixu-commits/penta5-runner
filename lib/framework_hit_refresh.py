"""Refresh verified framework rates without changing sealed prediction cards."""
from pathlib import Path
import argparse
import fcntl
import json

from ai_verified_stats import atomic_json
from framework_hit_stats import refresh_stats, make_summary
from framework_hit_view import (apply_stats_block, render_stats_block,
                                CARD_START, CARD_END, START, END)
from killer_hit_stats import summarize_killer
import killer_hit_view
from panel_io import update_panel
from ai_model_context import POLICY_STRATEGIES
from stats_history import load_snapshot, business_start
from strategy_features import KILLER_STRATEGY, FRAMEWORK_STRATEGY

ROOT = Path('/root/penta5-runner')


def _policy_targets(seals, strategy_id):
    result = set()
    for seal in seals.get('prediction_seals', {}).values():
        audit = seal.get('model_context_audit') if isinstance(seal, dict) else None
        body = audit.get('body') if isinstance(audit, dict) else None
        strategy = body.get('strategy') if isinstance(body, dict) else None
        if isinstance(strategy, dict) and strategy.get('strategy_id') == strategy_id:
            result.add(seal['target_period'])
    return result


def _read_object(path, missing_ok=False):
    try:
        value = Path(path).read_text(encoding='utf-8')
    except FileNotFoundError:
        if missing_ok:
            return {}
        raise
    result = json.loads(value)
    if not isinstance(result, dict):
        raise ValueError('Framework statistics data must be an object')
    return result


def _failure_summary(snapshot):
    return {'biz_date': business_start(snapshot['now']).strftime('%Y-%m-%d'),
            'stats_error': True, 'metrics': {}}


def _write_framework_summary(summary, root, panel_writer=None):
    def transform(content):
        if content.count(CARD_START) != 1 or content.count(CARD_END) != 1:
            raise ValueError('Missing or ambiguous framework card; rates not displayed')
        original = content[content.index(CARD_START):content.index(CARD_END) + len(CARD_END)]
        updated = apply_stats_block(content, summary)
        if (updated.count(START) != 1 or updated.count(END) != 1
                or render_stats_block(summary) not in updated
                or updated[updated.index(CARD_START):updated.index(CARD_END) + len(CARD_END)] != original):
            raise ValueError('Framework rate presentation did not preserve the sealed card')
        return updated
    (panel_writer or update_panel)(transform, path=root / 'panel.html')


def _write_killer_summary(summary, root, panel_writer=None):
    view = killer_hit_view
    def transform(content):
        if content.count(view.CARD_START) != 1 or content.count(view.CARD_END) != 1:
            raise ValueError('Missing or ambiguous killer card; daily rates not displayed')
        original = content[content.index(view.CARD_START):content.index(view.CARD_END) + len(view.CARD_END)]
        updated = view.apply_killer_stats_block(content, summary)
        if (updated.count(view.START) != 1 or updated.count(view.END) != 1
                or view.render_killer_stats_block(summary) not in updated
                or updated[updated.index(view.CARD_START):updated.index(view.CARD_END) + len(view.CARD_END)] != original):
            raise ValueError('Killer daily rates did not preserve the sealed card')
        return updated
    (panel_writer or update_panel)(transform, path=root / 'panel.html')


def refresh_framework_stats(snapshot=None, root=ROOT, panel_writer=None):
    root = Path(root)
    snapshot = snapshot if snapshot is not None else load_snapshot(persist=False)
    ledger_path = root / 'data' / 'r1_framework_hit_stats.json'
    with open(str(ledger_path) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            seals = _read_object(root / 'data' / 'r1_framework_seals.json', missing_ok=True)
            previous = _read_object(ledger_path, missing_ok=True)
            conflicts = snapshot.get('conflicting_periods', ())
            ledger = refresh_stats(seals, snapshot['records'], snapshot['now'], previous,
                                   conflicting_periods=conflicts)
            summary = make_summary(ledger, snapshot['now'], snapshot['records'],
                                   conflicting_periods=conflicts,
                                   allowed_targets=_policy_targets(seals, POLICY_STRATEGIES['framework']),
                                   strategy_id=POLICY_STRATEGIES['framework'])
        except (ValueError, TypeError, KeyError):
            _write_framework_summary(_failure_summary(snapshot), root, panel_writer)
            raise
        if ledger != previous:
            atomic_json(ledger_path, ledger)
        _write_framework_summary(summary, root, panel_writer)
        return summary


def refresh_killer_stats(snapshot=None, root=ROOT, panel_writer=None):
    """Read-only settlement avoids racing with inference's original ledger write."""
    root = Path(root)
    snapshot = snapshot if snapshot is not None else load_snapshot(persist=False)
    try:
        summary = summarize_killer(_read_object(root / 'data' / 'r1_stats.json', missing_ok=True),
                                   snapshot['records'], snapshot['now'],
                                   conflicting_periods=snapshot.get('conflicting_periods', ()),
                                   strategy_id=POLICY_STRATEGIES['killer'])
    except (ValueError, TypeError, KeyError):
        _write_killer_summary(_failure_summary(snapshot), root, panel_writer)
        raise
    _write_killer_summary(summary, root, panel_writer)
    return summary


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    summary = refresh_framework_stats()
    print(json.dumps({'biz_date': summary['biz_date'],
                      'latest_settled_period': summary['latest_settled_period'],
                      'pending_count': summary['pending_count'],
                      'metrics': summary['metrics']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
