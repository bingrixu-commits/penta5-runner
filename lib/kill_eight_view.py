"""AI 双反向冷形态排除建议面板区块（kill8 独立框架）。

数据源 data/kill8_tail/{seals,settlements}.json，由 scripts/run_kill8_tail.py 每期生成（手动触发）。
与 kill1 / kill3 完全独立：独立 AI 请求 / 封存 / 结算 / 统计 / 面板区块 / 攻击语。

命中口径（写死，面板脚注同步展示）：
- 位置排除数字由用户手动完成，不进本框架计算；
- 单双形态命中 = 目标期实际四位单双形态 != 封存单双形态（反向排除项，4 字符）；
- 大小形态命中 = 目标期实际四位大小形态 != 封存大小形态（反向排除项，4 字符）；
- 整组命中 = 单双 + 大小 2 项同时命中；
- 无合尾、无连用限制；只统计开奖前已发布、开奖后按真实开奖核验的记录；
- 北京时间 05:00 清零；只使用前 4 球（千/百/十/个），第 5 球不参与。

设计要点：
- 自包含 section（id 见 _section_open）+ 内联样式，不依赖面板已有 CSS 类；
- 用 START / END 起止注释标记原地替换；
- 标记缺失时按 KILL_ONE_END -> </body> 顺序插入；
- 任何异常原样返回 html，绝不影响整页刷新。
"""
from datetime import datetime
from html import escape
import json
import math

import kill8_tail

START = '<!-- KILL_EIGHT_START -->'
END = '<!-- KILL_EIGHT_END -->'

# 整组随机基线兜底：(项数, 精确期望命中率)。
# 2 项 = 单双反向冷形态 + 大小反向冷形态；均匀配置下 10^4 全枚举平均期望 (15/16)^2 = 0.8789。
# hitrate_baseline.RULE_BASELINE[('kill8_tail', RULE_VERSION)] 未登记时使用本值。
FALLBACK_BASELINE = (2, 0.8789)

_TITLE = '🤖 AI 双反向冷形态排除建议'

STYLE = """
#kill-eight-ai{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #c7d2fe;border-radius:12px;background:#f8faff;color:#334155;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#kill-eight-ai *{box-sizing:border-box}
#kill-eight-ai h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#kill-eight-ai .k8-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#kill-eight-ai .k8-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,170px),1fr));gap:10px;min-width:0;margin-bottom:10px}
#kill-eight-ai .k8-card{border:1px solid #ddd6fe;border-radius:8px;background:#fff;padding:10px 12px;text-align:center;min-width:0}
#kill-eight-ai .k8-pos{font-size:13px;color:#475569}
#kill-eight-ai .k8-form{font-size:26px;font-weight:700;color:#7c3aed;letter-spacing:2px;line-height:1.35}
#kill-eight-ai .k8-sub{font-size:12px;color:#64748b}
#kill-eight-ai .k8-drop{font-size:13px;color:#94a3b8;margin-bottom:6px}
#kill-eight-ai .k8-reason{font-size:13px;color:#475569;margin-bottom:8px}
#kill-eight-ai .k8-stats{display:flex;flex-wrap:wrap;gap:18px;font-size:13px;color:#334155;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-eight-ai .k8-stats b{font-size:16px;color:#1e3a8a}
#kill-eight-ai .k8-verify{margin-top:6px;font-size:13px;color:#334155}
#kill-eight-ai .k8-attack{margin-top:8px;border-top:1px dashed #c7d2fe;padding-top:8px}
#kill-eight-ai .k8-attack-title{font-size:13px;font-weight:600;color:#3730a3}
#kill-eight-ai .k8-foot{margin-top:8px;font-size:12px;color:#94a3b8}
#kill-eight-ai .k8-empty{padding:6px 2px;font-size:14px;color:#475569}
#kill-eight-ai .k8-posinput{border:1px dashed #a5b4fc;border-radius:8px;background:#f5f3ff;padding:10px 12px;margin-bottom:8px}
#kill-eight-ai .k8-posinput-title{font-size:13px;font-weight:600;color:#4c1d95;margin-bottom:8px}
#kill-eight-ai .k8-posinput-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,120px),1fr));gap:8px;margin-bottom:6px}
#kill-eight-ai .k8-posinput-grid label{display:flex;flex-direction:column;font-size:12px;color:#6b21a8;gap:4px;margin:0}
#kill-eight-ai .k8-posinput-grid input{width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid #c4b5fd;border-radius:6px;background:#fff;font:14px/1.4 monospace;color:#1e1b4b;text-align:center}
#kill-eight-ai .k8-posinput-hint{font-size:12px;color:#7c3aed}
#kill-eight-ai .k8-run{margin:8px 0 4px;padding:10px 12px;border:1px solid #c7d2fe;border-radius:8px;background:#eef2ff;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
#kill-eight-ai .k8-run button{padding:8px 16px;border:0;border-radius:7px;background:#2563eb;color:#fff;font-size:14px;font-weight:600;cursor:pointer}
#kill-eight-ai .k8-run button:disabled{background:#94a3b8;cursor:not-allowed}
#kill-eight-ai .k8-run .k8-run-status{font-size:13px;color:#1e3a8a}
#kill-eight-ai .k8-run details{width:100%;margin-top:6px;font-size:13px;color:#475569}
#kill-eight-ai .k8-run summary{cursor:pointer;color:#4338ca}
#kill-eight-ai .k8-run input{padding:6px 8px;border:1px solid #c4b5fd;border-radius:6px;margin:4px 6px 4px 0;font-size:13px}
"""


