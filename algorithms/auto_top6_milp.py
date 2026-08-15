#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""autoTop6 formal 3-position + 2-hewei MILP selector.

stdin JSON:
  {
    "positionPool": [{id, kind, key, side, code, stableScore, rate300, rate60}, ...],
    "heweiPool": [...],
    "positionCount": 3,
    "heweiCount": 2,
    "maxPerModel": 2,
    "allowSameExcludeDigit": false
  }

stdout JSON: {ok, selectedIds, objective, solver, meta}

Solver order: NVIDIA cuOpt → exact enumerate fallback.
"""

from __future__ import annotations

import itertools
import json
import sys
from typing import Any, Dict, List, Tuple


def _linear_weight(item: Dict[str, Any]) -> float:
    return (
        10000.0 * float(item.get("stableScore") or 0)
        + 100.0 * float(item.get("rate300") or 0)
        + float(item.get("rate60") or 0)
    )


def _hewei_digit(item: Dict[str, Any]) -> str:
    if item.get("kind") != "hewei":
        return ""
    if (item.get("side") or "recommend") == "backup":
        return ""
    digits = "".join(ch for ch in str(item.get("code") or "") if ch.isdigit())
    return digits if len(digits) == 1 else ""


def _exclude_digit(item: Dict[str, Any]) -> str:
    kind = item.get("kind")
    if kind not in ("position", "hewei"):
        return ""
    if (item.get("side") or "recommend") == "backup" and kind == "position":
        return ""
    digits = "".join(ch for ch in str(item.get("code") or "") if ch.isdigit())
    return digits if len(digits) == 1 else ""


def _model_key(item: Dict[str, Any]) -> str:
    return f"{item.get('kind')}:{item.get('key')}"


def _item_id(item: Dict[str, Any]) -> str:
    if item.get("id"):
        return str(item["id"])
    return f"{item.get('kind')}:{item.get('key')}:{item.get('side') or 'recommend'}:{item.get('code')}"


def _feasible(selected: List[Dict[str, Any]], allow_same_exclude: bool, max_per_model: int) -> bool:
    model_count: Dict[str, int] = {}
    hewei_digits = set()
    exclude_digits = set()
    for item in selected:
        mk = _model_key(item)
        model_count[mk] = model_count.get(mk, 0) + 1
        if model_count[mk] > max_per_model:
            return False
        hd = _hewei_digit(item)
        if hd:
            if hd in hewei_digits:
                return False
            hewei_digits.add(hd)
        if not allow_same_exclude:
            ed = _exclude_digit(item)
            if ed:
                if ed in exclude_digits:
                    return False
                exclude_digits.add(ed)
    return True


def solve_enumerate(payload: Dict[str, Any]) -> Dict[str, Any]:
    pos_need = int(payload.get("positionCount") or 3)
    hewei_need = int(payload.get("heweiCount") or 2)
    max_per_model = int(payload.get("maxPerModel") or 2)
    allow_same = bool(payload.get("allowSameExcludeDigit"))
    positions = list(payload.get("positionPool") or [])
    heweis = list(payload.get("heweiPool") or [])
    if len(positions) < pos_need or len(heweis) < hewei_need:
        return {
            "ok": False,
            "selectedIds": [],
            "objective": None,
            "solver": "enumerate",
            "meta": {"reason": "pool-short"},
        }

    best = None
    best_w = float("-inf")
    checked = 0
    for pos_set in itertools.combinations(positions, pos_need):
        for hewei_set in itertools.combinations(heweis, hewei_need):
            selected = list(pos_set) + list(hewei_set)
            checked += 1
            if not _feasible(selected, allow_same, max_per_model):
                continue
            w = sum(_linear_weight(item) for item in selected)
            if w > best_w:
                best_w = w
                best = selected

    if not best:
        return {
            "ok": False,
            "selectedIds": [],
            "objective": None,
            "solver": "enumerate",
            "meta": {"reason": "infeasible", "checked": checked},
        }
    return {
        "ok": True,
        "selectedIds": [_item_id(item) for item in best],
        "objective": best_w,
        "solver": "enumerate",
        "meta": {"checked": checked, "allowSameExcludeDigit": allow_same},
    }


def _safe_name(prefix: str, raw: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(raw))
    return f"{prefix}_{cleaned}"[:80]


def _sum_eq(problem: Any, variables: List[Any], rhs: int, name: str) -> None:
    if not variables:
        if rhs != 0:
            raise ValueError(f"empty-constraint:{name}")
        return
    try:
        from cuopt.linear_programming.problem import LinearExpression

        problem.addConstraint(
            LinearExpression(variables, [1.0] * len(variables), 0.0) == rhs,
            name=name,
        )
    except Exception:  # noqa: BLE001 — algebraic fallback for small pools
        problem.addConstraint(sum(variables) == rhs, name=name)


def _sum_le(problem: Any, variables: List[Any], rhs: int, name: str) -> None:
    if not variables:
        return
    try:
        from cuopt.linear_programming.problem import LinearExpression

        problem.addConstraint(
            LinearExpression(variables, [1.0] * len(variables), 0.0) <= rhs,
            name=name,
        )
    except Exception:  # noqa: BLE001
        problem.addConstraint(sum(variables) <= rhs, name=name)


def solve_cuopt(payload: Dict[str, Any]) -> Dict[str, Any]:
    from cuopt.linear_programming.problem import INTEGER, MAXIMIZE, Problem
    from cuopt.linear_programming.solver_settings import SolverSettings

    pos_need = int(payload.get("positionCount") or 3)
    hewei_need = int(payload.get("heweiCount") or 2)
    max_per_model = int(payload.get("maxPerModel") or 2)
    allow_same = bool(payload.get("allowSameExcludeDigit"))
    time_limit = float(payload.get("timeLimit") or 5)
    positions = list(payload.get("positionPool") or [])
    heweis = list(payload.get("heweiPool") or [])
    if len(positions) < pos_need or len(heweis) < hewei_need:
        return {"ok": False, "selectedIds": [], "objective": None, "solver": "cuopt", "meta": {"reason": "pool-short"}}

    tagged: List[Tuple[str, Dict[str, Any], str]] = []
    for idx, item in enumerate(positions):
        tagged.append(("position", item, f"p{idx}"))
    for idx, item in enumerate(heweis):
        tagged.append(("hewei", item, f"h{idx}"))

    problem = Problem("autoTop6Formal")
    var_by_tag = {}
    for _kind, item, tag in tagged:
        var_by_tag[tag] = problem.addVariable(lb=0, ub=1, vtype=INTEGER, name=tag)

    _sum_eq(problem, [var_by_tag[t] for k, _i, t in tagged if k == "position"], pos_need, "pos_count")
    _sum_eq(problem, [var_by_tag[t] for k, _i, t in tagged if k == "hewei"], hewei_need, "hewei_count")

    by_model: Dict[str, List[Any]] = {}
    for _kind, item, tag in tagged:
        by_model.setdefault(_model_key(item), []).append(var_by_tag[tag])
    for mk, xs in by_model.items():
        _sum_le(problem, xs, max_per_model, _safe_name("model", mk))

    by_hewei: Dict[str, List[Any]] = {}
    for kind, item, tag in tagged:
        if kind != "hewei":
            continue
        d = _hewei_digit(item)
        if d:
            by_hewei.setdefault(d, []).append(var_by_tag[tag])
    for d, xs in by_hewei.items():
        _sum_le(problem, xs, 1, f"hewei_digit_{d}")

    if not allow_same:
        by_ex: Dict[str, List[Any]] = {}
        for _kind, item, tag in tagged:
            d = _exclude_digit(item)
            if d:
                by_ex.setdefault(d, []).append(var_by_tag[tag])
        for d, xs in by_ex.items():
            _sum_le(problem, xs, 1, f"exclude_digit_{d}")

    obj = sum(_linear_weight(item) * var_by_tag[tag] for _kind, item, tag in tagged)
    problem.setObjective(obj, sense=MAXIMIZE)
    settings = SolverSettings()
    settings.set_parameter("time_limit", time_limit)
    settings.set_parameter("mip_relative_gap", 0.0)
    settings.set_parameter("log_to_console", 0)
    problem.solve(settings)

    status = getattr(problem.Status, "name", str(problem.Status))
    if status not in ("Optimal", "FeasibleFound"):
        return {
            "ok": False,
            "selectedIds": [],
            "objective": None,
            "solver": "cuopt",
            "meta": {"reason": "status", "status": status},
        }

    selected = []
    for _kind, item, tag in tagged:
        if float(var_by_tag[tag].getValue()) > 0.5:
            selected.append(_item_id(item))
    return {
        "ok": len(selected) == pos_need + hewei_need,
        "selectedIds": selected,
        "objective": float(problem.ObjValue),
        "solver": "cuopt",
        "meta": {"status": status, "allowSameExcludeDigit": allow_same},
    }


def solve(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Prefer hard digit; soft only if hard fails.
    # cuoptOnly: Node 桥专用，失败不穷举（由 JS 带 comboScore 回退）。
    cuopt_only = bool(payload.get("cuoptOnly"))
    cuopt_err = None
    for allow_same in (False, True):
        local = dict(payload)
        local["allowSameExcludeDigit"] = allow_same
        try:
            out = solve_cuopt(local)
            if out.get("ok"):
                out.setdefault("meta", {})["digitMode"] = "soft-digit" if allow_same else "hard-digit"
                return out
        except Exception as exc:  # noqa: BLE001 — fall back
            cuopt_err = str(exc)
        else:
            cuopt_err = out.get("meta", {}).get("reason") if isinstance(out, dict) else "cuopt-failed"

        if cuopt_only:
            continue

        out = solve_enumerate(local)
        if out.get("ok"):
            out.setdefault("meta", {})["digitMode"] = "soft-digit" if allow_same else "hard-digit"
            out.setdefault("meta", {})["cuoptError"] = cuopt_err
            return out

    if cuopt_only:
        return {
            "ok": False,
            "selectedIds": [],
            "objective": None,
            "solver": "cuopt",
            "meta": {"reason": "cuopt-unavailable", "cuoptError": cuopt_err},
        }

    return {
        "ok": False,
        "selectedIds": [],
        "objective": None,
        "solver": "none",
        "meta": {"reason": "infeasible"},
    }


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        json.dump({"ok": False, "error": f"bad-json:{exc}"}, sys.stdout, ensure_ascii=False)
        return 1
    out = solve(payload if isinstance(payload, dict) else {})
    json.dump(out, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
