#!/usr/bin/env python3
"""Penta5 开奖源代理（加固版）。

设计目标：
- 优先同步拉取官方 latest，校验通过后才写缓存、才对后端返回 200
- 网络失败时：仅在缓存未过期且号码/期号校验通过时短暂回退；否则 502，禁止“乱出数字”
- 默认禁止回环读本机 /state（避免官方失败时拿本机旧账本当新开奖）
"""

from __future__ import annotations

import base64
import calendar
import json
import os
import re
import ssl
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional, Tuple

HOST = os.environ.get("LOCAL_PROXY_HOST", "127.0.0.1")
PORT = int(os.environ.get("LOCAL_PROXY_PORT", "3000"))
REMOTE_STATE_URL = os.environ.get("REMOTE_STATE_URL", "").strip()
REMOTE_HEALTH_URL = os.environ.get("REMOTE_HEALTH_URL", "").strip()
REMOTE_USER = os.environ.get("REMOTE_PANEL_USER", "")
REMOTE_PASSWORD = os.environ.get("REMOTE_PANEL_PASSWORD", "")
REQUEST_TIMEOUT = float(os.environ.get("REMOTE_REQUEST_TIMEOUT", "10"))
# 缓存最长可回退秒数：超时后对 /latest 返回 502，避免后端把陈旧期号当新开奖
MAX_STALE_SECONDS = float(os.environ.get("PROXY_MAX_STALE_SECONDS", "180"))
# 默认禁止 /state 回环；仅显式 PROXY_ALLOW_STATE_FALLBACK=1 才启用
ALLOW_STATE_FALLBACK = os.environ.get("PROXY_ALLOW_STATE_FALLBACK", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
OFFICIAL_LATEST_URL = os.environ.get(
    "OFFICIAL_LATEST_URL",
    "https://www.uk-wl.net/Penta5Classic/0d2d48772cd48f41e761ef99f40e3ac0/latest",
).strip()
CACHE_FILE = os.environ.get(
    "REMOTE_PROXY_CACHE_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "proxy_latest_cache.json"),
)
SSL_INSECURE = os.environ.get("PENTA5_SSL_INSECURE", "").strip().lower() in {"1", "true", "yes"}

PERIOD_RE = re.compile(r"^\d{8,20}$")
REFRESH_LOCK = threading.Lock()
LAST_FETCH_ERROR: Optional[str] = None
LAST_FETCH_AT: float = 0.0


def _build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if SSL_INSECURE:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        print("WARN_SSL_VERIFY_DISABLED 1", flush=True)
    return ctx


SSL_CONTEXT = _build_ssl_context()
NO_PROXY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=SSL_CONTEXT),
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def write_json_file(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = f"{path}.tmp"
    with open(temp_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temp_path, path)


def read_json_file(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except FileNotFoundError:
        return None
    except Exception:
        traceback.print_exc()
        return None


def make_auth_header() -> Optional[str]:
    if not REMOTE_USER or not REMOTE_PASSWORD:
        return None
    token = base64.b64encode(f"{REMOTE_USER}:{REMOTE_PASSWORD}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def derive_health_url(state_url: str) -> str:
    if not state_url:
        return ""
    if state_url.endswith("/state"):
        return state_url[: -len("/state")] + "/health"
    if "/state?" in state_url:
        return state_url.replace("/state?", "/health?", 1)
    return state_url


def validate_period(period: Any) -> str:
    text = str(period or "").strip()
    if not PERIOD_RE.match(text):
        raise RuntimeError(f"invalid period: {period!r}")
    return text


def validate_numbers(value: Any) -> list:
    if isinstance(value, list):
        digits = [str(item).strip() for item in value]
    else:
        text = str(value or "").strip()
        if "," in text or " " in text:
            parts = [p for p in re.split(r"[\s,]+", text) if p != ""]
            digits = [p.strip() for p in parts]
        elif re.fullmatch(r"\d{5}", text):
            digits = list(text)
        else:
            digits = [ch for ch in text if ch.isdigit()]
    if len(digits) != 5:
        raise RuntimeError(f"numbers must be exactly 5 digits, got {digits!r}")
    for digit in digits:
        if not re.fullmatch(r"\d", digit):
            raise RuntimeError(f"invalid digit in numbers: {digits!r}")
    return digits


def validate_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """统一校验对外/落盘结构，防止脏数据入库。"""
    if not isinstance(payload, dict):
        raise RuntimeError("payload is not an object")
    period = validate_period(payload.get("period"))
    numbers = validate_numbers(payload.get("numbers"))
    latest = payload.get("latestRecord")
    if not isinstance(latest, dict):
        raise RuntimeError("latestRecord missing")
    latest_period = validate_period(latest.get("period") or period)
    latest_numbers = validate_numbers(latest.get("numbers") or numbers)
    if latest_period != period or latest_numbers != numbers:
        raise RuntimeError("latestRecord does not match top-level period/numbers")
    opentime = str(payload.get("opentime") or latest.get("opentime") or "--")
    positions = {
        "wan": numbers[0],
        "qian": numbers[1],
        "bai": numbers[2],
        "shi": numbers[3],
        "ge": numbers[4],
    }
    metrics = make_metrics(numbers)
    remote = payload.get("remote") if isinstance(payload.get("remote"), dict) else {}
    cleaned = {
        "ok": True,
        "fresh": bool(payload.get("fresh", False)),
        "proxiedAt": str(payload.get("proxiedAt") or utc_now()),
        "fetchedAt": str(payload.get("fetchedAt") or payload.get("proxiedAt") or utc_now()),
        "period": period,
        "opentime": opentime,
        "numbers": numbers,
        "positions": positions,
        "metrics": metrics,
        "latestRecord": {
            "uid": str(latest.get("uid") or f"period:{period}"),
            "period": period,
            "opentime": opentime,
            "numbers": numbers,
            "positions": positions,
            "metrics": metrics,
            "rawJson": latest.get("rawJson"),
            "capturedAt": str(latest.get("capturedAt") or utc_now()),
        },
        "remote": dict(remote),
    }
    return cleaned


def cache_age_seconds(payload: Dict[str, Any]) -> Optional[float]:
    for key in ("fetchedAt", "proxiedAt"):
        text = str(payload.get(key) or "").strip()
        if not text:
            continue
        try:
            # fetchedAt/proxiedAt 使用 UTC，例如 2026-07-14T16:33:12Z
            ts = calendar.timegm(time.strptime(text.replace("Z", ""), "%Y-%m-%dT%H:%M:%S"))
            return max(0.0, time.time() - ts)
        except Exception:
            continue
    return None


def fetch_json_url(url: str, use_auth: bool = True) -> Tuple[Dict[str, Any], int]:
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": "penta5-remote-state-proxy/2.0",
    }
    auth = make_auth_header()
    if use_auth and auth:
        headers["Authorization"] = auth

    separator = "&" if "?" in url else "?"
    request_url = f"{url}{separator}_={int(time.time() * 1000)}"
    request = urllib.request.Request(request_url, headers=headers, method="GET")

    try:
        with NO_PROXY_OPENER.open(request, timeout=REQUEST_TIMEOUT) as response:
            status = getattr(response, "status", None) or response.getcode()
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
            text = raw.decode(charset, "replace").strip()
            if not text:
                raise RuntimeError("remote response is empty")
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise RuntimeError("remote response is not a JSON object")
            if status != 200:
                raise RuntimeError(f"remote HTTP {status}")
            return payload, int(status)
    except TimeoutError as exc:
        raise RuntimeError(f"remote timeout after {REQUEST_TIMEOUT}s") from exc
    except ssl.SSLError as exc:
        raise RuntimeError(f"remote SSL error: {exc}") from exc
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"remote HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLError):
            raise RuntimeError(f"remote SSL error: {reason}") from exc
        raise RuntimeError(f"remote connection failed: {reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"remote response is not valid JSON: {exc}") from exc


def hewei(*digits: str) -> str:
    return str(sum(int(item) for item in digits) % 10)


def make_metrics(numbers: list) -> Dict[str, Any]:
    wan, qian, bai, shi, ge = numbers[:5]
    total_sum = sum(int(item) for item in numbers[:5])
    return {
        "totalSum": total_sum,
        "totalHewei": str(total_sum % 10),
        "wanQianBaiHewei": hewei(wan, qian, bai),
        "qianBaiShiHewei": hewei(qian, bai, shi),
        "wanQianBaiShiHewei": hewei(wan, qian, bai, shi),
        "baiShiGeHewei": hewei(bai, shi, ge),
        "qianBaiShiGeHewei": hewei(qian, bai, shi, ge),
    }


def normalize_official_payload(payload: Dict[str, Any], source_url: Optional[str] = None) -> Dict[str, Any]:
    if payload.get("Status") is False or payload.get("ok") is False:
        raise RuntimeError(f"official source marked failure: {payload.get('ErrorMessage') or payload.get('error') or 'Status/ok false'}")

    data = payload.get("Data") if isinstance(payload.get("Data"), dict) else payload
    results = data.get("Results") if isinstance(data.get("Results"), list) else []
    if not results:
        raise RuntimeError("official latest response has empty Results")
    row = results[0]
    if not isinstance(row, dict):
        raise RuntimeError("official latest Results[0] is not an object")

    period = validate_period(row.get("PeriodNo") or row.get("period") or row.get("issue") or row.get("expect"))
    numbers = validate_numbers(row.get("OpenCode") or row.get("openCode") or row.get("code") or row.get("numbers"))
    open_time = str(row.get("OpenTime") or row.get("opentime") or row.get("openTime") or row.get("time") or "--")
    now = utc_now()
    positions = {
        "wan": numbers[0],
        "qian": numbers[1],
        "bai": numbers[2],
        "shi": numbers[3],
        "ge": numbers[4],
    }
    metrics = make_metrics(numbers)
    latest = {
        "uid": f"period:{period}",
        "period": period,
        "opentime": open_time,
        "numbers": numbers,
        "positions": positions,
        "metrics": metrics,
        "rawJson": payload,
        "capturedAt": now,
    }
    return validate_payload(
        {
            "ok": True,
            "fresh": True,
            "proxiedAt": now,
            "fetchedAt": now,
            "period": period,
            "opentime": open_time,
            "numbers": numbers,
            "positions": positions,
            "metrics": metrics,
            "latestRecord": latest,
            "remote": {
                "ok": True,
                "fresh": True,
                "stale": False,
                "allowIntegrate": True,
                "updatedAt": now,
                "httpStatus": 200,
                "source": source_url or OFFICIAL_LATEST_URL,
                "status": "official-latest",
                "lastSync": {
                    "at": now,
                    "text": time.strftime("%Y/%m/%d %H:%M:%S", time.localtime()),
                    "trigger": "official-direct",
                    "latestPeriod": period,
                    "latestNumbers": numbers,
                    "latestTotalHewei": metrics["totalHewei"],
                },
            },
        }
    )


def normalize_state_payload(remote: Dict[str, Any], source_url: Optional[str] = None) -> Dict[str, Any]:
    summary = remote.get("summary") if isinstance(remote.get("summary"), dict) else None
    source = summary if summary else remote
    latest = source.get("latestRecord")
    if not isinstance(latest, dict):
        history = source.get("history")
        if isinstance(history, list) and history and isinstance(history[0], dict):
            latest = history[0]
    if not isinstance(latest, dict):
        raise RuntimeError("remote state does not contain latestRecord")

    period = validate_period(latest.get("period") or latest.get("uid"))
    numbers = validate_numbers(latest.get("numbers"))
    open_time = str(latest.get("opentime") or "--")
    now = utc_now()
    positions = {
        "wan": numbers[0],
        "qian": numbers[1],
        "bai": numbers[2],
        "shi": numbers[3],
        "ge": numbers[4],
    }
    metrics = make_metrics(numbers)
    return validate_payload(
        {
            "ok": True,
            "fresh": True,
            "proxiedAt": now,
            "fetchedAt": now,
            "period": period,
            "opentime": open_time,
            "numbers": numbers,
            "positions": positions,
            "metrics": metrics,
            "latestRecord": {
                "uid": str(latest.get("uid") or f"period:{period}"),
                "period": period,
                "opentime": open_time,
                "numbers": numbers,
                "positions": positions,
                "metrics": metrics,
                "rawJson": latest.get("rawJson"),
                "capturedAt": str(latest.get("capturedAt") or now),
            },
            "remote": {
                "ok": bool(source.get("ok", remote.get("ok", True))),
                "fresh": True,
                "stale": False,
                "allowIntegrate": True,
                "updatedAt": source.get("updatedAt", remote.get("updatedAt")),
                "lastSync": source.get("lastSync", remote.get("lastSync")),
                "historyCount": source.get("historyCount", remote.get("historyCount")),
                "predictionCount": source.get("predictionCount", remote.get("predictionCount")),
                "source": source_url or REMOTE_STATE_URL,
                "status": "health-summary" if summary else "state",
            },
        }
    )


def fetch_fresh_payload() -> Tuple[Dict[str, Any], int, str]:
    """同步拉取新鲜开奖；成功才返回。"""
    global LAST_FETCH_ERROR, LAST_FETCH_AT
    errors = []

    if OFFICIAL_LATEST_URL:
        try:
            payload, status = fetch_json_url(OFFICIAL_LATEST_URL, use_auth=False)
            normalized = normalize_official_payload(payload, source_url=OFFICIAL_LATEST_URL)
            LAST_FETCH_ERROR = None
            LAST_FETCH_AT = time.time()
            return normalized, status, OFFICIAL_LATEST_URL
        except Exception as exc:
            errors.append(f"official:{exc}")

    if ALLOW_STATE_FALLBACK and REMOTE_STATE_URL:
        health_url = REMOTE_HEALTH_URL or derive_health_url(REMOTE_STATE_URL)
        for url in (REMOTE_STATE_URL, health_url):
            if not url:
                continue
            try:
                payload, status = fetch_json_url(url, use_auth=True)
                normalized = normalize_state_payload(payload, source_url=url)
                LAST_FETCH_ERROR = None
                LAST_FETCH_AT = time.time()
                return normalized, status, url
            except Exception as exc:
                errors.append(f"{url}:{exc}")

    LAST_FETCH_ERROR = " | ".join(errors) if errors else "no upstream configured"
    LAST_FETCH_AT = time.time()
    raise RuntimeError("all upstream fetches failed: " + LAST_FETCH_ERROR)


def read_valid_cache() -> Optional[Dict[str, Any]]:
    cached = read_json_file(CACHE_FILE)
    if not cached:
        return None
    try:
        return validate_payload(cached)
    except Exception as exc:
        print(f"WARN_CACHE_INVALID {exc}", flush=True)
        return None


def stale_fallback_payload(cached: Dict[str, Any], error: str) -> Optional[Dict[str, Any]]:
    age = cache_age_seconds(cached)
    if age is None or age > MAX_STALE_SECONDS:
        return None
    out = dict(cached)
    out["ok"] = True
    out["fresh"] = False
    out["proxiedAt"] = utc_now()
    remote = dict(out.get("remote") or {})
    remote.update(
        {
            "ok": False,
            "fresh": False,
            "stale": True,
            "allowIntegrate": True,  # 仅短期回退：同/旧期不会新增；新期需新鲜源
            "staleAgeSeconds": round(age, 1),
            "maxStaleSeconds": MAX_STALE_SECONDS,
            "lastProxyError": error[:500],
            "refreshing": False,
        }
    )
    out["remote"] = remote
    return out


def persist_fresh(payload: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = validate_payload(payload)
    cleaned["fresh"] = True
    cleaned["remote"] = dict(cleaned.get("remote") or {})
    cleaned["remote"]["fresh"] = True
    cleaned["remote"]["stale"] = False
    cleaned["remote"]["allowIntegrate"] = True
    write_json_file(CACHE_FILE, cleaned)
    return cleaned


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "Penta5RemoteStateProxy/2.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # 忽略 TLS 误打到 HTTP 端口的噪音
        msg = fmt % args
        if "Bad request version" in msg or "Bad HTTP/0.9" in msg:
            return
        sys.stdout.write("[%s] %s\n" % (utc_now(), msg))
        sys.stdout.flush()

    def send_json(self, status: int, payload: Dict[str, Any]) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_json(204, {})

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path == "/health":
                cached = read_valid_cache()
                age = cache_age_seconds(cached) if cached else None
                self.send_json(
                    200,
                    {
                        "ok": True,
                        "time": utc_now(),
                        "officialLatestUrl": OFFICIAL_LATEST_URL,
                        "allowStateFallback": ALLOW_STATE_FALLBACK,
                        "remoteStateUrl": REMOTE_STATE_URL or None,
                        "requestTimeout": REQUEST_TIMEOUT,
                        "maxStaleSeconds": MAX_STALE_SECONDS,
                        "cacheFile": CACHE_FILE,
                        "cachePeriod": cached.get("period") if cached else None,
                        "cacheAgeSeconds": age,
                        "lastFetchError": LAST_FETCH_ERROR,
                        "hasAuth": bool(make_auth_header()),
                        "sslInsecure": SSL_INSECURE,
                    },
                )
                return

            if path not in ("/", "/latest"):
                self.send_json(
                    404,
                    {"ok": False, "error": "not found", "endpoints": ["GET /latest", "GET /health"]},
                )
                return

            # 关键路径：同步拉官方，成功才 200 + 写缓存
            if not REFRESH_LOCK.acquire(blocking=True, timeout=max(1.0, REQUEST_TIMEOUT + 2)):
                raise RuntimeError("proxy busy: another refresh in progress")
            try:
                fresh, status, source_url = fetch_fresh_payload()
                fresh["remote"]["httpStatus"] = status
                fresh["remote"]["source"] = source_url
                out = persist_fresh(fresh)
                self.send_json(200, out)
                return
            finally:
                REFRESH_LOCK.release()

        except Exception as exc:
            traceback.print_exc()
            cached = read_valid_cache()
            fallback = stale_fallback_payload(cached, str(exc)) if cached else None
            if fallback is not None:
                # 短窗口回退：期号未变时后端不会新增；禁止超龄缓存
                self.send_json(200, fallback)
                return
            self.send_json(
                502,
                {
                    "ok": False,
                    "fresh": False,
                    "time": utc_now(),
                    "error": str(exc),
                    "officialLatestUrl": OFFICIAL_LATEST_URL,
                    "maxStaleSeconds": MAX_STALE_SECONDS,
                    "cachePeriod": cached.get("period") if cached else None,
                    "remote": {
                        "ok": False,
                        "fresh": False,
                        "stale": True,
                        "allowIntegrate": False,
                        "lastProxyError": str(exc)[:500],
                    },
                },
            )


def main() -> int:
    if not OFFICIAL_LATEST_URL and not (ALLOW_STATE_FALLBACK and REMOTE_STATE_URL):
        print("OFFICIAL_LATEST_URL is required (or enable PROXY_ALLOW_STATE_FALLBACK with REMOTE_STATE_URL)", file=sys.stderr)
        return 2
    if SSL_INSECURE:
        print("WARNING: SSL verification disabled", file=sys.stderr)

    server = ThreadingHTTPServer((HOST, PORT), ProxyHandler)
    print(f"Penta5 remote state proxy listening on http://{HOST}:{PORT}/latest", flush=True)
    print(f"Official URL: {OFFICIAL_LATEST_URL}", flush=True)
    print(f"State fallback: {ALLOW_STATE_FALLBACK} url={REMOTE_STATE_URL or '-'}", flush=True)
    print(f"Max stale seconds: {MAX_STALE_SECONDS}; timeout: {REQUEST_TIMEOUT}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("Proxy stopped", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
