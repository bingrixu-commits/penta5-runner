#!/usr/bin/env python3
"""T4 四定回删满窗服务（本机 127.0.0.1:8790，由 cuOpt :8788 反代）。

回删时从指定开奖期往回扫，最多 1500 期。不是灌一次就停。
不走 CUDA / cuOpt：四定回删是整串四位旧码集合差。
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BIND = os.environ.get("FOUR_BACKDELETE_BIND", "127.0.0.1")
PORT = int(os.environ.get("FOUR_BACKDELETE_PORT", "8790") or 8790)
MAX_ROWS = int(os.environ.get("FOUR_BACKDELETE_MAX", "1500") or 1500)
HISTORY_MIN = int(os.environ.get("FOUR_BACKDELETE_MIN", "100") or 100)
DATA_DIR = Path(os.environ.get("FOUR_BACKDELETE_DATA_DIR", "/opt/penta5-four-backdelete/data"))
CACHE_FILE = DATA_DIR / "history.json"

_lock = threading.RLock()
_rows = []  # newest first: {period, opentime, balls[4]}


def _json_bytes(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _norm_row(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    period = "".join(ch for ch in str(raw.get("period") or raw.get("uid") or "") if ch.isdigit())
    if not period:
        return None
    opentime = str(raw.get("opentime") or raw.get("openTime") or raw.get("open_time") or "").strip()
    balls = raw.get("numbers") or raw.get("balls")
    if not isinstance(balls, list):
        balls = [raw.get("ball1"), raw.get("ball2"), raw.get("ball3"), raw.get("ball4")]
    digits = []
    for item in list(balls or [])[:4]:
        text = str(item if item is not None else "")
        if len(text) != 1 or not text.isdigit():
            return None
        digits.append(text)
    if len(digits) != 4:
        return None
    return {"period": period, "opentime": opentime, "balls": digits}


def _sort_rows(rows: list) -> list:
    return sorted(rows, key=lambda r: r["period"], reverse=True)


def _save_unlocked() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"rows": _rows[:MAX_ROWS]}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_FILE)


def _load() -> None:
    global _rows
    if not CACHE_FILE.exists():
        return
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8") or "{}")
    except Exception:
        return
    incoming = payload.get("rows") if isinstance(payload, dict) else payload
    if not isinstance(incoming, list):
        return
    seen = {}
    for item in incoming:
        row = _norm_row(item)
        if row:
            seen[row["period"]] = row
    _rows = _sort_rows(list(seen.values()))[:MAX_ROWS]


def _merge(incoming: list, replace: bool) -> dict:
    global _rows
    with _lock:
        seen = {} if replace else {row["period"]: row for row in _rows}
        added = 0
        for item in incoming:
            row = _norm_row(item)
            if not row:
                continue
            if row["period"] not in seen:
                added += 1
            seen[row["period"]] = row
        _rows = _sort_rows(list(seen.values()))[:MAX_ROWS]
        _save_unlocked()
        return _meta()


def _meta() -> dict:
    count = len(_rows)
    return {
        "ok": True,
        "service": "four-backdelete-gpu",
        "count": count,
        "drawCount": count,
        "latestPeriod": _rows[0]["period"] if count else "",
        "oldestPeriod": _rows[-1]["period"] if count else "",
        "max": MAX_ROWS,
        "pid": os.getpid(),
    }


def _opened(limit: int, from_period: str = "") -> dict:
    with _lock:
        count = len(_rows)
        head = _rows[0]["period"] if count else ""
        start = 0
        want = "".join(ch for ch in str(from_period or "") if ch.isdigit())
        if want:
            if head and want > head:
                return {
                    "ok": False,
                    "error": "四定回删：GPU 首期 %s 落后当前期 %s，需从开奖期重扫" % (head, want),
                    "scanned": 0,
                    "limit": limit,
                    "drawCount": count,
                    "latestPeriod": head,
                    "fromPeriod": want,
                    "stale": True,
                    "truncated": False,
                    "windowShort": True,
                    "source": "four-backdelete-gpu",
                }
            found = False
            for i, row in enumerate(_rows):
                if row["period"] == want:
                    start = i
                    found = True
                    break
                if row["period"] < want:
                    start = i
                    found = True
                    break
            if not found:
                return {
                    "ok": False,
                    "error": "四定回删：当前期 %s 不在 GPU 窗内，需从开奖期重扫" % want,
                    "scanned": 0,
                    "limit": limit,
                    "drawCount": count,
                    "latestPeriod": head,
                    "fromPeriod": want,
                    "stale": True,
                    "truncated": False,
                    "windowShort": True,
                    "source": "four-backdelete-gpu",
                }
        window = _rows[start:]
        if len(window) < HISTORY_MIN:
            return {
                "ok": False,
                "error": "四定回删：从 %s 往回不足 %s 期（当前 %s），无法回删" % (want or head or "空", HISTORY_MIN, len(window)),
                "scanned": len(window),
                "limit": limit,
                "drawCount": count,
                "historyMin": HISTORY_MIN,
                "fromPeriod": want,
                "truncated": False,
                "windowShort": True,
                "source": "four-backdelete-gpu",
            }
        take = window[:limit]
        scanned = len(take)
        if scanned < limit and len(window) >= limit:
            return {
                "ok": False,
                "error": "四定回删：从 %s 往回半截（要 %s，只给 %s），禁止截断扫码" % (want or head, limit, scanned),
                "scanned": scanned,
                "limit": limit,
                "drawCount": count,
                "fromPeriod": want,
                "truncated": True,
                "windowShort": True,
                "source": "four-backdelete-gpu",
            }
        seen_code = {}
        opened = []
        for row in take:
            code = "".join(row["balls"])
            if code not in seen_code:
                seen_code[code] = True
                opened.append(code)
        return {
            "ok": True,
            "source": "four-backdelete-gpu",
            "cache": True,
            "live": True,
            "limit": limit,
            "scanned": scanned,
            "drawCount": count,
            "fromPeriod": want or take[0]["period"],
            "truncated": False,
            "windowShort": scanned < limit,
            "openedUnique": len(opened),
            "latestPeriod": take[0]["period"],
            "oldestPeriod": take[-1]["period"],
            "opened": opened,
        }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("[four-backdelete-gpu] " + (fmt % args) + "\n")

    def _send(self, code: int, obj: dict) -> None:
        body = _json_bytes(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 8 * 1024 * 1024:
            raise ValueError("body too large")
        raw = self.rfile.read(length) if length > 0 else b"{}"
        payload = json.loads(raw.decode("utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            with _lock:
                self._send(200, _meta())
            return
        if path == "/four-backdelete-opened":
            qs = parse_qs(parsed.query or "")
            try:
                limit = int((qs.get("limit") or ["1500"])[0] or 1500)
            except ValueError:
                limit = 1500
            limit = max(1, min(MAX_ROWS, limit))
            from_period = "".join(ch for ch in str((qs.get("from") or [""])[0] or "") if ch.isdigit())
            out = _opened(limit, from_period)
            self._send(200 if out.get("ok") else 503, out)
            return
        self._send(404, {"ok": False, "error": "Not Found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            payload = self._read_json()
        except Exception as exc:
            self._send(400, {"ok": False, "error": str(exc)})
            return
        rows = payload.get("history") or payload.get("rows") or payload.get("items") or []
        if not isinstance(rows, list):
            self._send(400, {"ok": False, "error": "history-not-array"})
            return
        if path == "/hydrate":
            self._send(200, _merge(rows, True))
            return
        if path == "/ingest":
            self._send(200, _merge(rows, False))
            return
        self._send(404, {"ok": False, "error": "Not Found"})


def main() -> int:
    _load()
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    print(
        "[four-backdelete-gpu] listening %s:%s count=%s latest=%s"
        % (BIND, PORT, len(_rows), _rows[0]["period"] if _rows else ""),
        flush=True,
    )
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
