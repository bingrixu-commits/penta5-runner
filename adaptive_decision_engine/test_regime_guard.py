"""模式转变 / 观察期 / EMA 滞回 的单元测试。仅用标准库。"""

from __future__ import annotations

import unittest

from .regime_guard import (
    ProgressiveWeighter,
    RegimeGuard,
    RegimeShiftDetector,
    leading_disagreement,
)
from .types import EngineConfig, ExpertExclusion


def _prop(eid: str, excluded: dict[int, set[int]], conf: float = 0.7) -> ExpertExclusion:
    return ExpertExclusion(expert_id=eid, excluded=excluded, self_confidence=conf, note=eid)


def _cfg(**kwargs: object) -> EngineConfig:
    base = dict(
        n_positions=2,
        regime_short_window=5,
        regime_long_window=15,
        regime_winrate_drop=0.30,
        regime_disagreement_threshold=0.80,
        regime_entropy_threshold=0.90,
        regime_min_samples=5,
        regime_observe_periods=3,
        regime_retrigger_cooldown=2,
        ema_alpha=0.25,
        hysteresis_margin=0.05,
        takeover_min_hit_rate=0.60,
        takeover_min_confidence=0.40,
        takeover_persist=2,
    )
    base.update(kwargs)
    return EngineConfig(**base)  # type: ignore[arg-type]


class TestProgressiveWeighter(unittest.TestCase):
    def test_ema_does_not_jump_to_target(self) -> None:
        w = ProgressiveWeighter(["A", "B"], alpha=0.25)
        after = w.step({"A": 0.05, "B": 0.95})
        self.assertLess(after["B"], 0.80)
        self.assertGreater(after["B"], 0.50)

    def test_freeze_blocks_step(self) -> None:
        w = ProgressiveWeighter(["A", "B"], alpha=0.9)
        w.step({"A": 0.8, "B": 0.2})
        frozen = w.current()
        w.freeze()
        w.step({"A": 0.1, "B": 0.9})
        self.assertEqual(w.current()["A"], frozen["A"])


class TestDetector(unittest.TestCase):
    def test_winrate_drop_triggers(self) -> None:
        det = RegimeShiftDetector(_cfg())
        for _ in range(10):
            det.record_fused(True)
        for _ in range(5):
            det.record_fused(False)
        sig = det.detect([], {"A": 1.0}, entropy_norm=0.2)
        self.assertTrue(sig.triggered)
        self.assertGreaterEqual(sig.winrate_drop, 0.30)
        self.assertTrue(any("胜率落差" in r for r in sig.reasons))

    def test_entropy_triggers(self) -> None:
        det = RegimeShiftDetector(_cfg())
        sig = det.detect([], {"A": 1.0}, entropy_norm=0.95)
        self.assertTrue(sig.triggered)
        self.assertTrue(any("熵" in r for r in sig.reasons))

    def test_disagreement_triggers(self) -> None:
        props = [
            _prop("A", {1: {0, 1}, 2: {0, 1}}),
            _prop("B", {1: {8, 9}, 2: {8, 9}}),
        ]
        score = leading_disagreement(props, {"A": 0.6, "B": 0.4}, n_positions=2)
        self.assertGreaterEqual(score, 0.80)
        det = RegimeShiftDetector(_cfg())
        sig = det.detect(props, {"A": 0.6, "B": 0.4}, entropy_norm=0.1)
        self.assertTrue(sig.triggered)
        self.assertTrue(any("分歧" in r for r in sig.reasons))

    def test_quiet_when_stable(self) -> None:
        det = RegimeShiftDetector(_cfg())
        for _ in range(12):
            det.record_fused(True)
        same = {1: {1}, 2: {2}}
        props = [_prop("A", same), _prop("B", same)]
        sig = det.detect(props, {"A": 0.7, "B": 0.3}, entropy_norm=0.3)
        self.assertFalse(sig.triggered)


