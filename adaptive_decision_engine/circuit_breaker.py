"""熔断与风控：连错计数 + 最大回撤 + 强制观察期。

只在「真正出手的 EXCLUDE」上计分。SKIP 不奖不罚，避免空仓被当成失误。
观察期内仍然允许外部调用 ``observe``，以便看板继续学习，但 ``allow_output`` 为假。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .types import DrawRecord, EngineConfig, ExclusionDecision

RiskState = Literal["NORMAL", "HALT"]


@dataclass
class RiskSnapshot:
    state: RiskState
    consecutive_misses: int
    observe_left: int
    equity: float
    peak: float
    drawdown: float
    last_result: str
    note: str

    @property
    def allow_output(self) -> bool:
        return self.state == "NORMAL" and self.observe_left <= 0


class CircuitBreaker:
    """独立风控模块，不读取专家内部逻辑。"""

    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg
        self.consecutive_misses = 0
        self.observe_left = 0
        self.equity = 0.0
        self.peak = 0.0
        self.last_result = "none"

    def reset(self) -> None:
        self.consecutive_misses = 0
        self.observe_left = 0
        self.equity = 0.0
        self.peak = 0.0
        self.last_result = "none"

    def snapshot(self) -> RiskSnapshot:
        dd = self.peak - self.equity
        state: RiskState = "HALT" if self.observe_left > 0 else "NORMAL"
        note = (
            f"miss_streak={self.consecutive_misses},observe={self.observe_left},"
            f"eq={self.equity:.1f},dd={dd:.1f},last={self.last_result}"
        )
        return RiskSnapshot(
            state=state,
            consecutive_misses=self.consecutive_misses,
            observe_left=self.observe_left,
            equity=self.equity,
            peak=self.peak,
            drawdown=dd,
            last_result=self.last_result,
            note=note,
        )

    def trip(self, why: str) -> None:
        self.observe_left = self.cfg.observe_periods
        self.last_result = f"trip:{why}"

    def observe(self, decision: ExclusionDecision, actual: DrawRecord) -> RiskSnapshot:
        """用揭晓结果更新风控。观察期每期减 1。"""
        if self.observe_left > 0:
            self.observe_left -= 1
            self.last_result = "observing"
            if self.observe_left == 0:
                # 观察结束：清连错，并把回撤标尺重置到当前权益，避免刚恢复就因旧峰值再熔断
                self.consecutive_misses = 0
                self.peak = self.equity
                self.last_result = "resume"
            return self.snapshot()

        if decision.action != "EXCLUDE":
            self.last_result = f"flat:{decision.action}"
            return self.snapshot()

        missed = False
        for pos0, digit in enumerate(actual.digits):
            killed = decision.excluded.get(pos0 + 1, [])
            if digit in killed:
                missed = True
                break

        if missed:
            self.consecutive_misses += 1
            self.equity += self.cfg.miss_score
            self.last_result = "miss"
        else:
            self.consecutive_misses = 0
            self.equity += self.cfg.hit_score
            self.last_result = "hit"

        if self.equity > self.peak:
            self.peak = self.equity
        drawdown = self.peak - self.equity

        if self.consecutive_misses >= self.cfg.consecutive_miss_limit:
            self.trip(f"连错{self.consecutive_misses}")
        elif missed and drawdown >= self.cfg.max_drawdown:
            self.trip(f"回撤{drawdown:.1f}")

        return self.snapshot()
