#!/usr/bin/env python3
"""运行五维更稳排除算法。

用法示例：
  python3 run.py
  python3 run.py --csv data.csv
  python3 run.py --json '[[1,2,3,4,5],[6,7,8,9,0]]'
  python3 run.py --json-file draws.json
  python3 run.py --rds
  python3 run.py --rds --out ../data/stable_math_filter.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lottery_filter import (
    format_console_report,
    format_soft_recommend_console,
    run_stable_filter,
    soft_filter_combinations,
    soft_recommend_from_pool,
    slim_soft_recommend,
    merge_position_kills_into_result,
    SAFE_RETENTION_DEFAULT,
)

ROOT = Path(__file__).resolve().parent
DEFAULT_CSV = ROOT / "data.csv"
DEFAULT_OUT = ROOT.parent / "data" / "stable_math_filter.json"


def _load_csv(path: Path) -> Tuple[List[List[int]], Optional[str]]:
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        dialect = csv.Sniffer().sniff(sample) if sample.strip() else csv.excel
        fh.seek(0)
        reader = csv.DictReader(fh, dialect=dialect) if "," in sample.split("\n", 1)[0] else None
        if reader and reader.fieldnames:
            for row in reader:
                rows.append(row)
        else:
            fh.seek(0)
            plain = csv.reader(fh)
            for parts in plain:
                if not parts:
                    continue
                if parts[0].lower() in {"period", "期号"}:
                    continue
                if len(parts) >= 6:
                    rows.append(
                        {
                            "period": parts[0],
                            "n1": parts[1],
                            "n2": parts[2],
                            "n3": parts[3],
                            "n4": parts[4],
                            "n5": parts[5],
                        }
                    )
                elif len(parts) >= 5:
                    rows.append({"numbers": parts[:5]})

    if not rows:
        raise ValueError(f"empty csv: {path}")

    # CSV 默认按时间正序写入；算法需要 newest_first
    newest_first = list(reversed(rows))
    latest_period = None
    first = newest_first[0]
    if isinstance(first, dict):
        latest_period = str(first.get("period") or first.get("期号") or "") or None
    return newest_first, latest_period  # type: ignore[return-value]


def _load_json_text(text: str) -> Tuple[List[Any], Optional[str]]:
    data = json.loads(text)
    latest_period = None
    if isinstance(data, dict):
        latest_period = data.get("latest_period") or data.get("period")
        if "history" in data:
            data = data["history"]
        elif "draws" in data:
            data = data["draws"]
        elif "numbers" in data:
            data = [data]
    if not isinstance(data, list) or not data:
        raise ValueError("JSON 需为开奖列表，或含 history/draws 字段的对象")
    if isinstance(data[0], dict):
        latest_period = latest_period or data[0].get("period")
    return data, str(latest_period) if latest_period else None


def _load_rds(limit: int = 0) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """从本机 api/db.js 同配置的 MySQL official_draws 读取（阿里服务器默认数据源）。"""
    try:
        import pymysql  # type: ignore
    except ImportError as exc:
        raise RuntimeError("读取 RDS 需要安装 pymysql：pip install pymysql") from exc

    env_path = ROOT.parent / ".env"
    env: Dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

    host = os.environ.get("RDS_HOST") or env.get("RDS_HOST")
    port = int(os.environ.get("RDS_PORT") or env.get("RDS_PORT") or 3306)
    user = os.environ.get("RDS_USER") or env.get("RDS_USER")
    password = os.environ.get("RDS_PASSWORD") or env.get("RDS_PASSWORD")
    database = os.environ.get("RDS_DATABASE") or env.get("RDS_DATABASE")
    if not all([host, user, password, database]):
        raise RuntimeError("缺少 RDS_HOST/RDS_USER/RDS_PASSWORD/RDS_DATABASE 配置")

    sql = (
        "SELECT period, ball1, ball2, ball3, ball4, ball5 "
        "FROM official_draws ORDER BY period DESC"
    )
    if limit and limit > 0:
        sql += f" LIMIT {int(limit)}"

    conn = pymysql.connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        connect_timeout=8,
        read_timeout=30,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            rows = cur.fetchall()
    finally:
        conn.close()

    history = [
        {
            "period": str(r[0]),
            "numbers": [int(r[1]), int(r[2]), int(r[3]), int(r[4]), int(r[5])],
        }
        for r in rows
    ]
    latest_period = history[0]["period"] if history else None
    return history, latest_period


def _load_from_backend_history_endpoint(url: str) -> Tuple[List[Any], Optional[str]]:
    import urllib.request

    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310
        payload = json.loads(resp.read().decode("utf-8"))
    if isinstance(payload, dict) and "history" in payload:
        history = payload["history"]
        latest = None
        if history:
            latest = str(history[0].get("period") or "")
        return history, latest
    return _load_json_text(json.dumps(payload, ensure_ascii=False))


def resolve_history(args: argparse.Namespace) -> Tuple[List[Any], Optional[str], str]:
    if args.json:
        data, period = _load_json_text(args.json)
        return data, period, "json-arg"
    if args.json_file:
        text = Path(args.json_file).read_text(encoding="utf-8")
        data, period = _load_json_text(text)
        return data, period, f"json-file:{args.json_file}"
    if args.stdin_json:
        text = sys.stdin.read()
        data, period = _load_json_text(text)
        return data, period, "stdin-json"
    if args.history_url:
        data, period = _load_from_backend_history_endpoint(args.history_url)
        return data, period, f"url:{args.history_url}"
    if args.rds:
        data, period = _load_rds(limit=args.limit)
        return data, period, "rds"
    if args.csv:
        data, period = _load_csv(Path(args.csv))
        return data, period, f"csv:{args.csv}"
    if DEFAULT_CSV.exists():
        data, period = _load_csv(DEFAULT_CSV)
        return data, period, f"csv:{DEFAULT_CSV}"
    # 默认：阿里服务器 RDS
    data, period = _load_rds(limit=args.limit)
    return data, period, "rds-default"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="五维更稳排除算法运行器")
    p.add_argument("--csv", help="历史 CSV 路径（period,n1..n5 或纯号码）")
    p.add_argument("--json", help="直接传入 JSON 列表字符串")
    p.add_argument("--json-file", help="JSON 文件路径")
    p.add_argument("--stdin-json", action="store_true", help="从 stdin 读 JSON")
    p.add_argument("--rds", action="store_true", help="从 RDS official_draws 读取")
    p.add_argument("--history-url", help="从后端 /history 拉取")
    p.add_argument("--limit", type=int, default=1200, help="RDS 最多读取期数，0=全部（长跑建议 <=1200 防 OOM）")
    p.add_argument("--oldest-first", action="store_true", help="输入已是从旧到新")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="结果 JSON 输出路径")
    p.add_argument("--quiet", action="store_true", help="不打印控制台报告，只写文件")
    p.add_argument("--dump-json", action="store_true", help="向 stdout 打印完整 JSON")
    p.add_argument(
        "--score-draws",
        help="对候选组合做软筛选（逗号分隔四码），结果写入 result['soft_filter']",
    )
    p.add_argument(
        "--retention",
        type=float,
        default=SAFE_RETENTION_DEFAULT,
        help="软筛选安全保留比例（夹到 0.20~0.30）",
    )
    p.add_argument(
        "--no-soft-pool",
        action="store_true",
        help="跳过全量 0000-9999 Soft Risk 选号（默认启用）",
    )
    p.add_argument(
        "--position-kills",
        help='位杀 JSON，如 \'{"wan":"6","qian":"1","bai":"3","shi":"8"}\'（软加分）',
    )
    p.add_argument(
        "--position-kills-file",
        help="位杀 JSON 文件路径",
    )
    p.add_argument(
        "--print-limit",
        type=int,
        default=80,
        help="控制台每档最多打印号码数（默认 80）",
    )
    p.add_argument(
        "--soft-code-limit",
        type=int,
        default=0,
        help="写入 JSON 的推荐号码上限，0=保留全部 retained（文件可能较大）",
    )
    return p


def _load_position_kills(args: argparse.Namespace) -> Optional[Dict[str, Any]]:
    raw = None
    if args.position_kills_file:
        raw = Path(args.position_kills_file).read_text(encoding="utf-8")
    elif args.position_kills:
        raw = args.position_kills
    if not raw:
        return None
    data = json.loads(raw)
    if isinstance(data, dict) and "positionKills" in data and isinstance(data["positionKills"], dict):
        return data["positionKills"]
    if isinstance(data, dict) and "position_kills" in data and isinstance(data["position_kills"], dict):
        return data["position_kills"]
    if isinstance(data, dict):
        return data
    return None


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    history, latest_period, source = resolve_history(args)
    result = run_stable_filter(
        history,
        newest_first=not args.oldest_first,
        latest_period=latest_period,
    )
    result["source"] = source
    # 软筛选元数据：exclude_* / 位杀 是建议维度，组合筛选走 Risk Scoring
    result["softRisk"] = {
        "mode": "soft_risk",
        "fuzzyZone": {"low": "0~1", "mid": "2", "high": ">=3"},
        "retentionDefault": SAFE_RETENTION_DEFAULT,
        "retentionMin": 0.20,
        "retentionMax": 0.30,
        "note": "组合筛选走 Risk Scoring；exclude_* / 位杀 为建议维度，非绝对删号",
    }

    position_kills = _load_position_kills(args)
    if position_kills:
        result = merge_position_kills_into_result(result, position_kills)

    if args.score_draws:
        draws = [part.strip() for part in str(args.score_draws).split(",") if part.strip()]
        result["soft_filter"] = soft_filter_combinations(
            draws,
            result=result,
            retention_ratio=args.retention,
        )

    soft_recommend = None
    if not args.no_soft_pool:
        soft_recommend = soft_recommend_from_pool(
            result,
            position_kills=position_kills,
            retention_ratio=args.retention,
            code_limit=int(args.soft_code_limit or 0),
            keep_ranked=False,
        )
        # 磁盘保留较完整推荐列表；slim 摘要也写入便于下游
        result["soft_recommend"] = soft_recommend
        result["softRecommend"] = slim_soft_recommend(soft_recommend)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # 全量 recommend_codes 可达数千，写盘前对超大 ranked 已省略；codes 仍完整（除非 soft-code-limit）
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        print(f"[stable-math-filter] source={source} history={result['history_count']} latest={result.get('latest_period')}")
        print(format_console_report(result))
        if result.get("soft_filter"):
            sf = result["soft_filter"]
            print(
                f"[soft-risk] retained={sf['counts']['retained']} "
                f"dropped={sf['counts']['dropped']} force={sf['counts']['force_retained']}"
            )
        if soft_recommend:
            print(format_soft_recommend_console(soft_recommend, print_limit=int(args.print_limit or 80)))
        print(f"[stable-math-filter] wrote {out_path}")
    if args.dump_json:
        print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[stable-math-filter] ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