def _section_open(extra=''):
    """section 开头（id 字面量全文件仅在此处出现一次，便于 grep 计数）。"""
    return f'<section id="kill-eight-ai"{extra}>'


def _pct(value):
    return f'{value * 100:.2f}%' if isinstance(value, (int, float)) else '-'


def _clock(value):
    try:
        moment = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return '-'
    return moment.strftime('%H:%M:%S')


def _stats_html(stats):
    stats = stats if isinstance(stats, dict) else {}
    total = stats.get('total') or 0
    pending = stats.get('pending') or 0
    late = stats.get('late') or 0
    if total:
        parity = stats.get('parity') or {}
        size = stats.get('size') or {}
        join = ' · '.join([
            '整组命中率（2 项全中：单双 + 大小） <b>%s</b>（%s/%s 期）'
            % (_pct(stats.get('group_rate')), stats.get('group_hits'), total),
            '单双冷形态（随机基线93.75%%） <b>%s</b>（%s/%s 期）'
            % (_pct(parity.get('rate')), parity.get('hits'), parity.get('bets')),
            '大小冷形态（随机基线93.75%%） <b>%s</b>（%s/%s 期）'
            % (_pct(size.get('rate')), size.get('hits'), size.get('bets')),
            '待核验 <b>%s</b> 期' % pending,
        ])
    else:
        join = ('今日暂无已核验样本 · 待核验 <b>%s</b> 期 · '
                '最早一期开奖后核验（北京时间 05:00 清零）' % pending)
    if late:
        join += ' · 未计入（发布晚于开奖）%s 期' % late
    legacy = stats.get('legacy') or {}
    if legacy.get('total'):
        join += (' · 旧口径（历史版本封存）%s 期（整组 %s 中）已归档不计入'
                 % (legacy.get('total'), legacy.get('group_hits')))
    win = stats.get('current_win_streak') or 0
    loss = stats.get('current_loss_streak') or 0
    if win:
        join += ' · 当前 <b style="color:#15803d">连中 %s 期</b>' % win
    elif loss:
        join += ' · 当前 <b style="color:#b91c1c">连败 %s 期</b>' % loss
    join += ' · 今日四手连败 <b>%s</b> 次（不重叠计数）' % (stats.get('four_loss_cycles') or 0)
    if total and total < 200:
        sigma = 100.0 * math.sqrt(0.9375 * 0.0625 / total)
        join += (' · 单项样本 n=%s（&lt;200 属小样本：93.75%% 项 1σ≈%.1fpp、2σ≈%.1fpp），'
                 '单日单项只作波动参考' % (total, sigma, 2 * sigma))
    return '<div class="k8-stats"><span>%s</span></div>' % join


