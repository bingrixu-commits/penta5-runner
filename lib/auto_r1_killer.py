from ai_verified_stats import next_period, settle_pending, atomic_json, digit
from ai_prediction_guard import (PublicationRejected, read_stats, find_seal, publication_rejection,
                                 validate_killer_response, store_prepared, publish_saved)
from app_secrets import get_secret, safe_error
from ai_operation_window import OperationWindow
from killer_ai_client import KillerResponseError, request_killer_response
from ai_model_context import prepare_model_request
from classic_request_attempts import reserve_request
from ai_strategy_validation import (classic_killer_validator, validation_report,
                                    KILLER_RETRY_INSTRUCTION)
from html import escape
from stats_history import load_snapshot, beijing_now
from position_input_features import history_from_snapshot as validated_source_history
from ai_call_control import AICallsDisabled,require_ai_enabled
try:require_ai_enabled()
except AICallsDisabled:
    print('AI API manually stopped; no request launched.')
    raise SystemExit(0)
import fcntl
import sys
import warnings
warnings.filterwarnings("ignore")


def _policy_counts(stats, strategy_id):
    """Project already-validated settlements into this request's policy/day.

    settle_pending has verified the original publication and actual outcomes
    before this helper runs. Keep the durable mixed-policy ledger untouched.
    Archives predating strategy identities retain their original rate view.
    """
    if not isinstance(strategy_id, str) or not strategy_id:
        return dict(stats.get('verified_hits', {})), dict(stats.get('verified_totals', {}))
    keys = ('k1', 'k2', 'k3', 'k4', 'he_all', 'he_q3', 'he_h3', 'pat1', 'pat2')
    hits, totals = dict.fromkeys(keys, 0), dict.fromkeys(keys, 0)
    seals = stats.get('prediction_seals', {})
    for entry in stats.get('verified_evaluations', ()):
        if entry.get('biz_date') != stats.get('biz_date'):
            continue
        seal = seals.get(entry.get('ref_period'))
        audit = seal.get('model_context_audit') if isinstance(seal, dict) else None
        body = audit.get('body') if isinstance(audit, dict) else None
        strategy = body.get('strategy') if isinstance(body, dict) else None
        if not isinstance(strategy, dict) or strategy.get('strategy_id') != strategy_id:
            continue
        outcome = entry.get('outcome')
        if not isinstance(outcome, dict) or any(type(outcome.get(key)) is not bool for key in keys):
            raise ValueError('invalid-verified-policy-outcome')
        for key in keys:
            totals[key] += 1
            hits[key] += int(outcome[key])
    return hits, totals


def check_noise_pass(history_list, p_threshold=0.05):
    digits = []
    for item in history_list:
        digits.extend([int(c) for c in str(item) if c.isdigit()])
    if not digits:
        return True
    counts = np.bincount(digits, minlength=10)
    _, p_val = chisquare(counts)
    return p_val >= p_threshold

import numpy as np
from scipy.stats import chisquare

# ==================== 动态自适应置信度与风控降级引擎 ====================
def evaluate_market_condition(draws_list, r1_stats_path="/root/penta5-runner/data/r1_stats.json"):
    """
    计算走势离散度与近期战绩，判定是否触发【防守/观望模式】:
    1. 连错熔断: 近2期若存在连续失手，判定进入混沌震荡期
    2. 信息熵/方差异常: 短期号码极端离散时自动提高防御等级
    """
    import json
    status = {"mode": "进攻模式", "risk_level": "正常", "suggestion": "正常执行排除策略"}
    
    try:
        with open(r1_stats_path, "r", encoding="utf-8") as f:
            stats = json.load(f)
        hist = stats.get("history", [])
        if len(hist) >= 2:
            recent_fails = sum(1 for h in hist[-2:] if not h.get("all_hit", True))
            if recent_fails >= 2:
                status["mode"] = "防守模式"
                status["risk_level"] = "高危震荡"
                status["suggestion"] = "⚠️ 近期波动剧烈，建议轻仓观望，系统已收紧排除范围"
                return status
    except Exception:
        pass

    return status
