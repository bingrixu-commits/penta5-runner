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


def solve_cuopt(payload: Dict[str, Any]) -> Dict[str, Any]:
    from cuopt.linear_programming.problem import INTEGER, MAXIMIZE, Problem
    from cuopt.linear_programming.solver_settings import SolverSettings

    pos_need = int(payload.get("positionCount") or 3)
    hewei_need = int(payload.get("heweiCount") or 2)
    max_per_model = int(payload.get("maxPerModel") or 2)
    allow_same = bool(payload.get("allowSameExcludeDigit"))
    positions = list(payload.get("positionPool") or [])
    heweis = list(payload.get("heweiPool") or [])
    all_items = positions + heweis
    if len(positions) < pos_need or len(heweis) < hewei_need:
        return {"ok": False, "selectedIds": [], "objective": None, "solver": "cuopt", "meta": {"reason": "pool-short"}}

    problem = Problem("autoTop6Formal")
    vars_by_id = {}
    for item in all_items:
        iid = _item_id(item)
        vars_by_id[iid] = problem.addVariable(lb=0, ub=1, vtype=INTEGER, name=iid)

    problem.addConstraint(sum(vars_by_id[_item_id(i)] for i in positions) == pos_need, name="pos_count")
    problem.addConstraint(sum(vars_by_id[_item_id(i)] for i in heweis) == hewei_need, name="hewei_count")

    by_model: Dict[str, List[Any]] = {}
    for item in all_items:
        by_model.setdefault(_model_key(item), []).append(vars_by_id[_item_id(item)])
    for mk, xs in by_model.items():
        problem.addConstraint(sum(xs) <= max_per_model, name=f"model_{mk}")

    by_hewei: Dict[str, List[Any]] = {}
    for item in heweis:
        d = _hewei_digit(item)
        if d:
            by_hewei.setdefault(d, []).append(vars_by_id[_item_id(item)])
    for d, xs in by_hewei.items():
        problem.addConstraint(sum(xs) <= 1, name=f"hewei_digit_{d}")

    if not allow_same:
        by_ex: Dict[str, List[Any]] = {}
        for item in all_items:
            d = _exclude_digit(item)
            if d:
                by_ex.setdefault(d, []).append(vars_by_id[_item_id(item)])
        for d, xs in by_ex.items():
            problem.addConstraint(sum(xs) <= 1, name=f"exclude_digit_{d}")

    obj = sum(_linear_weight(item) * vars_by_id[_item_id(item)] for item in all_items)
    problem.setObjective(obj, sense=MAXIMIZE)
    settings = SolverSettings()
    settings.set_parameter("time_limit", 5)
    settings.set_parameter("mip_relative_gap", 0.0)
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
    for item in all_items:
        iid = _item_id(item)
        if float(vars_by_id[iid].getValue()) > 0.5:
            selected.append(iid)
    return {
        "ok": len(selected) == pos_need + hewei_need,
        "selectedIds": selected,
        "objective": float(problem.ObjValue),
        "solver": "cuopt",
        "meta": {"status": status, "allowSameExcludeDigit": allow_same},
    }


def solve(payload: Dict[str, Any]) -> Dict[str, Any]:
    # Prefer hard digit; soft only if hard fails.
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

        out = solve_enumerate(local)
        if out.get("ok"):
            out.setdefault("meta", {})["digitMode"] = "soft-digit" if allow_same else "hard-digit"
            out.setdefault("meta", {})["cuoptError"] = cuopt_err
            return out

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
