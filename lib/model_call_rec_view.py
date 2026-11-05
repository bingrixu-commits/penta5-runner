# -*- coding: utf-8 -*-
"""面板区块「AI 模型调用推荐」（flash / pro 档位，只读不切换）。

口径（写死）：
- 按 AI 任务（killer / framework / selected_six / he3 / kill3 / kill1）
  对比现行档与替代档的版本锁定累计命中率（model_split，按封存 requested_model 分档）；
- 换档条件：两档累计样本均 ≥200 且替代档高 ≥2pp、双侧 z 检验 p<0.05；
- 整组期望基线（10^4 精确枚举）只作对照列；余额/燃耗/守护状态来自
  data/model_usage/summary.json 与 data/ai_balance_guard.json；
- 本区块只读：不改 .env、不重启任务。

数据源 data/model_call_rec.json（lib/model_call_rec.py 每期结算后重算）。
自包含 section + 内联 style；<!-- MODEL_CALL_REC_START/END --> 标记原地替换；
标记缺失时回退插入到 <!-- THREE_AVOID2_END -->（再退 <!-- HITRATE_BASE_END -->）之后；
任何异常原样返回 html。
"""
import json
from pathlib import Path

import model_call_rec as rec

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data' / 'model_call_rec.json'
MARKER = 'MODEL_CALL_REC'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'

STYLE = """
#model-call-rec{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;
  padding:14px;border:1px solid #93c5fd;border-radius:12px;background:#f0f9ff;color:#0f172a;
  font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#model-call-rec *{box-sizing:border-box}
#model-call-rec h3{font-size:17px;color:#075985;margin:0 0 4px}
#model-call-rec .mc-meta{font-size:12.5px;color:#475569;margin-bottom:8px}
#model-call-rec .mc-chips{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 10px}
#model-call-rec .mc-chip{background:#fff;border:1px solid #bae6fd;border-radius:999px;padding:3px 10px;
  font-size:12px;font-weight:700;color:#0c4a6e}
#model-call-rec .mc-chip.warn{background:#fffbeb;border-color:#fde68a;color:#92400e}
#model-call-rec table{width:100%;border-collapse:collapse;font-size:12.5px;color:#334155}
#model-call-rec th,#model-call-rec td{padding:6px 8px;text-align:left;border-top:1px dashed #bae6fd;vertical-align:top}
#model-call-rec th{color:#475569;font-weight:700;font-size:11.5px;background:rgba(14,165,233,.07)}
#model-call-rec td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
#model-call-rec tr.switch td{background:#fefce8}
#model-call-rec tr.observe td{color:#64748b}
#model-call-rec .mc-decision{font-weight:800;white-space:nowrap}
#model-call-rec .mc-decision.maintain{color:#15803d}
#model-call-rec .mc-decision.switch{color:#b45309}
#model-call-rec .mc-decision.observe{color:#64748b}
#model-call-rec .mc-decision.unknown{color:#b91c1c}
#model-call-rec .mc-foot{font-size:11.5px;color:#475569;line-height:1.55;margin-top:8px}
"""


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _pct(rate):
    return ('%.2f%%' % (rate * 100)) if isinstance(rate, (int, float)) else '—'


def _stat_text(entry):
    if not isinstance(entry, dict) or not entry.get('n'):
        return '—'
    return '%d 期 · %s' % (entry['n'], _pct(entry.get('rate')))


