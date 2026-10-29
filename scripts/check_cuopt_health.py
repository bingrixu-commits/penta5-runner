#!/usr/bin/env python3
"""Probe the remote cuopt endpoint used by PENTA5_AUTOTOP6_MILP=cuopt.

The bridge in algorithms/auto_top6_milp_bridge.js returns cuopt-http-failed to
its caller when this host is unreachable, but nothing recorded that anywhere.
This job makes the dependency's state observable.
"""
import json
import os
import socket
import sys
import time
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = "/root/penta5-runner"
LOG = os.path.join(ROOT, "data/cuopt_health.log")
TZ = ZoneInfo("Asia/Shanghai")


def endpoint():
    raw = (os.environ.get("PENTA5_CUOPT_URL") or "").strip()
    if not raw:
        try:
            for line in open(os.path.join(ROOT, ".env"), encoding="utf-8"):
                if line.startswith("PENTA5_CUOPT_URL="):
                    raw = line.split("=", 1)[1].strip()
                    break
        except OSError:
            pass
    if not raw:
        # .env leaves it commented; the live value lives in the pm2 process env.
        try:
            import json as _json
            import subprocess
            out = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=15).stdout
            for proc in _json.loads(out):
                value = (proc.get("pm2_env") or {}).get("PENTA5_CUOPT_URL")
                if value:
                    return str(value).strip().rstrip("/")
        except Exception:
            pass
    return raw.rstrip("/")


def probe(url):
    if not url:
        return {"ok": False, "error": "cuopt-url-unset"}
    parsed = urllib.parse.urlparse(url)
    host, port = parsed.hostname, parsed.port or 80
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=5):
            pass
        return {"ok": True, "host": host, "port": port,
                "connect_ms": round((time.monotonic() - started) * 1000, 1)}
    except OSError as error:
        return {"ok": False, "host": host, "port": port,
                "error": type(error).__name__,
                "detail": str(error)[:120],
                "connect_ms": round((time.monotonic() - started) * 1000, 1)}


def main():
    url = endpoint()
    result = probe(url)
    record = {"checked_at": datetime.now(TZ).isoformat(), "url": url,
              "local_python_fallback": os.environ.get("PENTA5_CUOPT_LOCAL", "0"),
              **result}
    line = "CUOPT_HEALTH " + json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except OSError:
        pass
    # Exit 0 always: this is an observability probe, not a gate.
    return 0


if __name__ == "__main__":
    sys.exit(main())
