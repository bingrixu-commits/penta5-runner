#!/usr/bin/env python3
"""五维更稳数学排除算法（五星 0-9）。

规则：
1. 极值偏差排除（Z-Score / 卡方）：近 100 期单码频次，|Z| > 2.58 则排除
2. 遗漏极限防守：当前遗漏 >= 历史最大遗漏 * 80%，且近期无回暖迹象
3. 极偏形态过滤：全奇/全偶/全大/全小/全 012 单一路（P < 3.1%）
4. 极限跨度排除：跨度 0 / 1 / 9
5. 极限和值排除：和值 < 8 或 > 37
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

DigitDraw = Sequence[int]
DrawLike = Union[DigitDraw, Dict[str, Any], str]

Z_SCORE_THRESHOLD = 2.58
RECENT_WINDOW = 100
MISS_RATIO = 0.80
WARM_LOOKBACK = 10
WARM_MAX_HITS = 1  # 近 WARM_LOOKBACK 期内出现次数 <= 1 视为无回暖
BALL_COUNT = 5
DIGIT_COUNT = 10
EXCLUDE_SPANS = (0, 1, 9)
SUM_MIN_EXCLUSIVE = 8  # 排除 < 8
SUM_MAX_EXCLUSIVE = 37  # 排除 > 37
EXTREME_SHAPE_PROB_CEILING = 0.031


def _to_int_digit(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if 0 <= n <= 9:
        return n
    return None


def normalize_draw(item: DrawLike) -> List[int]:
    """将一行开奖规范为长度 5 的 int 列表。"""
    if isinstance(item, dict):
        if "numbers" in item and isinstance(item["numbers"], (list, tuple)):
            raw = list(item["numbers"])
        else:
            raw = [
                item.get("n1", item.get("ball1", item.get("wan"))),
                item.get("n2", item.get("ball2", item.get("qian"))),
                item.get("n3", item.get("ball3", item.get("bai"))),
                item.get("n4", item.get("ball4", item.get("shi"))),
                item.get("n5", item.get("ball5", item.get("ge"))),
            ]
    elif isinstance(item, str):
        text = item.strip().replace(" ", "").replace(",", "").replace("-", "")
        raw = list(text)
    else:
        raw = list(item)

    digits = [_to_int_digit(x) for x in raw[:BALL_COUNT]]
    if len(digits) < BALL_COUNT or any(d is None for d in digits):
        raise ValueError(f"invalid draw: {item!r}")
    return [int(d) for d in digits]  # type: ignore[misc]


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
    """返回 (current_miss, historical_max_miss)。遗漏按“期”计：任一位出现即打断。"""
    asc = list(reversed(draws_newest_first))
    cur = [0] * DIGIT_COUNT
    max_miss = [0] * DIGIT_COUNT
    seen = [False] * DIGIT_COUNT

    for draw in asc:
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
    """规则3：极偏形态（静态低概率集合）。"""
    p_binary = _shape_prob_all_same_binary()
    p_road = _shape_prob_single_road()
    shapes = [
        {"code": "全奇", "label": "奇偶比5:0", "prob": round(p_binary, 6)},
        {"code": "全偶", "label": "奇偶比0:5", "prob": round(p_binary, 6)},
        {"code": "全大", "label": "大小比5:0", "prob": round(p_binary, 6)},
        {"code": "全小", "label": "大小比0:5", "prob": round(p_binary, 6)},
        {"code": "全0路", "label": "单一路占满(0路)", "prob": round(p_road, 6)},
        {"code": "全1路", "label": "单一路占满(1路)", "prob": round(p_road, 6)},
        {"code": "全2路", "label": "单一路占满(2路)", "prob": round(p_road, 6)},
    ]
    # 用户指定形态一律纳入；概率说明保留
    return {
        "rule": "extreme_shape_filter",
        "prob_ceiling": EXTREME_SHAPE_PROB_CEILING,
        "excluded_shapes": [s["code"] for s in shapes],
        "excluded_shape_labels": [s["label"] for s in shapes],
        "details": shapes,
    }


def extreme_span_filters() -> Dict[str, Any]:
    """规则4：极限跨度。"""
    return {
        "rule": "extreme_span_exclude",
        "excluded_spans": list(EXCLUDE_SPANS),
    }


def extreme_sum_filters() -> Dict[str, Any]:
    """规则5：极限和值。"""
    return {
        "rule": "extreme_sum_exclude",
        "excluded_sum_ranges": [f"<{SUM_MIN_EXCLUSIVE}", f">{SUM_MAX_EXCLUSIVE}"],
        "sum_lt": SUM_MIN_EXCLUSIVE,
        "sum_gt": SUM_MAX_EXCLUSIVE,
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


def matches_excluded_combo(draw_like: DrawLike, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """判断一注是否命中本期静态/动态排除规则（用于回测）。"""
    draw = normalize_draw(draw_like)
    excluded_digits = set((result or {}).get("exclude_digits") or [])
    hit_digits = sorted({d for d in draw if d in excluded_digits})
    shapes = draw_shapes(draw)
    span = draw_span(draw)
    total = draw_sum(draw)
    shape_hit = [s for s in shapes if s in ((result or {}).get("exclude_shapes") or extreme_shape_filters()["excluded_shapes"])]
    span_hit = span in EXCLUDE_SPANS
    sum_hit = total < SUM_MIN_EXCLUSIVE or total > SUM_MAX_EXCLUSIVE
    return {
        "draw": draw,
        "hit_digits": hit_digits,
        "hit_shapes": shape_hit,
        "span": span,
        "span_hit": span_hit,
        "sum": total,
        "sum_hit": sum_hit,
        "excluded": bool(hit_digits or shape_hit or span_hit or sum_hit),
    }


def run_stable_filter(
    history: Iterable[DrawLike],
    newest_first: bool = True,
    latest_period: Optional[str] = None,
) -> Dict[str, Any]:
    """执行完整五维过滤，返回可直接给框架消费的结构。"""
    draws = normalize_history(history, newest_first=newest_first)
    if not draws:
        raise ValueError("history is empty")

    rule1 = extreme_deviation_exclude(draws)
    rule2 = miss_limit_defense_exclude(draws)
    rule3 = extreme_shape_filters()
    rule4 = extreme_span_filters()
    rule5 = extreme_sum_filters()

    exclude_digits = sorted(set(rule1["excluded_digits"]) | set(rule2["excluded_digits"]))
    exclude_shapes = list(rule3["excluded_shapes"])
    exclude_spans = list(rule4["excluded_spans"])
    exclude_sum_ranges = list(rule5["excluded_sum_ranges"])

    return {
        "version": "stable-math-filter-v1",
        "algorithm": "five-dim-stable-exclude",
        "ball_count": BALL_COUNT,
        "history_count": len(draws),
        "latest_period": latest_period,
        "latest_draw": list(draws[0]),
        "exclude_digits": exclude_digits,
        "exclude_shapes": exclude_shapes,
        "exclude_spans": exclude_spans,
        "exclude_sum_ranges": exclude_sum_ranges,
        "summary": {
            "建议排除单码": exclude_digits if exclude_digits else ["无"],
            "建议排除形态": exclude_shapes,
            "建议排除跨度": exclude_spans,
            "建议排除和值": exclude_sum_ranges,
        },
        "rules": {
            "extreme_deviation": rule1,
            "miss_limit_defense": rule2,
            "extreme_shape": rule3,
            "extreme_span": rule4,
            "extreme_sum": rule5,
        },
        # 框架友好字段：可直接映射到 extra / 面板
        "framework": {
            "digitAvoid": exclude_digits,
            "shapeAvoid": exclude_shapes,
            "spanAvoid": exclude_spans,
            "sumRangeAvoid": exclude_sum_ranges,
            "parityRatioAvoid": ["5单0双", "0单5双"],
            "sizeRatioAvoid": ["5大0小", "0大5小"],
            "roadAvoid": ["全0路", "全1路", "全2路"],
        },
    }


def format_console_report(result: Dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    digits = summary.get("建议排除单码") or ["无"]
    shapes = summary.get("建议排除形态") or []
    spans = summary.get("建议排除跨度") or []
    sums = summary.get("建议排除和值") or []
    lines = [
        f"- 建议排除单码：{digits}",
        f"- 建议排除形态：{shapes}",
        f"- 建议排除跨度：{spans}",
        f"- 建议排除和值：{sums}",
    ]
    return "\n".join(lines)