def _settlement_line(settlement):
    if not isinstance(settlement, dict):
        return '已发布，等待目标期开奖核验。'
    parts = ['目标期已开奖：' + ''.join(str(value) for value in (settlement.get('actual') or []))]
    parity = settlement.get('parity') or {}
    if isinstance(parity, dict) and parity:
        parts.append('单双形态 %s %s（实际 %s）' % (
            parity.get('form', '-'),
            '命中' if parity.get('hit') is True else '未中', parity.get('actual', '-')))
    size = settlement.get('size') or {}
    if isinstance(size, dict) and size:
        parts.append('大小形态 %s %s（实际 %s）' % (
            size.get('form', '-'),
            '命中' if size.get('hit') is True else '未中', size.get('actual', '-')))
    if settlement.get('verified') is not True:
        parts.append('发布晚于开奖，未计入统计')
    else:
        parts.append('整组 ' + ('<b style="color:#15803d">命中</b>'
                                if settlement.get('group_hit') is True
                                else '<b style="color:#b91c1c">未中</b>'))
    return '；'.join(parts) + '。'


def _version_html():
    """口径追溯行：rule_version、自 ref 起期数、整组精确基线（未登记回退 FALLBACK_BASELINE）。"""
    try:
        rule = str(getattr(kill8_tail, 'RULE_VERSION', '') or '')
        info = {}
        try:
            info = kill8_tail.version_info() or {}
            rule = str(info.get('rule_version') or rule)
        except Exception:
            info = {}
        baseline = None
        try:
            from hitrate_baseline import RULE_BASELINE
            baseline = RULE_BASELINE.get(('kill8_tail', rule))
        except Exception:
            baseline = None
        if not baseline:
            baseline = FALLBACK_BASELINE
        parts = ['口径 ' + escape(rule)]
        first = info.get('first_ref')
        if first:
            parts.append('自 ref ' + escape(str(first)) + ' 起（本版 '
                         + str(int(info.get('count') or 0)) + ' 期）')
        parts.append('整组精确基线 %.2f%%（%d 项）'
                     % (float(baseline[1]) * 100.0, int(baseline[0])))
        parts.append('kill8 独立框架：与 kill1 / kill3 独立封存 / 结算 / 统计')
        return '<div class="k8-drop">' + ' · '.join(parts) + '</div>'
    except Exception:
        return ''


def render_block():
    try:
        data = kill8_tail.current_summary()
        data = data if isinstance(data, dict) else {}
        stats = data.get('stats') or {}
        seal = data.get('seal')
        if not isinstance(seal, dict):
            body = '<div class="k8-empty">本期 AI 建议尚未发布（手动触发脚本后计算，或本轮调用失败）。</div>'
            previous = data.get('previous')
            if isinstance(previous, dict):
                body += ('<div class="k8-meta">最近一次：参考期 %s · 目标期 %s</div>'
                         % (escape(str(previous.get('ref_period', ''))),
                            escape(str(previous.get('target_period', '')))))
            return ('%s%s<style>%s</style><h3>%s</h3>%s%s%s%s%s%s%s'
                    % (START, _section_open(), STYLE, _TITLE, _run_html(), body,
                       _attack_html(None), _stats_html(stats), _version_html(),
                       _foot_html(), END))
        cards = []
        parity_form = seal.get('parity_form')
        if isinstance(parity_form, str) and len(parity_form) == 4:
            cards.append(
                '<div class="k8-card"><div class="k8-pos">%s</div>'
                '<div class="k8-form">%s</div>'
                '<div class="k8-sub">排除该四位单双形态（计入整组）</div></div>'
                % (escape(str(kill8_tail.REVERSE_LABELS['parity'])), escape(parity_form)))
        size_form = seal.get('size_form')
        if isinstance(size_form, str) and len(size_form) == 4:
            cards.append(
                '<div class="k8-card"><div class="k8-pos">%s</div>'
                '<div class="k8-form">%s</div>'
                '<div class="k8-sub">排除该四位大小形态（计入整组）</div></div>'
                % (escape(str(kill8_tail.REVERSE_LABELS['size'])), escape(size_form)))
        reason = escape(str(seal.get('reason') or '')) or '（无）'
        scope_html = ('<div class="k8-drop">整组 2 项：单双冷形态 + 大小冷形态；'
                      '无合尾、无连用限制（每期按冷热重选）；'
                      '位置排除数字由用户手动完成，不在此框架内计算</div>')
        model = seal.get('model_label') or kill8_tail.model_label()
        meta = ('参考期 %s ｜ 目标期 %s ｜ AI 计算 %s（北京时间） ｜ %s'
                % (escape(str(seal.get('ref_period', ''))),
                   escape(str(seal.get('target_period', ''))),
                   escape(_clock(seal.get('generated_at'))), escape(str(model))))
        settlement = data.get('settlement')
        state = seal.get('publication_state')
        if isinstance(settlement, dict):
            verify = _settlement_line(settlement)
        elif state == 'published':
            verify = '已发布（%s），等待目标期开奖核验。' % escape(_clock(seal.get('published_at')))
        else:
            verify = '已封存，等待发布确认。'
        as_of = escape(str(seal.get('ref_period', '')))
        return ('%s%s<style>%s</style><h3>%s</h3>'
                '%s'
                '<div class="k8-meta">%s</div>'
                '<div class="k8-grid">%s</div>%s'
                '<div class="k8-reason">理由：%s</div>'
                '%s%s<div class="k8-verify">%s</div>%s%s%s'
                % (START, _section_open(' data-as-of="%s"' % as_of), STYLE, _TITLE,
                   _run_html(),
                   meta, ''.join(cards), scope_html, reason,
                   _stats_html(stats), _version_html(), verify, _attack_html(seal),
                   _foot_html(), END))
    except Exception:
        return ''


