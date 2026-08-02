#!/usr/bin/env python3
"""阿里 runner 文件哈希 / 面板 marker 对照（加固版）。

按 .clinerules：
- 面板入口 http://8.210.5.221/panel
- 阿里代码目录 /root/penta5-runner
- 禁止把密码写入仓库；HTTP 拉 /panel 时可选读环境变量 PENTA5_PANEL_PASSWORD 登录

用法（任选）：
  # 在阿里本机：读磁盘文件
  python3 _compare_remote_hash.py

  # 在本机：对照本地 current_source + HTTP 拉线上 panel
  $env:PENTA5_PANEL_PASSWORD="你的面板密码"
  python _compare_remote_hash.py --local --http
"""

from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REMOTE_ROOT = Path(os.environ.get("PENTA5_REMOTE_ROOT", "/root/penta5-runner"))
DEFAULT_LOCAL_ROOT = SCRIPT_DIR / "current_source" / "penta5-runner"
DEFAULT_PANEL_URL = os.environ.get("PENTA5_PANEL_URL", "http://8.210.5.221/panel")
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("PENTA5_HTTP_TIMEOUT", "30"))
TARGET_FILES = ("penta5_backend_runner.js", "panel.html")
# 密码只读环境变量，禁止写入仓库
PANEL_PASSWORD_ENV = "PENTA5_PANEL_PASSWORD"

BACKEND_MARKERS = (
    "MODEL_NEAR_WINDOW",
    "version: 8",
    "rebuildMissingModelCountersFromPredictions",
    "ensurePredictionThreeDuishuSnapshot",
    "expectedFormalCount",
    "independentTotalHeweiSuccess",
)
PANEL_MARKERS = (
    "model_audit_v221",
    "MODEL_MIN_SAMPLE",
    "MODEL_NEAR_WINDOW",
    "数据不足",
)
BAD_BACKEND_VARS = ("expectedAutoItemCount",)


class CompareError(RuntimeError):
    """可预期的对照失败（文件缺失、HTTP/SSL 等）。"""


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest().upper()


def read_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except FileNotFoundError as exc:
        raise CompareError(f"文件不存在：{path}") from exc
    except OSError as exc:
        raise CompareError(f"读取失败：{path} ({exc})") from exc


def build_ssl_context(*, insecure: bool = False) -> ssl.SSLContext:
    """HTTPS 默认校验证书；仅 --insecure / PENTA5_SSL_INSECURE=1 才关闭校验。"""
    if insecure or os.environ.get("PENTA5_SSL_INSECURE", "").strip().lower() in {"1", "true", "yes"}:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        print("WARN_SSL_VERIFY_DISABLED 1", flush=True)
        return ctx
    return ssl.create_default_context()


def login_url_from_panel(panel_url: str) -> str:
    parts = urlsplit(panel_url)
    return urlunsplit((parts.scheme, parts.netloc, "/login", "", ""))


def build_opener(*, insecure: bool = False) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
    ]
    # HTTPSHandler 始终挂上，保证 https:// 走显式 SSL context
    handlers.append(urllib.request.HTTPSHandler(context=build_ssl_context(insecure=insecure)))
    return urllib.request.build_opener(*handlers)


