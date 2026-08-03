#!/usr/bin/env python3
"""
软筛选演示（生产端 lottery_filter.py）：
对比硬杀 excluded=True vs soft_filter_combinations。

运行：
  cd algorithms && python3 demo_soft_filter.py
  python3 demo_soft_filter.py --draws 1234,1111,2581,5678
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from lottery_filter import (  # noqa: E402
    SAFE_RETENTION_DEFAULT,
    matches_excluded_combo,
    soft_filter_combinations,
)


DEFAULT_DRAWS = [
    "1234",
    "1111",
    "1357",
    "2468",
    "0000",
    "9999",
    "1123",
    "5678",
    "2581",
    "3472",
    "6813",
    "0945",
]


def hard_kill_summary(draws, filter_result=None):
    kept = []
    killed = []
    for draw in draws:
        row = matches_excluded_combo(draw, filter_result)
        code = "".join(str(d) for d in row["draw"])
        payload = {
            "normalized": code,
            "excluded": row["excluded"],
            "triggered_rules": row.get("triggered_rules") or [],
            "risk_score": row.get("risk_score"),
        }
        if row["excluded"]:
            killed.append(payload)
        else:
            kept.append(payload)
    return kept, killed


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="演示软筛选 vs 硬杀号（生产端）")
    parser.add_argument("--draws", help="逗号分隔号码")
    parser.add_argument("--retention", type=float, default=SAFE_RETENTION_DEFAULT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    draws = (
        [x.strip() for x in args.draws.split(",") if x.strip()]
        if args.draws
        else list(DEFAULT_DRAWS)
    )

    # 无动态 filter 时仍用静态极值规则（matches_excluded_combo 内置默认）
    hard_kept, hard_killed = hard_kill_summary(draws, None)
    soft = soft_filter_combinations(draws, result=None, retention_ratio=args.retention)

    report = {
        "input_count": len(draws),
        "hard_kill": {
            "kept": len(hard_kept),
            "killed": len(hard_killed),
            "kept_codes": [r["normalized"] for r in hard_kept],
            "killed_codes": [
                {"code": r["normalized"], "rules": r["triggered_rules"]} for r in hard_killed
            ],
        },
        "soft_risk": {
            "retention_ratio": soft["retention_ratio"],
            "min_keep": soft["min_keep"],
            "counts": soft["counts"],
            "ranked": [
                {
                    "rank": r["rank"],
                    "code": r["normalized"],
                    "risk_score": r["risk_score"],
                    "risk_level": r["risk_level"],
                    "triggered_rules": r["triggered_rules"],
                    "decision": r.get("decision"),
                }
                for r in soft["ranked"]
            ],
            "retained_codes": [r["normalized"] for r in soft["retained"]],
            "dropped_codes": [r["normalized"] for r in soft["dropped"]],
        },
        "delta": {
            "extra_saved_vs_hard": max(0, soft["counts"]["retained"] - len(hard_kept)),
        },
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print("=" * 64)
    print("硬杀号 vs 软筛选（penta5-runner / lottery_filter.py）")
    print("=" * 64)
    print(f"输入: {len(draws)}  硬杀保留 {len(hard_kept)}  软筛选保留 {soft['counts']['retained']}")
    for row in soft["ranked"]:
        print(
            f"  #{row['rank']:02d} {row['normalized']}  "
            f"score={row['risk_score']}  {row['risk_label']}  "
            f"{row.get('decision')}  {row['triggered_rules']}"
        )
    print(f"软筛选比硬杀多保住 {report['delta']['extra_saved_vs_hard']} 注")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
