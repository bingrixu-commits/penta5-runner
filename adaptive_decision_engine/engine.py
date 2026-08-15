"""自适应决策引擎：编排窗口、专家、路由、置信度、风控。

本文件是唯一编排层。不实现具体排除公式，也不依赖仓库内其它算法包。
"""

from __future__ import annotations

from typing import Sequence

from .adaptive_window import AdaptiveWindowController
from .bandit_router import BanditRouter
from .circuit_breaker import CircuitBreaker
from .confidence import ConfidenceGate
from .experts import ExclusionExpert, default_experts, score_expert
from .regime_guard import RegimeGuard
from .types import DrawRecord, EngineConfig, EngineState, ExclusionDecision, ExpertExclusion


class AdaptiveDecisionEngine:
    """面向「历史开奖序列 → 排除 / SKIP / HALT」的独立引擎。"""

    def __init__(
        self,
        cfg: EngineConfig | None = None,
        experts: Sequence[ExclusionExpert] | None = None,
        seed: int = 20260815,
    ) -> None:
        self.cfg = cfg or EngineConfig()
        self.experts = list(experts) if experts is not None else default_experts(self.cfg)
        if not self.experts:
            raise ValueError("至少需要 1 个专家")
        ids = [e.expert_id for e in self.experts]
        if len(ids) != len(set(ids)):
            raise ValueError(f"专家 ID 重复: {ids}")
        self.window = AdaptiveWindowController(self.cfg)
        self.router = BanditRouter(ids, self.cfg, seed=seed)
        self.confidence = ConfidenceGate(self.cfg)
        self.risk = CircuitBreaker(self.cfg)
        self.regime = RegimeGuard(ids, self.cfg)
        self._last_proposals: list[ExpertExclusion] = []

    def reset(self) -> None:
        self.window.reset()
        self.router.reset()
        self.risk.reset()
        self.regime.reset()
        self._last_proposals = []

    def _validate_history(self, history: Sequence[DrawRecord]) -> str | None:
        cfg = self.cfg
        if len(history) < cfg.min_history:
            return f"历史不足 {len(history)}/{cfg.min_history}"
        for rec in history:
            if len(rec.digits) != cfg.n_positions:
                return f"期 {rec.period} 位数={len(rec.digits)}，期望 {cfg.n_positions}"
            for d in rec.digits:
                if d < cfg.digit_min or d > cfg.digit_max:
                    return f"期 {rec.period} 出现非法数字 {d}"
        return None

    def _blank(
        self,
        action: str,
        reason: str,
        period_hint: str,
        confidence: float,
        weights: dict[str, float],
        window_size: int,
        meta: dict,
        engine_state: EngineState = "NORMAL",
    ) -> ExclusionDecision:
        empty = {p: [] for p in self.cfg.positions_1based()}
        return ExclusionDecision(
            action=action,  # type: ignore[arg-type]
            period_hint=period_hint,
            excluded=empty,
            confidence=confidence,
            reason=reason,
            expert_weights=weights,
            window_size=window_size,
            engine_state=engine_state,
            meta=meta,
        )

    def decide(self, history: Sequence[DrawRecord], next_period: str = "") -> ExclusionDecision:
        """根据截至目前的历史，对「下一期」给出排除或放弃信号。

        Parameters
        ----------
        history:
            已开奖序列，按时间升序。最后一期是最近开奖。
        next_period:
            即将预测的期号，仅写入输出，缺省时用 last+?。
        """
        cfg = self.cfg
        hint = next_period or (f"{history[-1].period}+" if history else "?")
        err = self._validate_history(history)
        risk = self.risk.snapshot()
        weights0 = {e.expert_id: 0.0 for e in self.experts}

        if err:
            self._last_proposals = []
            return self._blank("SKIP", err, hint, 0.0, weights0, 0, {"gate": "validate"})

        # 历史足够时始终跑专家并缓存，观察期也能继续给 Bandit 打分。
        assessment = self.window.assess(history)
        window = history[-assessment.window_size :]
        proposals = [expert.propose(window) for expert in self.experts]
        self._last_proposals = proposals
        snap = self.router.snapshot()
        regime_snap = self.regime.on_decide(
            period=hint,
            proposals=proposals,
            target_weights=snap.weights,
            entropy_norm=assessment.entropy_norm,
            risk_frozen=not risk.allow_output,
        )
        display_weights = self.regime.display_weights(snap.weights)
        fused = self.router.fuse(proposals, display_weights)
        gate = self.confidence.evaluate(proposals, fused, assessment, snap)

        if regime_snap.state == "FROZEN" or not risk.allow_output:
            return self._blank(
                "HALT",
                f"风控熔断（{risk.note}）",
                hint,
                gate.confidence,
                display_weights,
                assessment.window_size,
                {"gate": "circuit_breaker", "risk": risk.note, "regime": regime_snap.note},
                engine_state="FROZEN",
            )

        if regime_snap.state == "OBSERVATION":
            return self._blank(
                "SKIP",
                f"模式转变观察期剩余 {regime_snap.observe_left} 期（在任={regime_snap.incumbent}）",
                hint,
                gate.confidence,
                display_weights,
                assessment.window_size,
                {
                    "gate": "regime_observation",
                    "regime": regime_snap.note,
                    "shift": regime_snap.last_signal.note if regime_snap.last_signal else "",
                },
                engine_state="OBSERVATION",
            )

        meta = {
            "window": assessment.note,
            "router": snap.note,
            "consensus": round(gate.consensus, 4),
            "leader_gap": round(gate.leader_gap, 4),
            "chaos": assessment.chaos,
            "change_detected": assessment.change_detected,
            "regime": regime_snap.note,
            "incumbent": regime_snap.incumbent,
            "pending": regime_snap.pending,
            "expert_notes": {p.expert_id: p.note for p in proposals},
            "expert_board": [
                {
                    "id": row.expert_id,
                    "hit_rate": row.hit_rate,
                    "mean_reward": row.mean_reward,
                    "ucb": row.ucb,
                    "weight": row.weight,
                }
                for row in snap.board
            ],
        }

        if gate.skip:
            return self._blank(
                "SKIP",
                gate.reason,
                hint,
                gate.confidence,
                display_weights,
                assessment.window_size,
                {**meta, "gate": "confidence"},
                engine_state="NORMAL",
            )

        excluded = {p: sorted(fused.get(p, set())) for p in cfg.positions_1based()}
        return ExclusionDecision(
            action="EXCLUDE",
            period_hint=hint,
            excluded=excluded,
            confidence=gate.confidence,
            reason=f"融合排除（在任={regime_snap.incumbent}）",
            expert_weights=display_weights,
            window_size=assessment.window_size,
            engine_state="NORMAL",
            meta=meta,
        )

    def observe(self, actual: DrawRecord, decision: ExclusionDecision) -> None:
        """揭晓后更新：变化检测、专家看板、风控。必须每期都调用。"""
        self.window.observe_period(actual)

        # 使用 decide() 缓存的专家提案打分，避免揭晓后用「含本期」的窗口重算。
        cached = self._last_proposals
        hits: dict[str, bool] = {}
        if cached:
            rewards = {
                p.expert_id: score_expert(p, actual, self.cfg) for p in cached
            }
            for p in cached:
                miss = any(
                    actual.digits[pos0] in p.excluded.get(pos0 + 1, set())
                    for pos0 in range(self.cfg.n_positions)
                )
                hits[p.expert_id] = not miss
            self.router.update(rewards, hits)
        fused_hit: bool | None = None
        if decision.action == "EXCLUDE":
            fused_hit = not any(
                actual.digits[pos0] in decision.excluded.get(pos0 + 1, [])
                for pos0 in range(self.cfg.n_positions)
            )
        self.risk.observe(decision, actual)
        target = self.router.snapshot().weights
        confs = {p.expert_id: p.self_confidence for p in cached} if cached else {}
        self.regime.on_observe(
            period=actual.period,
            expert_hits=hits,
            expert_conf=confs,
            target_weights=target,
            fused_hit=fused_hit,
            risk_frozen=not self.risk.snapshot().allow_output,
        )

    def replay(self, records: Sequence[DrawRecord]) -> list[ExclusionDecision]:
        """走前期回放：用 records[:t] 决策 records[t]，再观察。

        返回与 records 等长的决策列表；前 min_history 期固定为 SKIP。
        """
        self.reset()
        self._last_proposals = []
        decisions: list[ExclusionDecision] = []
        for idx, actual in enumerate(records):
            past = records[:idx]
            decision = self.decide(past, next_period=actual.period)
            self.observe(actual, decision)
            decisions.append(decision)
        return decisions
