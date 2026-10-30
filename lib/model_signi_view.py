"""模型显著性监控面板区块:版本锁定 + 样本进度 + 实时判定。

数据源 data/model_significance.json(由 scripts/model_significance.py 每期重算)。
命中 = 该位置开奖号 != 被排除的号(杀号成功),基线 90%。

自包含 section + 内联 style;用 <!-- MODEL_SIGNI_START/END --> 标记原地替换;
任何异常原样返回 html。
"""
from pathlib import Path
import json

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data/model_significance.json'
MARKER = 'MODEL_SIGNI'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'

STYLE = """
#model-signi{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #a3a3a3;border-radius:12px;background:#fafafa;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#model-signi *{box-sizing:border-box}
#model-signi h3{font-size:18px;color:#1f2937;margin:0 0 4px}
#model-signi .ms-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#model-signi .ms-rev{font-family:ui-monospace,monospace;font-size:13px;color:#0f172a;background:#e2e8f0;padding:1px 6px;border-radius:4px}
#model-signi .ms-verdict{font-size:15px;font-weight:700;margin:8px 0;padding:8px 10px;border-radius:8px;background:#fff;border:1px solid #e5e7eb}
#model-signi .ms-bad{color:#b91c1c}
#model-signi .ms-good{color:#15803d}
#model-signi .ms-neutral{color:#b45309}
#model-signi .ms-grid{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;margin-bottom:8px}
#model-signi .ms-grid b{font-size:16px;color:#0f172a}
#model-signi .ms-bar{position:relative;height:14px;background:#e5e7eb;border-radius:7px;overflow:hidden;margin:6px 0 4px}
#model-signi .ms-bar span{display:block;height:100%;background:#60a5fa}
#model-signi .ms-days{font-size:12px;color:#475569;margin-top:6px}
#model-signi .ms-foot{margin-top:8px;font-size:12px;color:#94a3b8}
"""


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _pct(value):
    return f'{value * 100:.2f}%' if isinstance(value, (int, float)) else '-'


def _verdict_class(text):
    if '显著优于' in text:
        return 'ms-good'
    if '显著差于' in text:
        return 'ms-bad'
    return 'ms-neutral'


def render_block(report=None):
    report = report if report is not None else _load()
    if not report or not report.get('current'):
        return (f'{START}<section id="model-signi"><h3>模型显著性监控</h3>'
                f'<div class="ms-meta">监控尚未生成</div></section>{END}')

    cur = report['current']
    verdict = report.get('verdict') or '-'
    progress = report.get('progress') or 0
    width = max(1, min(100, progress * 100))
    days = report.get('recent_daily') or []
    day_text = ' · '.join(f"{d['biz']} {_pct(d['rate'])}({d['bets']}注)" for d in days)

    return (
        f'{START}<section id="model-signi" data-as-of="{report.get("locked_since", "")}">'
        f'<style>{STYLE}</style>'
        f'<h3>模型显著性监控</h3>'
        f'<div class="ms-meta">锁定版本 <span class="ms-rev">{report.get("locked_revision", "-")}</span>'
        f' · 自 {report.get("locked_since", "-")} 起 {report.get("locked_days", "-")} 天'
        f' · 基线 {_pct(report.get("baseline"))}(杀 1 码)</div>'
        f'<div class="ms-verdict {_verdict_class(verdict)}">判定:{verdict}</div>'
        f'<div class="ms-grid">'
        f'<span>命中率 <b>{_pct(cur.get("rate"))}</b></span>'
        f'<span>样本 <b>{cur.get("n", 0)}</b> 注</span>'
        f'<span>z=<b>{cur.get("z")}</b></span>'
        f'<span>双侧 p=<b>{cur.get("p_two_sided")}</b></span>'
        f'<span>单侧 p=<b>{cur.get("p_one_sided")}</b></span>'
        f'</div>'
        f'<div class="ms-bar"><span style="width:{width:.1f}%"></span></div>'
        f'<div class="ms-days">样本进度 {cur.get("n", 0)}/{report.get("target_bets", "-")}'
        f'({progress:.1%}),还需 {report.get("remaining_bets", "-")} 注'
        f' · 目标:证明真实水平达 {_pct(report.get("target_rate"))}</div>'
        f'<div class="ms-days">近几日:{day_text or "-"}</div>'
        f'<div class="ms-foot">换版本即重置计数,旧版本样本不并入统计(避免多重比较)。'
        f'单日命中率波动约 ±3pp,不足以判断模型优劣,以整体显著性为准。'
        f'命中口径:只用前 4 球(千/百/十/个),第 5 球不参与。</div>'
        f'</section>{END}')


def apply_model_signi_block(content, report=None):
    """原地替换/插入显著性监控块;任何异常原样返回。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        fragment = render_block(report)
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + fragment + content[end + len(END):]
        for anchor in ('<!-- KILL_THREE_END -->', '<!-- COLD_HIT_STATS_END -->',
                       '<!-- POSITION_COLD_END -->'):
            position = content.find(anchor)
            if position != -1:
                position += len(anchor)
                return content[:position] + fragment + content[position:]
        closing = content.rfind('</body>')
        if closing != -1:
            return content[:closing] + fragment + content[closing:]
        return content + fragment
    except Exception:
        return content
