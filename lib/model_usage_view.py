"""面板「模型运行」区块：Token / 缓存 / 延迟 / 错误 / 余额（只读派生数据）。

数据源：data/model_usage/summary.json（lib/model_usage.py 每 10 分钟生成）。
自包含 section + 内联 style；<!-- MODEL_USAGE_START/END --> 原地替换；
标记不存在时插入到 RUNTIME_OBSERVABILITY_END 之后；任何异常原样返回 html。
"""
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path

ROOT = Path('/root/penta5-runner')
MARKER = 'MODEL_USAGE'
START = '<!-- %s_START -->' % MARKER
END = '<!-- %s_END -->' % MARKER
TZ = timezone(timedelta(hours=8))
STALE_SECONDS = 1800

STYLE = """<style>
#model-usage{box-sizing:border-box;width:calc(100% - 24px);max-width:1240px;margin:12px auto;padding:14px;border:1px solid #93c5fd;border-radius:12px;background:#f8fbff;color:#1e293b;font:14px/1.6 sans-serif;min-width:0;overflow-wrap:anywhere}
#model-usage *{box-sizing:border-box}
#model-usage h3{font-size:18px;color:#1e3a8a;margin:0 0 4px}
#model-usage .mu-meta{font-size:13px;color:#64748b;margin-bottom:10px}
#model-usage .mu-warn{color:#b45309;font-weight:700}
#model-usage .mu-note{font-size:12.5px;color:#92400e;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:6px 8px;margin:8px 0}
#model-usage .mu-ok{font-size:12.5px;color:#15803d;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:6px;padding:6px 8px;margin:8px 0}
#model-usage .mu-kpis{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
#model-usage .mu-kpi{min-width:96px;flex:1 1 96px;border:1px solid #dbeafe;border-radius:8px;background:#fff;padding:6px 10px}
#model-usage .mu-kpi b{display:block;font-size:20px;line-height:1.2;color:#1d4ed8}
#model-usage .mu-kpi span{font-size:12px;color:#64748b}
#model-usage .mu-kpi.mu-bad b{color:#b91c1c}
#model-usage .mu-grid{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0}
#model-usage .mu-card{flex:1 1 220px;border:1px solid #e2e8f0;border-radius:8px;background:#fff;padding:8px 10px}
#model-usage .mu-label{font-size:12px;color:#64748b}
#model-usage .mu-value{font-size:18px;font-weight:700;color:#0f172a}
#model-usage .mu-sub{font-size:12px;color:#64748b}
#model-usage details{margin-top:8px}
#model-usage summary{cursor:pointer;color:#1d4ed8;font-size:13px}
#model-usage table{width:100%;border-collapse:collapse;font-size:12.5px;color:#334155;margin-top:6px}
#model-usage th,#model-usage td{padding:4px 6px;text-align:right;border-top:1px dashed #e2e8f0}
#model-usage th:first-child,#model-usage td:first-child{text-align:left}
#model-usage th{color:#475569;font-weight:600}
#model-usage .mu-foot{font-size:11.5px;color:#64748b;line-height:1.55;margin-top:8px}
</style>"""


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _stamp(value):
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TZ)
    return moment.astimezone(TZ)


def _num(value, digits=0):
    if not _is_number(value):
        return '-'
    return f'{value:,.{digits}f}'


def _tokens(value):
    if not _is_number(value):
        return '-'
    if value >= 1000000:
        return f'{value / 1000000:.2f}M'
    if value >= 10000:
        return f'{value / 1000:.1f}K'
    return _num(value)


def _pct(value):
    if not _is_number(value):
        return '-'
    return f'{value * 100:.1f}%'


def _money(value):
    if not _is_number(value):
        return '-'
    return f'¥{value:,.2f}'


def _secs(value):
    if not _is_number(value):
        return '-'
    return f'{value:.1f}s'


def _freshness(summary, now):
    updated = _stamp(summary.get('updated_at'))
    if updated is None:
        return '采集时间未知', True
    age = (now - updated).total_seconds()
    if age > STALE_SECONDS:
        return f'采集停更 {int(age // 60)} 分钟', True
    return f'更新 {updated.strftime("%H:%M:%S")}', False


def _kpi(label, value, bad=False):
    return ('<div class="mu-kpi%s"><b>%s</b><span>%s</span></div>'
            % (' mu-bad' if bad else '', escape(str(value)), escape(label)))


def _model_row(name, row):
    return ('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (escape(str(name)), _num(row.get('responses')), _tokens(row.get('prompt')),
               _tokens(row.get('completion')), _pct(row.get('cache_rate')),
               _secs(row.get('p50'))))


def _task_row(name, row):
    attempts = row.get('attempts')
    responses = row.get('responses')
    return ('<tr><td>%s</td><td>%s / %s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
            % (escape(str(name)), _num(responses), _num(attempts),
               _tokens(row.get('prompt')), _tokens(row.get('completion')),
               _num(row.get('errors'))))