def _run_html():
    """启动按钮：点击触发 /api/kill8-run（结算上一期 + 封存下一期 + 刷面板）。"""
    html = (
        '<div class="k8-run">'
        '<button id="k8-run-btn" type="button">▶ 启动本框架（结算上一期 + 封存下一期）</button>'
        '<span class="k8-run-status" id="k8-run-status" role="status" aria-live="polite"></span>'
        '<details id="k8-run-admin" hidden><summary>管理员验证（只读登录需填写）</summary>'
        '<input id="k8-run-user" type="text" autocomplete="username" maxlength="128" placeholder="管理员账号">'
        '<input id="k8-run-password" type="password" autocomplete="current-password" maxlength="512" placeholder="管理员密码">'
        '<div style="font-size:12px;color:#64748b;margin-top:4px">仅用于本次启动请求，不保存密码。</div>'
        '</details></div>'
    )
    script = (
        "<script>(function(){"
        "'use strict';"
        "var v='kill8-run-v1',old=window.__PENTA5_KILL8_RUN__;"
        "if(old&&old.version===v)return;window.__PENTA5_KILL8_RUN__={version:v};"
        "var busy=false,running=false,sessionAdmin=null;"
        "function node(id){return document.getElementById(id);}"
        "function say(t){var x=node('k8-run-status');if(x)x.textContent=t;}"
        "function setBtn(b){var x=node('k8-run-btn');if(x)x.disabled=b;}"
        "function req(o){return fetch('/api/kill8-run',Object.assign({credentials:'same-origin',cache:'no-store',redirect:'error'},o));}"
        "function auth(){if(sessionAdmin===true)return null;"
        "var u=node('k8-run-user'),p=node('k8-run-password'),d=node('k8-run-admin');"
        "if(!u||!p||!u.value||!p.value){if(d){d.hidden=false;d.open=true;}"
        "say('请填写管理员账号密码（只读登录不能启动）');"
        "if(u&&!u.value)u.focus({preventScroll:true});else if(p)p.focus({preventScroll:true});return false;}"
        "if(u.value.length>128||p.value.length>512){say('账号或密码格式无效');return false;}"
        "var raw=u.value+':'+p.value,bytes=new TextEncoder().encode(raw),bin='';"
        "bytes.forEach(function(b){bin+=String.fromCharCode(b);});p.value='';raw='';return 'Basic '+btoa(bin);}"
        "async function poll(){for(var i=0;i<80;i++){await new Promise(function(r){setTimeout(r,3000);});"
        "try{var r=await req({method:'GET',headers:{Accept:'application/json'}});var d=null;try{d=await r.json();}catch(e){}"
        "if(d&&d.ok===true&&d.running===false){say('完成，正在刷新面板…');"
        "setTimeout(function(){location.reload();},400);return;}"
        "if(d&&d.ok===true)say('计算中… 已约 '+((i+1)*3)+' 秒');}catch(e){}}"
        "say('长时间未确认完成，请手动刷新页面查看结果');}"
        "async function run(){if(busy)return;busy=true;setBtn(true);"
        "var a=auth();if(a===false){busy=false;setBtn(false);return;}"
        "var h={Accept:'application/json'};if(a)h.Authorization=a;"
        "say('正在启动（结算上一期 + 封存下一期）…');"
        "try{var r=await req({method:'POST',headers:h});var d=null;try{d=await r.json();}catch(e){}"
        "if(r.status===401||r.status===403){sessionAdmin=false;var ad=node('k8-run-admin');"
        "if(ad){ad.hidden=false;ad.open=true;}"
        "say('需要管理员权限，请填写账号密码后重试');return;}"
        "if(r.status===409){say('上一次仍在运行，请稍候再点');return;}"
        "if(!r.ok||!d||d.ok!==true)throw new Error('run');"
        "sessionAdmin=true;running=true;"
        "say('已启动，正在计算（约1-2分钟），完成后自动刷新…');poll();}"
        "catch(e){say('启动失败，请重试');}"
        "finally{busy=false;setBtn(false);}}"
        "function bind(){var b=node('k8-run-btn');if(b)b.addEventListener('click',run);}"
        "async function init(){try{var r=await req({method:'GET',headers:{Accept:'application/json'}});"
        "var d=null;try{d=await r.json();}catch(e){}"
        "if(d&&d.ok===true){sessionAdmin=(d.can_run===true);running=(d.running===true);"
        "if(running)say('上一次仍在运行中…');}}catch(e){}bind();}"
        "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',init);}else{init();}"
        "})();</script>"
    )
    return html + script


