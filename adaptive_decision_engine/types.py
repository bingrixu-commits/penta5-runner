"""公共数据契约：输入开奖、输出决策、引擎配置。

本模块是唯一需要被外部调用方依赖的数据结构入口。
位置编号对外使用 1-based（第 1～4 位），与常见四星/前四玩法一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

# 决策动作：排除 / 主动空仓 / 风控熔断暂停
DecisionAction = Literal["EXCLUDE", "SKIP", "HALT"]

# 引擎运行态：正常出手 / 转变后纯观察 / 风控熔断
EngineState = Literal["NORMAL", "OBSERVATION", "FROZEN"]

# 路由算法
BanditAlgo = Literal["ucb", "epsilon_greedy"]

# 融合方式：只切最优专家 / 按权重投票
FusionMode = Literal["switch", "blend"]


@dataclass(frozen=True)
class DrawRecord:
    """单期开奖记录。

    Attributes:
        period: 期号，仅作日志标识，不参与计算。
        digits: 各位置开出的数字，长度必须等于 ``n_positions``。
                下标 0 对应第 1 位。取值范围 [digit_min, digit_max]。
    """

    period: str
    digits: tuple[int, ...]

    def digit_at(self, position_0: int) -> int:
        """按 0-based 位置取号。"""
        return self.digits[position_0]


@dataclass
class ExpertExclusion:
    """单个专家对当期的排除提案。"""

    expert_id: str
    # 1-based 位置 -> 建议排除的数字集合
    excluded: dict[int, set[int]]
    # 专家自评置信度 [0, 1]，供路由与共识评估参考
    self_confidence: float = 0.5
    note: str = ""


@dataclass
class ExclusionDecision:
    """引擎标准化输出。"""

    action: DecisionAction
    period_hint: str
    # 1-based 位置 -> 升序数字列表；SKIP/HALT 时各位置为空
    excluded: dict[int, list[int]]
    confidence: float
    reason: str
    expert_weights: dict[str, float]
    window_size: int
    engine_state: EngineState = "NORMAL"
    meta: dict[str, Any] = field(default_factory=dict)

    def is_active(self) -> bool:
        """是否真正给出排除（非空仓、非熔断）。"""
        return self.action == "EXCLUDE"

    def to_dict(self) -> dict[str, Any]:
        """便于日志 / JSON 序列化。"""
        return {
            "action": self.action,
            "period_hint": self.period_hint,
            "excluded": {str(k): v for k, v in self.excluded.items()},
            "confidence": round(self.confidence, 4),
            "reason": self.reason,
            "expert_weights": {k: round(v, 4) for k, v in self.expert_weights.items()},
            "window_size": self.window_size,
            "engine_state": self.engine_state,
            "meta": self.meta,
        }


@dataclass
class EngineConfig:
    """引擎超参。全部带默认值，可按玩法覆盖。"""

    n_positions: int = 4
    digit_min: int = 0
    digit_max: int = 9

    # 少于此期数只观察、不排除
    min_history: int = 12

    # 自适应窗口边界（专家看盘用）
    window_min: int = 8
    window_max: int = 30

    # 专家评分滑动窗口（Bandit 看板）
    bandit_window: int = 20
    bandit_algo: BanditAlgo = "ucb"
    ucb_c: float = 1.2
    epsilon: float = 0.12
    fusion_mode: FusionMode = "blend"
    # 加权投票阈值：某数字得票 / 权重和 >= 该值才纳入最终排除
    vote_threshold: float = 0.40
    # softmax 温度，越小越偏向最优专家
    weight_temperature: float = 0.32

    # 每位最多排除几个数字，防止杀太狠
    max_exclude_per_position: int = 2

    # 置信度低于此值 -> SKIP
    confidence_threshold: float = 0.36
    # 归一化熵高于此值视为混沌周期
    entropy_chaos_threshold: float = 0.92
    # 弱结构时，Jaccard 低于此值才硬 SKIP（强结构只减分）
    min_consensus: float = 0.12

    # 风控
    consecutive_miss_limit: int = 3
    observe_periods: int = 5
    # 权益回撤（连续记分峰值回落）达到该值则熔断
    max_drawdown: float = 4.0
    # 单期记分：安全排除 +1，误杀 -1，SKIP 0
    hit_score: float = 1.0
    miss_score: float = -1.0

    # --- 模式转变检测 ---
    # 近端胜率窗口 / 远端对照窗口
    regime_short_window: int = 5
    regime_long_window: int = 15
    # 远端胜率 − 近端胜率 >= 该值 → 胜率塌陷
    regime_winrate_drop: float = 0.32
    # 领先双子专家的分歧（1 - Jaccard）；全员分歧天然偏高，不用全员
    regime_disagreement_threshold: float = 0.88
    # 与窗口模块同一套归一化熵
    regime_entropy_threshold: float = 0.93
    # 胜率检测最少样本，避免开局误报
    regime_min_samples: int = 5

    # --- 观察期 / 冷启动 ---
    regime_observe_periods: int = 4
    # 离开观察期后的再触发冷却，防止观察期本身来回抖
    regime_retrigger_cooldown: int = 5

    # --- 渐进加权与滞回 ---
    ema_alpha: float = 0.22
    # 挑战者须比在任高出的胜率/权重裕度
    hysteresis_margin: float = 0.08
    takeover_min_hit_rate: float = 0.60
    takeover_min_confidence: float = 0.40
    # 观察期结束后，挑战者连续达标期数才正式换帅
    takeover_persist: int = 2

    def digit_count(self) -> int:
        return self.digit_max - self.digit_min + 1

    def positions_1based(self) -> tuple[int, ...]:
        return tuple(range(1, self.n_positions + 1))
