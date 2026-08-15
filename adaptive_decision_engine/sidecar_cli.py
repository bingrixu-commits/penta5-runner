"""只读 sidecar CLI：stdin JSON → 走期回放 + 当期决策 JSON。

输入由 Node 桥提供（history 默认最新在前）。stdout 只打一行 JSON。
不写回任何锁/杀；只报告与当期真实锁/杀的重叠与新增建议。
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .engine import AdaptiveDecisionEngine
from .types import DrawRecord, EngineConfig, ExclusionDecision


SIDECAR_NAME = "adaptive-exclusion"
SIDECAR_VERSION = "v1-readonly"


def _digit(value: Any) -> int | None:
    text = str(value).strip() if value is not None else ""
    if len(text) == 1 and text.isdigit():
        return int(text)
    return None


def parse_history(raw: list[Any], newest_first: bool) -> list[DrawRecord]:
    records: list[DrawRecord] = []
    for row in raw:
        if not isinstance(row, dict):
            continue
        nums = row.get("numbers") or []
        digits: list[int] = []
        for item in list(nums)[:4]:
            parsed = _digit(item)
            if parsed is None:
                digits = []
                break
            digits.append(parsed)
        if len(digits) != 4:
            continue
        period = str(row.get("period") or row.get("uid") or "")
        records.append(DrawRecord(period=period, digits=tuple(digits)))
    if newest_first:
        records.reverse()
    return records


def overlay(decision: ExclusionDecision, live_kills: dict[str, Any]) -> dict[str, Any]:
    live_pos = live_kills.get("position") or {}
    overlap: dict[str, list[int]] = {}
    novel: dict[str, list[int]] = {}
    for pos, digits in decision.excluded.items():
        key = str(pos)
        live_raw = live_pos.get(key, live_pos.get(pos, []))
        live_set = {int(x) for x in live_raw if _digit(x) is not None}
        side_set = {int(x) for x in digits}
        overlap[key] = sorted(side_set & live_set)
        novel[key] = sorted(side_set - live_set)
    return {
        "sidecar": SIDECAR_NAME,
        "version": SIDECAR_VERSION,
        "readOnly": True,
        "appliesToTip": False,
        "engineState": decision.engine_state,
        "decision": decision.to_dict(),
        "liveKills": live_kills,
        "overlap": overlap,
        "novel": novel,
    }


def run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    raw_history = payload.get("history") or []
    if not isinstance(raw_history, list):
        return {"ok": False, "error": "history-not-list", "sidecar": SIDECAR_NAME}
    newest_first = bool(payload.get("newest_first", True))
    records = parse_history(raw_history, newest_first)
    window = int(payload.get("replay_window") or 80)
    window = max(16, min(160, window))
    records = records[-window:]
    live_kills = payload.get("liveKills") if isinstance(payload.get("liveKills"), dict) else {}
    target_period = str(payload.get("targetPeriod") or "")
    if len(records) < 12:
        return {
            "ok": False,
            "error": f"history-short:{len(records)}",
            "sidecar": SIDECAR_NAME,
            "readOnly": True,
            "appliesToTip": False,
            "liveKills": live_kills,
        }

    cfg = EngineConfig()
    engine = AdaptiveDecisionEngine(cfg=cfg)
    if len(records) >= 2:
        engine.replay(records[:-1])
        # 用截至上一期的状态，对最后一期再 decide（与线上「预测下一期」对齐时：
        # 传入的 history 已含最新开奖，则对 targetPeriod 决策。
    decision = engine.decide(records, next_period=target_period or f"{records[-1].period}+")
    body = overlay(decision, live_kills)
    body["ok"] = True
    body["historyCount"] = len(records)
    body["basePeriod"] = records[-1].period
    body["targetPeriod"] = target_period or body["decision"].get("period_hint")
    return body


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:
        demo = {
            "newest_first": False,
            "replay_window": 24,
            "targetPeriod": "TEST+1",
            "history": [
                {"period": f"T{i:03d}", "numbers": [(i + p) % 10 for p in range(4)]}
                for i in range(20)
            ],
            "liveKills": {"position": {"1": [1], "2": [2], "3": [3], "4": [4]}, "hewei": {}},
        }
        out = run_payload(demo)
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
        sys.stdout.write("\n")
        return 0 if out.get("ok") else 2

    try:
        raw = sys.stdin.read()
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise ValueError("payload-not-object")
    except Exception as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": f"bad-json:{exc}", "sidecar": SIDECAR_NAME}))
        sys.stdout.write("\n")
        return 2

    try:
        out = run_payload(payload)
    except Exception as exc:
        out = {"ok": False, "error": f"engine:{exc}", "sidecar": SIDECAR_NAME, "readOnly": True}

    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.stdout.write("\n")
    out_path = ""
    if "--out" in args:
        try:
            out_path = args[args.index("--out") + 1]
        except Exception:
            out_path = ""
    if out_path:
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(out, fh, ensure_ascii=False)
                fh.write("\n")
        except Exception:
            pass
    return 0 if out.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
