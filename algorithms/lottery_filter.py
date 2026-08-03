#!/usr/bin/env python3
"""更稳数学排除算法（与主模型 extra 结构口径一致：球一~球四，球五不参与）。

在原五维（单码 Z/遗漏、形态、跨度、和值）基础上，对齐主模型结构排除：
- 重号：三重 / 四重（及伞形「重号」匹配）
- 连号：三连 / 四连（及伞形「连号」匹配）
- 奇偶比：4单0双 / 0单4双
- 大小比：4大0小 / 0大4小
- 和值区间：0-5 / 31-36（与 <6 / >30 同义，便于框架消费）
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

DigitDraw = Sequence[int]
DrawLike = Union[DigitDraw, Dict[str, Any], str]

Z_SCORE_THRESHOLD = 2.58
RECENT_WINDOW = 100
MISS_RATIO = 0.80
WARM_LOOKBACK = 10
WARM_MAX_HITS = 1  # 近 WARM_LOOKBACK 期内出现次数 <= 1 视为无回暖
# 与主模型 / converter-history / calculateMetrics 一致：只用球一~球四
BALL_COUNT = 4
DIGIT_COUNT = 10
EXCLUDE_SPANS = (0, 1, 9)
SUM_MIN_EXCLUSIVE = 6  # 排除 < 6（四码和值）
SUM_MAX_EXCLUSIVE = 30  # 排除 > 30（四码和值上限 36）
EXCLUDE_SUM_RANGES = ("0-5", "31-36")  # 与主模型 sumRangeAvoid 同口径
EXTREME_SHAPE_PROB_CEILING = 0.031
# 与主模型 repeatAvoid / consecutiveAvoid / parityRatioAvoid / sizeRatioAvoid 候选集对齐的「更稳」静态极值
EXCLUDE_REPEATS = ("三重", "四重")
EXCLUDE_CONSECUTIVES = ("三连", "四连")
EXCLUDE_PARITY_RATIOS = ("4单0双", "0单4双")
EXCLUDE_SIZE_RATIOS = ("4大0小", "0大4小")


def _to_int_digit(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if 0 <= n <= 9:
        return n
    return None


def _digits_from_text(text: str) -> List[str]:
    """只保留 0-9，过滤空格/标点/字母等特殊字符。"""
    return re.findall(r"[0-9]", str(text or ""))


def normalize_draw(item: DrawLike) -> List[int]:
    """将一行开奖规范为长度 4 的 int 列表（球一~球四；多给的球五忽略）。"""
    if isinstance(item, dict):
        if "numbers" in item and isinstance(item["numbers"], (list, tuple)):
            raw = list(item["numbers"])
        else:
            raw = [
                item.get("n1", item.get("ball1", item.get("wan"))),
                item.get("n2", item.get("ball2", item.get("qian"))),
                item.get("n3", item.get("ball3", item.get("bai"))),
                item.get("n4", item.get("ball4", item.get("shi"))),
            ]
    elif isinstance(item, str):
        raw = _digits_from_text(item)
    else:
        raw = list(item)

    # 列表元素也可能带特殊字符（如 "1," / " 2" / "A3"）
    cleaned: List[Optional[int]] = []
    for x in raw:
        if isinstance(x, str) and not x.strip().isdigit():
            parts = _digits_from_text(x)
            if len(parts) == 1:
                cleaned.append(_to_int_digit(parts[0]))
            elif not parts:
                cleaned.append(None)
            else:
                # 单格多数字：拆开继续吃，避免整行作废
                for p in parts:
                    cleaned.append(_to_int_digit(p))
        else:
            cleaned.append(_to_int_digit(x))

    digits = [d for d in cleaned if d is not None][:BALL_COUNT]
    if len(digits) < BALL_COUNT:
        raise ValueError(f"invalid draw: {item!r}")
    return [int(d) for d in digits]


def normalize_history(history: Iterable[DrawLike], newest_first: bool = True) -> List[List[int]]:
    """规范化历史。默认 newest_first=True（与框架 history[0]=最新 一致）。"""
    draws = [normalize_draw(item) for item in history]
    if not newest_first:
        draws = list(reversed(draws))
    return draws


def _freq_window(draws_newest_first: Sequence[Sequence[int]], window: int) -> List[int]:
    freq = [0] * DIGIT_COUNT
    for draw in draws_newest_first[:window]:
        for d in draw:
            freq[int(d)] += 1
    return freq


def _theoretical_std(trials: int, p: float = 0.1) -> float:
    return math.sqrt(max(trials * p * (1.0 - p), 1e-12))


def extreme_deviation_exclude(
    draws_newest_first: Sequence[Sequence[int]],
    window: int = RECENT_WINDOW,
    z_threshold: float = Z_SCORE_THRESHOLD,
) -> Dict[str, Any]:
    """规则1：近 window 期频次 Z-Score / 卡方极端热冷号。"""
    used = min(window, len(draws_newest_first))
    trials = used * BALL_COUNT
    expected = trials / DIGIT_COUNT
    std = _theoretical_std(trials)
    freq = _freq_window(draws_newest_first, used)

    details = []
    excluded = []
    for digit in range(DIGIT_COUNT):
        obs = freq[digit]
        z = (obs - expected) / std
        chi2 = ((obs - expected) ** 2) / expected if expected > 0 else 0.0
        hit = abs(z) > z_threshold
        row = {
            "digit": digit,
            "freq": obs,
            "expected": round(expected, 4),
            "std": round(std, 6),
            "z_score": round(z, 6),
            "chi_square": round(chi2, 6),
            "extreme": hit,
            "side": "hot" if z > z_threshold else ("cold" if z < -z_threshold else "normal"),
        }
        details.append(row)
        if hit:
            excluded.append(digit)

    return {
        "rule": "extreme_deviation_zscore_chi2",
        "window": used,
        "trials": trials,
        "expected": round(expected, 4),
        "std": round(std, 6),
        "z_threshold": z_threshold,
        "chi2_threshold": round(z_threshold ** 2, 6),
        "excluded_digits": excluded,
        "details": details,
    }


def _miss_stats(draws_newest_first: Sequence[Sequence[int]]) -> Tuple[List[int], List[int]]:
    """返回 (current_miss, historical_max_miss)。遗漏按“期”计：任一位出现即打断。

    不用 list(reversed(...)) 复制全量：从旧到新用反向下标遍历，降低长历史峰值内存。
    """
    n = len(draws_newest_first)
    cur = [0] * DIGIT_COUNT
    max_miss = [0] * DIGIT_COUNT
    seen = [False] * DIGIT_COUNT

    for i in range(n - 1, -1, -1):
        draw = draws_newest_first[i]
        present = [False] * DIGIT_COUNT
        for d in draw:
            present[int(d)] = True
        for digit in range(DIGIT_COUNT):
            if present[digit]:
                if seen[digit]:
                    max_miss[digit] = max(max_miss[digit], cur[digit])
                cur[digit] = 0
                seen[digit] = True
            elif seen[digit]:
                cur[digit] += 1

    # 进行中的当前遗漏也参与历史最大
    for digit in range(DIGIT_COUNT):
        if seen[digit]:
            max_miss[digit] = max(max_miss[digit], cur[digit])
    return cur, max_miss


def _recent_hit_periods(draws_newest_first: Sequence[Sequence[int]], digit: int, lookback: int) -> int:
    hits = 0
    for draw in draws_newest_first[:lookback]:
        if digit in draw:
            hits += 1
    return hits


def miss_limit_defense_exclude(
    draws_newest_first: Sequence[Sequence[int]],
    miss_ratio: float = MISS_RATIO,
    warm_lookback: int = WARM_LOOKBACK,
    warm_max_hits: int = WARM_MAX_HITS,
) -> Dict[str, Any]:
    """规则2：遗漏极限防守 + 近期无回暖。"""
    cur_miss, max_miss = _miss_stats(draws_newest_first)
    details = []
    excluded = []
    for digit in range(DIGIT_COUNT):
        threshold = max_miss[digit] * miss_ratio
        recent_hits = _recent_hit_periods(draws_newest_first, digit, warm_lookback)
        no_warm = recent_hits <= warm_max_hits
        at_limit = max_miss[digit] > 0 and cur_miss[digit] >= threshold
        hit = at_limit and no_warm
        details.append(
            {
                "digit": digit,
                "current_miss": cur_miss[digit],
                "max_miss": max_miss[digit],
                "threshold": round(threshold, 4),
                "recent_hits": recent_hits,
                "no_warm_sign": no_warm,
                "exclude": hit,
            }
        )
        if hit:
            excluded.append(digit)
    return {
        "rule": "miss_limit_defense",
        "miss_ratio": miss_ratio,
        "warm_lookback": warm_lookback,
        "warm_max_hits": warm_max_hits,
        "excluded_digits": excluded,
        "details": details,
    }


def _shape_prob_all_same_binary() -> float:
    return 0.5 ** BALL_COUNT


def _shape_prob_single_road() -> float:
    return (1.0 / 3.0) ** BALL_COUNT


def extreme_shape_filters() -> Dict[str, Any]:
    """规则3：极偏形态（静态低概率集合；标签与主模型奇偶/大小比对齐）。"""
    p_binary = _shape_prob_all_same_binary()
    p_road = _shape_prob_single_road()
    shapes = [
        {"code": "全奇", "label": "4单0双", "prob": round(p_binary, 6)},
        {"code": "全偶", "label": "0单4双", "prob": round(p_binary, 6)},
        {"code": "全大", "label": "4大0小", "prob": round(p_binary, 6)},
        {"code": "全小", "label": "0大4小", "prob": round(p_binary, 6)},
        {"code": "全0路", "label": "单一路占满(0路)", "prob": round(p_road, 6)},
        {"code": "全1路", "label": "单一路占满(1路)", "prob": round(p_road, 6)},
        {"code": "全2路", "label": "单一路占满(2路)", "prob": round(p_road, 6)},
    ]
    return {
        "rule": "extreme_shape_filter",
        "prob_ceiling": EXTREME_SHAPE_PROB_CEILING,
        "excluded_shapes": [s["code"] for s in shapes],
        "excluded_shape_labels": [s["label"] for s in shapes],
        "details": shapes,
    }


def extreme_span_filters() -> Dict[str, Any]:
    """规则4：极限跨度（与主模型 spanAvoid 候选中的极值对齐）。"""
    return {
        "rule": "extreme_span_exclude",
        "excluded_spans": list(EXCLUDE_SPANS),
    }


def extreme_sum_filters() -> Dict[str, Any]:
    """规则5：极限和值（四码 0~36；同时输出主模型 sumRangeAvoid 口径）。"""
    return {
        "rule": "extreme_sum_exclude",
        "excluded_sum_ranges": [f"<{SUM_MIN_EXCLUSIVE}", f">{SUM_MAX_EXCLUSIVE}", *EXCLUDE_SUM_RANGES],
        "sum_lt": SUM_MIN_EXCLUSIVE,
        "sum_gt": SUM_MAX_EXCLUSIVE,
        "sum_range_avoid": list(EXCLUDE_SUM_RANGES),
    }


def extreme_repeat_filters() -> Dict[str, Any]:
    """规则6：极限重号（与主模型 repeatAvoid 候选对齐）。"""
    return {
        "rule": "extreme_repeat_exclude",
        "excluded_repeats": list(EXCLUDE_REPEATS),
        "umbrella": "重号",
        "note": "命中三重/四重，或伞形「重号」均视为结构排除",
    }


def extreme_consecutive_filters() -> Dict[str, Any]:
    """规则7：极限连号（与主模型 consecutiveAvoid 候选对齐）。"""
    return {
        "rule": "extreme_consecutive_exclude",
        "excluded_consecutives": list(EXCLUDE_CONSECUTIVES),
        "umbrella": "连号",
        "note": "命中三连/四连，或伞形「连号」均视为结构排除",
    }


def extreme_parity_ratio_filters() -> Dict[str, Any]:
    """规则8：极限奇偶比（主模型 parityRatioAvoid 标签）。"""
    return {
        "rule": "extreme_parity_ratio_exclude",
        "excluded_parity_ratios": list(EXCLUDE_PARITY_RATIOS),
    }


def extreme_size_ratio_filters() -> Dict[str, Any]:
    """规则9：极限大小比（主模型 sizeRatioAvoid 标签）。"""
    return {
        "rule": "extreme_size_ratio_exclude",
        "excluded_size_ratios": list(EXCLUDE_SIZE_RATIOS),
    }


def draw_span(draw: Sequence[int]) -> int:
    return max(draw) - min(draw)


def draw_sum(draw: Sequence[int]) -> int:
    return int(sum(draw))


def draw_shapes(draw: Sequence[int]) -> List[str]:
    odd = sum(1 for d in draw if d % 2 == 1)
    big = sum(1 for d in draw if d >= 5)
    roads = {d % 3 for d in draw}
    shapes = []
    if odd == BALL_COUNT:
        shapes.append("全奇")
    if odd == 0:
        shapes.append("全偶")
    if big == BALL_COUNT:
        shapes.append("全大")
    if big == 0:
        shapes.append("全小")
    if len(roads) == 1:
        shapes.append(f"全{next(iter(roads))}路")
    return shapes


def draw_parity_ratio(draw: Sequence[int]) -> str:
    odd = sum(1 for d in draw if int(d) % 2 == 1)
    return f"{odd}单{BALL_COUNT - odd}双"


def draw_size_ratio(draw: Sequence[int]) -> str:
    big = sum(1 for d in draw if int(d) >= 5)
    return f"{big}大{BALL_COUNT - big}小"


def draw_repeat_shape(draw: Sequence[int]) -> str:
    """与主模型 firstFourRepeatShape 一致。"""
    counts: Dict[int, int] = {}
    for d in draw:
        key = int(d)
        counts[key] = counts.get(key, 0) + 1
    values = sorted(counts.values(), reverse=True)
    if not values:
        return "无重"
    if values[0] >= 4:
        return "四重"
    if values[0] >= 3:
        return "三重"
    pair_count = sum(1 for c in values if c == 2)
    if pair_count >= 2:
        return "双重"
    if pair_count == 1:
        return "一对"
    return "无重"


def draw_max_consecutive_run(draw: Sequence[int]) -> int:
    """与主模型 firstFourMaxConsecutiveRun 一致（去重后最长连续段）。"""
    sorted_unique = sorted({int(d) for d in draw})
    if not sorted_unique:
        return 0
    best = 1
    current = 1
    for index in range(1, len(sorted_unique)):
        if sorted_unique[index] == sorted_unique[index - 1] + 1:
            current += 1
        else:
            current = 1
        if current > best:
            best = current
    return best


def draw_consecutive_shape(draw: Sequence[int]) -> str:
    run = draw_max_consecutive_run(draw)
    if run >= 4:
        return "四连"
    if run >= 3:
        return "三连"
    if run >= 2:
        return "二连"
    return "无连"


def _repeat_hit(shape: str, excluded: Sequence[str]) -> bool:
    if shape in excluded:
        return True
    if "重号" in excluded and shape not in {"", "无重"}:
        return True
    return False


def _consecutive_hit(shape: str, excluded: Sequence[str]) -> bool:
    if shape in excluded:
        return True
    if "连号" in excluded and shape not in {"", "无连"}:
        return True
    return False


# ---------------------------------------------------------------------------
# 软筛选 / 风险评估（在硬排除列表之上叠加；不破坏 run_stable_filter 契约）
# ---------------------------------------------------------------------------
# 低风险 0~1 / 中风险 2 / 高风险 >=3；安全保留默认 25%（夹在 20%~30%）
RISK_LOW_MAX = 1
RISK_MID = 2
RISK_HIGH_MIN = 3
SAFE_RETENTION_MIN = 0.20
SAFE_RETENTION_MAX = 0.30
SAFE_RETENTION_DEFAULT = 0.25
RISK_LEVEL_LABELS = {
    "low": "低风险/强烈推荐",
    "mid": "中风险/观察区",
    "high": "高风险/建议剔除",
}
POSITION_KEYS = ("wan", "qian", "bai", "shi")
POSITION_LABELS = {"wan": "千位", "qian": "百位", "bai": "十位", "shi": "个位"}


def risk_level_from_score(risk_score: int) -> str:
    score = int(risk_score or 0)
    if score <= RISK_LOW_MAX:
        return "low"
    if score == RISK_MID:
        return "mid"
    return "high"


def normalize_position_kills(result: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """千/百/十/个 位杀 → 每位至多一位数字（软加分，非硬删）。"""
    out = {k: "" for k in POSITION_KEYS}
    if not isinstance(result, dict):
        return out
    raw = (
        result.get("position_kills")
        or result.get("positionKills")
        or result.get("exclude_positions")
        or ((result.get("framework") or {}).get("positionAvoid") if isinstance(result.get("framework"), dict) else None)
    )
    if not isinstance(raw, dict) and not isinstance(raw, list):
        return out
    for idx, key in enumerate(POSITION_KEYS):
        entry: Any = None
        if isinstance(raw, dict):
            entry = raw.get(key)
            if entry is None:
                entry = raw.get(str(idx))
        elif isinstance(raw, list) and idx < len(raw):
            entry = raw[idx]
        if isinstance(entry, dict):
            entry = (
                entry.get("selectedDigit")
                if entry.get("selectedDigit") is not None
                else (
                    entry.get("recommendDigit")
                    if entry.get("recommendDigit") is not None
                    else (
                        entry.get("main")
                        if entry.get("main") is not None
                        else (entry.get("killDigits") or [None])[0]
                    )
                )
            )
        digits = "".join(ch for ch in str(entry or "") if ch.isdigit())
        out[key] = digits[:1] if digits else ""
    return out


def merge_position_kills_into_result(
    result: Optional[Dict[str, Any]],
    position_kills: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = dict(result or {})
    normalized = normalize_position_kills({"position_kills": position_kills or base.get("position_kills") or base.get("positionKills")})
    base["position_kills"] = normalized
    base["positionKills"] = normalized
    fw = dict(base.get("framework") or {})
    fw["positionAvoid"] = normalized
    base["framework"] = fw
    return base


def matches_excluded_combo(draw_like: DrawLike, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """判断一注是否命中本期静态/动态排除规则（用于回测；口径对齐主模型 extra）。

    兼容说明：
    - 仍返回布尔字段 excluded（任一规则命中即为 True，旧硬杀语义）
    - 额外返回 risk_score / risk_level / triggered_rules，供软筛选消费
      （每命中一类排除维度 Risk Score +1，不在此处删除组合）
    - 位杀（千/百/十/个）每位命中另 +1（positionAvoid.*），不硬删
    """
    draw = normalize_draw(draw_like)
    res = result or {}
    excluded_digits = set(res.get("exclude_digits") or [])
    hit_digits = sorted({d for d in draw if d in excluded_digits})
    shapes = draw_shapes(draw)
    span = draw_span(draw)
    total = draw_sum(draw)
    parity = draw_parity_ratio(draw)
    size = draw_size_ratio(draw)
    repeat = draw_repeat_shape(draw)
    consecutive = draw_consecutive_shape(draw)

    shape_hit = [s for s in shapes if s in (res.get("exclude_shapes") or extreme_shape_filters()["excluded_shapes"])]
    span_list = res.get("exclude_spans") or list(EXCLUDE_SPANS)
    span_hit = span in set(int(x) for x in span_list)
    sum_hit = total < SUM_MIN_EXCLUSIVE or total > SUM_MAX_EXCLUSIVE
    parity_list = res.get("exclude_parity_ratios") or list(EXCLUDE_PARITY_RATIOS)
    size_list = res.get("exclude_size_ratios") or list(EXCLUDE_SIZE_RATIOS)
    repeat_list = res.get("exclude_repeats") or list(EXCLUDE_REPEATS)
    consecutive_list = res.get("exclude_consecutives") or list(EXCLUDE_CONSECUTIVES)
    parity_hit = parity in set(parity_list)
    size_hit = size in set(size_list)
    repeat_matched = _repeat_hit(repeat, repeat_list)
    consecutive_matched = _consecutive_hit(consecutive, consecutive_list)

    position_kills = normalize_position_kills(res)
    hit_positions: List[str] = []
    for idx, key in enumerate(POSITION_KEYS):
        kill_digit = position_kills.get(key) or ""
        if kill_digit and idx < len(draw) and str(draw[idx]) == kill_digit:
            hit_positions.append(key)

    # 风险账本：一类维度命中记 +1（与硬杀「命中即死」解耦）；位杀每位 +1
    triggered_rules: List[str] = []
    if hit_digits:
        triggered_rules.append("digitAvoid")
    if shape_hit:
        triggered_rules.append("shapeAvoid")
    if span_hit:
        triggered_rules.append("spanAvoid")
    if sum_hit:
        triggered_rules.append("sumRangeAvoid")
    if parity_hit:
        triggered_rules.append("parityRatioAvoid")
    if size_hit:
        triggered_rules.append("sizeRatioAvoid")
    if repeat_matched:
        triggered_rules.append("repeatAvoid")
    if consecutive_matched:
        triggered_rules.append("consecutiveAvoid")
    for key in hit_positions:
        triggered_rules.append(f"positionAvoid.{key}")

    risk_score = len(triggered_rules)
    risk_level = risk_level_from_score(risk_score)

    return {
        "draw": draw,
        "hit_digits": hit_digits,
        "hit_shapes": shape_hit,
        "hit_positions": hit_positions,
        "position_kills": position_kills,
        "span": span,
        "span_hit": span_hit,
        "sum": total,
        "sum_hit": sum_hit,
        "parity_ratio": parity,
        "parity_hit": parity_hit,
        "size_ratio": size,
        "size_hit": size_hit,
        "repeat_shape": repeat,
        "repeat_hit": repeat_matched,
        "consecutive_shape": consecutive,
        "consecutive_hit": consecutive_matched,
        # 旧硬杀语义保留
        "excluded": bool(triggered_rules),
        # 软筛选字段
        "risk_score": risk_score,
        "risk_level": risk_level,
        "risk_label": RISK_LEVEL_LABELS[risk_level],
        "triggered_rules": triggered_rules,
        "passed": risk_level != "high",
        "recommend": risk_level == "low",
        "watch": risk_level == "mid",
        "suggest_drop": risk_level == "high",
    }


def score_combination(
    draw_like: DrawLike,
    result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对单个组合计算 Risk Score（软筛选入口；不删除组合）。"""
    return matches_excluded_combo(draw_like, result=result)


