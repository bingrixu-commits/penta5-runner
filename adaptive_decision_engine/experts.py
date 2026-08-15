"""专家策略池：每种排除逻辑独立、无共享可变状态。

约定
----
- 输入仅为「当前自适应窗口内」的开奖序列（由引擎裁剪）。
- 输出为每位要排除的数字集合，允许为空（专家主动弃权）。
- 不读取引擎、路由、风控的任何状态，便于单独单测与热插拔。
"""

from __future__ import annotations

import math
import statistics
from abc import ABC, abstractmethod
from typing import Sequence

from .types import DrawRecord, EngineConfig, ExpertExclusion


def _empty_sets(cfg: EngineConfig) -> dict[int, set[int]]:
    return {p: set() for p in cfg.positions_1based()}


def _clip_set(values: set[int], cfg: EngineConfig, cap: int | None = None) -> set[int]:
    """只保留合法数字，并按容量截断（数字本身升序，避免随机性）。"""
    legal = {v for v in values if cfg.digit_min <= v <= cfg.digit_max}
    limit = cap if cap is not None else cfg.max_exclude_per_position
    return set(sorted(legal)[:limit])


def _series(window: Sequence[DrawRecord], pos0: int) -> list[int]:
    return [rec.digits[pos0] for rec in window]


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    """皮尔逊相关；常数序列返回 0，避免除零。"""
    if len(a) < 3 or len(a) != len(b):
        return 0.0
    ma = sum(a) / len(a)
    mb = sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    if da < 1e-12 or db < 1e-12:
        return 0.0
    return max(-1.0, min(1.0, num / (da * db)))


class ExclusionExpert(ABC):
    """专家基类。子类只实现 ``propose``。"""

    expert_id: str
    title: str

    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg

    @abstractmethod
    def propose(self, window: Sequence[DrawRecord]) -> ExpertExclusion:
        raise NotImplementedError

    def _pack(
        self,
        excluded: dict[int, set[int]],
        confidence: float,
        note: str,
    ) -> ExpertExclusion:
        return ExpertExclusion(
            expert_id=self.expert_id,
            excluded=excluded,
            self_confidence=max(0.0, min(1.0, confidence)),
            note=note,
        )


class HotColdFrequencyExpert(ExclusionExpert):
    """短期冷热频率排除：杀「过热」数字（均值回归假设）。

    若窗口内频率分布已经很平坦，说明冷热信号弱，主动少杀或不杀。
    """

    expert_id = "hot_cold"
    title = "短期冷热频率"

    def propose(self, window: Sequence[DrawRecord]) -> ExpertExclusion:
        cfg = self.cfg
        out = _empty_sets(cfg)
        z_scores: list[float] = []
        notes: list[str] = []

        expected = len(window) / cfg.digit_count()
        for pos0 in range(cfg.n_positions):
            pos = pos0 + 1
            freq = {d: 0 for d in range(cfg.digit_min, cfg.digit_max + 1)}
            for rec in window:
                freq[rec.digits[pos0]] += 1
            counts = list(freq.values())
            spread = statistics.pstdev(counts) if len(counts) > 1 else 0.0
            # 频率几乎均匀 → 没有可杀的热号
            if spread < 0.45:
                notes.append(f"P{pos}:flat")
                continue
            ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
            hot_digits: set[int] = set()
            for digit, cnt in ranked:
                z = (cnt - expected) / (spread + 1e-9)
                z_scores.append(z)
                if z >= 0.85 and len(hot_digits) < cfg.max_exclude_per_position:
                    hot_digits.add(digit)
            out[pos] = _clip_set(hot_digits, cfg)
            if out[pos]:
                notes.append(f"P{pos}:hot={sorted(out[pos])}")

        conf = 0.25 + 0.55 * min(1.0, (statistics.fmean(z_scores) if z_scores else 0.0) / 2.0)
        if not any(out.values()):
            conf = 0.2
        return self._pack(out, conf, ";".join(notes) or "no-hot-signal")


