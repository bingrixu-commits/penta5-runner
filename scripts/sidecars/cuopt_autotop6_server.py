#!/usr/bin/env python3
"""T4 上的 autoTop6 cuOpt HTTP 服务。CPU 面板机通过 PENTA5_CUOPT_URL 调用。

GET  /health   → {ok, solver}
POST /autotop6 → stdin 同构 JSON，stdout 同构 {ok, selectedIds, solver, meta}
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
for cand in (HERE, HERE / "algorithms", HERE.parent / "algorithms"):
    if (cand / "auto_top6_milp.py").exists():
        if str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
        break

from auto_top6_milp import solve  # noqa: E402


def _json_bytes(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("[cuopt-autotop6] " + (fmt % args) + "\n")

    def _send(self, code: int, obj: dict) -> None:
        body = _json_bytes(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/health":
            self._send(404, {"ok": False, "error": "not-found"})
            return
        try:
            import cuopt  # noqa: F401
            solver = "cuopt"
        except Exception:
            solver = "missing"
        self._send(200, {"ok": solver == "cuopt", "service": "autotop6-cuopt", "solver": solver})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/autotop6":
            self._send(404, {"ok": False, "error": "not-found"})
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length > 0 else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            self._send(400, {"ok": False, "error": f"bad-json:{exc}"})
            return
        if not isinstance(payload, dict):
            self._send(400, {"ok": False, "error": "payload-not-object"})
            return
        payload["cuoptOnly"] = True
        out = solve(payload)
        self._send(200 if out.get("ok") else 503, out)


def main() -> int:
    host = os.environ.get("PENTA5_CUOPT_BIND", "0.0.0.0")
    port = int(os.environ.get("PENTA5_CUOPT_PORT", "8788") or 8788)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"cuopt autotop6 listening on {host}:{port}", flush=True)
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