def _attack_html(seal):
    """位置排除数字输入区 + 完整攻击语（位置排除 + AI 冷形态）+ 复制按钮。

    位置排除数字由用户手动输入（不进框架计算），前端 localStorage 记忆；
    完整攻击语 = 千<保留>百<保留>十<保留>个<保留>除<单双>除<大小>，
    保留数字按 1234567890 顺序去掉被排除数字得到。seal 为 None 时只有位置部分。
    """
    parity_form = ''
    size_form = ''
    if isinstance(seal, dict):
        if isinstance(seal.get('parity_form'), str) and len(seal['parity_form']) == 4:
            parity_form = seal['parity_form']
        if isinstance(seal.get('size_form'), str) and len(seal['size_form']) == 4:
            size_form = seal['size_form']
    data_div = ('<div id="k8-form-data" data-parity="%s" data-size="%s" style="display:none"></div>'
                % (escape(parity_form), escape(size_form)))
    position_labels = (('k8-ex-q', '千位'), ('k8-ex-b', '百位'),
                       ('k8-ex-s', '十位'), ('k8-ex-g', '个位'))
    inputs = []
    for input_id, label in position_labels:
        inputs.append(
            '<label>%s<input id="%s" inputmode="numeric" maxlength="10" '
            'autocomplete="off" placeholder="要排除的数字，如 8" '
            'aria-label="%s排除数字"></label>' % (label, input_id, label))
    posinput = ('<div class="k8-posinput">'
                '<div class="k8-posinput-title">✏️ 位置排除数字（你手动填，不进模型计算）</div>'
                '<div class="k8-posinput-grid">%s</div>'
                '<div class="k8-posinput-hint">每位置填 0-9 中要排除的数字（可多个，如 128 表示排除 1、2、8；'
                '重复自动去重）；留空 = 该位置不排除（保留 10 码）。输入自动记忆，刷新不丢。</div>'
                '</div>' % ''.join(inputs))
    copy_action = (
        "var b=document.getElementById('k8-combined-attack');if(!b)return;"
        "var t=b.value||b.textContent||'';"
        "var s=document.getElementById('k8-copy-status');"
        "var done=function(ok){if(s){s.textContent=ok?'已复制':'复制失败，请手动选择';"
        "s.style.color=ok?'#15803d':'#b91c1c';}};"
        "var fb=function(){var a=document.createElement('textarea');a.value=t;"
        "a.setAttribute('readonly','');a.style.cssText='position:fixed;left:-9999px;top:0;opacity:0;';"
        "document.body.appendChild(a);a.select();var ok=false;"
        "try{ok=document.execCommand('copy')===true;}catch(e){}a.remove();done(ok);};"
        "if(navigator.clipboard&&navigator.clipboard.writeText){"
        "navigator.clipboard.writeText(t).then(function(){done(true);},fb);}else{fb();}")
    script = (
        "<script>(function(){"
        "var KEY='kill8_position_exclusions_v1';var ORDER='1234567890';"
        "var IDS=['k8-ex-q','k8-ex-b','k8-ex-s','k8-ex-g'];"
        "function norm(v){v=(v||'').replace(/[^0-9]/g,'');var seen={},out='';"
        "for(var i=0;i<v.length;i++){if(!seen[v[i]]){seen[v[i]]=1;out+=v[i];}}return out;}"
        "function keep(v){v=norm(v);var out='';for(var i=0;i<ORDER.length;i++){"
        "if(v.indexOf(ORDER[i])<0)out+=ORDER[i];}return out;}"
        "function g(id){var el=document.getElementById(id);return el?el.value:'';}"
        "function forms(){var el=document.getElementById('k8-form-data');"
        "if(!el)return['',''];return[el.getAttribute('data-parity')||'',el.getAttribute('data-size')||''];}"
        "function build(){var f=forms();var p='千'+keep(g('k8-ex-q'))+'百'+keep(g('k8-ex-b'))"
        "+'十'+keep(g('k8-ex-s'))+'个'+keep(g('k8-ex-g'));"
        "if(f[0])p+='除'+f[0];if(f[1])p+='除'+f[1];return p;}"
        "function save(){var o={};for(var i=0;i<IDS.length;i++){o[IDS[i]]=norm(g(IDS[i]));}"
        "try{localStorage.setItem(KEY,JSON.stringify(o));}catch(e){}}"
        "function load(){var o={};try{o=JSON.parse(localStorage.getItem(KEY)||'{}')||{};}catch(e){}"
        "for(var i=0;i<IDS.length;i++){var el=document.getElementById(IDS[i]);"
        "if(el&&o[IDS[i]])el.value=o[IDS[i]];}}"
        "function update(){save();var a=document.getElementById('k8-combined-attack');"
        "if(a)a.value=build();}"
        "function init(){load();for(var i=0;i<IDS.length;i++){"
        "var el=document.getElementById(IDS[i]);if(el)el.addEventListener('input',update);}update();}"
        "if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',init);}else{init();}"
        "})();</script>")
    attack = ('<div class="k8-attack">'
              '<div class="k8-attack-title">完整攻击语（位置排除数字 + AI 冷形态）</div>'
              '<textarea id="k8-combined-attack" readonly rows="2" '
              'aria-label="kill8 完整攻击语（位置排除 + 冷形态）" '
              'style="display:block;width:100%;box-sizing:border-box;margin:7px 0;padding:9px;resize:vertical;'
              'border:1px solid #c7d2fe;border-radius:7px;background:#fff;font:15px/1.8 serif;overflow-wrap:anywhere;">'
              '</textarea>'
              '<button id="k8-attack-copy" type="button" onclick="' + copy_action + '" '
              'style="padding:7px 15px;border:0;border-radius:7px;background:#4f46e5;color:#fff;cursor:pointer;">'
              '复制完整攻击语</button>'
              '<span id="k8-copy-status" role="status" style="margin-left:8px;font-size:12px;"></span></div>')
    return data_div + posinput + attack + script


