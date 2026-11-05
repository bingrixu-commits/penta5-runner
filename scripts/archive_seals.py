#!/usr/bin/env python3
"""Seal/hit_stats 滚动归档 CLI：把超 keep_days 的期号按月压到 data/archive/*.json.gz。

只读主文件 + 原子写冷分片，不调用任何 provider，不改任何封存字段。
默认 dry-run 只出报告；--apply 才实际归档。

用法：
  python3 scripts/archive_seals.py --dry-run
  python3 scripts/archive_seals.py --target he3_refine_seals --keep-days 2 --apply
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path('/root/penta5-runner')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'lib'))

from seal_archive import PERIOD_DICT_KEYS, archive, read_hot, read_full  # noqa: E402


def _count(path):
    hot = read_hot(path)
    keys = PERIOD_DICT_KEYS.get(Path(path).stem, ())
    return {k: (len(hot.get(k)) if isinstance(hot.get(k), dict) else 0) for k in keys}


def main(argv=None):
    parser = argparse.ArgumentParser(description="seal/hit_stats 滚动归档")
    parser.add_argument("--target", action="append", default=None,
                        help="stem 白名单（可重复）；缺省全部 4 文件")
    parser.add_argument("--keep-days", type=int, default=2,
                        help="主文件保留最近 N 天期号，更老按月归档（默认 2；he3 每期约 90KB，7 天会超 110MiB 预警线）")
    parser.add_argument("--apply", action="store_true",
                        help="实际归档（缺省 dry-run 只出报告）")
    args = parser.parse_args(argv)

    targets = args.target or sorted(PERIOD_DICT_KEYS)
    total_archived = 0
    for stem in targets:
        if stem not in PERIOD_DICT_KEYS:
            print("skip unknown stem: %s" % stem, file=sys.stderr)
            continue
        path = ROOT / "data" / ("%s.json" % stem)
        if not path.exists():
            print("missing %s" % path)
            continue
        before = _count(path)
        result = archive(path, keep_days=args.keep_days, dry_run=not args.apply)
        mode = "applied" if args.apply else "dry-run"
        print("[%s] %s keep_days=%d hot_before=%s -> kept=%s archived=%d months=%s" % (
            mode, stem, args.keep_days, before, result["kept"],
            result["archived"], result["months"]))
        total_archived += result["archived"]
    print("total_archived=%d keep_days=%d mode=%s" % (
        total_archived, args.keep_days, "applied" if args.apply else "dry-run"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
