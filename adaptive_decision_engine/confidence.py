"""动态置信度与主动放弃（Active Skipping）。

置信度综合三件事：
1. 专家共识（排除集合的平均 Jaccard）—— 分歧大则不敢杀；
2. 分布是否混沌（归一化熵 / 变化检测）—— 乱市空仓；
3. 路由是否出现明显领先者（最优与次优权重差）—— 没主心骨则降权。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .adaptive_window import WindowAssessment
from .bandit_router import RouterSnapshot
from .types import EngineConfig, ExpertExclusion


def _jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def pairwise_consensus(proposals: Sequence[ExpertExclusion], n_positions: int) -> float:
    """所有专家对、所有位置的平均 Jaccard。"""
    if len(proposals) < 2:
        return 1.0
    scores: list[float] = []
    for i in range(len(proposals)):
        for j in range(i + 1, len(proposals)):
            for pos in range(1, n_positions + 1):
                scores.append(
                    _jaccard(
                        proposals[i].excluded.get(pos, set()),
                        proposals[j].excluded.get(pos, set()),
                    )
                )
    return sum(scores) / len(scores) if scores else 0.0


@dataclass
class ConfidenceReport:
    confidence: float
    consensus: float
    leader_gap: float
    skip: bool
    reason: str


class ConfidenceGate:
    """输出前的安全门：不通过则引擎必须返回 SKIP。"""

    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg

    def evaluate(
        self,
        proposals: Sequence[ExpertExclusion],
        fused: dict[int, set[int]],
        window: WindowAssessment,
        router: RouterSnapshot,
    ) -> ConfidenceReport:
        cfg = self.cfg
        consensus = pairwise_consensus(proposals, cfg.n_positions)
        weights = sorted(router.weights.values(), reverse=True)
        leader_gap = (weights[0] - weights[1]) if len(weights) >= 2 else 1.0

        self_conf = (
            sum(p.self_confidence for p in proposals) / len(proposals) if proposals else 0.0
        )
        has_kills = any(fused.values())
        # 融合数字的平均得票率：比五专家两两 Jaccard 更贴近「能不能出手」
        fused_support = 0.0
        if has_kills:
            supports: list[float] = []
            wsum = sum(max(0.0, router.weights.get(p.expert_id, 0.0)) for p in proposals) or 1.0
            for pos, digits in fused.items():
                for digit in digits:
                    vote = sum(
                        max(0.0, router.weights.get(p.expert_id, 0.0))
                        for p in proposals
                        if digit in p.excluded.get(pos, set())
                    )
                    supports.append(vote / wsum)
            fused_support = sum(supports) / len(supports) if supports else 0.0

        structure = 1.0 - window.entropy_norm
        confidence = (
            0.34 * structure
            + 0.26 * fused_support
            + 0.20 * min(1.0, leader_gap / 0.30)
            + 0.12 * self_conf
            + 0.08 * min(1.0, consensus / 0.35)
        )
        if window.change_detected:
            confidence *= 0.88
        if not has_kills:
            confidence *= 0.50

        reasons: list[str] = []
        skip = False
        if window.chaos:
            skip = True
            reasons.append(f"混沌周期(归一化熵={window.entropy_norm:.2f})")
        if confidence < cfg.confidence_threshold:
            skip = True
            reasons.append(f"置信度{confidence:.2f}<{cfg.confidence_threshold}")
        if not has_kills:
            skip = True
            reasons.append("融合结果为空")
        # 共识过低只作为减分；仅在结构也弱时才硬 SKIP，避免五专家天然互斥导致永远空仓
        if consensus < cfg.min_consensus and structure < 0.28:
            skip = True
            reasons.append(f"弱结构且分歧 consensus={consensus:.2f}")

        reason = "；".join(reasons) if skip else "通过"
        return ConfidenceReport(
            confidence=max(0.0, min(1.0, confidence)),
            consensus=consensus,
            leader_gap=leader_gap,
            skip=skip,
            reason=reason,
        )