def soft_filter_combinations(
    draws: Sequence[DrawLike],
    result: Optional[Dict[str, Any]] = None,
    *,
    retention_ratio: Optional[float] = None,
    high_risk_threshold: int = RISK_HIGH_MIN,
    keep_mid: bool = True,
) -> Dict[str, Any]:
    """
    批量软筛选：Risk Scoring + Fuzzy Zone + Safe Retention。

    - 每注按命中排除维度累计 risk_score，不直接 filter 掉
    - 低风险(0~1)优先保留；中风险(2)观察区；高风险(>=3)建议剔除
    - 无论规则多严，至少保留前 retention_ratio（默认 25%，夹在 20%~30%）
    """
    if retention_ratio is None:
        ratio = SAFE_RETENTION_DEFAULT
    else:
        try:
            ratio = float(retention_ratio)
        except (TypeError, ValueError):
            ratio = SAFE_RETENTION_DEFAULT
        ratio = max(SAFE_RETENTION_MIN, min(SAFE_RETENTION_MAX, ratio))

    scored: List[Dict[str, Any]] = []
    for item in draws:
        row = score_combination(item, result=result)
        code = "".join(str(d) for d in row["draw"])
        row["original"] = item if isinstance(item, str) else code
        row["normalized"] = code
        scored.append(row)

    scored.sort(key=lambda row: (row["risk_score"], row["normalized"]))
    total = len(scored)
    min_keep = 0 if total == 0 else max(1, int(math.ceil(total * ratio)))

    retained: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    forced_by_retention: List[Dict[str, Any]] = []

    ranked: List[Dict[str, Any]] = []

    for index, row in enumerate(scored):
        annotated = dict(row)
        annotated["rank"] = index + 1
        score = int(annotated["risk_score"])
        level = annotated["risk_level"]

        if score < high_risk_threshold:
            if level == "mid" and not keep_mid:
                if len(retained) < min_keep:
                    annotated["retention_forced"] = True
                    annotated["decision"] = "force_retain"
                    retained.append(annotated)
                    forced_by_retention.append(annotated)
                else:
                    annotated["retention_forced"] = False
                    annotated["decision"] = "drop_mid"
                    dropped.append(annotated)
            else:
                annotated["retention_forced"] = False
                annotated["decision"] = "retain"
                retained.append(annotated)
        else:
            if len(retained) < min_keep:
                annotated["retention_forced"] = True
                annotated["decision"] = "force_retain"
                annotated["passed"] = True
                retained.append(annotated)
                forced_by_retention.append(annotated)
            else:
                annotated["retention_forced"] = False
                annotated["decision"] = "drop_high"
                dropped.append(annotated)

        ranked.append(annotated)

    by_level = {
        "low": [r for r in ranked if r["risk_level"] == "low"],
        "mid": [r for r in ranked if r["risk_level"] == "mid"],
        "high": [r for r in ranked if r["risk_level"] == "high"],
    }

    return {
        "mode": "soft_risk",
        "total": total,
        "retention_ratio": ratio,
        "min_keep": min_keep,
        "high_risk_threshold": high_risk_threshold,
        "keep_mid": keep_mid,
        "fuzzy_zone": {
            "low": f"0~{RISK_LOW_MAX}",
            "mid": str(RISK_MID),
            "high": f">={RISK_HIGH_MIN}",
        },
        "counts": {
            "low": len(by_level["low"]),
            "mid": len(by_level["mid"]),
            "high": len(by_level["high"]),
            "retained": len(retained),
            "dropped": len(dropped),
            "force_retained": len(forced_by_retention),
        },
        "ranked": ranked,
        "retained": retained,
        "dropped": dropped,
        "force_retained": forced_by_retention,
        "by_level": by_level,
    }


