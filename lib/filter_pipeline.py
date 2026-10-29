import json
import numpy as np
from scipy.stats import chisquare
from ai_call_control import AICallsDisabled, ROOT, require_ai_enabled
from stats_history import beijing_now

def preprocess_and_validate(recent_draws: list, p_threshold: float = 0.05) -> bool:
    """
    前置卡方检验：验证局部数据是否显著偏离均匀分布。
    若 p >= p_threshold，说明符合均匀分布（纯随机噪声），直接跳过。
    """
    flat_numbers = [num for draw in recent_draws for num in draw]
    counts = np.bincount(flat_numbers, minlength=10)
    _, p_value = chisquare(counts)
    return p_value < p_threshold

def call_model_pipeline(recent_draws: list, client, model_name: str = "deepseek-reasoner",
                        *, root=ROOT, clock=beijing_now) -> dict:
    """
    调用封装管线：前置过滤 -> JSON 约束 -> 阈值校验
    """
    try:
        require_ai_enabled(root=root, clock=clock)
    except AICallsDisabled:
        return {"action": "PASS", "reason": "ai-api-stopped", "status": "paused"}
    # 1. 前置统计检验
    if not preprocess_and_validate(recent_draws, p_threshold=0.05):
        return {"action": "PASS", "reason": "chi_square_uniform_noise"}

    # 2. 构造严格格式要求的 Prompt
    prompt = (
        f"根据近期走势形态进行分析: {recent_draws[-20:]}。\n"
        "仅输出纯 JSON 格式，严禁包含任何 Markdown 标记或额外解释。\n"
        "格式规范:\n"
        '{"action": "EXECUTE" | "PASS", "target_pos": [int], "exclude_digits": [int], "confidence_score": float}\n'
        "若无统计显著性把握，action 必须为 PASS，confidence_score 设为 0.0。"
    )

    # Hidden SDK retries could issue another request after a manual stop.
    # Only a client that explicitly disables these retries may use this path.
    configure = getattr(client, 'with_options', None)
    if not callable(configure):
        return {"action": "PASS", "reason": "sdk-retry-control-unavailable"}
    try:
        caller = configure(max_retries=0)
    except Exception:
        return {"action": "PASS", "reason": "sdk-retry-control-unavailable"}
    if type(getattr(caller, 'max_retries', None)) is not int or caller.max_retries != 0:
        return {"action": "PASS", "reason": "sdk-retry-control-unavailable"}
    try:
        require_ai_enabled(root=root, clock=clock)
    except AICallsDisabled:
        return {"action": "PASS", "reason": "ai-api-stopped", "status": "paused"}
    response = caller.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.1
    )
    
    # 3. 解析与严格 Schema 校验
    try:
        raw_text = response.choices[0].message.content.strip()
        data = json.loads(raw_text)
    except Exception:
        return {"action": "PASS", "reason": "json_parse_error"}

    # 4. 置信度硬拦截
    if data.get("confidence_score", 0.0) < 0.90 or data.get("action") != "EXECUTE":
        return {"action": "PASS", "reason": "low_confidence_or_pass"}

    return {
        "action": "EXECUTE",
        "target_pos": data.get("target_pos", []),
        "exclude_digits": data.get("exclude_digits", []),
        "confidence_score": data.get("confidence_score")
    }
