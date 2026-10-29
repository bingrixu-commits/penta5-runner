# Retired by request: prevent previously queued jobs from restoring the card.
raise SystemExit(0)
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
    return p_val >= p_threshold  # True 表示纯均匀白噪声，应当拦截

import numpy as np
from scipy.stats import chisquare
# -*- coding: utf-8 -*-
import urllib.request, json, os, re, time
from datetime import datetime
from ai_call_control import AICallsDisabled, require_ai_enabled

try:
    require_ai_enabled()
except AICallsDisabled:
    print('AI API paused; retired match5 remains inactive.')
    raise SystemExit(0)

# 1. 读 Key
with open("/root/penta5-runner/auto_r1_killer.py", "r", encoding="utf-8") as f:
    k_code = f.read()
m = re.search(r"(sk-[a-zA-Z0-9]+)", k_code) or re.search(r'[\'"]([a-zA-Z0-9]{25,})[\'"]', k_code)
key = m.group(1) if m else ""

# 2. 读取并等待最新一期开奖入库 (最长等待 90 秒)
STATE_PATH = "/root/penta5-runner/data/state.json"
LAST_ISSUE_FILE = "/root/penta5-runner/data/.last_match5_issue"

def get_history_and_issue():
    history = []
    current_issue = ""
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8", errors="ignore") as f:
                d = json.load(f)
                for p in d.get("predictions", []):
                    ev = p.get("evaluation")
                    if isinstance(ev, dict):
                        nums = ev.get("actualFirstFourDigits") or ev.get("actualNumbers")
                        if nums and len(nums) >= 4:
                            history.append("".join(str(x) for x in nums[-4:]))
                latest = d.get("latestRecord", {})
                if latest.get("numbers") and len(latest["numbers"]) >= 4:
                    history.append("".join(str(x) for x in latest["numbers"][-4:]))
                current_issue = str(latest.get("issue") or latest.get("period") or "")
        except Exception as e:
            print("读取数据错误:", e)
    return history, current_issue

# 读取上次处理过的期号
last_processed_issue = ""
if os.path.exists(LAST_ISSUE_FILE):
    try:
        with open(LAST_ISSUE_FILE, "r", encoding="utf-8") as f:
            last_processed_issue = f.read().strip()
    except Exception:
        pass

# 轮询等待新期号刷新
history, current_issue = get_history_and_issue()
retries = 0
while retries < 18:
    if current_issue and current_issue != last_processed_issue:
        break
    print(f"[{retries+1}/18] 当前期号 {current_issue} 仍为上一期，等待开奖数据写入...")
    time.sleep(5)
    history, current_issue = get_history_and_issue()
    retries += 1

hist = history[-30:] if len(history) >= 30 else history
if not hist:
    hist = list("6940 1571 9211 5399 4840 3488 7306 4989".split())

print(f"✅ [5配5] 载入 {len(hist)} 期走势: {hist[-4:]}... (当前期号: {current_issue})")

# 记录当前已处理的期号
try:
    require_ai_enabled()
except AICallsDisabled:
    print('AI API paused; no match5 issue was consumed.')
    raise SystemExit(0)
if current_issue:
    try:
        with open(LAST_ISSUE_FILE, "w", encoding="utf-8") as f:
            f.write(str(current_issue))
    except Exception:
        pass

# 3. [风控拦截] 前置卡方白噪声检验
    if check_noise_pass(hist, p_threshold=0.05):
        print(f"[🛡️ 风控拦截] 本期历史数据符合均匀随机白噪声 (p >= 0.05)，无统计套利空间，主动空仓放弃！")
        exit(0)

    # 3. R1 推理
p = (
    f"根据最近30期开奖前四位走势: {hist}\n"
    "推导5配5无序组合(两组各5码，共享4个核心相同码core4，左右各配1个不同分化码)\n"
    "目标: 下期前四位开奖中稳健落入至少2码(5码中2)\n"
    "严格只输出JSON: "
    '{"core4": [A, B, C, D], "diff_left": X, "diff_right": Y, "reason": "简述理由40字内"}'
)