def build_full_four_digit_pool() -> List[str]:
    return [f"{i:04d}" for i in range(10000)]


def soft_recommend_from_pool(
    result: Optional[Dict[str, Any]] = None,
    *,
    position_kills: Optional[Dict[str, Any]] = None,
    retention_ratio: Optional[float] = None,
    draws: Optional[Sequence[DrawLike]] = None,
    code_limit: int = 0,
    keep_ranked: bool = False,
) -> Dict[str, Any]:
    """全量（或给定）组合池 → soft_filter_combinations → 风险分升序推荐列表。"""
    enriched = merge_position_kills_into_result(result, position_kills)
    pool: Sequence[DrawLike] = draws if draws is not None else build_full_four_digit_pool()
    batch = soft_filter_combinations(
        pool,
        result=enriched,
        retention_ratio=retention_ratio,
    )
    low_codes = [r["normalized"] for r in batch["by_level"]["low"]]
    mid_codes = [r["normalized"] for r in batch["by_level"]["mid"]]
    recommend_codes = [
        {
            "code": r["normalized"],
            "risk_score": r["risk_score"],
            "risk_level": r["risk_level"],
            "triggered_rules": r["triggered_rules"],
        }
        for r in sorted(batch["retained"], key=lambda row: (row["risk_score"], row["normalized"]))
    ]
    if code_limit and code_limit > 0:
        recommend_codes = recommend_codes[:code_limit]
        low_out = low_codes[:code_limit]
        mid_out = mid_codes[:code_limit]
    else:
        low_out = low_codes
        mid_out = mid_codes

    payload: Dict[str, Any] = {
        "mode": "soft_risk",
        "pool_size": batch["total"],
        "retained_count": batch["counts"]["retained"],
        "dropped_count": batch["counts"]["dropped"],
        "retention_ratio": batch["retention_ratio"],
        "position_kills": normalize_position_kills(enriched),
        "counts": batch["counts"],
        "fuzzy_zone": batch["fuzzy_zone"],
        "recommend_codes": recommend_codes,
        "low_risk_codes": low_out,
        "mid_risk_codes": mid_out,
        "low_risk_total": len(low_codes),
        "mid_risk_total": len(mid_codes),
        "high_risk_total": batch["counts"]["high"],
        "comparison": {
            "before": batch["total"],
            "after": batch["counts"]["retained"],
            "text": f"从 {batch['total']} 注安全缩减至 {batch['counts']['retained']} 注",
        },
    }
    if keep_ranked:
        payload["ranked"] = batch["ranked"]
        payload["retained"] = batch["retained"]
    return payload


