"""自适应变长滑动窗口 + 分布变化 / 混沌检测。

窗口只服务专家看盘：波动大、熵高时缩短，以跟上短期结构；
平稳时拉长，避免被单期噪音带跑。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Sequence

from .types import DrawRecord, EngineConfig


def _modular_diff_entropy_norm(
    records: Sequence[DrawRecord],
    n_positions: int,
    digit_count: int,
) -> float:
    """各位模步进的归一化熵。+1 链会接近 0，均匀乱数接近 1。"""
    if len(records) < 3:
        return 1.0
    max_h = math.log(digit_count, 2) or 1.0
    norms: list[float] = []
    for pos0 in range(n_positions):
        diffs = [
            (cur.digits[pos0] - prev.digits[pos0]) % digit_count
            for prev, cur in zip(records[:-1], records[1:])
        ]
        norms.append(_digit_entropy(diffs, digit_count) / max_h)
    return statistics.fmean(norms) if norms else 1.0


def _mean_transition_entropy_norm(
    records: Sequence[DrawRecord],
    n_positions: int,
    digit_count: int,
) -> float:
    """各位「上期→本期」条件分布的平均归一化熵。转移越尖，值越低。"""
    if len(records) < 3:
        return 1.0
    max_h = math.log(digit_count, 2) or 1.0
    norms: list[float] = []
    for pos0 in range(n_positions):
        counts = [[1] * digit_count for _ in range(digit_count)]  # Laplace
        for prev, cur in zip(records[:-1], records[1:]):
            src, dst = prev.digits[pos0], cur.digits[pos0]
            if 0 <= src < digit_count and 0 <= dst < digit_count:
                counts[src][dst] += 1
        last = records[-1].digits[pos0]
        if not (0 <= last < digit_count):
            continue
        row = counts[last]
        total = sum(row)
        ent = 0.0
        for c in row:
            p = c / total
            ent -= p * math.log(p, 2)
        norms.append(ent / max_h)
    return statistics.fmean(norms) if norms else 1.0


def _digit_entropy(values: Sequence[int], digit_count: int) -> float:
    """经验分布熵（bit）。空序列返回 0。"""
    if not values:
        return 0.0
    counts = [0] * digit_count
    # 假定数字已是 0..digit_count-1 或可映射到该范围；调用方保证合法。
    lo = min(values)
    # 用真实值做桶：0-9 直接索引
    for v in values:
        if 0 <= v < digit_count:
            counts[v] += 1
        else:
            # 非 0 起始的玩法：退回哈希桶
            counts[(v - lo) % digit_count] += 1
    n = len(values)
    ent = 0.0
    for c in counts:
        if c <= 0:
            continue
        p = c / n
        ent -= p * math.log(p, 2)
    return ent


@dataclass
class WindowAssessment:
    """窗口评估快照，供置信度模块复用，避免重复扫历史。"""

    window_size: int
    entropy: float
    entropy_norm: float
    volatility: float
    stress: float
    chaos: bool
    change_detected: bool
    note: str


class AdaptiveWindowController:
    """根据熵与波动度插值窗口，并用 Page-Hinkley 侦测均值漂移。"""

    def __init__(self, cfg: EngineConfig) -> None:
        self.cfg = cfg
        self._ph = 0.0
        self._vol_mean = 0.0
        self._n = 0
        self._prev_digits: tuple[int, ...] | None = None
        # Page-Hinkley 打在「期际绝对位移」上，避免 +1 链把水位均值一路推高。
        self._ph_delta = 0.12
        self._ph_lambda = 2.8
        self._fresh_change = False

    def reset(self) -> None:
        self._ph = 0.0
        self._vol_mean = 0.0
        self._n = 0
        self._prev_digits = None
        self._fresh_change = False

    def observe_period(self, record: DrawRecord) -> None:
        """每期开奖后更新变化检测器（与窗口长度计算解耦）。"""
        if self._prev_digits is None:
            self._prev_digits = record.digits
            return
        step = sum(abs(a - b) for a, b in zip(record.digits, self._prev_digits)) / len(
            record.digits
        )
        self._prev_digits = record.digits
        self._n += 1
        self._vol_mean += (step - self._vol_mean) / self._n
        self._ph = max(0.0, self._ph + (step - self._vol_mean - self._ph_delta))
        if self._ph >= self._ph_lambda:
            self._fresh_change = True
            self._ph = 0.0
            self._n = 0
            self._vol_mean = step
        else:
            self._fresh_change = False

    def change_flag(self) -> bool:
        return self._fresh_change

    def assess(self, history: Sequence[DrawRecord]) -> WindowAssessment:
        cfg = self.cfg
        # 先用一个探头窗口估计「现在有多乱」，再决定真正窗口
        probe_n = min(len(history), max(cfg.window_min, 12))
        probe = history[-probe_n:]
        flat = [d for rec in probe for d in rec.digits]
        digit_count = cfg.digit_count()
        occ_ent = _digit_entropy(flat, digit_count)
        max_ent = math.log(digit_count, 2)
        occ_norm = occ_ent / max_ent if max_ent > 0 else 0.0
        trans_norm = _mean_transition_entropy_norm(probe, cfg.n_positions, digit_count)
        diff_norm = _modular_diff_entropy_norm(probe, cfg.n_positions, digit_count)
        # 占用均匀但步进很尖（+1 链）不是混沌；乱盘要「占用 + 步进」都散
        ent = occ_ent
        ent_norm = 0.35 * occ_norm + 0.25 * trans_norm + 0.40 * diff_norm

        pos_stds: list[float] = []
        for pos0 in range(cfg.n_positions):
            xs = [rec.digits[pos0] for rec in probe]
            pos_stds.append(statistics.pstdev(xs) if len(xs) > 1 else 0.0)
        # 0-9 均匀分布标准差约 2.87，用作归一化上限
        vol = statistics.fmean(pos_stds) if pos_stds else 0.0
        vol_norm = min(1.0, vol / 2.87)

        stress = 0.55 * ent_norm + 0.45 * vol_norm
        span = cfg.window_max - cfg.window_min
        raw = cfg.window_max - stress * span
        window_size = int(round(raw))
        window_size = max(cfg.window_min, min(cfg.window_max, window_size))
        window_size = min(window_size, len(history))

        change = self.change_flag()
        # 混沌只看分布是否接近均匀；体制切换是「变盘」不是「乱盘」。
        chaos = ent_norm >= cfg.entropy_chaos_threshold

        note = (
            f"H={ent:.2f}({ent_norm:.2f}),vol={vol:.2f},stress={stress:.2f},"
            f"W={window_size},PH={self._ph:.2f},change={change}"
        )
        return WindowAssessment(
            window_size=window_size,
            entropy=ent,
            entropy_norm=ent_norm,
            volatility=vol,
            stress=stress,
            chaos=chaos,
            change_detected=change,
            note=note,
        )
