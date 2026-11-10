"""独立统计与候选对照页 /stats（主面板只留一行入口）。

2026-11-10 用户要求：主面板隐藏以下 7 个明细/候选区块，集中到独立子页面：
  HITRATE_ATTRIBUTION / HITRATE_STABILITY / REAL_HIT / MODEL_CALL_REC /
  TAIL_STABILITY_SHADOW / POSITION_KILL_SHADOW / TAIL_HOLD_MAX_SHADOW

- 内容：逐块复用各 view 模块的 render_block()，与原面板区块逐字一致；
  影子区块读 data/*_shadow.json，数据仍由原有 5 分钟只读采集任务写入。
- 生成：/root/run_stats.py 每轮统计刷新后写一次；三个影子 refresh() 每 5 分钟
  也会重建本页（纯展示层，不影响封存 / 攻击语 / 正式统计）。
- 服务：penta5_backend_runner.js 的 /stats 与 /stats.html 路由（登录后访问）。
"""
import json
import os
import tempfile
from html import escape
from pathlib import Path

import hitrate_attribution_view as _attribution
import hitrate_stability_view as _stability
import real_hitrate_view as _real_hit
import model_call_rec_view as _model_call
import position_kill_shadow as _position_shadow
import tail_stability_shadow as _tail_stability
import tail_hold_max_shadow as _tail_hold_max

ROOT = Path('/root/penta5-runner')
PAGE_PATH = ROOT / 'stats.html'
TITLE = '统计与候选对照'
SUBTITLE = '命中率归因 · 稳定性护栏 · 真实命中率 · 模型调用推荐 · 三组只读影子对照'

SHADOW_FILES = (
    ('tail_stability_shadow.json', _tail_stability),
    ('position_kill_shadow.json', _position_shadow),
    ('tail_hold_max_shadow.json', _tail_hold_max),
)

PAGE_STYLE = """
*{box-sizing:border-box}
html,body{margin:0;min-height:100%}
body{background:linear-gradient(180deg,#eef2ff 0%,#f8fafc 320px,#f1f5f9 100%);color:#1e293b;
  font:14px/1.6 "Microsoft YaHei","PingFang SC",sans-serif;padding:0 0 40px}
.wrap{width:calc(100% - 24px);max-width:1280px;margin:0 auto;padding-top:14px}
.head{display:flex;flex-wrap:wrap;align-items:flex-start;justify-content:space-between;gap:12px;
  margin:0 12px 2px;padding:12px 16px;background:#fff;border:1px solid #c7d2fe;border-radius:12px;
  box-shadow:0 1px 3px rgba(30,58,138,.06)}
.head h1{margin:0;font-size:19px;font-weight:800;color:#1e3a8a;letter-spacing:.02em}
.head .sub{margin-top:4px;font-size:12.5px;color:#64748b}
.head .note{font-size:11.5px;color:#94a3b8;margin-top:2px}
.nav{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.nav a{display:inline-block;border:1px solid #c7d2fe;background:#eef2ff;color:#1d4ed8;
  border-radius:999px;padding:6px 12px;font-size:12.5px;font-weight:700;text-decoration:none}
.nav a:hover{background:#dbeafe}
.nav .here{display:inline-block;border:1px solid #1d4ed8;background:#1d4ed8;color:#fff;
  border-radius:999px;padding:6px 12px;font-size:12.5px;font-weight:700}
.foot{width:calc(100% - 24px);max-width:1240px;margin:10px auto 0;font-size:11.5px;color:#64748b;line-height:1.6}
.foot code{background:#e2e8f0;border-radius:4px;padding:0 4px;font-size:11px}
.err{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:12px 14px;
  border:1px solid #fca5a5;border-radius:12px;background:#fef2f2;color:#991b1b;font:13px/1.55 sans-serif}
"""


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _safe(title, render):
    try:
        return render()
    except Exception as error:  # 页面级兜底：单块异常不影响整页
        return ('<section class="err"><h3>%s</h3><div>区块渲染异常：%s</div></section>'
                % (escape(title), escape('%s: %s' % (type(error).__name__, error))))


def _shadow_data(root, filename):
    data = _load_json(Path(root) / 'data' / filename)
    return data if isinstance(data, dict) else {}


def _blocks(root):
    root = Path(root)
    parts = [
        _safe('命中率归因', _attribution.render_block),
        _safe('命中率稳定性 / 断崖护栏', _stability.render_block),
        _safe('真实命中率', _real_hit.render_block),
        _safe('AI 模型调用推荐', _model_call.render_block),
    ]
    for filename, module in SHADOW_FILES:
        title = {'tail_stability_shadow.json': '合尾稳定化候选（影子）',
                 'position_kill_shadow.json': '位置杀码稳定化候选（影子）',
                 'tail_hold_max_shadow.json': '合尾杀码稳定化候选（多档 hold_max·影子）'}[filename]
        data = _shadow_data(root, filename)
        parts.append(_safe(title, lambda module=module, data=data: module.render_block(data)))
    return '\n'.join(parts)


def render_page(root=ROOT):
    return (
        '<!doctype html>\n'
        '<html lang="zh-CN">\n'
        '<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
        '  <title>%s · penta5</title>\n'
        '  <style>%s</style>\n'
        '</head>\n'
        '<body>\n'
        '<main class="wrap">\n'
        '  <header class="head">\n'
        '    <div>\n'
        '      <h1>%s</h1>\n'
        '      <div class="sub">%s</div>\n'
        '      <div class="note">主面板已隐藏这 7 个区块，只保留一行入口；本页随每轮统计刷新与影子采集重建，刷新页面即可看到最新。</div>\n'
        '    </div>\n'
        '    <nav class="nav"><a href="/panel">← 返回面板</a><a href="/hitrate">整组命中率统计</a>'
        '<a href="/accuracy">逐日明细看板</a><span class="here">统计与候选对照</span></nav>\n'
        '  </header>\n'
        '  %s\n'
        '  <div class="foot">本页由 <code>lib/stats_page_view.py</code> 生成：'
        '仅做展示层收拢，命中率归因/稳定性/真实命中率/模型调用推荐与三组影子对照的采集口径、'
        '数据文件与主面板原区块完全一致；影子区块只读对照，不改变任何封存、攻击语与正式统计。</div>\n'
        '</main>\n'
        '</body>\n'
        '</html>\n'
    ) % (escape(TITLE), PAGE_STYLE, escape(TITLE), escape(SUBTITLE), _blocks(root))


def write_page(path=None, *, root=ROOT, compute_model_call=False):
    root = Path(root)
    if compute_model_call:
        try:
            import model_call_rec as _rec
            _rec.compute()
        except Exception as error:
            print('Stats model-call refresh deferred: ' + type(error).__name__, flush=True)
    target = Path(path) if path else (root / 'stats.html')
    html = render_page(root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.stats-', suffix='.tmp', dir=str(target.parent))
    try:
        with os.fdopen(handle, 'w', encoding='utf-8') as stream:
            stream.write(html)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        try:
            os.chmod(target, 0o644)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temporary)
        except OSError:
            pass
    return target


if __name__ == '__main__':
    print(write_page(compute_model_call=True))
