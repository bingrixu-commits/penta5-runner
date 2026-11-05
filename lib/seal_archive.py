"""Seal/hit_stats 滚动归档：主文件留近期窗口，老期号按月压到冷分片 gz。

背景：各框架封存/结算文件是「期号 → 完整封存」的 JSON 账本，每期 append 后
全量重写，约 10 天涨到 ~1GB。归档不改动任何封存字段，只把超 keep_days 的
期号按月切到 data/archive/<stem>_<YYYYMM>.json.gz，主文件只留近期窗口。

安全性：封存完整性校验用 canonical json 哈希（json.dumps(sort_keys=True, separators=(',', ':')) 后 sha256），
与文件里空格/key 顺序无关，故 json 往返重写不改变语义哈希，证据字段保值。

用法：
  read_hot(path)    # 只读主文件近期窗口（等价原 read_stats；写方/判重用）
  read_full(path)   # 冷分片(老→新) + 主文件(新) 透明合并（统计/面板/回放用）
  archive(path, keep_days=7, now=None, dry_run=True)   # 独立 cron 归档
  write_full_separated(path, data, keep_days, now)      # 写方分离写（hit_stats）
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import gzip
import json
import os
import re
import tempfile

from ai_verified_stats import atomic_json, sync_parent_directory

BEIJING = timezone(timedelta(hours=8))

# stem → 期号 dict key（归档/合并目标；其余字段保留在主文件）
PERIOD_DICT_KEYS = {
    "he3_refine_seals": ("attempts", "prediction_seals"),
    "he3_refine_hit_stats": ("verified",),
}

ARCHIVE_DIR = "archive"
_PERIOD = re.compile(r"^\d{11}$")


def _archive_dir(path):
    return Path(path).parent / ARCHIVE_DIR


def _shards(path):
    """冷分片按月份（文件名）升序 = 时间升序。"""
    return sorted(_archive_dir(path).glob("%s_*.json.gz" % Path(path).stem))


def read_hot(path):
    """只读主文件（近期窗口），等价原 read_stats。文件不存在返回 {}。"""
    path = Path(path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_full(path):
    """冷分片（老期号，月份旧→新）+ 主文件（近期）透明合并。

    期号顺序：冷分片期号在前（月份升序 + 分片内序），主文件近期期号在后，
    与归档前主文件时间序一致。非期号字段（version/revision/pending 等）取主文件。
    """
    path = Path(path)
    keys = PERIOD_DICT_KEYS.get(path.stem)
    hot = read_hot(path)
    if not keys:
        return hot
    merged = {k: {} for k in keys}
    for shard in _shards(path):
        with gzip.open(shard, "rt", encoding="utf-8") as f:
            data = json.load(f)
        for k in keys:
            if isinstance(data.get(k), dict):
                merged[k].update(data[k])
    for k in keys:
        if isinstance(hot.get(k), dict):
            merged[k].update(hot[k])
    result = dict(hot)
    for k in keys:
        result[k] = merged[k]
    return result


def _atomic_gzip_write(path, data):
    """原子写 gzip JSON 冷分片（0o600 + fsync + 目录同步，同 atomic_json 语义）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="wb", dir=path.parent, delete=False) as file:
            temporary = file.name
            with gzip.GzipFile(fileobj=file, mode="wb", compresslevel=1) as gz:
                gz.write(json.dumps(data, ensure_ascii=False, separators=(",", ":"),
                                    sort_keys=True).encode("utf-8"))
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        sync_parent_directory(path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def split_periods(data, keys, cutoff):
    """把全量 data 的期号 dict 分离成 hot（近期）+ cold（按月归档）。

    返回 (hot, cold)，cold = {month: {dict_key: {period: entry}}}。
    """
    hot = dict(data)
    cold = {}
    for k in keys:
        entries = data.get(k)
        if not isinstance(entries, dict):
            continue
        remain = {}
        for period, entry in entries.items():
            if (isinstance(period, str) and _PERIOD.match(period)
                    and period[:8] < cutoff):
                cold.setdefault(period[:6], {}).setdefault(k, {})[period] = entry
            else:
                remain[period] = entry
        hot[k] = remain
    return hot, cold


def _write_cold(path, cold, log):
    """把 cold（按月归档条目）合并进已有冷分片并写回。"""
    stem = Path(path).stem
    for month in sorted(cold):
        shard = _archive_dir(path) / ("%s_%s.json.gz" % (stem, month))
        existing = {}
        if shard.exists():
            with gzip.open(shard, "rt", encoding="utf-8") as f:
                existing = json.load(f)
        for k, entries in cold[month].items():
            existing.setdefault(k, {}).update(entries)
        _atomic_gzip_write(shard, existing)
        log("archived shard %s: +%d entries (total %d)" % (
            shard.name, sum(len(v) for v in cold[month].values()),
            sum(len(v) for v in existing.values())))


def archive(path, keep_days=7, now=None, dry_run=True, log=print):
    """把主文件里超 keep_days 的期号按月压到冷分片，主文件只留近期窗口。

    返回 {"archived": 归档期号总数, "kept": {dict_key: 保留期号数},
          "months": [YYYYMM]}。
    """
    path = Path(path)
    keys = PERIOD_DICT_KEYS.get(path.stem)
    if not keys:
        raise ValueError("unknown seal/stats stem: %s" % path.stem)
    now = now or datetime.now(BEIJING)
    cutoff = (now - timedelta(days=keep_days)).strftime("%Y%m%d")
    current = read_hot(path)
    hot, cold = split_periods(current, keys, cutoff)
    months = sorted(cold)
    result = {"archived": sum(len(v) for m in cold.values() for v in m.values()),
              "kept": {k: (len(hot.get(k)) if isinstance(hot.get(k), dict) else 0)
                       for k in keys},
              "months": months}
    if dry_run:
        return result
    if not cold:
        log("nothing to archive for %s (keep_days=%d)" % (path.name, keep_days))
        return result
    _write_cold(path, cold, log)
    atomic_json(path, hot)
    log("rewrote hot %s: kept=%s" % (path.name, result["kept"]))
    return result


def write_full_separated(path, data, keep_days=7, now=None, log=print):
    """写方分离写：把全量 data 的期号 dict 按 keep_days 分离，老期号进冷分片，
    近期进主文件。用于 hit_stats 这类写方需全量重算但主文件只留近期窗口的账本。

    返回 hot（写入主文件的那份）。
    """
    path = Path(path)
    keys = PERIOD_DICT_KEYS.get(path.stem)
    if not keys:
        raise ValueError("unknown seal/stats stem: %s" % path.stem)
    now = now or datetime.now(BEIJING)
    cutoff = (now - timedelta(days=keep_days)).strftime("%Y%m%d")
    hot, cold = split_periods(dict(data), keys, cutoff)
    _write_cold(path, cold, log)
    atomic_json(path, hot)
    return hot