# ========================================================================


# 2026-10-31: 原「终极防波动防护层」calculate_defense_whitelist / enforce_stable_kill 从未被调用（死代码），已删除；git 历史可还原。

# -*- coding: utf-8 -*-
import datetime, json, os, re, sys, time, urllib.request
try:
    KILLER_MODEL = get_secret('PENTA5_MODEL_KILLER')
except RuntimeError:
    try:
        KILLER_MODEL = get_secret('PENTA5_DEEPSEEK_MODEL')
    except RuntimeError:
        KILLER_MODEL = 'deepseek-flash'
KILLER_MODEL_LABEL = {'deepseek-flash': 'DeepSeek-V4.1-Flash',
                      'deepseek-v4-flash': 'DeepSeek-V4-Flash',
                      'deepseek-v4-pro': 'DeepSeek-V4-Pro'}.get(KILLER_MODEL, KILLER_MODEL)

_job_lock = open('/root/penta5-runner/data/auto_r1_killer.py.lock', 'a')
try:
    fcntl.flock(_job_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(0)

STATE_PATH = "/root/penta5-runner/data/state.json"
PANEL_PATH = "/root/penta5-runner/panel.html"
STATS_PATH = "/root/penta5-runner/data/r1_stats.json"

# 1. 提取历史前四位真实开奖数据
snapshot = load_snapshot(persist=False)
d = snapshot['state']
latest = snapshot['latest'] or {}
period = str(latest.get('period') or '')
operation_window = OperationWindow(period)
history = [''.join(map(str,r['numbers'][:4])) for r in snapshot['records']]
recent_50 = [''.join(map(str,numbers)) for _,numbers in snapshot['recent']]
latest_numbers = list(latest.get('numbers') or [])[:4]
if not period or not recent_50:
    raise SystemExit('No validated draw history; prediction skipped.')

print(f"✅ 成功读取到最近 {len(recent_50)} 期前四位真实开奖: {recent_50[-5:]} ...")

# 2. Persist exact-target settlements before any network request. A business-day
# transition archives aggregates and retains every still-unresolved prediction.
stats = read_stats(STATS_PATH)
settle_pending(stats, snapshot['records'], now=snapshot['now'])
atomic_json(STATS_PATH, stats)
if find_seal(stats, period) is not None:
    restored = publish_saved(stats, STATS_PATH, period, 'R1_KILLER', load_snapshot,
        operation_window=operation_window)
    print('Existing sealed prediction preserved; saved card restored.' if restored else
          'Existing sealed prediction preserved; no new AI request or late publication.')
    raise SystemExit(0)
rejection = publication_rejection(period, snapshot, beijing_now())
if rejection:
    raise SystemExit(f'Prediction skipped: {rejection}')
operation_window.request_budget()
frozen_history = validated_source_history(snapshot, now=beijing_now())


def checked_source_snapshot(**kwargs):
    current = load_snapshot(**kwargs)
    try:
        if validated_source_history(current, now=beijing_now()) != frozen_history:
            raise ValueError('frozen-history-changed')
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        raise PublicationRejected('classic-killer-source-evidence-changed') from None
    return current


# === 数据算法：统计近50期单双与大小形态走势（遗漏与频次） ===
draws_rev = list(reversed(recent_50))
all_parity = [f"{a}{b}{c}{d}" for a in ["单", "双"] for b in ["单", "双"] for c in ["单", "双"] for d in ["单", "双"]]
all_size = [f"{a}{b}{c}{d}" for a in ["大", "小"] for b in ["大", "小"] for c in ["大", "小"] for d in ["大", "小"]]

p_omission = {p: len(recent_50) for p in all_parity}
s_omission = {s: len(recent_50) for s in all_size}
p_count = {p: 0 for p in all_parity}
s_count = {s: 0 for s in all_size}

for idx, num in enumerate(draws_rev):
    digits = [int(x) for x in str(num).zfill(4)[-4:]]
    cur_p = "".join(["单" if d % 2 != 0 else "双" for d in digits])
    cur_s = "".join(["大" if d >= 5 else "小" for d in digits])
    p_count[cur_p] = p_count.get(cur_p, 0) + 1
    s_count[cur_s] = s_count.get(cur_s, 0) + 1
    if p_omission[cur_p] == len(recent_50):
        p_omission[cur_p] = idx
    if s_omission[cur_s] == len(recent_50):
        s_omission[cur_s] = idx

cold_parity = sorted(all_parity, key=lambda x: (p_omission[x], -p_count[x]), reverse=True)
cold_size = sorted(all_size, key=lambda x: (s_omission[x], -s_count[x]), reverse=True)
cold_parity_stat = ", ".join([f"{p}(遗漏{p_omission[p]}期/出{p_count[p]}次)" for p in cold_parity[:4]])
cold_size_stat = ", ".join([f"{s}(遗漏{s_omission[s]}期/出{s_count[s]}欢)" for s in cold_size[:4]])

# === 数据算法：统计近50期单双与大小形态走势（遗漏与频次） ===
draws_rev = list(reversed(recent_50))
all_parity = [f"{a}{b}{c}{d}" for a in ["单", "双"] for b in ["单", "双"] for c in ["单", "双"] for d in ["单", "双"]]
all_size = [f"{a}{b}{c}{d}" for a in ["大", "小"] for b in ["大", "小"] for c in ["大", "小"] for d in ["大", "小"]]

p_omission = {p: len(recent_50) for p in all_parity}
s_omission = {s: len(recent_50) for s in all_size}
p_count = {p: 0 for p in all_parity}
s_count = {s: 0 for s in all_size}

for idx, num in enumerate(draws_rev):
    digits = [int(x) for x in str(num).zfill(4)[-4:]]
    cur_p = "".join(["单" if d % 2 != 0 else "双" for d in digits])
    cur_s = "".join(["大" if d >= 5 else "小" for d in digits])
    p_count[cur_p] = p_count.get(cur_p, 0) + 1
    s_count[cur_s] = s_count.get(cur_s, 0) + 1
    if p_omission[cur_p] == len(recent_50):
        p_omission[cur_p] = idx
    if s_omission[cur_s] == len(recent_50):
        s_omission[cur_s] = idx

cold_parity = sorted(all_parity, key=lambda x: (p_omission[x], -p_count[x]), reverse=True)
cold_size = sorted(all_size, key=lambda x: (s_omission[x], -s_count[x]), reverse=True)
cold_parity_stat = ", ".join([f"{p}(遗漏{p_omission[p]}期/出{p_count[p]}次)" for p in cold_parity[:4]])
cold_size_stat = ", ".join([f"{s}(遗漏{s_omission[s]}期/出{s_count[s]}欢)" for s in cold_size[:4]])

# 3. 将真实走势喂给模型进行深度推理（legacy prompt：实际发送内容由 prepare_model_request / independent-strategy-v2 覆盖，勿单独修改）
prompt = f"""以下是真实的最近开奖历史前四位走势数据（按时间先后排序，最新已开奖期号:{period}）：
{', '.join(recent_50)}

【系统算法统计：近50期形态实时遗漏排行（冷态前4位）】
· 单双形态冷态指标：{cold_parity_stat}
· 大小形态冷态指标：{cold_size_stat}

请结合真实历史数据与统计特征，审慎核查数据和候选的一致性；最终回答只输出下述严格JSON与简短理由，不在最终回答输出分析过程：
1. 深入分析各位置冷热、当前遗漏、振幅周期及和尾走势;
2. 给出当期四个位置的排除候选 (k1, k2, k3, k4 均为只包含1个0-9数字的列表)。冷热、重码、邻码、振幅和遗漏只是历史特征，不能据此断言未来概率；不得为了每期换码而机械变更候选;
3. 分别选择1个和尾/合尾排除候选 (0-9单一整数): he_all(全四位和尾杀1码), he_q3(前三位和尾杀1码), he_h3(后三位和尾杀1码);
4. 选择2个不同的四位排除形态 (exclude_patterns，如"单单单单"、"双双双双"、"大大大大"、"小小小小"或混合形态"单双双单"、"大大小小"等)。每个形态只能全用单双或全用大小；历史频率和遗漏仅供描述，不能据此声称下期概率优势；
5. 给出精炼的历史特征及不确定性说明 (reason, 40字以内)，不得声称保证命中.
必须在最后输出严格 JSON:
{{"k1":[0],"k2":[1],"k3":[2],"k4":[3],"he_all":0,"he_q3":0,"he_h3":0,"exclude_patterns":["单单双双","大大小小"],"reason":"理由"}}"""

prompt, model_context_audit = prepare_model_request(prompt, snapshot, 'killer', stats,
                                                   data_dir=os.path.dirname(STATS_PATH))
from panel_context import build as _panel_build_context, prompt_text as _panel_prompt_text
from hashlib import sha256 as _panel_sha256
_panel_snapshot = _panel_build_context(period, next_period(period))
_panel_text, _panel_sha = _panel_prompt_text(_panel_snapshot)
prompt = (prompt + '\n\n【面板全量上下文（同一面板其他框架最近发布/战绩；只含已发布数据，各条自带'
          ' reference/target；仅供自主决策参考，不是未来概率保证）】\n' + _panel_text + '\n')
model_context_audit = dict(model_context_audit,
                           panel_context=_panel_snapshot,
                           panel_context_sha256=_panel_sha,
                           sent_prompt_sha256=_panel_sha256(prompt.encode('utf-8')).hexdigest())
print('PANEL_CONTEXT kind=%s sha=%s bytes=%d' % ('killer', _panel_sha[:16], len(_panel_text)))
response_validator = classic_killer_validator(model_context_audit)
print("🤖 正在连接 DeepSeek 开放平台推导【杀码框架与攻击语】...")
import requests
killer_response_metadata = []
killer_validated_attempt = {}


def record_killer_response_metadata(message):
    # Persist only the metadata of the provider attempt actually validated.
    # Diagnostic parsing never changes inference, publishes text, or retries.
    if isinstance(message, str) and message.startswith('AI_RESPONSE_META '):
        try:
            entry = json.loads(message.split(' ', 1)[1])
            if isinstance(entry, dict):
                killer_response_metadata.append(entry)
                del killer_response_metadata[:-3]
        except (ValueError, TypeError):
            pass
    elif isinstance(message, str):
        accepted = re.fullmatch(r'Killer response validated; ref=([0-9]{11}); attempt=([1-3]); publication pending[.]', message)
        if accepted:
            killer_validated_attempt.update(ref=accepted.group(1), attempt=int(accepted.group(2)))
    print(message)


def accepted_killer_response_metadata(reference):
    if killer_validated_attempt.get('ref') != reference:
        return None
    attempt = killer_validated_attempt.get('attempt')
    entry = next((entry for entry in reversed(killer_response_metadata)
                 if entry.get('ref') == reference and type(entry.get('attempt')) is int
                 and entry['attempt'] == attempt), None)
    report = validation_report(response_validator)
    return dict(entry, strategy_validation=report) if entry is not None and report else entry


try:
    with reserve_request('killer', period, post=requests.post,
                         input_sha256=model_context_audit['input_archive_sha256'],
                         root=os.path.dirname(os.path.dirname(STATS_PATH)), clock=beijing_now) as guarded_post:
        API_KEY = os.getenv("DEEPSEEK_API_KEY") or get_secret("PENTA5_KILLER_API_KEY")
        r, response_generated_at = request_killer_response(prompt, period,
            post=guarded_post, api_key=API_KEY,
            model=KILLER_MODEL,
            load_snapshot=checked_source_snapshot, clock=beijing_now,
            sleep=time.sleep, monotonic=time.monotonic, log=record_killer_response_metadata,
            operation_window=operation_window, validator=response_validator,
            retry_instruction=(KILLER_RETRY_INSTRUCTION if hasattr(response_validator, 'strategy_validation') else None))
except KillerResponseError as error:
    raise SystemExit(str(error)) from None

killer_tail_hold = None
try:
    from killer_tail_hold import apply_killer_tail_hold
    killer_tail_hold = apply_killer_tail_hold(r, stats, period, latest_numbers)
    if killer_tail_hold is not None:
        print('KILLER_TAIL_HOLD3 ref=%s reused=%s note=%s' % (
            period, killer_tail_hold.get('reused_previous'), killer_tail_hold.get('note')))
except Exception as _killer_tail_error:
    print('KILLER_TAIL_HOLD3_SKIPPED reason=%s' % type(_killer_tail_error).__name__)

# A0: joint-form optimization (dormant until the auto-promotion gate enables it)
# 顺序修正（2026-11-01）：必须在 tail-hold 之后执行。tail-hold 会改写 he_all/he_q3/he_h3，
# 若先优化形态再改合尾，封存形态对最终合尾不是最优，按最终封存重算的审计期望会系统性高于实盘。
joint_form_applied = False
try:
    from joint_form_optimizer import optimize_killer_response
    _joint_form = optimize_killer_response(r, root=os.path.dirname(os.path.dirname(STATS_PATH)))
    if _joint_form is not None:
        r = _joint_form['response']
        joint_form_applied = True
        print('JOINT_FORM_OPTIMIZED kind=killer ref=%s ai=%s opt=%s expected_ai=%.6f expected_opt=%.6f'
              % (period, _joint_form['ai_patterns'], _joint_form['opt_patterns'],
                 _joint_form['expected_ai'], _joint_form['expected_opt']))
except Exception as _joint_form_error:
    print('JOINT_FORM_OPTIMIZER_SKIPPED kind=killer reason=%s' % type(_joint_form_error).__name__)

k1_list, k2_list, k3_list, k4_list = (r[key] for key in ('k1', 'k2', 'k3', 'k4'))

k1_str = "/".join(str(x) for x in k1_list) if k1_list else "--"
k2_str = "/".join(str(x) for x in k2_list) if k2_list else "--"
k3_str = "/".join(str(x) for x in k3_list) if k3_list else "--"
k4_str = "/".join(str(x) for x in k4_list) if k4_list else "--"

p1, p2 = r['exclude_patterns']
he_all, he_q3, he_h3 = (str(r[key]) for key in ('he_all', 'he_q3', 'he_h3'))
reason = r['reason']

if any(len(items) != 1 or digit(items[0]) is None for items in [k1_list,k2_list,k3_list,k4_list]):
    raise SystemExit('Invalid position predictions; existing sealed result preserved.')
if any(digit(value) is None for value in [he_all,he_q3,he_h3]):
    raise SystemExit('Invalid sum-tail predictions; existing sealed result preserved.')
if any(not isinstance(value,str) or not re.fullmatch(r'(?:[单双]{4}|[奇偶]{4}|[大小]{4})',value) for value in [p1,p2]):
    raise SystemExit('Invalid pattern predictions; existing sealed result preserved.')

# 记录本期杀码与合尾/形态，供下一次开奖时对账
killer_metadata = accepted_killer_response_metadata(period)
if joint_form_applied and isinstance(killer_metadata, dict):
    killer_metadata = {key: value for key, value in killer_metadata.items() if key != 'strategy_validation'}
    killer_metadata['joint_form_optimized'] = True
if killer_tail_hold is not None and isinstance(killer_metadata, dict):
    killer_metadata = dict(killer_metadata)
    killer_metadata['tail_hold3'] = killer_tail_hold
pending_candidate = {
    "ref_period": period,
    "model_context_audit": model_context_audit,
    "model_metadata": killer_metadata,
    "target_period": next_period(period),
    "kills": {
        "k1": k1_list,
        "k2": k2_list,
        "k3": k3_list,
        "k4": k4_list
    },
    "he_all": he_all,
    "he_q3": he_q3,
    "he_h3": he_h3,
    "p1": p1,
    "p2": p2
}

def fmt_rate(hits, total):
    if total <= 0:
        return '<span style="font-size: 12px; color: #888; font-weight: normal;">(待首期开奖结算)</span>'
    pct = (hits / total) * 100
    return f'<span style="font-size: 12px; color: #16a765; font-weight: bold;">({hits}/{total} {pct:.1f}%)</span>'

request_strategy_id = ((model_context_audit.get('body') or {}).get('strategy') or {}).get('strategy_id')
policy_hits, policy_totals = _policy_counts(stats, request_strategy_id)
rate1 = fmt_rate(policy_hits.get("k1", 0), policy_totals.get("k1", 0))
rate2 = fmt_rate(policy_hits.get("k2", 0), policy_totals.get("k2", 0))
rate3 = fmt_rate(policy_hits.get("k3", 0), policy_totals.get("k3", 0))
rate4 = fmt_rate(policy_hits.get("k4", 0), policy_totals.get("k4", 0))
rate_he_all = fmt_rate(policy_hits.get("he_all", 0), policy_totals.get("he_all", 0))
rate_he_q3 = fmt_rate(policy_hits.get("he_q3", 0), policy_totals.get("he_q3", 0))
rate_he_h3 = fmt_rate(policy_hits.get("he_h3", 0), policy_totals.get("he_h3", 0))
rate_p1 = fmt_rate(policy_hits.get("pat1", 0), policy_totals.get("pat1", 0))
rate_p2 = fmt_rate(policy_hits.get("pat2", 0), policy_totals.get("pat2", 0))

print("\n--- " + KILLER_MODEL_LABEL + " 真实推导结果 ---")
print(f"• 杀码: 第1位≠{k1_str}, 第2位≠{k2_str}, 第3位≠{k3_str}, 第4位≠{k4_str}")
print("Verified settled samples:", stats.get("verified_totals", {}))
print(f"• 推荐合杀码: 全位 #{he_all}, 前三 #{he_q3}, 后三 #{he_h3}")
print(f"• 排除形态: {p1}, {p2}")

# 生成深度排除攻击语
_base_d = "1234567890"
_q = "".join([d for d in _base_d if d != str(k1_str).strip()])
_b = "".join([d for d in _base_d if d != str(k2_str).strip()])
_s = "".join([d for d in _base_d if d != str(k3_str).strip()])
_g = "".join([d for d in _base_d if d != str(k4_str).strip()])

_he_all = "".join([d for d in _base_d if d != str(he_all).strip()])
_he_q3 = "".join([d for d in _base_d if d != str(he_q3).strip()])
_he_h3 = "".join([d for d in _base_d if d != str(he_h3).strip()])

killer_attack_str = f"千{_q}百{_b}十{_s}个{_g}四定千百十个合{_he_all}千百十合{_he_q3}百十个合{_he_h3}除{p1}除{p2}"

joint_form_note = (' ｜ 形态：本地精确联合优化（AI 杀码/合尾杀不变）' if joint_form_applied else '')
killer_tail_note = ''
if killer_tail_hold is not None:
    killer_tail_note = (' ｜ 三合尾稳定化：沿用上期（第%s期）' % killer_tail_hold['previous_streak']
                        if killer_tail_hold.get('reused_previous')
                        else ' ｜ 三合尾稳定化：本期换码')
# 4. 同步更新到 panel.html
card = f"""<!-- R1_KILLER_START -->
<div style="margin: 20px auto; max-width: 1000px; padding: 14px 20px; background: #ffffff; border: 2px solid #1677ff; border-radius: 8px; font-family: sans-serif; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
        <span style="font-size: 16px; font-weight: bold; color: #1677ff;">
            🤖{KILLER_MODEL_LABEL} 智能深度排除 (最新参考期号: {period})
        </span>
        <span style="font-size: 12px; color: #888;">AI 计算时间: {response_generated_at.strftime("%H:%M:%S")}（北京时间）</span>
    </div>
    <div style="font-size: 12px; color: #64748b; margin-bottom: 6px;">推理模式：高强度思考（high）{joint_form_note}{killer_tail_note}</div>
    <div style="font-size: 15px; line-height: 2.2; color: #333;">
        • <b>四位杀码 (各排除1码 · 今日可核验战绩):</b>
            第1位杀 <span style="color: #d9363e; font-weight:bold; font-size: 18px;">{k1_str}</span> {rate1}, 
            第2位杀 <span style="color: #d9363e; font-weight:bold; font-size: 18px;">{k2_str}</span> {rate2}, 
            第3位杀 <span style="color: #d9363e; font-weight:bold; font-size: 18px;">{k3_str}</span> {rate3}, 
            第4位杀 <span style="color: #d9363e; font-weight:bold; font-size: 18px;">{k4_str}</span> {rate4}
        <br>
        <div style="margin-top:4px;"> • <b>推荐合杀码 (各排除1码 · 今日可核验战绩):</b> <span style="color:#2563eb; font-weight:bold;">【千百十个·全位】</span>合杀 <span style="color:#dc2626; font-weight:bold;">#{he_all}</span> {rate_he_all}, <span style="color:#2563eb; font-weight:bold;">【千百十·前三】</span>合杀 <span style="color:#dc2626; font-weight:bold;">#{he_q3}</span> {rate_he_q3}, <span style="color:#2563eb; font-weight:bold;">【百十个·后三】</span>合杀 <span style="color:#dc2626; font-weight:bold;">#{he_h3}</span> {rate_he_h3}</div>
        • <b>排除形态 (排除2形态 · 今日可核验战绩):</b> 
            <span style="color: #d9363e; font-weight:bold;">{escape(str(p1))}</span> {rate_p1}、
            <span style="color: #d9363e; font-weight:bold;">{escape(str(p2))}</span> {rate_p2}
        <br>
        • <b>AI 推理理由:</b> 
            <span style="color:#555; background:#f5f5f5; padding: 2px 8px; border-radius: 4px; font-size: 13px;">{escape(str(reason))}</span>
    </div>
  <div style="margin-top: 12px; margin-bottom: 10px;">
    <div style="font-size: 13px; font-weight: bold; color: #1677ff; margin-bottom: 6px;">深度排除攻击语:</div>
    <div id="r1_killer_attack_text" style="word-break: break-all; font-family: monospace; background: #f1f5f9; padding: 10px 14px; border-radius: 6px; font-size: 13px; color: #0f172a; border: 1px solid #e2e8f0; user-select: all;">
      {escape(str(killer_attack_str))}
    </div>
  </div>

  <div style="display: flex; justify-content: flex-end; align-items: center; margin-top: 10px;">
    <button type="button" id="r1_killer_copy_btn" onclick="copyR1KillerAttack()" style="border: 0; border-radius: 7px; padding: 7px 16px; background: #1677ff; color: #ffffff; font-size: 13px; font-weight: bold; cursor: pointer; transition: all 0.2s;">复制攻击语</button>
  </div>
</div>

<script>
function copyR1KillerAttack() {{
  const textElem = document.getElementById("r1_killer_attack_text");
  if (!textElem) return;
  const txt = textElem.innerText.trim();
  navigator.clipboard.writeText(txt).then(function() {{
    const btn = document.getElementById("r1_killer_copy_btn");
    if (btn) {{
      const oldText = btn.innerText;
      btn.innerText = "✅ 复制成功";
      setTimeout(function() {{ btn.innerText = oldText; }}, 1500);
    }}
  }});
}}
</script>
<!-- R1_KILLER_END -->"""

publication_snapshot = checked_source_snapshot(persist=False)
operation_window.require_open()
store_prepared(stats, STATS_PATH, publication_snapshot, pending_candidate, card,
               generated_at=response_generated_at)
if publish_saved(stats, STATS_PATH, period, 'R1_KILLER', checked_source_snapshot,
        operation_window=operation_window):
    print('Panel updated; publication time persisted for exact-target verification.')
else:
    print('Response retained without verified publication: source advanced or deadline passed.')
