"""三位置杀码建议面板区块。

数据源 data/kill_three_pick.json(由 scripts/kill_three_pick.py 每期重算)。
命中口径:该位置实际开奖号 != 杀码(即杀号成功)。

设计要点:
- 自包含 <section> + 内联 <style>,不依赖面板已有 CSS 类。
- 用 <!-- KILL_THREE_START/END --> 标记原地替换;标记缺失时插到
  <!-- COLD_HIT_STATS_END --> 之后。
- 任何异常都原样返回 html,绝不影响整页刷新。
"""
from pathlib import Path
import json

ROOT = Path('/root/penta5-runner')
REPORT_PATH = ROOT / 'data/kill_three_pick.json'
MARKER = 'KILL_THREE'
START = f'<!-- {MARKER}_START -->'
END = f'<!-- {MARKER}_END -->'

STYLE = """
#kill-three{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #fca5a5;border-radius:12px;background:#fef7f7;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-three *{box-sizing:border-box}
#kill-three h3{font-size:18px;color:#991b1b;margin:0 0 4px}
#kill-three .kt-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#kill-three .kt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,190px),1fr));gap:10px;min-width:0;margin-bottom:10px}
#kill-three .kt-pick{border:1px solid #fecaca;border-radius:8px;background:#fff;padding:10px 12px;text-align:center;min-width:0}
#kill-three .kt-pos{font-size:13px;color:#475569}
#kill-three .kt-kill{font-size:32px;font-weight:700;color:#b91c1c;line-height:1.2}
#kill-three .kt-sub{font-size:12px;color:#64748b}
#kill-three .kt-skip{font-size:13px;color:#94a3b8;margin-bottom:10px}
#kill-three .kt-stats{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;border-top:1px dashed #fecaca;padding-top:8px}
#kill-three .kt-stats b{font-size:16px;color:#991b1b}
#kill-three .kt-foot{margin-top:8px;font-size:12px;color:#94a3b8}
"""


def _load():
    try:
        return json.loads(REPORT_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _current_streak_html(backtest):
    """当前进行中的连中/连败（回测序列末尾仍在延续的一段）。"""
    win = backtest.get('current_win_streak')
    loss = backtest.get('current_loss_streak')
    if isinstance(win, int) and win > 0:
        return ('<span>当前 <b style="color:#15803d">连中 ' + str(win) + ' 手</b>'
                '<span style="font-size:12px;color:#64748b"> (进行中)</span></span>')
    if isinstance(loss, int) and loss > 0:
        return ('<span>当前 <b style="color:#b91c1c">连败 ' + str(loss) + ' 手</b>'
                '<span style="font-size:12px;color:#64748b"> (进行中)</span></span>')
    return '<span>当前连中/连败 <b>-</b></span>'


def _pct(value):
    return f'{value * 100:.2f}%' if isinstance(value, (int, float)) else '-'


def render_block(report=None):
    report = report if report is not None else _load()
    if not report or not report.get('recommendation'):
        return (f'{START}<section id="kill-three"><h3>三位置杀码建议</h3>'
                f'<div class="kt-meta">建议尚未生成</div></section>{END}')

    rec = report['recommendation']
    backtest = report.get('backtest') or {}
    picks = []
    for item in rec.get('positions') or ():
        picks.append(
            f'<div class="kt-pick"><div class="kt-pos">{item.get("label", "")}</div>'
            f'<div class="kt-kill">{item.get("kill")}</div>'
            f'<div class="kt-sub">遗漏 {item.get("omission", "-")} 期 · '
            f'近测 {_pct(item.get("recent_rate"))}</div></div>')
    if not picks:
        return (f'{START}<section id="kill-three"><h3>三位置杀码建议</h3>'
                f'<div class="kt-meta">建议内容为空</div></section>{END}')

    skipped = '、'.join(item.get('label', '') for item in rec.get('skipped') or ())
    skip_line = f'<div class="kt-skip">本期放弃:{skipped}</div>' if skipped else ''

    return (
        f'{START}<section id="kill-three" data-as-of="{report.get("as_of_period", "")}">'
        f'<style>{STYLE}</style>'
        f'<h3>三位置杀码建议</h3>'
        f'<div class="kt-meta">数据截至 {report.get("as_of_period", "")} · '
        f'窗口 {report.get("window", "-")} 期 · 回看 {report.get("lookback", "-")} 期 · '
        f'计算于 {report.get("generated_at", "")}</div>'
        f'<div class="kt-grid">{"".join(picks)}</div>'
        f'{skip_line}'
        f'<div class="kt-stats">'
        f'<span>该规则组合命中率 <b>{_pct(backtest.get("combined_rate"))}</b>'
        f'<span style="font-size:12px;color:#64748b"> (理论乘积 {_pct(backtest.get("expected_product"))})</span></span>'
        f'<span>最多连中 <b>{backtest.get("max_win_streak", "-")}</b> 手</span>'
        f'<span>最多连败 <b>{backtest.get("max_loss_streak", "-")}</b> 手</span>'
        f'{_current_streak_html(backtest)}'
        f'<span>回测期数 <b>{backtest.get("periods", "-")}</b></span>'
        f'</div>'
        f'<div class="kt-foot">命中 = 该位置实际开奖号<b>不在</b>杀码上(杀号成功)。'
        f'规则:每位置杀第 1 冷码,按近回看期各位置命中率取前 3 个位置。'
        f'3 个位置各自约 90%,组合约 73%。<br>'
        f'核算只使用前 4 球(千、百、十、个),第 5 球不参与;'
        f'位置按 千/百/十/个 对齐展示;当前连中/连败按已结算期连续计算。</div>'
        f'</section>{END}')


def apply_kill_three_block(content, report=None):
    """原地替换/插入三位置杀码块;任何异常原样返回。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        fragment = render_block(report)
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + fragment + content[end + len(END):]
        for anchor in ('<!-- COLD_HIT_STATS_END -->', '<!-- POSITION_COLD_END -->'):
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
