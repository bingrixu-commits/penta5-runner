#!/usr/bin/env python3
"""Render the joint-form shadow summary block into panel.html (server side only)."""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path('/root/penta5-runner')
START = '<!-- JOINT_FORM_SHADOW_START -->'
END = '<!-- JOINT_FORM_SHADOW_END -->'


def main(argv=None):
    root = Path(argv[1]) if argv and len(argv) > 1 else ROOT
    try:
        shadow = json.loads((root / 'data/joint_form_shadow.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return 0
    lines = [START,
             '<div id="joint-form-shadow" style="max-width:1050px;margin:12px auto;padding:12px 16px;'
             'border:1px solid #a7f3d0;border-radius:8px;background:#f0fdf4;'
             'font:13px/1.7 sans-serif;color:#334155;">',
             '<b>🧪 联合形态优化（影子对照，不改现网封存）</b>']
    for model, label in (('framework', 'framework'), ('killer', 'killer')):
        summary = ((shadow.get('models') or {}).get(model) or {}).get('summary') or {}
        n = summary.get('n') or 0
        if n:
            lines.append('<div>%s：AI %.2f%% → 优化 %.2f%%（Δ%+.2fpp，已核验 %d 期；实盘 %d 期）</div>'
                         % (label, summary.get('ai_rate', 0) * 100, summary.get('opt_rate', 0) * 100,
                            summary.get('delta_pp', 0), n, summary.get('live_n', 0)))
        else:
            lines.append('<div>%s：等待首期核验…</div>' % label)
    try:
        promotion = json.loads((root / 'data/joint_form_promotion.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        promotion = {}
    if isinstance(promotion, dict) and isinstance(promotion.get('status'), str):
        gate = promotion.get('gate') or {}
        parts = []
        models = promotion.get('models') or {}
        for model in ('framework', 'killer'):
            live = (models.get(model) or {}).get('live') or {}
            parts.append('%s %d/%d Δ%+.2fpp' % (model, live.get('n', 0),
                         gate.get('burn_in_periods', 72), live.get('delta_pp', 0.0)))
        target = gate.get('strong_periods', 288)
        lines.append('<div>实盘门禁：%d/%d 期（%s）｜ %s；强验证目标 %d 期</div>'
                     % (promotion.get('live_periods', 0), gate.get('burn_in_periods', 72),
                        promotion.get('status'), ' ｜ '.join(parts), target))
    lines.extend(['<div style="font-size:12px;color:#64748b;">规则：AI 杀码/合尾/保留集不变，'
                  '仅按 10^4 开奖组合精确选择两个排除形态，使整组同时失手概率最低；'
                  '旧封存与现网主卡不受影响。</div>', '</div>', END])
    block = '\n'.join(lines)
    panel = root / 'panel.html'
    try:
        html = panel.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return 0
    if START in html and END in html:
        before, rest = html.split(START, 1)
        _, after = rest.split(END, 1)
        updated = before + block + after
    else:
        matches = list(re.finditer(r'</body>', html, re.I))
        if not matches:
            return 0
        position = matches[-1].start()
        updated = html[:position] + block + '\n' + html[position:]
    if updated == html:
        print('joint-form-shadow panel unchanged')
        return 0
    temporary = tempfile.NamedTemporaryFile('w', dir=str(panel.parent), delete=False, encoding='utf-8')
    try:
        temporary.write(updated)
        temporary.flush()
        os.fsync(temporary.fileno())
    finally:
        temporary.close()
    os.chmod(temporary.name, 0o644)
    os.replace(temporary.name, panel)
    print('joint-form-shadow panel updated')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
