"""只读 sidecar CLI：stdin JSON → 走期回放 + 当期决策 JSON。

输入由 Node 桥提供（history 默认最新在前）。stdout 只打一行 JSON。
不写回任何锁/杀；只报告与当期真实锁/杀的重叠与新增建议。
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

from .engine import AdaptiveDecisionEngine
from .types import DrawRecord, EngineConfig, ExclusionDecision


SIDECAR_NAME = "adaptive-exclusion"
SIDECAR_VERSION = "v1-readonly"


def _digit(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    if len(text) == 1 and "0" <= text <= "9":
        return int(text)
    return None


def _period(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("period-type")
    text = str(value)
    if len(text) != 11 or any(c < "0" or c > "9" for c in text):
        raise ValueError("period-format")
    datetime.strptime(text[:8], "%Y%m%d")
    if not 1 <= int(text[8:]) <= 288:
        raise ValueError("period-sequence")
    return text


def _next_period(period: str) -> str:
    sequence = int(period[8:])
    if sequence < 288:
        return f"{period[:8]}{sequence + 1:03d}"
    day = datetime.strptime(period[:8], "%Y%m%d") + timedelta(days=1)
    return f"{day:%Y%m%d}001"


def parse_history(raw: list[Any], newest_first: bool) -> list[DrawRecord]:
    records: list[DrawRecord] = []
    seen: set[str] = set()
    for index, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"row-type:{index}")
        nums = row.get("numbers")
        if not isinstance(nums, list) or len(nums) < 4:
            raise ValueError(f"numbers-shape:{index}")
        digits: list[int] = []
        for item in nums[:4]:
            parsed = _digit(item)
            if parsed is None:
                raise ValueError(f"invalid-digit:{index}")
            digits.append(parsed)
        period = _period(row.get("period") if row.get("period") is not None else row.get("uid"))
        if period in seen:
            raise ValueError(f"duplicate-period:{period}")
        seen.add(period)
        records.append(DrawRecord(period=period, digits=tuple(digits)))
    if newest_first:
        records.reverse()
    if any(previous.period >= current.period for previous, current in zip(records, records[1:])):
        raise ValueError("history-not-chronological")
    if any(_next_period(previous.period) != current.period for previous, current in zip(records, records[1:])):
        raise ValueError("history-period-gap")
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
    def fail(error: str) -> dict[str, Any]:
        return {"ok": False, "error": error, "sidecar": SIDECAR_NAME,
                "readOnly": True, "appliesToTip": False}

    raw_history = payload.get("history", [])
    if not isinstance(raw_history, list):
        return fail("history-not-list")
    newest_first = payload.get("newest_first", True)
    if not isinstance(newest_first, bool):
        return fail("newest-first-not-boolean")
    try:
        records = parse_history(raw_history, newest_first)
        raw_window = payload.get("replay_window", 80)
        if isinstance(raw_window, bool) or not isinstance(raw_window, (int, float)) or int(raw_window) != raw_window:
            return fail("replay-window-not-integer")
        window = int(raw_window)
        target_period = _period(payload.get("targetPeriod"))
    except (ValueError, TypeError, OverflowError) as exc:
        return fail(f"invalid-input:{exc}")
    window = max(16, min(160, window))
    records = records[-window:]
    live_kills = payload.get("liveKills") if isinstance(payload.get("liveKills"), dict) else {}
    if len(records) < 12:
        return {
            "ok": False,
            "error": f"history-short:{len(records)}",
            "sidecar": SIDECAR_NAME,
            "readOnly": True,
            "appliesToTip": False,
            "liveKills": live_kills,
        }
    if target_period != _next_period(records[-1].period):
        return fail("target-not-next-period")

    cfg = EngineConfig()
    engine = AdaptiveDecisionEngine(cfg=cfg)
    # Replay decides before each actual is observed, including the latest draw.
    # The next decision must use router/risk state updated through that latest draw.
    engine.replay(records)
    decision = engine.decide(records, next_period=target_period)
    body = overlay(decision, live_kills)
    body["ok"] = True
    body["historyCount"] = len(records)
    body["basePeriod"] = records[-1].period
    body["targetPeriod"] = target_period
    body["generatedAt"] = datetime.now(timezone.utc).isoformat()
    return body


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--self-test" in args:
        demo = {
            "newest_first": False,
            "replay_window": 24,
            "targetPeriod": "20260101021",
            "history": [
                {"period": f"20260101{i+1:03d}", "numbers": [(i + p) % 10 for p in range(4)]}
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