def slim_soft_recommend(
    soft_recommend: Optional[Dict[str, Any]],
    *,
    max_low: int = 200,
    max_mid: int = 100,
    max_recommend: int = 300,
) -> Optional[Dict[str, Any]]:
    if not isinstance(soft_recommend, dict):
        return None
    return {
        "mode": "soft_risk",
        "pool_size": soft_recommend.get("pool_size"),
        "retained_count": soft_recommend.get("retained_count"),
        "dropped_count": soft_recommend.get("dropped_count"),
        "retention_ratio": soft_recommend.get("retention_ratio"),
        "position_kills": soft_recommend.get("position_kills"),
        "counts": soft_recommend.get("counts"),
        "fuzzy_zone": soft_recommend.get("fuzzy_zone"),
        "comparison": soft_recommend.get("comparison"),
        "low_risk_total": soft_recommend.get("low_risk_total"),
        "mid_risk_total": soft_recommend.get("mid_risk_total"),
        "high_risk_total": soft_recommend.get("high_risk_total"),
        "low_risk_codes": list(soft_recommend.get("low_risk_codes") or [])[:max_low],
        "mid_risk_codes": list(soft_recommend.get("mid_risk_codes") or [])[:max_mid],
        "recommend_codes": list(soft_recommend.get("recommend_codes") or [])[:max_recommend],
    }


