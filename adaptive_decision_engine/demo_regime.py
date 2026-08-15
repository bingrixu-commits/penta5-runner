"""模拟日志：发现转变 → 强制观察 → 渐进切换。

运行::

    python3 -m adaptive_decision_engine.demo_regime
"""

from __future__ import annotations

from .regime_guard import RegimeGuard
from .types import EngineConfig, ExpertExclusion


def _prop(eid: str, digits: set[int], conf: float) -> ExpertExclusion:
    return ExpertExclusion(
        expert_id=eid,
        excluded={1: set(digits), 2: set(digits)},
        self_confidence=conf,
        note=eid,
    )


def run() -> int:
    cfg = EngineConfig(
        n_positions=2,
        regime_observe_periods=4,
        regime_retrigger_cooldown=5,
        regime_entropy_threshold=0.90,
        regime_winrate_drop=0.30,
        regime_min_samples=5,
        ema_alpha=0.28,
        hysteresis_margin=0.05,
        takeover_min_hit_rate=0.60,
        takeover_min_confidence=0.40,
        takeover_persist=2,
    )
    guard = RegimeGuard(["alpha", "beta"], cfg)
    props_stable = [
        _prop("alpha", {3, 4}, 0.72),
        _prop("beta", {3, 4}, 0.40),
    ]
    props_shift = [
        _prop("alpha", {0, 1}, 0.25),
        _prop("beta", {8, 9}, 0.80),
    ]

    print("=" * 72)
    print("模式转变检测与防震荡缓冲 — 模拟走期")
    print("=" * 72)

    # 1) 稳定期：alpha 持续安全，EMA 慢慢偏向 alpha
    print("\n[阶段1] 稳定期  alpha 占优")
    for i in range(1, 7):
        period = f"T{i:02d}"
        snap = guard.on_decide(period, props_stable, {"alpha": 0.75, "beta": 0.25}, 0.35, False)
        guard.on_observe(
            period,
            {"alpha": True, "beta": False},
            {"alpha": 0.72, "beta": 0.40},
            {"alpha": 0.80, "beta": 0.20},
            fused_hit=True,
            risk_frozen=False,
        )
        w = snap.ema_weights
        print(
            f"  {period}  state={guard.state:<12} inc={guard.incumbent:<6} "
            f"ema α={w['alpha']:.2f} β={w['beta']:.2f}  出手=EXCLUDE"
        )

    # 2) 突变：高熵 + 随后胜率塌陷（本段用熵立刻触发）
    print("\n[阶段2] 模式突变  高熵触发防御")
    period = "T07"
    snap = guard.on_decide(period, props_shift, {"alpha": 0.70, "beta": 0.30}, 0.96, False)
    print(
        f"  {period}  state={snap.state:<12} 观察剩余={snap.observe_left}  "
        f"原因={snap.last_signal.note if snap.last_signal else '-'}"
    )
    print("  → 对外 SKIP，不切换专家，冻结权重")

    # 3) 观察期：只记账，beta 在新环境里打赢
    print("\n[阶段3] 纯观察期  后台记录（beta 适应新盘）")
    for i in range(8, 12):
        period = f"T{i:02d}"
        snap = guard.on_decide(period, props_shift, {"alpha": 0.2, "beta": 0.8}, 0.70, False)
        guard.on_observe(
            period,
            {"alpha": False, "beta": True},
            {"alpha": 0.22, "beta": 0.82},
            {"alpha": 0.20, "beta": 0.80},
            fused_hit=None,
            risk_frozen=False,
        )
        print(
            f"  {period}  decide={snap.state:<12} after={guard.state:<12} "
            f"剩余={guard.observe_left}  后台 α=0 β=1  对外=SKIP  "
            f"冻α={snap.ema_weights['alpha']:.2f}"
        )

    # 4) 观察结束 + 渐进。提案对齐，避免「领先分歧」把冷却后的恢复期再次打回观察。
    print("\n[阶段4] 观察结束 → 渐进加权（在任仍是 alpha，beta 只加权重）")
    props_new = [
        _prop("alpha", {8, 9}, 0.35),
        _prop("beta", {8, 9}, 0.85),
    ]
    for i in range(12, 17):
        period = f"T{i:02d}"
        before_inc = guard.incumbent
        snap = guard.on_decide(period, props_new, {"alpha": 0.15, "beta": 0.85}, 0.38, False)
        if snap.state != "NORMAL":
            print(f"  {period}  state={snap.state}（未预期，演示中断）")
            break
        guard.on_observe(
            period,
            {"alpha": False, "beta": True},
            {"alpha": 0.20, "beta": 0.85},
            {"alpha": 0.15, "beta": 0.85},
            fused_hit=True,
            risk_frozen=False,
        )
        w = guard.weighter.current()
        tag = "换帅" if guard.incumbent != before_inc else "渐进"
        print(
            f"  {period}  state={guard.state:<12} inc={guard.incumbent:<6} "
            f"pend={str(guard.pending):<6} persist={guard.persist}  "
            f"ema α={w['alpha']:.2f} β={w['beta']:.2f}  {tag}"
        )

    print("\n事件时间线")
    for ev in guard.events:
        if ev.kind in {"SHIFT", "GRADUATE", "TAKEOVER", "FREEZE"}:
            print(f"  [{ev.period}] {ev.kind:<9} {ev.message}")

    print()
    print(f"终态 incumbent={guard.incumbent} pending={guard.pending} state={guard.state}")
    print("期望：SHIFT → 连续 OBSERVE → GRADUATE（beta 达标但未立刻换帅）→ TAKEOVER")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
