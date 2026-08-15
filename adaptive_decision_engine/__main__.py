"""主入口：合成多体制数据，演示路由切换、主动空仓与熔断。

运行（在仓库根目录）::

    python -m adaptive_decision_engine
"""

from __future__ import annotations

import random
from collections import Counter

from .engine import AdaptiveDecisionEngine
from .types import DrawRecord, EngineConfig


def _rec(idx: int, digits: tuple[int, ...]) -> DrawRecord:
    return DrawRecord(period=f"S{idx:04d}", digits=digits)


def build_synthetic(seed: int = 7) -> list[DrawRecord]:
    """四段体制 + 一段对抗，用来逼出切换 / SKIP / HALT。

    1. 马尔可夫：各位约 +1 mod 10
    2. 持续热号：各位锁在 {0,1,2}（杀热专家会连续误杀）
    3. 均匀混沌：应触发主动 SKIP
    4. 震荡：1↔8 交替（震荡专家应抬头）
    5. 对抗：连续开出 0/1（马尔可夫常杀的尾部），逼出误杀与熔断
    """
    rng = random.Random(seed)
    records: list[DrawRecord] = []
    idx = 1

    def push(digits: tuple[int, ...]) -> None:
        nonlocal idx
        records.append(_rec(idx, digits))
        idx += 1

    # --- 体制 1：马尔可夫链 ---
    state = [1, 3, 5, 7]
    for _ in range(36):
        nxt = []
        for s in state:
            nxt.append((s + 1) % 10 if rng.random() > 0.12 else rng.randint(0, 9))
        state = nxt
        push(tuple(state))

    # --- 体制 2：热号持续 ---
    hot = (0, 1, 2)
    for _ in range(28):
        push(tuple(rng.choice(hot) for _ in range(4)))

    # --- 体制 3：混沌 ---
    for _ in range(24):
        push(tuple(rng.randint(0, 9) for _ in range(4)))

    # --- 体制 4：震荡 ---
    for i in range(22):
        a, b = (1, 8) if i % 2 == 0 else (8, 1)
        push((a, b, a, b))

    # --- 体制 5：对抗（专打「杀最低转移 / 杀边」）---
    for i in range(14):
        push((0, 1, 0, 1) if i % 2 == 0 else (1, 0, 1, 0))

    return records


def _fmt_excl(excluded: dict[int, list[int]]) -> str:
    parts = [f"P{p}:{sorted(v)}" for p, v in excluded.items() if v]
    return ",".join(parts) if parts else "-"


def _top_weight(weights: dict[str, float]) -> str:
    if not weights:
        return "-"
    eid = max(weights, key=lambda k: weights[k])
    return f"{eid}={weights[eid]:.2f}"


def run_demo() -> int:
    cfg = EngineConfig(
        min_history=12,
        window_min=8,
        window_max=28,
        bandit_window=18,
        bandit_algo="ucb",
        fusion_mode="blend",
        vote_threshold=0.38,
        confidence_threshold=0.36,
        entropy_chaos_threshold=0.92,
        min_consensus=0.12,
        consecutive_miss_limit=3,
        observe_periods=4,
        max_drawdown=4.0,
        max_exclude_per_position=2,
    )
    engine = AdaptiveDecisionEngine(cfg=cfg, seed=20260815)
    records = build_synthetic()
    decisions = engine.replay(records)

    counts = Counter(d.action for d in decisions)
    print("=" * 78)
    print("自适应排除引擎演示（合成四位 0-9，五段体制）")
    print("=" * 78)
    print(
        f"总期数={len(records)}  EXCLUDE={counts.get('EXCLUDE', 0)}  "
        f"SKIP={counts.get('SKIP', 0)}  HALT={counts.get('HALT', 0)}"
    )
    print()
    print(f"{'期号':<8}{'开奖':<12}{'动作':<8}{'窗':>3}  {'置信':>5}  主导专家  排除 / 原因")
    print("-" * 78)

    # 只打印「有信息量」的行：动作变化、EXCLUDE、HALT、体制边界附近
    regime_cuts = {1, 36, 37, 64, 65, 88, 89, 110, 111, len(records)}
    last_action = ""
    last_leader = ""
    printed = 0
    for rec, dec in zip(records, decisions):
        leader = _top_weight(dec.expert_weights)
        interesting = (
            int(rec.period[1:]) in regime_cuts
            or dec.action != last_action
            or dec.action == "HALT"
            or (
                dec.action == "EXCLUDE"
                and leader.split("=")[0] != last_leader.split("=")[0]
            )
        )
        if interesting:
            draw = "".join(str(d) for d in rec.digits)
            excl = _fmt_excl(dec.excluded) if dec.action == "EXCLUDE" else dec.reason[:36]
            print(
                f"{rec.period:<8}{draw:<12}{dec.action:<8}{dec.window_size:>3}  "
                f"{dec.confidence:5.2f}  {leader:<16} {excl}"
            )
            printed += 1
        last_action = dec.action
        last_leader = leader

    print("-" * 78)
    print(f"（上表为抽样 {printed} 行：动作切换 / 主导专家切换 / 熔断 / 体制边界）")
    print()

    # 分体制统计
    bounds = [
        ("马尔可夫 +1", 0, 36),
        ("热号 012", 36, 64),
        ("均匀混沌", 64, 88),
        ("震荡 1↔8", 88, 110),
        ("对抗 01", 110, len(records)),
    ]
    print("分体制动作分布")
    for name, lo, hi in bounds:
        chunk = decisions[lo:hi]
        c = Counter(d.action for d in chunk)
        # 该段末尾看板
        last = chunk[-1] if chunk else None
        leader = _top_weight(last.expert_weights) if last else "-"
        wsize = last.window_size if last else 0
        print(
            f"  {name:<12} n={hi-lo:<3}  "
            f"EX={c.get('EXCLUDE', 0):<3} SKIP={c.get('SKIP', 0):<3} "
            f"HALT={c.get('HALT', 0):<3}  末窗={wsize:<3} 末主导={leader}"
        )

    print()
    print("期末专家看板（滑动窗口命中率 / UCB / 权重）")
    board = engine.router.snapshot().board
    for row in sorted(board, key=lambda r: -r.weight):
        bar = "#" * int(round(row.weight * 20))
        print(
            f"  {row.expert_id:<14} hit={row.hit_rate:.2f}  "
            f"rew={row.mean_reward:.2f}  ucb={row.ucb:.2f}  "
            f"w={row.weight:.2f} {bar}"
        )

    risk = engine.risk.snapshot()
    print()
    print(f"风控期末：{risk.note}")
    print()
    print("接口示例：decision.to_dict() 最后一期 =")
    import json

    print(json.dumps(decisions[-1].to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_demo())
