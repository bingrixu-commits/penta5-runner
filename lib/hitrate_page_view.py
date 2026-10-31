"""独立统计页：整组命中率 vs 随机期望（累计·版本锁定）。

2026-11-01 用户要求：主面板不再内嵌这块大表（只保留一行入口），
另设独立页 /hitrate，把各框架的累计命中率集中在一起看。

- 内容：直接复用 hitrate_baseline_view.render_block() 的完整区块
  （累计表 + 滚动漂移监控 + 口径脚注），数据源 data/framework_hitrate_cumulative.json
  与 data/hitrate_baseline.json，因此与原面板区块逐字一致。
- 生成：scripts/framework_hitrate_cumulative.py 每 15 分钟写一次
  /root/penta5-runner/hitrate.html（原子写；页面异常不影响 JSON 产物）。
- 服务：penta5_backend_runner.js 的 /hitrate 与 /hitrate.html 路由（登录后访问）。
"""
import json
import os
import tempfile
from html import escape
from pathlib import Path

import hitrate_baseline_view as _block

ROOT = Path('/root/penta5-runner')
PAGE_PATH = ROOT / 'hitrate.html'
CUMULATIVE_PATH = _block.CUMULATIVE_PATH
TITLE = '整组命中率 vs 随机期望（累计·版本锁定）'

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
.nav{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.nav a{display:inline-block;border:1px solid #c7d2fe;background:#eef2ff;color:#1d4ed8;
  border-radius:999px;padding:6px 12px;font-size:12.5px;font-weight:700;text-decoration:none}
.nav a:hover{background:#dbeafe}
.nav .here{display:inline-block;border:1px solid #1d4ed8;background:#1d4ed8;color:#fff;
  border-radius:999px;padding:6px 12px;font-size:12.5px;font-weight:700}
.foot{width:calc(100% - 24px);max-width:1240px;margin:10px auto 0;font-size:11.5px;color:#64748b;line-height:1.6}
.foot code{background:#e2e8f0;border-radius:4px;padding:0 4px;font-size:11px}
"""


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _meta():
    cumulative = _load(CUMULATIVE_PATH) or {}
    generated = cumulative.get('generated_at') or '-'
    as_of = cumulative.get('as_of_period') or '-'
    return generated, as_of


def _render_block():
    try:
        return _block.render_block()
    except Exception as error:  # 页面级兜底：异常时也给出可读提示，不抛给 cron
        return ('<section id="hitrate-base"><h3>%s</h3>'
                '<div class="hb-warn">区块渲染异常：%s</div></section>'
                % (escape(TITLE), escape('%s: %s' % (type(error).__name__, error))))


def render_page():
    generated, as_of = _meta()
    block = _render_block()
    return (
        '<!doctype html>\n'
        '<html lang="zh-CN">\n'
        '<head>\n'
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
        '  <title>%s · %s</title>\n'
        '  <style>%s</style>\n'
        '</head>\n'
        '<body>\n'
        '<main class="wrap">\n'
        '  <header class="head">\n'
        '    <div>\n'
        '      <h1>%s</h1>\n'
        '      <div class="sub">数据截至 %s · 累计报告生成于 %s · 每 15 分钟自动刷新（刷新页面即可看到最新）</div>\n'
        '    </div>\n'
        '    <nav class="nav"><a href="/panel">← 返回面板</a><a href="/accuracy">逐日明细看板</a>'
        '<span class="here">整组累计统计</span></nav>\n'
        '  </header>\n'
        '  %s\n'
        '  <div class="foot">本页由 <code>scripts/framework_hitrate_cumulative.py</code> 生成：'
        '主面板已隐藏这块大表，只保留一行入口。整组命中率是「一期 N 项全部命中」的联合概率，'
        '不同项数的框架不能按整组百分比横向排名；累计样本 n&lt;200 只报数字、不下结论。</div>\n'
        '</main>\n'
        '</body>\n'
        '</html>\n'
    ) % (escape(TITLE), escape(str(as_of)), PAGE_STYLE, escape(TITLE),
         escape(str(as_of)), escape(str(generated)), block)


def write_page(path=None):
    target = Path(path) if path else PAGE_PATH
    html = render_page()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.hitrate-', suffix='.tmp', dir=str(target.parent))
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
    print(write_page())