def http_open(
    opener: urllib.request.OpenerDirector,
    url: str,
    *,
    timeout: float,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    method: str | None = None,
) -> bytes:
    req_headers = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "User-Agent": "penta5-compare-remote-hash/2.0",
    }
    if headers:
        req_headers.update(headers)
    request = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = getattr(response, "status", None) or response.getcode()
            if status != 200:
                raise CompareError(f"HTTP {status}：{url}")
            return response.read()
    except TimeoutError as exc:
        raise CompareError(f"请求超时（{timeout}s）：{url}") from exc
    except ssl.SSLError as exc:
        raise CompareError(f"SSL 校验失败：{url} ({exc})") from exc
    except urllib.error.HTTPError as exc:
        raise CompareError(f"HTTP {exc.code}：{url}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError):
            raise CompareError(f"SSL 校验失败：{url} ({reason})") from exc
        raise CompareError(f"网络错误：{url} ({reason})") from exc
    except OSError as exc:
        raise CompareError(f"连接失败：{url} ({exc})") from exc


def maybe_login(
    opener: urllib.request.OpenerDirector,
    panel_url: str,
    *,
    timeout: float,
    password: str | None,
) -> bool:
    """若提供密码则 POST /login；成功返回 True。密码来自环境变量，不落盘。"""
    if not password:
        return False
    payload = json.dumps({"password": password}).encode("utf-8")
    http_open(
        opener,
        login_url_from_panel(panel_url),
        timeout=timeout,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return True


def print_file_hashes(label: str, root: Path, names: Iterable[str]) -> dict[str, bytes]:
    if not root.is_dir():
        raise CompareError(f"目录不存在：{root}")
    payloads: dict[str, bytes] = {}
    for name in names:
        path = root / name
        data = read_bytes(path)
        payloads[name] = data
        print(f"{label}_{name}_SIZE {len(data)}", flush=True)
        print(f"{label}_{name}_MD5 {md5_hex(data)}", flush=True)
    return payloads


def print_text_markers(label: str, backend: str, panel: str) -> None:
    match = re.search(r'PANEL_UI_VERSION\s*=\s*"([^"]+)"', panel)
    print(f"{label}_UI", match.group(1) if match else None, flush=True)
    for marker in BACKEND_MARKERS:
        print(f"{label}_BACKEND_MARKER", marker, backend.count(marker), flush=True)
    for marker in PANEL_MARKERS:
        print(f"{label}_PANEL_MARKER", marker, panel.count(marker), flush=True)
    for bad in BAD_BACKEND_VARS:
        print(f"{label}_BAD_VAR", backend.count(bad), flush=True)


def audit_disk(label: str, root: Path) -> int:
    try:
        payloads = print_file_hashes(label, root, TARGET_FILES)
        backend = payloads["penta5_backend_runner.js"].decode("utf-8", errors="replace")
        panel = payloads["panel.html"].decode("utf-8", errors="replace")
        print_text_markers(label, backend, panel)
        return 0
    except CompareError as exc:
        print(f"ERROR_{label}", exc, file=sys.stderr, flush=True)
        return 1


def audit_http_panel(panel_url: str, *, timeout: float, insecure: bool) -> int:
    try:
        opener = build_opener(insecure=insecure)
        password = (os.environ.get(PANEL_PASSWORD_ENV) or "").strip() or None
        logged_in = maybe_login(opener, panel_url, timeout=timeout, password=password)
        raw = http_open(opener, panel_url, timeout=timeout, method="GET")
        text = raw.decode("utf-8", errors="replace")
        print(f"HTTP_PANEL_URL {panel_url}", flush=True)
        print(f"HTTP_LOGIN {'1' if logged_in else '0'}", flush=True)
        print(f"HTTP_PANEL_SIZE {len(raw)}", flush=True)
        print(f"HTTP_PANEL_MD5 {md5_hex(raw)}", flush=True)
        match = re.search(r'PANEL_UI_VERSION\s*=\s*"([^"]+)"', text)
        ui_version = match.group(1) if match else None
        print("HTTP_UI", ui_version, flush=True)
        if ui_version is None and "password" in text.lower() and len(raw) < 8192:
            print(
                f"WARN_HTTP_LOGIN_PAGE 未拿到真实 panel；请设置环境变量 {PANEL_PASSWORD_ENV}",
                flush=True,
            )
        for marker in PANEL_MARKERS:
            print("HTTP_PANEL_MARKER", marker, text.count(marker), flush=True)
        # backend.js 不对外静态暴露，HTTP 模式只验收 panel
        print("HTTP_BACKEND_NOTE skipped (not served publicly)", flush=True)
        return 0
    except CompareError as exc:
        print(f"ERROR_HTTP {exc}", file=sys.stderr, flush=True)
        return 1


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对照阿里 runner 哈希与 panel marker")
    parser.add_argument(
        "--remote-root",
        type=Path,
        default=DEFAULT_REMOTE_ROOT,
        help=f"阿里磁盘根目录（默认 {DEFAULT_REMOTE_ROOT}）",
    )
    parser.add_argument(
        "--local-root",
        type=Path,
        default=DEFAULT_LOCAL_ROOT,
        help=f"本机源码根目录（默认 {DEFAULT_LOCAL_ROOT}）",
    )
    parser.add_argument(
        "--panel-url",
        default=DEFAULT_PANEL_URL,
        help=f"线上 panel URL（默认 {DEFAULT_PANEL_URL}）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=REQUEST_TIMEOUT_SECONDS,
        help=f"HTTP 超时秒数（默认 {REQUEST_TIMEOUT_SECONDS}）",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="同时审计本机 current_source/penta5-runner",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="HTTP 拉取线上 /panel（带 timeout；HTTPS 默认校验证书）",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="关闭 HTTPS 证书校验（仅排障；默认开启校验）",
    )
    parser.add_argument(
        "--skip-remote-disk",
        action="store_true",
        help="跳过阿里磁盘审计（本机无 /root/penta5-runner 时使用）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    exit_code = 0

    # 默认：有远程磁盘目录就审；本机没有则自动跳过并提示用 --http/--local
    do_remote_disk = not args.skip_remote_disk
    if do_remote_disk and not args.remote_root.is_dir():
        print(f"WARN_SKIP_REMOTE_DISK missing {args.remote_root}", flush=True)
        do_remote_disk = False
        if not args.local and not args.http:
            # 本机默认自动启用 local+http，避免脚本空跑
            args.local = True
            args.http = True
            print("INFO_AUTO_MODE --local --http", flush=True)

    if do_remote_disk:
        exit_code |= audit_disk("REMOTE", args.remote_root)

    if args.local:
        exit_code |= audit_disk("LOCAL", args.local_root)

    if args.http:
        if args.timeout <= 0:
            print("ERROR_HTTP timeout 必须 > 0", file=sys.stderr, flush=True)
            return 2
        exit_code |= audit_http_panel(args.panel_url, timeout=args.timeout, insecure=args.insecure)

    if exit_code == 0 and not (do_remote_disk or args.local or args.http):
        print("ERROR_NO_MODE 未选择任何审计目标", file=sys.stderr, flush=True)
        return 2

    return exit_code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("ERROR_INTERRUPTED", file=sys.stderr, flush=True)
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001 — 顶层兜底，防止未捕获崩溃
        print(f"ERROR_FATAL {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