def format_soft_recommend_console(
    soft_recommend: Optional[Dict[str, Any]],
    *,
    print_limit: int = 80,
) -> str:
    if not isinstance(soft_recommend, dict):
        return "[soft-risk] 无软筛选推荐结果"
    limit = max(1, int(print_limit or 80))
    before = soft_recommend.get("pool_size") or ((soft_recommend.get("comparison") or {}).get("before"))
    after = soft_recommend.get("retained_count") or ((soft_recommend.get("comparison") or {}).get("after"))
    pk = soft_recommend.get("position_kills") or {}
    pk_text = " ".join(f"{POSITION_LABELS[k]}={pk.get(k) or '-'}" for k in POSITION_KEYS)

    def _block(title: str, codes: Any, total: Any) -> str:
        lst = list(codes or [])
        shown = lst[:limit]
        total_n = total if total is not None else len(lst)
        head = f"{title}\n  合计 {total_n} 注" + (f"（控制台仅展示前 {limit} 注）" if len(lst) > limit else "")
        body = "  " + (" ".join(shown) if shown else "（无）")
        return f"{head}\n{body}"

    lines = [
        "========== Soft Risk Scoring 选号输出 ==========",
        f"位杀软加分：{pk_text}",
        f"过滤对比：从 {before} 注安全缩减至 {after} 注"
        + (f"（剔除 {soft_recommend.get('dropped_count')}）" if soft_recommend.get("dropped_count") is not None else ""),
        f"分层统计：低风险={soft_recommend.get('low_risk_total')} "
        f"中风险={soft_recommend.get('mid_risk_total')} "
        f"高风险={soft_recommend.get('high_risk_total')}",
        _block("【低风险推荐号码】（0-1分，建议重点关注）", soft_recommend.get("low_risk_codes"), soft_recommend.get("low_risk_total")),
        _block("【中风险观察号码】（2分，保留人工参考）", soft_recommend.get("mid_risk_codes"), soft_recommend.get("mid_risk_total")),
        "================================================",
    ]
    return "\n".join(lines)


