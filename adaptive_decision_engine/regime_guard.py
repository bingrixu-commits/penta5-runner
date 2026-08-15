"""模式转变检测 + 观察期缓冲 + EMA 渐进加权。

本模块只依赖 ``types``，不读取专家公式、RDS 或面板。
三态状态机：

- ``NORMAL``：用 EMA 权重融合出手
- ``OBSERVATION``：转变/冷启动后强制 SKIP，后台只记账
- ``FROZEN``：风控熔断，对外 HALT；解除后先冷启动观察再恢复
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .types import EngineConfig, EngineState, ExpertExclusion


def _jaccard(a: set[int], b: set[int]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def leading_disagreement(
    proposals: Sequence[ExpertExclusion],
    weights: dict[str, float],
    n_positions: int,
) -> float:
    """领先双子专家的平均 (1 - Jaccard)。全员互斥时不会被这项单独打爆。"""
    if len(proposals) < 2:
        return 0.0
    by_id = {p.expert_id: p for p in proposals}
    ranked = sorted(weights, key=lambda k: weights.get(k, 0.0), reverse=True)
    top = [eid for eid in ranked if eid in by_id][:2]
    if len(top) < 2:
        return 0.0
    a, b = by_id[top[0]], by_id[top[1]]
    scores = [
        _jaccard(a.excluded.get(pos, set()), b.excluded.get(pos, set()))
        for pos in range(1, n_positions + 1)
    ]
    consensus = sum(scores) / len(scores) if scores else 1.0
    return 1.0 - consensus


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


@dataclass
class ShiftSignal:
    """单期转变检测快照。"""

    triggered: bool
    winrate_drop: float
    short_winrate: float
    long_winrate: float
    disagreement: float
    entropy: float
    reasons: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class RegimeEvent:
    """状态机事件，供日志 / 单测断言。"""

    period: str
    kind: str
    message: str
    payload: dict = field(default_factory=dict)


@dataclass
class RegimeSnapshot:
    state: EngineState
    observe_left: int
    cooldown_left: int
    incumbent: str
    pending: str | None
    persist: int
    ema_weights: dict[str, float]
    last_signal: ShiftSignal | None
    note: str


class ProgressiveWeighter:
    """指数滑动平均权重。观察/熔断期冻结，避免一次把权重拉满。"""

    def __init__(self, expert_ids: Sequence[str], alpha: float) -> None:
        self.ids = list(expert_ids)
        if not self.ids:
            raise ValueError("ProgressiveWeighter 需要至少一个专家")
        self.alpha = min(1.0, max(1e-6, alpha))
        uniform = 1.0 / len(self.ids)
        self.ema: dict[str, float] = {eid: uniform for eid in self.ids}
        self._frozen: dict[str, float] | None = None

    def reset(self) -> None:
        uniform = 1.0 / len(self.ids)
        self.ema = {eid: uniform for eid in self.ids}
        self._frozen = None

    def freeze(self) -> None:
        self._frozen = dict(self.ema)

    def unfreeze(self) -> None:
        self._frozen = None

    @property
    def locked(self) -> bool:
        return self._frozen is not None

    def current(self) -> dict[str, float]:
        src = self._frozen if self._frozen is not None else self.ema
        return dict(src)

    def step(self, target: dict[str, float]) -> dict[str, float]:
        """``ema ← (1-α)·ema + α·target``，然后归一化。冻结时原样返回。"""
        if self._frozen is not None:
            return dict(self._frozen)
        a = self.alpha
        for eid in self.ids:
            tgt = max(0.0, float(target.get(eid, 0.0)))
            self.ema[eid] = (1.0 - a) * self.ema[eid] + a * tgt
        self._normalize()
        return dict(self.ema)

    def bias_toward(self, preferred: str, strength: float = 0.55) -> None:
        """观察期结束时给合格挑战者一个温和目标，而不是 one-hot。"""
        if preferred not in self.ema:
            return
        rest = (1.0 - strength) / max(1, len(self.ids) - 1)
        target = {eid: (strength if eid == preferred else rest) for eid in self.ids}
        self.unfreeze()
        self.step(target)

    def _normalize(self) -> None:
        z = sum(self.ema.values())
        if z <= 1e-12:
            u = 1.0 / len(self.ids)
            self.ema = {eid: u for eid in self.ids}
            return
        self.ema = {eid: v / z for eid, v in self.ema.items()}


class RegimeShiftDetector:
    """近端胜率落差 + 领先专家分歧 + 信息熵。"""

    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg
        self.fused_hits: deque[int] = deque(maxlen=max(cfg.regime_long_window, 8))

    def reset(self) -> None:
        self.fused_hits.clear()

    def record_fused(self, hit: bool | None) -> None:
        """只记真正出手的期；SKIP/HALT 传 None，不稀释胜率。"""
        if hit is None:
            return
        self.fused_hits.append(1 if hit else 0)

    def detect(
        self,
        proposals: Sequence[ExpertExclusion],
        weights: dict[str, float],
        entropy_norm: float,
    ) -> ShiftSignal:
        cfg = self.cfg
        hits = list(self.fused_hits)
        short_n = min(cfg.regime_short_window, len(hits))
        long_n = min(cfg.regime_long_window, len(hits))
        short = _mean(hits[-short_n:]) if short_n else 0.5
        long = _mean(hits[-long_n:]) if long_n else 0.5
        drop = long - short
        disagree = leading_disagreement(proposals, weights, cfg.n_positions)

        reasons: list[str] = []
        if len(hits) >= cfg.regime_min_samples and drop >= cfg.regime_winrate_drop:
            reasons.append(f"胜率落差{drop:.2f}>={cfg.regime_winrate_drop}")
        if disagree >= cfg.regime_disagreement_threshold:
            reasons.append(f"领先分歧{disagree:.2f}>={cfg.regime_disagreement_threshold}")
        if entropy_norm >= cfg.regime_entropy_threshold:
            reasons.append(f"熵{entropy_norm:.2f}>={cfg.regime_entropy_threshold}")

        triggered = bool(reasons)
        note = "；".join(reasons) if reasons else "无转变"
        return ShiftSignal(
            triggered=triggered,
            winrate_drop=drop,
            short_winrate=short,
            long_winrate=long,
            disagreement=disagree,
            entropy=entropy_norm,
            reasons=reasons,
            note=note,
        )


class RegimeGuard:
    """对外唯一入口：检测、切态、冻结/渐进权重、接管滞回。"""

    def __init__(self, expert_ids: Iterable[str], cfg: EngineConfig) -> None:
        self.cfg = cfg
        self.ids = list(expert_ids)
        if not self.ids:
            raise ValueError("RegimeGuard 需要至少一个专家")
        self.detector = RegimeShiftDetector(cfg)
        self.weighter = ProgressiveWeighter(self.ids, cfg.ema_alpha)
        self.state: EngineState = "NORMAL"
        self.observe_left = 0
        self.cooldown_left = 0
        self.incumbent = self.ids[0]
        self.pending: str | None = None
        self.persist = 0
        self.obs_hits: dict[str, list[int]] = {eid: [] for eid in self.ids}
        self.obs_conf: dict[str, list[float]] = {eid: [] for eid in self.ids}
        self.last_signal: ShiftSignal | None = None
        self.events: list[RegimeEvent] = []
        self._period = ""

    def reset(self) -> None:
        self.detector.reset()
        self.weighter.reset()
        self.state = "NORMAL"
        self.observe_left = 0
        self.cooldown_left = 0
        self.incumbent = self.ids[0]
        self.pending = None
        self.persist = 0
        self.obs_hits = {eid: [] for eid in self.ids}
        self.obs_conf = {eid: [] for eid in self.ids}
        self.last_signal = None
        self.events.clear()
        self._period = ""

    def snapshot(self) -> RegimeSnapshot:
        return RegimeSnapshot(
            state=self.state,
            observe_left=self.observe_left,
            cooldown_left=self.cooldown_left,
            incumbent=self.incumbent,
            pending=self.pending,
            persist=self.persist,
            ema_weights=self.weighter.current(),
            last_signal=self.last_signal,
            note=self._note(),
        )

    def display_weights(self, target: dict[str, float] | None = None) -> dict[str, float]:
        """decide() 用的权重：观察/熔断期是冻结快照，正常期是当前 EMA。"""
        if target and self.state == "NORMAL" and not self.weighter.locked:
            # 正常期不在 decide 里 step，避免同一期 step 两次；只读 EMA
            pass
        return self.weighter.current()

    def on_decide(
        self,
        period: str,
        proposals: Sequence[ExpertExclusion],
        target_weights: dict[str, float],
        entropy_norm: float,
        risk_frozen: bool,
    ) -> RegimeSnapshot:
        """决策前调用：熔断优先；NORMAL 下若检出转变则立刻进入观察并 SKIP。"""
        self._period = period
        if risk_frozen:
            if self.state != "FROZEN":
                self._enter_frozen("风控熔断")
            return self.snapshot()

        if self.state == "FROZEN" and not risk_frozen:
            self._enter_observation("熔断解除，冷启动观察")
            return self.snapshot()

        if self.state == "OBSERVATION":
            return self.snapshot()

        signal = self.detector.detect(proposals, target_weights, entropy_norm)
        self.last_signal = signal
        if signal.triggered and self.cooldown_left <= 0:
            self._enter_observation(signal.note)
        return self.snapshot()

    def on_observe(
        self,
        period: str,
        expert_hits: dict[str, bool],
        expert_conf: dict[str, float],
        target_weights: dict[str, float],
        fused_hit: bool | None,
        risk_frozen: bool,
    ) -> RegimeSnapshot:
        """揭晓后调用：记账、走观察期倒计时、EMA、滞回换帅。"""
        self._period = period
        self.detector.record_fused(fused_hit)

        if risk_frozen:
            if self.state != "FROZEN":
                self._enter_frozen("揭晓后风控熔断")
            return self.snapshot()

        if self.state == "FROZEN":
            self._enter_observation("熔断解除，冷启动观察")
            return self.snapshot()

        if self.state == "OBSERVATION":
            for eid in self.ids:
                if eid in expert_hits:
                    self.obs_hits[eid].append(1 if expert_hits[eid] else 0)
                if eid in expert_conf:
                    self.obs_conf[eid].append(float(expert_conf[eid]))
            self.observe_left = max(0, self.observe_left - 1)
            self._emit(
                "OBSERVE",
                f"观察期记账 剩余={self.observe_left}",
                {"hits": {k: list(v) for k, v in self.obs_hits.items()}},
            )
            if self.observe_left <= 0:
                self._graduate()
            return self.snapshot()

        if self.cooldown_left > 0:
            self.cooldown_left -= 1

        before = dict(self.weighter.ema)
        after = self.weighter.step(target_weights)
        self._emit(
            "EMA",
            "渐进更新权重",
            {"before": {k: round(v, 4) for k, v in before.items()},
             "after": {k: round(v, 4) for k, v in after.items()}},
        )
        self._hysteresis(expert_hits, expert_conf)
        return self.snapshot()

    def _enter_frozen(self, why: str) -> None:
        self.state = "FROZEN"
        self.weighter.freeze()
        self._emit("FREEZE", why, {})

    def _enter_observation(self, why: str) -> None:
        self.state = "OBSERVATION"
        self.observe_left = self.cfg.regime_observe_periods
        self.pending = None
        self.persist = 0
        self.obs_hits = {eid: [] for eid in self.ids}
        self.obs_conf = {eid: [] for eid in self.ids}
        self.weighter.freeze()
        self._emit(
            "SHIFT",
            f"进入观察期 {self.observe_left} 期：{why}",
            {"incumbent": self.incumbent, "frozen": self.weighter.current()},
        )

    def _graduate(self) -> None:
        cfg = self.cfg
        rates = {
            eid: _mean(self.obs_hits[eid]) if self.obs_hits[eid] else 0.0
            for eid in self.ids
        }
        confs = {
            eid: _mean(self.obs_conf[eid]) if self.obs_conf[eid] else 0.0
            for eid in self.ids
        }
        challenger = max(self.ids, key=lambda e: (rates[e], confs[e], e))
        inc_rate = rates[self.incumbent]
        qualified = (
            challenger != self.incumbent
            and rates[challenger] >= cfg.takeover_min_hit_rate
            and confs[challenger] >= cfg.takeover_min_confidence
            and rates[challenger] - inc_rate >= cfg.hysteresis_margin
        )
        self.weighter.unfreeze()
        if qualified:
            self.pending = challenger
            self.persist = 0
            self.weighter.bias_toward(challenger, strength=0.56)
            self._emit(
                "GRADUATE",
                f"观察结束，{challenger} 达标，开始渐进接管（在任仍为 {self.incumbent}）",
                {"rates": rates, "confs": confs, "pending": challenger},
            )
        else:
            self.pending = None
            self.weighter.bias_toward(self.incumbent, strength=0.52)
            self._emit(
                "GRADUATE",
                f"观察结束，无人达标，保持在任 {self.incumbent}",
                {"rates": rates, "confs": confs},
            )
        self.state = "NORMAL"
        self.cooldown_left = cfg.regime_retrigger_cooldown

    def _hysteresis(self, expert_hits: dict[str, bool], expert_conf: dict[str, float]) -> None:
        if self.pending is None or self.pending == self.incumbent:
            return
        cfg = self.cfg
        # 用观察期 + 当前期的混合信号：当前期 hit/conf
        hit = 1.0 if expert_hits.get(self.pending, False) else 0.0
        conf = float(expert_conf.get(self.pending, 0.0))
        ema = self.weighter.ema
        lead = ema.get(self.pending, 0.0) - ema.get(self.incumbent, 0.0)
        ok = (
            hit >= 1.0
            and conf >= cfg.takeover_min_confidence
            and lead >= cfg.hysteresis_margin
        )
        if ok:
            self.persist += 1
        else:
            self.persist = 0
        if self.persist >= cfg.takeover_persist:
            old = self.incumbent
            self.incumbent = self.pending
            self.pending = None
            self.persist = 0
            self._emit(
                "TAKEOVER",
                f"正式换帅 {old} → {self.incumbent}",
                {"ema": {k: round(v, 4) for k, v in ema.items()}},
            )

    def _note(self) -> str:
        return (
            f"state={self.state},obs={self.observe_left},cd={self.cooldown_left},"
            f"inc={self.incumbent},pend={self.pending},persist={self.persist}"
        )

    def _emit(self, kind: str, message: str, payload: dict) -> None:
        self.events.append(
            RegimeEvent(period=self._period, kind=kind, message=message, payload=payload)
        )