class MarkovTransitionExpert(ExclusionExpert):
    """一阶马尔可夫：在「上期数字」条件下，杀掉转移概率最低的去向。

    使用拉普拉斯平滑，避免稀有转移被当成绝对不可能。
    """

    expert_id = "markov"
    title = "马尔可夫转移"

    def propose(self, window: Sequence[DrawRecord]) -> ExpertExclusion:
        cfg = self.cfg
        if len(window) < 3:
            return self._pack(_empty_sets(cfg), 0.15, "short-window")

        out = _empty_sets(cfg)
        notes: list[str] = []
        strength = 0.0

        digits = list(range(cfg.digit_min, cfg.digit_max + 1))
        last = window[-1]
        for pos0 in range(cfg.n_positions):
            pos = pos0 + 1
            trans: dict[int, dict[int, int]] = {
                src: {dst: 0 for dst in digits} for src in digits
            }
            for prev, cur in zip(window[:-1], window[1:]):
                trans[prev.digits[pos0]][cur.digits[pos0]] += 1

            src = last.digits[pos0]
            raw = trans[src]
            # Laplace: +1
            smoothed = {d: raw[d] + 1 for d in digits}
            total = sum(smoothed.values())
            probs = {d: smoothed[d] / total for d in digits}
            # 转移越集中，越敢杀尾部
            entropy = -sum(p * math.log(p, 2) for p in probs.values())
            max_h = math.log(cfg.digit_count(), 2)
            peaked = 1.0 - entropy / max_h
            strength += peaked

            # 杀概率最低的若干去向（从未出现的转移优先）
            ranked = sorted(probs.items(), key=lambda kv: (kv[1], kv[0]))
            kill = {d for d, _ in ranked[: cfg.max_exclude_per_position]}
            out[pos] = _clip_set(kill, cfg)
            notes.append(f"P{pos}:from={src},kill={sorted(out[pos])},peak={peaked:.2f}")

        conf = 0.2 + 0.7 * (strength / cfg.n_positions)
        return self._pack(out, conf, ";".join(notes))