def run_stable_filter(
    history: Iterable[DrawLike],
    newest_first: bool = True,
    latest_period: Optional[str] = None,
) -> Dict[str, Any]:
    """执行完整更稳过滤（四码），返回可直接给框架 / 主模型 extra 消费的结构。"""
    draws = normalize_history(history, newest_first=newest_first)
    if not draws:
        raise ValueError("history is empty")

    rule1 = extreme_deviation_exclude(draws)
    rule2 = miss_limit_defense_exclude(draws)
    rule3 = extreme_shape_filters()
    rule4 = extreme_span_filters()
    rule5 = extreme_sum_filters()
    rule6 = extreme_repeat_filters()
    rule7 = extreme_consecutive_filters()
    rule8 = extreme_parity_ratio_filters()
    rule9 = extreme_size_ratio_filters()

    exclude_digits = sorted(set(rule1["excluded_digits"]) | set(rule2["excluded_digits"]))
    exclude_shapes = list(rule3["excluded_shapes"])
    exclude_spans = list(rule4["excluded_spans"])
    exclude_sum_ranges = list(rule5["excluded_sum_ranges"])
    exclude_repeats = list(rule6["excluded_repeats"])
    exclude_consecutives = list(rule7["excluded_consecutives"])
    exclude_parity_ratios = list(rule8["excluded_parity_ratios"])
    exclude_size_ratios = list(rule9["excluded_size_ratios"])

    return {
        "version": "stable-math-filter-v3",
        "algorithm": "model-aligned-stable-exclude-4digit",
        "ball_count": BALL_COUNT,
        "ball5_excluded_from_model": True,
        "history_count": len(draws),
        "latest_period": latest_period,
        "latest_draw": list(draws[0]),
        "exclude_digits": exclude_digits,
        "exclude_shapes": exclude_shapes,
        "exclude_spans": exclude_spans,
        "exclude_sum_ranges": exclude_sum_ranges,
        "exclude_repeats": exclude_repeats,
        "exclude_consecutives": exclude_consecutives,
        "exclude_parity_ratios": exclude_parity_ratios,
        "exclude_size_ratios": exclude_size_ratios,
        "summary": {
            "建议排除单码": exclude_digits if exclude_digits else ["无"],
            "建议排除形态": exclude_shapes,
            "建议排除跨度": exclude_spans,
            "建议排除和值": exclude_sum_ranges,
            "建议排除重号": exclude_repeats,
            "建议排除连号": exclude_consecutives,
            "建议排除奇偶比": exclude_parity_ratios,
            "建议排除大小比": exclude_size_ratios,
        },
        "rules": {
            "extreme_deviation": rule1,
            "miss_limit_defense": rule2,
            "extreme_shape": rule3,
            "extreme_span": rule4,
            "extreme_sum": rule5,
            "extreme_repeat": rule6,
            "extreme_consecutive": rule7,
            "extreme_parity_ratio": rule8,
            "extreme_size_ratio": rule9,
        },
        # 框架友好字段：直接映射主模型 extra.* 键名
        "framework": {
            "digitAvoid": exclude_digits,
            "shapeAvoid": exclude_shapes,
            "spanAvoid": exclude_spans,
            "sumRangeAvoid": list(EXCLUDE_SUM_RANGES),
            "sumAvoidRanges": exclude_sum_ranges,
            "repeatAvoid": exclude_repeats,
            "consecutiveAvoid": exclude_consecutives,
            "parityRatioAvoid": exclude_parity_ratios,
            "sizeRatioAvoid": exclude_size_ratios,
            "roadAvoid": ["全0路", "全1路", "全2路"],
        },
    }


def format_console_report(result: Dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    lines = [
        f"- 建议排除单码：{summary.get('建议排除单码') or ['无']}",
        f"- 建议排除形态：{summary.get('建议排除形态') or []}",
        f"- 建议排除跨度：{summary.get('建议排除跨度') or []}",
        f"- 建议排除和值：{summary.get('建议排除和值') or []}",
        f"- 建议排除重号：{summary.get('建议排除重号') or []}",
        f"- 建议排除连号：{summary.get('建议排除连号') or []}",
        f"- 建议排除奇偶比：{summary.get('建议排除奇偶比') or []}",
        f"- 建议排除大小比：{summary.get('建议排除大小比') or []}",
    ]
    return "\n".join(lines)