def render_block(report=None):
    report = report if report is not None else _load()
    if not isinstance(report, dict) or not report.get('tasks'):
        return (f'{START}<section id="model-call-rec"><h3>AI 模型调用推荐</h3>'
                f'<div class="mc-meta">统计尚未生成，等待下一轮面板重建</div></section>{END}')
    rows = []
    for task in report.get('tasks') or ():
        decision = task.get('decision') or 'unknown'
        cur = task.get('current') or {}
        alt = task.get('alt') or {}
        expected = task.get('expected_pct')
        usage = task.get('usage_7d') or {}
        usage_text = ''
        if usage.get('attempts') is not None:
            bits = ['近7日 %d 次' % usage['attempts']]
            if usage.get('errors'):
                bits.append('错误 %d' % usage['errors'])
            if usage.get('p50') is not None:
                bits.append('p50 %.1fs' % usage['p50'])
            usage_text = ' · '.join(bits)
        rows.append(
            '<tr class="%s"><td>%s</td><td class="num">%s</td><td class="num">%s</td>'
            '<td class="num">%s</td><td class="num">%s</td>'
            '<td><span class="mc-decision %s">%s</span></td><td>%s%s</td></tr>' % (
                decision, task.get('label'), task.get('current_model') or '—',
                _stat_text(cur), task.get('alt_model') or '—', _stat_text(alt),
                decision, task.get('decision_label') or decision, task.get('reason') or '',
                (' · ' + usage_text) if usage_text else ''))
    balance = report.get('balance') or {}
    guard = report.get('guard') or {}
    errors = report.get('errors_7d') or {}
    balance_text = ('¥%.2f' % balance['balance']) if isinstance(balance.get('balance'), (int, float)) else '—'
    runway = balance.get('runway_hours')
    runway_text = ('约 %.1f 小时' % runway) if isinstance(runway, (int, float)) else '—'
    paused = guard.get('auto_paused')
    guard_text = '自动守护：已暂停' if paused is True else '自动守护：在线'
    error_parts = []
    labels = {'payment': '欠费类402', 'rate_limit': '限流429', 'timeout': '超时', 'invalid_response': '无效响应',
              'provider': '服务商错误'}
    for key, label in labels.items():
        if errors.get(key):
            error_parts.append('%s×%d' % (label, errors[key]))
    error_text = ('近7日错误：' + '、'.join(error_parts)) if error_parts else '近7日无错误'
    burn = balance.get('burn_1h_cny')
    if isinstance(burn, (int, float)) and not isinstance(burn, bool):
        burn_chip = '近1h燃耗 ¥%.3f/h' % burn
    elif balance.get('recharged_recently') is True:
        burn_chip = '近1h燃耗 充值后重新起算'
    else:
        burn_chip = '近1h燃耗 待重算'
    chips = ('<span class="mc-chip">余额 %s</span>'
             '<span class="mc-chip">%s</span>'
             '<span class="mc-chip">余额可跑 %s</span>'
             '<span class="mc-chip%s">%s</span>'
             '<span class="mc-chip">%s</span>' % (
                 balance_text, burn_chip, runway_text,
                 ' warn' if paused is True else '', guard_text, error_text))
    return (
        f'{START}<section id="model-call-rec" data-as-of="{report.get("as_of_period") or ""}">'
        f'<style>{STYLE}</style>'
        f'<h3>🧭 AI 模型调用推荐（flash / pro 档位）</h3>'
        f'<div class="mc-meta">{report.get("rule") or ""}</div>'
        f'<div class="mc-chips">{chips}</div>'
        f'<table><thead><tr><th>任务</th><th>现行档</th><th>现行实测</th>'
        f'<th>替代档</th><th>替代实测</th><th>建议</th><th>依据</th></tr></thead>'
        f'<tbody>{rows}</tbody></table>'
        f'<div class="mc-foot">本区块只读：不改 .env、不重启任务 · 整组期望基线（10^4 精确枚举）'
        f'为随机对照、不参与换档判定 · 累计数据截至期号 {report.get("as_of_period") or "—"}'
        f'（累计报告生成 {report.get("cumulative_generated_at") or "—"}；用量更新 '
        f'{report.get("usage_updated_at") or "—"}）· 每期开奖结算后面板自动重建</div>'
        f'</section>{END}'
    )


def apply_model_call_rec_block(content):
    """把「AI 模型调用推荐」区块写入面板；任何异常原样返回。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        try:
            rec.compute()  # 每次面板重建先刷新派生数据（异常不阻断渲染旧值）
        except Exception:
            pass
        block = render_block()
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + block + content[end + len(END):]
        for anchor in ('<!-- THREE_AVOID2_END -->', '<!-- HITRATE_BASE_END -->'):
            position = content.find(anchor)
            if position != -1:
                position += len(anchor)
                return content[:position] + block + content[position:]
        return content
    except Exception:
        return content


if __name__ == '__main__':
    print(render_block())