class AutocorrelationLagExpert(ExclusionExpert):
    """自相关滞后排除：找到主导滞后 k，再按相关符号决定杀谁。

    - 正相关：下期倾向重复 t-k 的数字附近，杀掉距离该锚点远的数字。
    - 负相关：下期倾向避开 t-k，杀掉锚点本身及其紧邻。
    """

    expert_id = "autocorr"
    title = "自相关滞后"

    def propose(self, window: Sequence[DrawRecord]) -> ExpertExclusion:
        cfg = self.cfg
        out = _empty_sets(cfg)
        if len(window) < 8:
            return self._pack(out, 0.15, "short-window")

        max_lag = min(6, len(window) // 2)
        notes: list[str] = []
        abs_corrs: list[float] = []

        for pos0 in range(cfg.n_positions):
            pos = pos0 + 1
            xs = [float(v) for v in _series(window, pos0)]
            best_k = 1
            best_r = 0.0
            for k in range(1, max_lag + 1):
                r = _pearson(xs[:-k], xs[k:])
                if abs(r) > abs(best_r):
                    best_r, best_k = r, k
            abs_corrs.append(abs(best_r))
            if abs(best_r) < 0.28:
                notes.append(f"P{pos}:weak-r={best_r:.2f}")
                continue

            anchor = int(xs[-best_k])
            if best_r > 0:
                # 杀远离锚点的数字（不跟随滞后结构）
                far = {
                    d
                    for d in range(cfg.digit_min, cfg.digit_max + 1)
                    if min(abs(d - anchor), 10 - abs(d - anchor)) >= 4
                }
                out[pos] = _clip_set(far, cfg)
                notes.append(f"P{pos}:lag={best_k},r=+{best_r:.2f},anchor={anchor}")
            else:
                near = {
                    d
                    for d in range(cfg.digit_min, cfg.digit_max + 1)
                    if min(abs(d - anchor), 10 - abs(d - anchor)) <= 1
                }
                out[pos] = _clip_set(near, cfg)
                notes.append(f"P{pos}:lag={best_k},r={best_r:.2f},kill-near={anchor}")

        conf = 0.2 + 0.7 * (statistics.fmean(abs_corrs) if abs_corrs else 0.0)
        return self._pack(out, conf, ";".join(notes))


class OscillationVolatilityExpert(ExclusionExpert):
    """波动震荡排除。

    - 高低交替明显：按震荡方向外推，杀掉反向极端数字。
    - 波动大且无序：杀中间带（震荡杀中）。
    - 波动极小：杀边端 0/1/8/9（盘整杀边）。
    """

    expert_id = "oscillation"
    title = "波动震荡"

    def propose(self, window: Sequence[DrawRecord]) -> ExpertExclusion:
        cfg = self.cfg
        out = _empty_sets(cfg)
        if len(window) < 6:
            return self._pack(out, 0.15, "short-window")

        notes: list[str] = []
        conf_acc = 0.0

        for pos0 in range(cfg.n_positions):
            pos = pos0 + 1
            xs = _series(window, pos0)
            diffs = [xs[i] - xs[i - 1] for i in range(1, len(xs))]
            signs = [1 if d > 0 else (-1 if d < 0 else 0) for d in diffs]
            nz = [s for s in signs if s != 0]
            altern = 0.0
            if len(nz) >= 3:
                flips = sum(1 for a, b in zip(nz, nz[1:]) if a == -b)
                altern = flips / (len(nz) - 1)
            vol = statistics.pstdev(xs)

            if altern >= 0.62 and signs[-1] != 0:
                # 预测下一跳与上一跳反向
                direction = -signs[-1]
                last = xs[-1]
                if direction > 0:
                    kill = {d for d in range(cfg.digit_min, last) if d <= last - 3}
                else:
                    kill = {d for d in range(last + 1, cfg.digit_max + 1) if d >= last + 3}
                out[pos] = _clip_set(kill, cfg)
                notes.append(f"P{pos}:osc={altern:.2f},dir={direction}")
                conf_acc += 0.55 + 0.35 * altern
            elif vol >= 2.8:
                mid = {3, 4, 5, 6}
                out[pos] = _clip_set(mid, cfg)
                notes.append(f"P{pos}:high-vol={vol:.2f},kill-mid")
                conf_acc += 0.45
            elif vol <= 1.15:
                edge = {cfg.digit_min, cfg.digit_min + 1, cfg.digit_max - 1, cfg.digit_max}
                out[pos] = _clip_set(edge, cfg)
                notes.append(f"P{pos}:low-vol={vol:.2f},kill-edge")
                conf_acc += 0.40
            else:
                notes.append(f"P{pos}:no-regime,vol={vol:.2f}")
                conf_acc += 0.15

        conf = conf_acc / cfg.n_positions
        return self._pack(out, conf, ";".join(notes))


class GapRecencyExpert(ExclusionExpert):
    """遗漏 / 重号排除：优先杀掉刚刚出现过的数字（短期不重假设）。

    若上期数字在窗口内重复率已经很高，则不再杀重（避免与数据打架）。
    """

    expert_id = "gap_recency"
    title = "遗漏重号"

    def propose(self, window: Sequence[DrawRecord]) -> ExpertExclusion:
        cfg = self.cfg
        out = _empty_sets(cfg)
        notes: list[str] = []
        conf_acc = 0.0

        for pos0 in range(cfg.n_positions):
            pos = pos0 + 1
            xs = _series(window, pos0)
            last = xs[-1]
            repeats = sum(1 for a, b in zip(xs, xs[1:]) if a == b)
            repeat_rate = repeats / max(1, len(xs) - 1)

            # 各位数字的当前遗漏
            last_seen: dict[int, int | None] = {
                d: None for d in range(cfg.digit_min, cfg.digit_max + 1)
            }
            for idx, val in enumerate(xs):
                last_seen[val] = idx
            end = len(xs) - 1
            gaps = {
                d: (end - seen) if seen is not None else len(xs)
                for d, seen in last_seen.items()
            }

            if repeat_rate >= 0.28:
                # 重号环境：改为杀超长遗漏（它们更可能补热，不应杀刚出号）
                long_gap = sorted(gaps.items(), key=lambda kv: (-kv[1], kv[0]))
                kill = {d for d, g in long_gap[: cfg.max_exclude_per_position] if g >= 6}
                out[pos] = _clip_set(kill, cfg)
                notes.append(f"P{pos}:repeatish,kill-long-gap={sorted(out[pos])}")
                conf_acc += 0.35
            else:
                # 默认杀重：上期号 + 遗漏=1 的号
                kill = {last}
                kill.update(d for d, g in gaps.items() if g == 1)
                out[pos] = _clip_set(kill, cfg)
                notes.append(f"P{pos}:kill-recent={sorted(out[pos])}")
                conf_acc += 0.55

        return self._pack(out, conf_acc / cfg.n_positions, ";".join(notes))


def default_experts(cfg: EngineConfig) -> list[ExclusionExpert]:
    """默认 5 专家池，顺序固定，便于复现。"""
    return [
        HotColdFrequencyExpert(cfg),
        MarkovTransitionExpert(cfg),
        AutocorrelationLagExpert(cfg),
        OscillationVolatilityExpert(cfg),
        GapRecencyExpert(cfg),
    ]


def score_expert(proposal: ExpertExclusion, actual: DrawRecord, cfg: EngineConfig) -> float:
    """事后评分：误杀直接 0 分；空提案给中性偏低分；安全且有杀伤给高分。

    该分数进入 Bandit 滑动窗口，而不是直接当「命中率」对外展示。
    对外命中率由路由看板单独按「是否误杀」统计。
    """
    total_kills = sum(len(s) for s in proposal.excluded.values())
    if total_kills == 0:
        return 0.32

    misses = 0
    for pos0, digit in enumerate(actual.digits):
        if digit in proposal.excluded.get(pos0 + 1, set()):
            misses += 1
    if misses:
        return 0.0

    density = min(1.0, total_kills / (cfg.n_positions * cfg.max_exclude_per_position))
    return 0.55 + 0.45 * density