def render(summary, *, now=None):
    now = now.astimezone(TZ) if isinstance(now, datetime) else datetime.now(TZ)
    if not isinstance(summary, dict):
        summary = {}
    today = summary.get('today') if isinstance(summary.get('today'), dict) else {}
    tokens = today.get('tokens') if isinstance(today.get('tokens'), dict) else {}
    latency = today.get('latency') if isinstance(today.get('latency'), dict) else {}
    errors = today.get('errors') if isinstance(today.get('errors'), dict) else {}
    last7d = summary.get('last7d') if isinstance(summary.get('last7d'), dict) else {}
    last7d_errors = last7d.get('errors') if isinstance(last7d.get('errors'), dict) else {}
    balance = summary.get('balance') if isinstance(summary.get('balance'), dict) else {}
    health = summary.get('health') if isinstance(summary.get('health'), dict) else {}
    by_model = today.get('by_model') if isinstance(today.get('by_model'), dict) else {}
    by_task = today.get('by_task') if isinstance(today.get('by_task'), dict) else {}
    updated_text, stale = _freshness(summary, now)
    as_of = escape(str(summary.get('updated_at') or ''))
    biz_date = escape(str(summary.get('biz_date') or ''))

    kpis = [
        _kpi('HTTP 200 响应', _num(today.get('responses'))),
        _kpi('尝试', _num(today.get('attempts'))),
        _kpi('重试', _num(today.get('retries'))),
        _kpi('HTTP 402', _num(errors.get('payment') or 0), bool(errors.get('payment'))),
        _kpi('超时/未完成', _num(errors.get('timeout') or 0), bool(errors.get('timeout'))),
        _kpi('模型不符', _num(today.get('mismatch')), bool(today.get('mismatch'))),
    ]

    cache_hint = '缓存命中 %s · 命中率 %s' % (_tokens(tokens.get('cache_hit')),
                                             _pct(tokens.get('cache_rate')))
    latency_sub = 'p50 %s · p95 %s · 样本 %s' % (_secs(latency.get('p50')),
                                                _secs(latency.get('p95')),
                                                _num(latency.get('n')))
    if _is_number(balance.get('balance')):
        balance_value = _money(balance.get('balance'))
        balance_sub = '更新 %s' % (escape(str(balance.get('at') or '')[:16].replace('T', ' ')))
        if balance.get('auto_paused'):
            balance_sub += ' · 余额守护已暂停 AI'
    else:
        balance_value = '-'
        balance_sub = '余额接口未返回可解析余额'
    burn = balance.get('burn_1h_cny')
    burn_span = balance.get('burn_1h_span_hours')
    if _is_number(burn) and _is_number(burn_span) and burn_span < 0.75:
        balance_sub += ' · 近期(%.0f分钟)约 %s/h' % (burn_span * 60, _money(burn))
    elif _is_number(burn):
        balance_sub += ' · 近1h约 %s/h' % _money(burn)
    elif balance.get('recharged_recently') is True:
        balance_sub += ' · 充值后重新起算'
    else:
        balance_sub += ' · 烧钱速率待样本积累'
    runway = balance.get('runway_hours')
    if _is_number(runway):
        balance_sub += ' · 预计可用 %.1fh' % runway

    cards = (
        '<div class="mu-card"><div class="mu-label">输入 Token</div>'
        '<div class="mu-value">%s</div><div class="mu-sub">%s</div></div>'
        '<div class="mu-card"><div class="mu-label">输出 Token</div>'
        '<div class="mu-value">%s</div><div class="mu-sub">其中思考 %s</div></div>'
        '<div class="mu-card"><div class="mu-label">响应延迟（provider）</div>'
        '<div class="mu-value">%s</div><div class="mu-sub">%s</div></div>'
        '<div class="mu-card"><div class="mu-label">DeepSeek 余额</div>'
        '<div class="mu-value">%s</div><div class="mu-sub">%s</div></div>'
        % (_tokens(tokens.get('prompt')), cache_hint,
           _tokens(tokens.get('completion')), _tokens(tokens.get('reasoning')),
           _secs(latency.get('p95')), latency_sub,
           balance_value, balance_sub)
    )

    reasons = health.get('reasons') if isinstance(health.get('reasons'), list) else []
    if reasons:
        notice = '<div class="mu-note">⚠ %s</div>' % escape('；'.join(str(item) for item in reasons))
    elif today.get('responses'):
        notice = '<div class="mu-ok">运行平稳：今日无异常信号</div>'
    else:
        notice = '<div class="mu-note">今日暂无已落日志的模型调用（AI 开关关闭或余额守护暂停时属正常）</div>'
    error_labels = (('provider', '服务商'), ('rate_limit', '限流429'),
                    ('invalid_response', '无效响应'), ('payment', '欠费402'),
                    ('timeout', '超时'))
    def _error_text(row):
        return '、'.join('%s×%s' % (label, _num(row.get(key)))
                         for key, label in error_labels if _is_number(row.get(key)) and row.get(key))
    today_error_text = _error_text(errors)
    last7d_error_text = _error_text(last7d_errors)
    if today_error_text or last7d_error_text:
        error_text = ' · '.join(part for part in (
            ('今日 ' + today_error_text) if today_error_text else '',
            ('近7日 ' + last7d_error_text) if last7d_error_text else '') if part)
        notice += '<div class="mu-note">错误分布：%s</div>' % escape(error_text)

    window_rows = ''
    for label, key in (('今日', 'today'), ('昨日', 'yesterday'), ('近7日', 'last7d')):
        row = summary.get(key) if isinstance(summary.get(key), dict) else {}
        row_tokens = row.get('tokens') if isinstance(row.get('tokens'), dict) else {}
        row_errors = row.get('errors') if isinstance(row.get('errors'), dict) else {}
        window_rows += ('<tr><td>%s</td><td>%s / %s</td><td>%s</td><td>%s</td>'
                        '<td>%s</td><td>%s</td><td>%s</td></tr>'
                        % (label, _num(row.get('responses')), _num(row.get('attempts')),
                           _num(row.get('error_total')), _num(row_errors.get('payment') or 0),
                           _tokens(row_tokens.get('prompt')), _tokens(row_tokens.get('completion')),
                           _pct(row_tokens.get('cache_rate'))))
    window_table = ('<details open><summary>窗口对照</summary>'
                    '<table><thead><tr><th>窗口</th><th>响应/尝试</th><th>失败</th>'
                    '<th>其中402</th><th>输入</th><th>输出</th><th>缓存命中率</th></tr></thead>'
                    '<tbody>' + window_rows + '</tbody></table></details>')

    model_rows = ''.join(_model_row(name, row) for name, row in
                         sorted(by_model.items(), key=lambda item: -(item[1].get('responses') or 0)))
    task_rows = ''.join(_task_row(name, row) for name, row in
                        sorted(by_task.items(), key=lambda item: -(item[1].get('responses') or 0)))
    details = ('<details><summary>按模型 / 按任务明细</summary>'
               '<table><thead><tr><th>模型（请求档）</th><th>响应</th><th>输入</th>'
               '<th>输出</th><th>缓存命中率</th><th>p50</th></tr></thead><tbody>'
               + (model_rows or '<tr><td colspan="6">暂无数据</td></tr>') +
               '</tbody></table>'
               '<table><thead><tr><th>任务</th><th>响应/尝试</th><th>输入</th><th>输出</th>'
               '<th>失败</th></tr></thead><tbody>'
               + (task_rows or '<tr><td colspan="5">暂无数据</td></tr>') +
               '</tbody></table></details>')

    stale_text = (' <span class="mu-warn">· %s</span>' % escape(updated_text)) if stale else ''
    return (START + '<section id="model-usage" data-as-of="%s">' % as_of + STYLE +
            '<h3>模型运行（Token / 缓存 / 延迟 / 错误 / 余额）</h3>'
            '<div class="mu-meta">统计日 %s（05:00 起）· %s · 来源：AI 任务日志的 '
            'AI_RESPONSE_META / AI_HTTP_PHASE / AI_RETRY_META（只读派生）%s</div>'
            % (biz_date, escape(updated_text), stale_text) +
            '<div class="mu-kpis">' + ''.join(kpis) + '</div>' +
            notice +
            '<div class="mu-grid">' + cards + '</div>' +
            window_table +
            details +
            '<div class="mu-foot">口径：只统计已写入日志的调用，未落日志的请求不估算；'
            '「成功响应」按 HTTP 200 计，包含被策略校验拒绝的 attempt；'
            '缓存命中率 = 缓存命中输入 token /（命中 + 未命中）输入 token；延迟为 provider 返回的 '
            'elapsed_seconds（不含排队与重试间隔）；余额来自 DeepSeek 余额接口经 ai_balance_guard '
            '的每分钟快照，扣费速率为相邻快照差值推算，充值后自动重新起算。</div>'
            '</section>' + END)


def apply(content, fragment):
    """Replace our own top-level marker range; insert after runtime observability."""
    if not isinstance(content, str) or not isinstance(fragment, str):
        raise ValueError('invalid panel content or fragment')
    if fragment.count('id="model-usage"') != 1:
        raise ValueError('model usage fragment must contain exactly one block')
    start = content.find(START)
    end = content.find(END)
    if start >= 0 and end > start:
        return content[:start] + fragment + content[end + len(END):]
    if 'id="model-usage"' in content:
        raise ValueError('model usage block without markers')
    anchor = '<!-- RUNTIME_OBSERVABILITY_END -->'
    position = content.find(anchor)
    if position >= 0:
        position += len(anchor)
        return content[:position] + '\n' + fragment + '\n' + content[position:]
    body = content.lower().find('</body>')
    if body >= 0:
        return content[:body] + fragment + '\n' + content[body:]
    raise ValueError('no usable anchor for model usage block')


def publish(summary, *, root=ROOT):
    """Atomic shared panel writer for the derived model-usage block."""
    from panel_io import update_panel
    fragment = render(summary)
    return update_panel(lambda content: apply(content, fragment),
                        path=Path(root) / 'panel.html')
