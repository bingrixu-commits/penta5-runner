"""独立的自适应数字排除决策引擎。

不依赖本仓库其它算法、RDS 或面板代码。用法::

    from adaptive_decision_engine import (
        AdaptiveDecisionEngine,
        DrawRecord,
        EngineConfig,
    )

    engine = AdaptiveDecisionEngine()
    decision = engine.decide(history, next_period="20260815012")
    # 开奖后
    engine.observe(actual, decision)
"""

from .engine import AdaptiveDecisionEngine
from .experts import ExclusionExpert, default_experts
from .regime_guard import RegimeGuard, RegimeSnapshot, ShiftSignal
from .types import DrawRecord, EngineConfig, EngineState, ExclusionDecision, ExpertExclusion

__all__ = [
    "AdaptiveDecisionEngine",
    "DrawRecord",
    "EngineConfig",
    "EngineState",
    "ExclusionDecision",
    "ExclusionExpert",
    "ExpertExclusion",
    "RegimeGuard",
    "RegimeSnapshot",
    "ShiftSignal",
    "default_experts",
]
