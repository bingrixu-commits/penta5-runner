#!/usr/bin/env python3
"""Penta5 autoTop6 正式 3定位+2合尾（cuOpt MILP sidecar，离线选型）。

硬约束：
- 不读/不写 state.json seal
- 不调用 ensurePredictionFrameworkSnapshots
- 不改 RDS / panel
- 不接入 :3001 tip 主路径（主路径仍用 algorithms/auto_top6_milp_select.js 穷举）

求解顺序：NVIDIA cuOpt → 精确穷举回退（与 JS 同构：硬同码错开 → 放宽）。
本机无 GPU 时走 enumerate，不装 cuOpt 也能自检。

运行：
  python3 scripts/sidecars/cuopt_autotop6_select.py --fixture ok
  python3 scripts/sidecars/cuopt_autotop6_select.py --fixture soft-digit
  python3 scripts/sidecars/cuopt_autotop6_select.py --payload-json /tmp/pool.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]
SOLVER_PATH = ROOT / "algorithms" / "auto_top6_milp.py"


def _load_solver():
    spec = importlib.util.spec_from_file_location("auto_top6_milp", SOLVER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot-load-solver:{SOLVER_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _item(kind: str, key: str, code: str, stable: float, rate300: float, rate60: float) -> Dict[str, Any]:
    return {
        "kind": kind,
        "key": key,
        "side": "recommend",
        "code": code,
        "stableScore": stable,
        "rate300": rate300,
        "rate60": rate60,
    }


def fixture_ok() -> Dict[str, Any]:
    """硬同码错开可行：最优应避开 ball2 的同码 1，选 ball1/3/4 + 合尾 5/6。"""
    return {
        "period": "demo-ok",
        "positionCount": 3,
        "heweiCount": 2,
        "maxPerModel": 2,
        "positionPool": [
            _item("position", "ball1", "1", 0.90, 80, 70),
            _item("position", "ball2", "1", 0.80, 75, 60),
            _item("position", "ball3", "2", 0.70, 70, 50),
            _item("position", "ball4", "3", 0.60, 60, 40),
            _item("position", "ball1", "8", 0.50, 50, 30),
        ],
        "heweiPool": [
            _item("hewei", "12", "5", 0.90, 80, 70),
            _item("hewei", "34", "6", 0.80, 70, 60),
            _item("hewei", "12", "7", 0.40, 40, 20),
        ],
    }


def fixture_soft_digit() -> Dict[str, Any]:
    """硬约束不可行：仅 3 个定位且其中两个同杀码 1，必须放宽同码错开。"""
    return {
        "period": "demo-soft-digit",
        "positionCount": 3,
        "heweiCount": 2,
        "maxPerModel": 2,
        "positionPool": [
            _item("position", "ball1", "1", 0.90, 80, 70),
            _item("position", "ball2", "1", 0.80, 75, 60),
            _item("position", "ball3", "2", 0.70, 70, 50),
        ],
        "heweiPool": [
            _item("hewei", "12", "5", 0.90, 80, 70),
            _item("hewei", "34", "6", 0.80, 70, 60),
        ],
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Penta5 autoTop6 cuOpt sidecar (offline)")
    parser.add_argument(
        "--fixture",
        choices=("ok", "soft-digit"),
        default="ok",
        help="内置样例：ok=硬同码可行，soft-digit=必须放宽同码",
    )
    parser.add_argument("--payload-json", help="从 JSON 文件读 positionPool/heweiPool")
    parser.add_argument(
        "--enumerate-only",
        action="store_true",
        help="跳过 cuOpt，只跑与 JS 同构的穷举（本机无 GPU 用）",
    )
    args = parser.parse_args(argv)

    if args.payload_json:
        with open(args.payload_json, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    else:
        payload = fixture_soft_digit() if args.fixture == "soft-digit" else fixture_ok()

    solver = _load_solver()
    if args.enumerate_only:
        # 与正式 solve() 一样：先硬后软
        out = None
        for allow_same in (False, True):
            local = dict(payload)
            local["allowSameExcludeDigit"] = allow_same
            out = solver.solve_enumerate(local)
            if out.get("ok"):
                out.setdefault("meta", {})["digitMode"] = "soft-digit" if allow_same else "hard-digit"
                break
        if out is None:
            out = {"ok": False, "solver": "enumerate", "meta": {"reason": "infeasible"}}
    else:
        out = solver.solve(payload if isinstance(payload, dict) else {})

    out.setdefault("meta", {})
    out["meta"]["period"] = payload.get("period")
    out["meta"]["sidecar"] = "cuopt_autotop6_select"
    out["meta"]["wiredToTip"] = False
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