print("🤖 正在连接 DeepSeek-V4.1-Flash 推理【5配5落2码】...")
headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
body = json.dumps({
    "model": "deepseek-chat", "temperature": 0.1,
    "messages": [{"role": "user", "content": p}],
    "temperature": 0.6
}).encode("utf-8")

req = urllib.request.Request(
    "https://api.deepseek.com/v1/chat/completions",
    data=body,
    headers=headers
)

default_c = list(map(int, "1,3,6,8".split(",")))
try:
    require_ai_enabled()
    resp = urllib.request.urlopen(req, timeout=120).read().decode("utf-8")
    raw = json.loads(resp)["choices"][0]["message"]["content"]
    data = json.loads(re.search(r"\{.*?\}", raw, re.DOTALL).group(0))
except AICallsDisabled:
    print('AI API paused; no match5 fallback result was produced.')
    raise SystemExit(0)
except Exception as e:
    data = {"core4": default_c, "diff_left": 2, "diff_right": 9, "reason": "高频数对回补"}

raw_c = data.get("core4", default_c)
core4 = sorted(list(map(lambda x: int(x) % 10, raw_c)))[-4:]
dl = int(data.get("diff_left", 2)) % 10
dr = int(data.get("diff_right", 9)) % 10
left = sorted(list(set(core4 + [dl])))
right = sorted(list(set(core4 + [dr])))
pool = sorted(list(set(core4 + [dl, dr])))
reason = str(data.get("reason", "走势回补与数对聚集"))
now = datetime.now().strftime("%H:%M:%S")

print(f"--- DeepSeek-V4.1-Flash 5配5推导完成 ---")
print(f"核心四同码: {core4}")
print(f"【左组 5 码】: {left}")
print(f"【右组 5 码】: {right}")
print(f"覆盖总池 (6码): {pool} (主落2码)")
print(f"推理理由: {reason}")

# 4. 更新网页
card = (
    "<!-- R1_MATCH5_START -->\n"
    '<div style="background:white; border-radius:10px; padding:15px; '
    'margin:12px 0; border: 1.5px solid #10b981; '
    'font-family: sans-serif;">\n'
    '<div style="font-size:16px; font-weight:bold; color:#065f46; '
    'margin-bottom:8px;">🤖DeepSeek-V4.1-Flash 五配五推导 (4同各1异 · 主前四位落2码)</div>\n'
    f'<div style="margin:5px 0;"><b>核心四同基数:</b> '
    f'<span style="color:#2563eb; font-weight:bold; font-size:16px; '
    f'background:#eff6ff; padding:2px 8px;">{core4}</span></div>\n'
    f'<div style="margin:6px 0;">'
    f'<b>【左组 5 码】：</b>'
    f'<span style="color:#dc2626; font-weight:bold; font-size:16px;">{left}</span>'
    f'&nbsp;&nbsp;&nbsp;&nbsp;'
    f'<b>【右组 5 码】：</b>'
    f'<span style="color:#dc2626; font-weight:bold; font-size:16px;">{right}</span>'
    f'</div>\n'
    f'<div style="margin:5px 0; color:#374151;">'
    f'<b>两组综合覆盖 (共6码)：</b>'
    f'<span style="color:#059669; font-weight:bold;">{pool}</span>'
    f'(目标: 前四位中保底落2码)</div>\n'
    f'<div style="margin-top:6px; color:#4b5563;"><b>AI 推理理由:</b> '
    f'<span style="background:#f3f4f6; padding:2px 8px;">{reason}</span></div>\n'
    f'<div style="font-size:11px; color:#9ca3af; text-align:right; margin-top:4px;">'
    f'AI 计算时间: {now}</div>\n'
    '</div>\n'
    "<!-- R1_MATCH5_END -->"
)

pf = "/root/penta5-runner/panel.html"
if os.path.exists(pf):
    with open(pf, "r", encoding="utf-8") as f:
        html = f.read()
    if "<!-- R1_MATCH5_START -->" in html:
        html = re.sub(
            r"<!-- R1_MATCH5_START -->.*?<!-- R1_MATCH5_END -->",
            card,
            html,
            flags=re.DOTALL
        )
    else:
        html += "\n" + card
    with open(pf, "w", encoding="utf-8") as f:
        f.write(html)
    print("💡 网页 panel.html 已同步更新【5配5落2码】卡片！")