def _foot_html():
    return ('<div class="k8-foot">'
            '位置排除数字由你手动填写（下方输入框，自动记忆），<b>不进本框架计算</b>；本框架由 DeepSeek 决定：'
            '从 16 种四位单双形态（单/双，千百十个顺序）中选 1 种、'
            '从 16 种四位大小形态（大/小）中选 1 种，作为本期「冷形态反向排除项」。<br>'
            '冷形态定位依据：近 50 期出现次数少、当前遗漏长（仅历史统计，不代表未来概率）。<br>'
            '命中 = 目标期实际四位单双形态<b>不等于</b>封存单双形态，'
            '且实际四位大小形态<b>不等于</b>封存大小形态（2 项全中才算整组命中）。<br>'
            '无合尾、无连用限制（每期按冷热重选，不沿用上期）；'
            '旧口径（历史版本封存）保留但不计入本统计；'
            '只统计开奖前发布、开奖后核验的记录；北京时间 05:00 清零；'
            '只使用前 4 球（千/百/十/个），第 5 球不参与；平台提前 70 秒关盘。'
            '</div>')


def apply_kill_eight_block(content, report=None):
    """原地替换/插入 AI 双反向冷形态排除区块；任何异常原样返回 content。"""
    try:
        if not isinstance(content, str) or not content:
            return content
        fragment = render_block()
        if not fragment:
            return content
        start = content.find(START)
        end = content.find(END)
        if start != -1 and end != -1 and end > start:
            return content[:start] + fragment + content[end + len(END):]
        for anchor in ('<!-- KILL_ONE_END -->',):
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