class TestStateMachine(unittest.TestCase):
    def _guard(self) -> RegimeGuard:
        return RegimeGuard(["A", "B"], _cfg())

    def _props(self) -> list[ExpertExclusion]:
        return [
            _prop("A", {1: {0}, 2: {0}}, 0.7),
            _prop("B", {1: {1}, 2: {1}}, 0.7),
        ]

    def test_shift_enters_observation(self) -> None:
        g = self._guard()
        snap = g.on_decide("P1", self._props(), {"A": 0.7, "B": 0.3}, 0.96, False)
        self.assertEqual(snap.state, "OBSERVATION")
        self.assertEqual(snap.observe_left, 3)
        self.assertTrue(g.weighter.locked)
        kinds = [e.kind for e in g.events]
        self.assertIn("SHIFT", kinds)

    def test_observation_records_then_graduates(self) -> None:
        g = self._guard()
        g.on_decide("P1", self._props(), {"A": 0.7, "B": 0.3}, 0.96, False)
        for i in range(3):
            g.on_observe(
                period=f"O{i}",
                expert_hits={"A": False, "B": True},
                expert_conf={"A": 0.3, "B": 0.8},
                target_weights={"A": 0.2, "B": 0.8},
                fused_hit=None,
                risk_frozen=False,
            )
        self.assertEqual(g.state, "NORMAL")
        self.assertEqual(g.pending, "B")
        self.assertEqual(g.incumbent, "A")
        self.assertFalse(g.weighter.locked)
        self.assertIn("GRADUATE", [e.kind for e in g.events])

    def test_no_immediate_takeover(self) -> None:
        g = self._guard()
        g.on_decide("P1", self._props(), {"A": 0.7, "B": 0.3}, 0.96, False)
        for i in range(3):
            g.on_observe(
                f"O{i}",
                {"A": False, "B": True},
                {"A": 0.3, "B": 0.8},
                {"A": 0.2, "B": 0.8},
                None,
                False,
            )
        self.assertEqual(g.incumbent, "A")
        self.assertEqual(g.pending, "B")

    def test_takeover_after_persist(self) -> None:
        g = self._guard()
        g.on_decide("P1", self._props(), {"A": 0.7, "B": 0.3}, 0.96, False)
        for i in range(3):
            g.on_observe(
                f"O{i}",
                {"A": False, "B": True},
                {"A": 0.3, "B": 0.8},
                {"A": 0.2, "B": 0.8},
                None,
                False,
            )
        for i in range(4):
            g.on_observe(
                f"N{i}",
                {"A": False, "B": True},
                {"A": 0.3, "B": 0.8},
                {"A": 0.15, "B": 0.85},
                True,
                False,
            )
        self.assertEqual(g.incumbent, "B")
        self.assertIsNone(g.pending)
        self.assertIn("TAKEOVER", [e.kind for e in g.events])

    def test_hysteresis_blocks_flip_flop(self) -> None:
        g = self._guard()
        g.on_decide("P1", self._props(), {"A": 0.7, "B": 0.3}, 0.96, False)
        for i in range(3):
            g.on_observe(
                f"O{i}",
                {"A": False, "B": True},
                {"A": 0.3, "B": 0.8},
                {"A": 0.2, "B": 0.8},
                None,
                False,
            )
        # 交替命中，persist 清零
        for i in range(6):
            hit_b = i % 2 == 0
            g.on_observe(
                f"F{i}",
                {"A": not hit_b, "B": hit_b},
                {"A": 0.5, "B": 0.5},
                {"A": 0.5, "B": 0.5},
                True,
                False,
            )
        self.assertEqual(g.incumbent, "A")
        self.assertNotIn("TAKEOVER", [e.kind for e in g.events])

    def test_frozen_has_priority(self) -> None:
        g = self._guard()
        snap = g.on_decide("P1", self._props(), {"A": 1.0, "B": 0.0}, 0.1, True)
        self.assertEqual(snap.state, "FROZEN")
        self.assertIn("FREEZE", [e.kind for e in g.events])

    def test_frozen_release_enters_observation(self) -> None:
        g = self._guard()
        g.on_decide("P1", self._props(), {"A": 1.0, "B": 0.0}, 0.1, True)
        snap = g.on_decide("P2", self._props(), {"A": 1.0, "B": 0.0}, 0.1, False)
        self.assertEqual(snap.state, "OBSERVATION")


class TestSimulationSequence(unittest.TestCase):
    """端到端脚本：发现转变 → 观察 → 渐进切换。"""

    def test_event_order(self) -> None:
        g = RegimeGuard(["A", "B"], _cfg(regime_observe_periods=3, takeover_persist=2))
        props = [
            _prop("A", {1: {0}, 2: {0}}, 0.7),
            _prop("B", {1: {1}, 2: {1}}, 0.8),
        ]
        g.on_decide("S01", props, {"A": 0.8, "B": 0.2}, 0.97, False)
        for i in range(3):
            g.on_observe(
                f"S{i+2:02d}",
                {"A": False, "B": True},
                {"A": 0.2, "B": 0.85},
                {"A": 0.2, "B": 0.8},
                None,
                False,
            )
        for i in range(3):
            g.on_observe(
                f"S{i+5:02d}",
                {"A": False, "B": True},
                {"A": 0.2, "B": 0.85},
                {"A": 0.1, "B": 0.9},
                True,
                False,
            )
        kinds = [e.kind for e in g.events]
        self.assertEqual(kinds[0], "SHIFT")
        self.assertIn("OBSERVE", kinds)
        self.assertIn("GRADUATE", kinds)
        self.assertIn("EMA", kinds)
        self.assertIn("TAKEOVER", kinds)
        self.assertLess(kinds.index("SHIFT"), kinds.index("GRADUATE"))
        self.assertLess(kinds.index("GRADUATE"), kinds.index("TAKEOVER"))
        self.assertEqual(g.incumbent, "B")


if __name__ == "__main__":
    unittest.main()
