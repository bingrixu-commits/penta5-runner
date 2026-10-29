from app_secrets import get_secret, safe_error
from ai_operation_window import OperationWindow
from ai_prediction_guard import (PublicationRejected, read_stats, find_seal, publication_rejection,
                                 validate_framework_response, store_prepared, publish_saved)
from framework_ai_client import request_framework_response
from ai_model_context import prepare_model_request
from classic_request_attempts import reserve_request
from ai_strategy_validation import (classic_framework_validator, validation_report,
                                    FRAMEWORK_RETRY_INSTRUCTION)
from ai_verified_stats import next_period
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
# ==================== DeepSeek 杀码框架与攻击语 (量化高稳优化版) ====================

import datetime, json, os, re, sys, time
try:
    FRAMEWORK_MODEL = get_secret('PENTA5_DEEPSEEK_MODEL')
except RuntimeError:
    FRAMEWORK_MODEL = 'deepseek-flash'
from collections import Counter


_job_lock = open('/root/penta5-runner/data/auto_r1_framework.py.lock', 'a')
try:
    fcntl.flock(_job_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit(0)

STATE_PATH = "/root/penta5-runner/data/state.json"
PANEL_PATH = "/root/penta5-runner/panel.html"
SEALS_PATH = "/root/penta5-runner/data/r1_framework_seals.json"

# 1. 提取历史真实开奖数据
if not os.path.exists(STATE_PATH):
    print(f"❌ 找不到数据文件: {STATE_PATH}")
    sys.exit(1)

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

print(f"✅ 成功读取到最近 {len(recent_50)} 期开奖数据，参考期号: {period}")

seals = read_stats(SEALS_PATH)
if find_seal(seals, period) is not None:
    restored = publish_saved(seals, SEALS_PATH, period, 'R1_FRAMEWORK', load_snapshot,
        operation_window=operation_window)
    print('Existing framework preserved; saved card restored.' if restored else
          'Existing framework preserved; no new AI request or late publication.')
    raise SystemExit(0)
rejection = publication_rejection(period, snapshot, beijing_now())
if rejection:
    raise SystemExit(f'Framework skipped: {rejection}')
operation_window.request_budget()
frozen_history = validated_source_history(snapshot, now=beijing_now())


def checked_source_snapshot(**kwargs):
    current = load_snapshot(**kwargs)
    try:
        if validated_source_history(current, now=beijing_now()) != frozen_history:
            raise ValueError('frozen-history-changed')
    except (ValueError, TypeError, KeyError, OSError, OverflowError):
        raise PublicationRejected('classic-framework-source-evidence-changed') from None
    return current



# 2. 本地真实量化统计 (防大模型幻觉)
def calc_gaps(seq_list, pos_idx):
    gaps = {i: -1 for i in range(10)}
    rev = list(reversed(seq_list))
    for p, num_str in enumerate(rev):
        d = int(num_str[pos_idx])
        if gaps[d] == -1:
            gaps[d] = p
    for k in gaps:
        if gaps[k] == -1: gaps[k] = len(seq_list)
    return gaps

q_gaps = calc_gaps(recent_50, 0)
b_gaps = calc_gaps(recent_50, 1)

# 找出真实的温弱码（遗漏4-8期）
q_weak = [k for k, v in q_gaps.items() if 4 <= v <= 8]
b_weak = [k for k, v in b_gaps.items() if 4 <= v <= 8]

# 3. 向 DeepSeek 官方请求决策
prompt = f"""以下是真实的最近开奖历史走势数据（最新已开奖参考期号:{period}，本次预测目标期号:{next_period(period)}）：
{', '.join(recent_50)}

【任务定义与正式核验口径】
历史按开奖时间从旧到新排列，最后一条是参考期{period}的已开奖数据；只对下一目标期{next_period(period)}提出候选，不为已开奖期改写候选。
每条历史只含第1至4球，依次记为千位q、百位b、十位s、个位g；四位字符串中的0及前导0均为有效数字。第五球不参与本任务的统计、候选或核验。
q_kill1是千位排除1码，目标期千位不等于该码才算排除成功；b_kill1是百位排除1码，目标期百位不等于该码才算排除成功。
pos_kills的q/b/s/g分别是千/百/十/个四位各排2码的单独参考，不与q_kill1/b_kill1合并，不替代这两项单码排除，也不计入正式8项命中统计或本条攻击语。
he_q3_kill排除千百十合尾，即(千+百+十)%10；he_h3_kill排除百十个合尾，即(百+十+个)%10。目标期对应合尾不等于该字段的单一码，才算该项排除成功。
pattern_ds是按千百十个顺序排除的完整四位单双形态：1/3/5/7/9为单，0/2/4/6/8为双；目标期完整形态不等于该字段才算排除成功。
pattern_dx是按千百十个顺序排除的完整四位大小形态：0至4为小，5至9为大；目标期完整形态不等于该字段才算排除成功。
he2_keep保留4码：计算前四位任意2数的6个位置组合(千百、千十、千个、百十、百个、十个)，各组合求和后取%10；目标期这6个合尾中任意一个落入保留集，即算该项命中，不要求全部落入，也不是排除这4码。
he3_keep保留6码：计算前四位任意3数的4个位置组合(千百十、千百个、千十个、百十个)，各组合求和后取%10；目标期这4个合尾中任意一个落入保留集，即算该项命中，不要求全部落入，也不是排除这6码。
上述定义用于确保候选与正式核验一致，不代表已证明预测有效。冷热、遗漏和温弱码仅描述历史，不能据此断言下期概率优势，不得声称保证命中或保证提高命中率。

【Python已精确计算的统计特征】：
- 千位当前温弱码候选(遗漏4-8期): {q_weak}
- 百位当前温弱码候选(遗漏4-8期): {b_weak}

请结合真实历史数据审慎核查统计特征和候选约束的一致性；最终回答只输出严格 JSON 与简短理由，不在最终回答输出分析过程：
所有字段必须齐全。q_kill1/b_kill1/he_q3_kill/he_h3_kill均为一个0至9整数，不能为布尔值或小数。
he2_keep必须恰好4个不重复的ASCII数字，且仅能从02468选择4码；he3_keep必须恰好6个不重复的ASCII数字，且仅能从0123456789选择6码。
he2_keep/he3_keep推荐输出字符串并保留前导0；也允许包含完全相同数字的JSON数组。不得补位、重复、默认凑数或输出所有候选。
pos_kills中的q/b/s/g四个数组全部必填，每个数组恰好2个不同的0至9整数。pattern_ds恰好4个单双字符，pattern_dx恰好4个大小字符。
{{
  "q_kill1": 0,
  "b_kill1": 1,
  "pos_kills": {{"q": [1,2], "b": [3,4], "s": [5,6], "g": [7,8]}},
  "he_q3_kill": 2,
  "he_h3_kill": 8,
  "pattern_ds": "单双双双",
  "pattern_dx": "大大大小",
  "he2_keep": "2468",
  "he3_keep": "012359",
  "reason": "40字以内的推理理由"
}}"""

# Existing legacy seals remain authoritative on first deployment. Check these
# before importing or calling any inference client.
try:
    with open(PANEL_PATH, 'r', encoding='utf-8', errors='ignore') as _pf:
        if f'杀码框架 (参考期号: {period})' in _pf.read():
            print(f'Existing legacy framework preserved; ref={period}; no new inference.')
            raise SystemExit(0)
except OSError:
    pass
lock_file = '/root/penta5-runner/r1_sealed_period.txt'
if os.path.exists(lock_file):
    with open(lock_file, 'r', encoding='utf-8') as _lf:
        if _lf.read().strip() == str(period):
            print(f'Existing legacy framework preserved; ref={period}; no new inference.')
            raise SystemExit(0)

print(f'Framework inference starting; ref={period}; publication pending.')
prompt, model_context_audit = prepare_model_request(prompt, snapshot, 'framework', seals,
                                                   data_dir=os.path.dirname(SEALS_PATH))
response_validator = classic_framework_validator(model_context_audit)
import requests
framework_response_metadata = []
framework_validated_attempt = {}

def record_framework_response_metadata(message):
    # Metadata describes the provider's actual model, never its response text.
    # A missing diagnostic cannot alter the main prediction or repeat a call.
    if isinstance(message, str) and message.startswith('AI_RESPONSE_META '):
        try:
            entry = json.loads(message.split(' ', 1)[1])
            if isinstance(entry, dict):
                framework_response_metadata.append(entry)
                del framework_response_metadata[:-3]
        except (ValueError, TypeError):
            pass
    elif isinstance(message, str):
        accepted = re.fullmatch(r'Framework response validated; ref=([0-9]{11}); attempt=([1-3]); publication pending[.]', message)
        if accepted:
            framework_validated_attempt.update(ref=accepted.group(1), attempt=int(accepted.group(2)))
    print(message)

def accepted_framework_response_metadata(reference):
    if framework_validated_attempt.get('ref') != reference:
        return None
    attempt = framework_validated_attempt.get('attempt')
    entry = next((entry for entry in reversed(framework_response_metadata)
                 if entry.get('ref') == reference and type(entry.get('attempt')) is int
                 and entry['attempt'] == attempt), None)
    report = validation_report(response_validator)
    return dict(entry, strategy_validation=report) if entry is not None and report else entry

with reserve_request('framework', period, post=requests.post,
                     input_sha256=model_context_audit['input_archive_sha256'],
                     root=os.path.dirname(os.path.dirname(SEALS_PATH)), clock=beijing_now) as guarded_post:
    API_KEY = os.getenv("DEEPSEEK_API_KEY") or get_secret("PENTA5_FRAMEWORK_API_KEY")
    if API_KEY.startswith("sk-sk-"):
        API_KEY = API_KEY[3:]
    r, response_generated_at = request_framework_response(prompt, period,
        post=guarded_post, api_key=API_KEY,
        model=FRAMEWORK_MODEL,
        load_snapshot=checked_source_snapshot, clock=beijing_now, safe_error=safe_error,
        sleep=time.sleep, monotonic=time.monotonic, log=record_framework_response_metadata,
        operation_window=operation_window, validator=response_validator,
        retry_instruction=(FRAMEWORK_RETRY_INSTRUCTION if hasattr(response_validator, 'strategy_validation') else None))

# 兜底默认值
def get_keep_digits(kill_list):
    all_d = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"]
    kills = set(int(x) for x in kill_list)
    return "".join(d for d in all_d if int(d) not in kills)

q_kill1 = int(r.get("q_kill1", q_weak[0] if q_weak else 3))
b_kill1 = int(r.get("b_kill1", b_weak[0] if b_weak else 4))
q_keep = get_keep_digits([q_kill1])
b_keep = get_keep_digits([b_kill1])
s_keep = "1234567890"
g_keep = "1234567890"

he_q3_kill = int(r.get("he_q3_kill", 2)) % 10
he_h3_kill = int(r.get("he_h3_kill", 8)) % 10
he_q3_keep = get_keep_digits([he_q3_kill])
he_h3_keep = get_keep_digits([he_h3_kill])

p_ds = str(r.get("pattern_ds", "单双双双"))
p_dx = str(r.get("pattern_dx", "大大大小"))
he2_keep = r['he2_keep']
he3_keep = r['he3_keep']
reason = str(r.get("reason", "结合量化遗漏走势与冷热平衡推导"))

pk = r.get("pos_kills", {})
q_pk = "".join(str(x) for x in pk.get("q", []))
b_pk = "".join(str(x) for x in pk.get("b", []))
s_pk = "".join(str(x) for x in pk.get("s", []))
g_pk = "".join(str(x) for x in pk.get("g", []))

# 组装标准攻击语
attack_str = f"千{q_keep}百{b_keep}十{s_keep}个{g_keep}四定千百十合{he_q3_keep}百十个合{he_h3_keep}除{p_ds}除{p_dx}二数合{he2_keep}三数合{he3_keep}各"

print("\n--- DeepSeek 杀码框架推导完成 ---")
print(f"• 定位排除: 千杀{q_kill1} 百杀{b_kill1}")
print(f"• 合尾排除: 千百十合杀{he_q3_kill}, 百十个合杀{he_h3_kill}")
print(f"• 最终攻击语:\n{attack_str}\n")

# 5. 生成卡片并注入 panel.html
calc_time = response_generated_at.strftime("%H:%M:%S")

framework_card = f"""<!-- R1_FRAMEWORK_START -->
<div style="margin: 20px auto; max-width: 1000px; padding: 16px 22px; background: #ffffff; border: 2px solid #0284c7; border-radius: 8px; font-family: sans-serif; box-shadow: 0 2px 10px rgba(0,0,0,0.06);">
    <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #f0f0f0; padding-bottom: 10px; margin-bottom: 12px;">
        <div>
            <span style="font-size: 16px; font-weight: bold; color: #0f172a;">🎯 DeepSeek 杀码框架 (参考期号: {period})</span>
            <div style="font-size: 12px; color: #64748b; margin-top: 4px;">2个定位排除 + 2个合尾排除 + 2个形态排除 + 二数合保留4码 + 三数合保留6码；开奖前封存</div>
        </div>
        <span style="font-size: 12px; color: #888;">AI 计算时间: {calc_time}（北京时间）</span>
    </div>

    <div style="font-size: 12px; color: #64748b; margin-bottom: 8px;">推理模式：高强度思考（high）</div>
    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 10px 24px; font-size: 14px; color: #334155; margin-bottom: 14px;">
        <div>1. <b>球一（千位）排除:</b> <span style="display:inline-block; border: 1px solid #cbd5e1; border-radius: 12px; padding: 1px 12px; font-weight: bold; color: #0284c7; background: #f8fafc;">{q_kill1}</span></div>
        <div>2. <b>球二（百位）排除:</b> <span style="display:inline-block; border: 1px solid #cbd5e1; border-radius: 12px; padding: 1px 12px; font-weight: bold; color: #0284c7; background: #f8fafc;">{b_kill1}</span></div>
        <div>3. <b>百十个合尾排除:</b> <span style="display:inline-block; border: 1px solid #fecaca; border-radius: 12px; padding: 1px 12px; font-weight: bold; color: #dc2626; background: #fef2f2;">{he_h3_kill}</span></div>
        <div>4. <b>千百十合尾排除:</b> <span style="display:inline-block; border: 1px solid #fecaca; border-radius: 12px; padding: 1px 12px; font-weight: bold; color: #dc2626; background: #fef2f2;">{he_q3_kill}</span></div>
        <div>5. <b>单双反向形态:</b> <span style="display:inline-block; border: 1px solid #bbf7d0; border-radius: 12px; padding: 1px 12px; font-weight: bold; color: #16a34a; background: #f0fdf4;">{escape(str(p_ds))}</span></div>
        <div>6. <b>大小反向形态:</b> <span style="display:inline-block; border: 1px solid #bbf7d0; border-radius: 12px; padding: 1px 12px; font-weight: bold; color: #16a34a; background: #f0fdf4;">{escape(str(p_dx))}</span></div>
        <div>7. <b>二数合保留4码（02468排1码）:</b> <span style="font-weight: bold; color: #2563eb;">{he2_keep}</span></div>
        <div>8. <b>三数合尾保留6码:</b> <span style="font-weight: bold; color: #2563eb;">{he3_keep}</span></div>
    </div>

    <div style="font-size: 13px; color: #475569; margin-bottom: 12px; padding: 6px 12px; background: #f8fafc; border-radius: 6px;">
        <b>四位各排2码参考:</b> 千 排除 <b style="color:#d9363e;">{escape(str(q_pk))}</b>&nbsp;|&nbsp; 百 排除 <b style="color:#d9363e;">{escape(str(b_pk))}</b>&nbsp;|&nbsp; 十 排除 <b style="color:#d9363e;">{escape(str(s_pk))}</b>&nbsp;|&nbsp; 个 排除 <b style="color:#d9363e;">{escape(str(g_pk))}</b>
        <div style="font-size: 12px; margin-top: 4px; color: #64748b;">此行仅供单独参考，不加入本条攻击语及上述8项命中统计。</div>
    </div>

    <div style="margin-bottom: 10px;">
        <div style="font-size: 13px; font-weight: bold; color: #334155; margin-bottom: 6px;">杀码框架攻击语:</div>
        <div id="r1_framework_attack_text" style="word-break: break-all; font-family: monospace; background: #f1f5f9; padding: 10px 14px; border-radius: 6px; font-size: 13px; color: #0f172a; border: 1px solid #e2e8f0; user-select: all;">
            {escape(str(attack_str))}
        </div>
    </div>

    <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 10px;">
        <span style="font-size: 12px; color: #64748b;"><b>推理理由:</b> {escape(str(reason))}</span>
        <button type="button" id="r1_copy_btn" onclick="copyR1FrameworkAttack()" style="border: 0; border-radius: 7px; padding: 7px 16px; background: #334155; color: #ffffff; font-size: 13px; font-weight: bold; cursor: pointer; transition: all 0.2s;">复制攻击语</button>
    </div>
</div>

<script>
function copyR1FrameworkAttack() {{
    const textElem = document.getElementById("r1_framework_attack_text");
    if (!textElem) return;
    const txt = textElem.innerText.trim();
    navigator.clipboard.writeText(txt).then(function() {{
        const btn = document.getElementById("r1_copy_btn");
        if (btn) {{
            const oldText = btn.innerText;
            btn.innerText = "✅ 复制成功";
            btn.style.background = "#16a34a";
            setTimeout(function() {{
                btn.innerText = oldText;
                btn.style.background = "#334155";
            }}, 1500);
        }}
    }}).catch(function() {{
        alert("复制失败，请手动长按或选择文本复制！");
    }});
}}
</script>
<!-- R1_FRAMEWORK_END -->"""

publication_snapshot = checked_source_snapshot(persist=False)
operation_window.require_open()
prepared_seal = store_prepared(seals, SEALS_PATH, publication_snapshot,
               {'ref_period': period, 'response': r, 'model_context_audit': model_context_audit,
                'model_metadata': accepted_framework_response_metadata(period)}, framework_card,
               generated_at=response_generated_at, enqueue=False)
if publish_saved(seals, SEALS_PATH, period, 'R1_FRAMEWORK', checked_source_snapshot,
        operation_window=operation_window):
    print('Framework panel updated; immutable response and publication evidence persisted.')
    print(f'FRAMEWORK_PUBLISHED ref={period} target={prepared_seal["target_period"]}')
else:
    print('Framework response retained without late publication.')
    raise SystemExit(1)

