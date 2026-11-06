"""kill3 纸面跟投账本面板块（只读：纸面模拟，不产生任何真实下注）。

数据来源 data/kill3_shadow_ledger.json（scripts/kill3_shadow_ledger.py 每 5 分钟
增量刷新）；本模块只负责把汇总渲染成自包含 HTML 区块，并在 panel.html 里按
<!-- KILL3_SHADOW_START --> / <!-- KILL3_SHADOW_END --> 原位替换。

由 scripts/update_kill3_shadow_panel.py 调用；不修改封存/结算/预测口径。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

START = '<!-- KILL3_SHADOW_START -->'
END = '<!-- KILL3_SHADOW_END -->'
DEFAULT_ROOT = Path('/root/penta5-runner')
ANCHOR = '<!-- KILL_THREE_END -->'
FALLBACK = '</body>'
DATA_REL = Path('data') / 'kill3_shadow_ledger.json'


def _pct(value, digits=2):
    if value is None:
        return '-'
    return ('%.' + str(digits) + 'f%%') % (value * 100)


def _units(value):
    if value is None:
        return '-'
    return '{:,}'.format(int(round(value)))


def _cny(value):
    if value is None:
        return '-'
    return '{:,.1f} 元'.format(value * 0.1)


def _signed(value):
    if value is None:
        return '-'
    return ('+' if value > 0 else '−' if value < 0 else '') + _units(abs(value))


def _short(rule_version):
    text = str(rule_version or '')
    match = re.match(r'^kill3-tail-v(\d+)(?:-(.*))?$', text)
    if not match:
        return text or '-'
    number, body = match.group(1), match.group(2) or ''
    tags = []
    if 'cold-drop' in body:
        tags.append('cold-drop')
    elif 'hold3' in body:
        tags.append('hold3')
    return 'v%s%s' % (number, (' · ' + '/'.join(tags)) if tags else '')


def _color(value):
    if value is None:
        return '#475569'
    if value > 0:
        return '#15803d'
    if value < 0:
        return '#b91c1c'
    return '#475569'


def _empty_block():
    return '\n'.join([START,
                      '<div id="kill3-shadow-ledger" style="max-width:1050px;margin:12px auto;'
                      'padding:12px 16px;border:1px solid #c7d2fe;border-radius:8px;'
                      'background:#eef2ff;font:13px/1.7 sans-serif;color:#334155;">',
                      '<b>🧾 kill3 纸面跟投账本（只读 · 纸面模拟，不产生任何真实下注）</b>',
                      '<div>等待首期已结算数据…（data/kill3_shadow_ledger.json）</div>',
                      '</div>', END])


def render_block(payload):
    current = (payload or {}).get('current') or {}
    overall = (payload or {}).get('overall') or {}
    if not current or current.get('n') in (None, 0):
        return _empty_block()
    pnl = current.get('pnl')
    roi = current.get('roi')
    baseline = current.get('coverage_baseline')
    coverage = current.get('coverage_avg')
    breakeven = current.get('breakeven_rate')
    lines = [START,
             '<div id="kill3-shadow-ledger" style="max-width:1050px;margin:12px auto;'
             'padding:12px 16px;border:1px solid #c7d2fe;border-radius:8px;'
             'background:#eef2ff;font:13px/1.7 sans-serif;color:#334155;">',
             '<b>🧾 kill3 纸面跟投账本（只读 · 纸面模拟，不产生任何真实下注）</b>'
             '<span style="font-size:12px;color:#64748b;"> · data-as-of %s</span>'
             % (current.get('last_period') or '-')]
    baseline_text = ('覆盖基线 %s' % _pct(baseline)) if baseline is not None else '覆盖基线 -'
    lines.append('<div>本版 <b>%s</b> · 已结算 <b>%d</b> 期 · 命中 <b>%d</b>（<b>%s</b>）'
                 ' · 平均覆盖 <b>%s</b>（%s） · 盈亏平衡命中率 <b>%s</b>'
                 '（命中率需 ≥ 覆盖/0.99 才不亏）</div>'
                 % (_short(current.get('rule_version')), current.get('n', 0),
                    current.get('hits', 0), _pct(current.get('rate')), _pct(coverage),
                    baseline_text, _pct(breakeven)))
    lines.append('<div>固定 1 单位/码：投入 <b>%s</b> 单位 · 回收 <b>%s</b> 单位'
                 ' · 纸面盈亏 <b style="color:%s">%s</b> 单位（ROI %s）'
                 ' · 最大回撤 %s 单位 · 最长连败 %d 期</div>'
                 % (_units(current.get('cum_stake')), _units(current.get('cum_return')),
                    _color(pnl), _signed(pnl), _pct(roi, 2),
                    _units(current.get('max_drawdown')),
                    current.get('max_loss_streak') or 0))
    lines.append('<div>阶梯倍投压力测试（1→2→3→4，连败封顶 4 倍，命中归 1）：'
                 '纸面盈亏 <b style="color:%s">%s</b> 单位 · 最大回撤 %s 单位</div>'
                 % (_color(current.get('ladder_pnl')), _signed(current.get('ladder_pnl')),
                    _units(current.get('ladder_max_drawdown'))))
    lines.append('<div>按每码 0.1 元折算：投入 %s · 回收 %s · 盈亏 <b style="color:%s">%s</b></div>'
                 % (_cny(current.get('cum_stake')), _cny(current.get('cum_return')),
                    _color(pnl), _cny(pnl)))
    if overall.get('n'):
        lines.append('<div style="color:#475569;">全口径累计（v1 起 %d 期）：命中率 %s'
                     ' · 纸面盈亏 <b style="color:%s">%s</b> 单位（ROI %s）</div>'
                     % (overall.get('n', 0), _pct(overall.get('rate')),
                        _color(overall.get('pnl')), _signed(overall.get('pnl')),
                        _pct(overall.get('roi'))))
    sanity = (payload or {}).get('sanity') or {}
    if sanity.get('mismatches'):
        lines.append('<div style="color:#b91c1c;font-weight:bold;">⚠ 口径自检不一致 %d 期'
                     '（复算命中 vs 结算 group_hit），请先核对账本</div>'
                     % sanity.get('mismatches'))
    lines.append('<div style="font-size:12px;color:#64748b;">口径：命中 = 开奖前 4 球码落在攻击语'
                 '排除后剩余码集内（等价封存「整组 8 项全中」）；四定赔率 9900 含本金（平台抽水约 1%%），'
                 '每期把剩余码各买 1 单位；只统计已结算期；数据生成 %s，每 5 分钟增量刷新'
                 '（<code>data/kill3_shadow_ledger.json</code>）。</div>'
                 % (payload.get('generated_at') or '-'))
    lines.extend(['</div>', END])
    return '\n'.join(lines)


def load_payload(root=None):
    root = Path(root or DEFAULT_ROOT)
    try:
        return json.loads((root / DATA_REL).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def apply_kill3_shadow_block(content, payload=None, root=None):
    """在 panel.html 内容里原位替换/插入 kill3 纸面账本块。"""
    if payload is None:
        payload = load_payload(root)
    block = render_block(payload)
    if START in content and END in content:
        before, rest = content.split(START, 1)
        _, after = rest.split(END, 1)
        return before + block + after
    if ANCHOR in content:
        before, rest = content.split(ANCHOR, 1)
        return before + ANCHOR + '\n' + block + rest
    position = content.lower().rfind(FALLBACK)
    if position < 0:
        return content
    return content[:position] + block + '\n' + content[position:]
