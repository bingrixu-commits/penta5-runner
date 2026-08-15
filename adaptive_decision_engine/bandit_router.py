"""动态路由：滑动窗口专家看板 + UCB / Epsilon-Greedy。

每个专家是一只臂。奖励来自 ``experts.score_expert``。
看板命中率单独按「该期是否误杀开奖号」统计，便于运维阅读。
"""

from __future__ import annotations

import math
import random
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable

from .types import EngineConfig, ExpertExclusion


@dataclass
class ExpertBoardRow:
    """单个专家的滑动窗口评分行。"""

    expert_id: str
    pulls: int
    window_rewards: list[float]
    mean_reward: float
    hit_rate: float
    ucb: float
    weight: float


@dataclass
class RouterSnapshot:
    weights: dict[str, float]
    board: list[ExpertBoardRow]
    explored: bool
    note: str


class BanditRouter:
    """维护专家权重，并负责把多专家提案融合成一份排除表。"""

    def __init__(self, expert_ids: Iterable[str], cfg: EngineConfig, seed: int = 20260815) -> None:
        self.cfg = cfg
        self.expert_ids = list(expert_ids)
        self.rng = random.Random(seed)
        self._rewards: dict[str, deque[float]] = {
            eid: deque(maxlen=cfg.bandit_window) for eid in self.expert_ids
        }
        self._hits: dict[str, deque[int]] = {
            eid: deque(maxlen=cfg.bandit_window) for eid in self.expert_ids
        }
        self._pulls: dict[str, int] = defaultdict(int)
        self._t = 0

    def reset(self) -> None:
        for eid in self.expert_ids:
            self._rewards[eid].clear()
            self._hits[eid].clear()
            self._pulls[eid] = 0
        self._t = 0

    def update(self, rewards: dict[str, float], hits: dict[str, bool]) -> None:
        """用刚揭晓的一期结果更新看板。"""
        self._t += 1
        for eid in self.expert_ids:
            if eid in rewards:
                self._rewards[eid].append(float(rewards[eid]))
                self._pulls[eid] += 1
            if eid in hits:
                self._hits[eid].append(1 if hits[eid] else 0)

    def _mean(self, eid: str) -> float:
        buf = self._rewards[eid]
        if not buf:
            return 0.5  # 未观察：中性先验，避免开局锁死
        return sum(buf) / len(buf)

    def short_hit_rates(self, n: int | None = None) -> dict[str, float]:
        """近 n 期专家安全率（未误杀比例）。"""
        span = n if n is not None else self.cfg.regime_short_window
        out: dict[str, float] = {}
        for eid in self.expert_ids:
            buf = list(self._hits[eid])[-max(1, span) :]
            out[eid] = (sum(buf) / len(buf)) if buf else 0.5
        return out

    def _hit_rate(self, eid: str) -> float:
        buf = self._hits[eid]
        if not buf:
            return 0.5
        return sum(buf) / len(buf)

    def _ucb_score(self, eid: str) -> float:
        mean = self._mean(eid)
        n = max(1, self._pulls[eid])
        bonus = self.cfg.ucb_c * math.sqrt(math.log(self._t + 2) / n)
        return mean + bonus

    def _softmax(self, scores: dict[str, float]) -> dict[str, float]:
        temp = max(1e-6, self.cfg.weight_temperature)
        peak = max(scores.values())
        exps = {k: math.exp((v - peak) / temp) for k, v in scores.items()}
        z = sum(exps.values()) or 1.0
        return {k: v / z for k, v in exps.items()}

    def snapshot(self) -> RouterSnapshot:
        cfg = self.cfg
        scores = {eid: self._ucb_score(eid) for eid in self.expert_ids}
        explored = False

        if cfg.bandit_algo == "epsilon_greedy":
            if self.rng.random() < cfg.epsilon:
                explored = True
                weights = {eid: 1.0 / len(self.expert_ids) for eid in self.expert_ids}
                note = f"epsilon-explore eps={cfg.epsilon}"
            else:
                best = max(self.expert_ids, key=lambda e: self._mean(e))
                if cfg.fusion_mode == "switch":
                    weights = {eid: 1.0 if eid == best else 0.0 for eid in self.expert_ids}
                else:
                    means = {eid: self._mean(eid) for eid in self.expert_ids}
                    weights = self._softmax(means)
                note = f"epsilon-greedy best={best}"
        else:
            # UCB
            if cfg.fusion_mode == "switch":
                best = max(self.expert_ids, key=lambda e: scores[e])
                weights = {eid: 1.0 if eid == best else 0.0 for eid in self.expert_ids}
                note = f"ucb-switch best={best}"
            else:
                weights = self._softmax(scores)
                note = "ucb-blend"

        board = [
            ExpertBoardRow(
                expert_id=eid,
                pulls=self._pulls[eid],
                window_rewards=list(self._rewards[eid]),
                mean_reward=round(self._mean(eid), 4),
                hit_rate=round(self._hit_rate(eid), 4),
                ucb=round(scores[eid], 4),
                weight=round(weights[eid], 4),
            )
            for eid in self.expert_ids
        ]
        return RouterSnapshot(weights=weights, board=board, explored=explored, note=note)

    def fuse(
        self,
        proposals: list[ExpertExclusion],
        weights: dict[str, float],
    ) -> dict[int, set[int]]:
        """按权重投票融合。switch 模式下等价于只听权重最大的专家。"""
        cfg = self.cfg
        fused: dict[int, set[int]] = {p: set() for p in cfg.positions_1based()}
        if not proposals:
            return fused

        by_id = {p.expert_id: p for p in proposals}
        weight_sum = sum(max(0.0, weights.get(p.expert_id, 0.0)) for p in proposals)
        if weight_sum <= 1e-12:
            return fused

        for pos in cfg.positions_1based():
            tally: dict[int, float] = defaultdict(float)
            for prop in proposals:
                w = max(0.0, weights.get(prop.expert_id, 0.0))
                for digit in prop.excluded.get(pos, set()):
                    tally[digit] += w
            # 得票过线的数字，再按得票取前 N
            passed = [
                (d, s / weight_sum)
                for d, s in tally.items()
                if (s / weight_sum) >= cfg.vote_threshold
            ]
            passed.sort(key=lambda kv: (-kv[1], kv[0]))
            fused[pos] = {d for d, _ in passed[: cfg.max_exclude_per_position]}

        # switch 且没有过线票时，回退到最优专家原文
        if cfg.fusion_mode == "switch" and not any(fused.values()):
            best_id = max(weights, key=lambda k: weights[k])
            best = by_id.get(best_id)
            if best:
                return {p: set(best.excluded.get(p, set())) for p in cfg.positions_1based()}
        return fused
