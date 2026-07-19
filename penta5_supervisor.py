#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿里云常驻监督：保算力在线 + 降智升级修复。

职责（仅跑在 /root/penta5-runner，由 pm2 penta5-supervisor 托管）：
1) L0 进程：penta5-runner / penta5-rds-api / penta5-source-proxy 掉线则 pm2 restart
2) L0 健康：8787 / 3000 / 3001 /health 不通则重启对应进程
3) L1 模型：读 /health.summary.modelAudit + /state.modelWatchdog
4) L2：完整性反复失败或 watchdog-error → POST /sync
5) L3：L2 后仍失败 → pm2 restart penta5-runner（带冷却，防重启风暴）

不启本机后台。风暴模式期间可停/（可选）恢复机器人；非风暴急救仍用 rescue_ali_stop_robots。
开奖源保持单通道。告警写 data/penta5_alerts.log，可选 PENTA5_ALERT_WEBHOOK_URL。
"""

from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(os.environ.get("PENTA5_RUNNER_ROOT", "/root/penta5-runner")).resolve()
ENV_FILE = ROOT / ".env"
LOG_FILE = Path(os.environ.get("PENTA5_SUPERVISOR_LOG", str(ROOT / "data" / "penta5_supervisor.log")))
STATE_FILE = Path(os.environ.get("PENTA5_SUPERVISOR_STATE", str(ROOT / "data" / "penta5_supervisor_state.json")))

RUNNER_HEALTH = os.environ.get("PENTA5_RUNNER_HEALTH", "http://127.0.0.1:3001/health")
RUNNER_STATE = os.environ.get("PENTA5_RUNNER_STATE", "http://127.0.0.1:3001/state")
RUNNER_SYNC = os.environ.get("PENTA5_RUNNER_SYNC", "http://127.0.0.1:3001/sync")
RUNNER_LOGIN = os.environ.get("PENTA5_RUNNER_LOGIN", "http://127.0.0.1:3001/login")
RUNNER_EXCLUSION_MODEL = os.environ.get("PENTA5_RUNNER_EXCLUSION_MODEL", "http://127.0.0.1:3001/exclusion-model")
RUNNER_STABLE_MATH = os.environ.get("PENTA5_RUNNER_STABLE_MATH", "http://127.0.0.1:3001/stable-math-filter")
RDS_HEALTH = os.environ.get("PENTA5_RDS_HEALTH", "http://127.0.0.1:8787/health")
PROXY_HEALTH = os.environ.get("PENTA5_PROXY_HEALTH", "http://127.0.0.1:3000/health")

CORE_APPS = [
    "penta5-runner",
    "penta5-rds-api",
    "penta5-source-proxy",
]

APP_BY_PROBE = {
    "runner": "penta5-runner",
    "rds": "penta5-rds-api",
    "proxy": "penta5-source-proxy",
}

STORM_MODE_FILE = Path(os.environ.get("PENTA5_STORM_MODE_FILE", str(ROOT / "data" / "storm_mode.json")))
ALERT_WEBHOOK_URL = os.environ.get("PENTA5_ALERT_WEBHOOK_URL", "").strip()
ALERT_LOG_FILE = Path(os.environ.get("PENTA5_ALERT_LOG", str(ROOT / "data" / "penta5_alerts.log")))
STORM_RESUME_ROBOTS = os.environ.get("PENTA5_STORM_RESUME_ROBOTS", "0").strip() in {"1", "true", "yes", "on"}
ROBOT_APPS = ["penta5-auto-robot-1", "penta5-auto-robot-2"]
ROBOT_STATE_DIR = ROOT / "robot_runtime" / "robot_state"
ROBOT_HEALTH_FILES = {
    "penta5-auto-robot-1": ROBOT_STATE_DIR / "penta5_auto_shooter_health_ali_1.json",
    "penta5-auto-robot-2": ROBOT_STATE_DIR / "penta5_auto_shooter_health_ali_2.json",
}
DEFAULT_INTERVAL = int(os.environ.get("PENTA5_SUPERVISOR_INTERVAL", "90"))
RESTART_COOLDOWN_SEC = int(os.environ.get("PENTA5_SUPERVISOR_RESTART_COOLDOWN", "300"))
INTEGRITY_FAIL_THRESHOLD = int(os.environ.get("PENTA5_SUPERVISOR_INTEGRITY_FAILS", "5"))
WATCHDOG_ERROR_THRESHOLD = int(os.environ.get("PENTA5_SUPERVISOR_WATCHDOG_ERRORS", "2"))
SYNC_COOLDOWN_SEC = int(os.environ.get("PENTA5_SUPERVISOR_SYNC_COOLDOWN", "120"))
EXCLUSION_REFRESH_COOLDOWN_SEC = int(os.environ.get("PENTA5_SUPERVISOR_EXCLUSION_REFRESH_COOLDOWN", "180"))
TIP_NOT_READY_CONFIRM_TICKS = int(os.environ.get("PENTA5_SUPERVISOR_TIP_CONFIRM_TICKS", "3"))
HTTP_TIMEOUT = float(os.environ.get("PENTA5_SUPERVISOR_HTTP_TIMEOUT", "20"))
SYNC_HTTP_TIMEOUT = float(os.environ.get("PENTA5_SUPERVISOR_SYNC_HTTP_TIMEOUT", "120"))
ALERT_COOLDOWN_SEC = int(os.environ.get("PENTA5_ALERT_COOLDOWN", "180"))
ROBOT_ACTION_COOLDOWN_SEC = int(os.environ.get("PENTA5_ROBOT_ACTION_COOLDOWN", "120"))
RUNNER_WARMUP_SEC = int(os.environ.get("PENTA5_RUNNER_WARMUP_SEC", "120"))
RDS_LAG_ALERT_PERIODS = int(os.environ.get("PENTA5_SUPERVISOR_RDS_LAG_PERIODS", "3"))
ROBOT_HEALTH_MAX_AGE_SEC = int(os.environ.get("PENTA5_ROBOT_HEALTH_MAX_AGE", "180"))
ROBOT_PERIOD_LAG_LIMIT = int(os.environ.get("PENTA5_ROBOT_PERIOD_LAG_LIMIT", "3"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(message: str) -> None:
    line = f"[{now_text()}] {message}"
    print(line, flush=True)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass


def load_dotenv(path: Path) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not path.exists():
        return result
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        log(f"read .env failed: {exc}")
        return result
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            result[key] = value
            # 注入进程环境，确保 Basic 鉴权与子进程都能读到 PANEL_*
            if key not in os.environ or not str(os.environ.get(key) or "").strip():
                os.environ[key] = value
    return result


def read_supervisor_state() -> Dict[str, Any]:
    try:
        if STATE_FILE.exists():
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        log(f"read supervisor state failed: {exc}")
    return {}


def read_json_object(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def write_supervisor_state(data: Dict[str, Any]) -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["updatedAt"] = now_iso()
        STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        log(f"write supervisor state failed: {exc}")


def run_cmd(argv: List[str], timeout: float = 60) -> Tuple[int, str, str]:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return completed.returncode, completed.stdout or "", completed.stderr or ""
    except Exception as exc:
        return 1, "", str(exc)


def pm2_status_map() -> Dict[str, str]:
    code, out, err = run_cmd(["pm2", "jlist"], timeout=30)
    if code != 0:
        log(f"pm2 jlist failed: {err or out}")
        return {}
    try:
        items = json.loads(out)
    except Exception as exc:
        log(f"pm2 jlist parse failed: {exc}")
        return {}
    result: Dict[str, str] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        status = str((item.get("pm2_env") or {}).get("status") or "")
        if name:
            result[name] = status
    return result


def parse_time(value: str) -> float:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text).timestamp()
    except Exception:
        pass
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        return 0.0


def cooldown_ok(state: Dict[str, Any], key: str, cooldown_sec: int) -> bool:
    last = state.get(key)
    if not last:
        return True
    try:
        last_ms = parse_time(str(last))
    except Exception:
        return True
    return (time.time() - last_ms) >= cooldown_sec


def mark_action(state: Dict[str, Any], key: str) -> None:
    state[key] = now_iso()


def restart_app(app: str, state: Dict[str, Any], reason: str) -> bool:
    cooldown_key = f"lastRestartAt:{app}"
    if not cooldown_ok(state, cooldown_key, RESTART_COOLDOWN_SEC):
        log(f"skip restart {app}: cooldown ({reason})")
        return False
    log(f"pm2 restart {app}: {reason}")
    code, out, err = run_cmd(["pm2", "restart", app, "--update-env"], timeout=90)
    mark_action(state, cooldown_key)
    state["lastRestartApp"] = app
    state["lastRestartReason"] = reason
    state["lastRestartAt"] = now_iso()
    if code != 0:
        log(f"restart failed {app}: {err or out}")
        return False
    log(f"restart ok {app}")
    return True


class PanelClient:
    """面板客户端：写操作必须走 admin Basic 鉴权。

    /login 密码口令只会发 viewer cookie；POST /sync 需要 admin。
    因此监督统一带 PANEL_USER:PANEL_PASSWORD 的 Basic Authorization。
    """

    def __init__(self, env: Dict[str, str]) -> None:
        self.env = env
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self._logged_in = False
        self._auth_mode = ""

    def _admin_user(self) -> str:
        return str(
            os.environ.get("PANEL_USER")
            or self.env.get("PANEL_USER")
            or os.environ.get("PENTA5_PANEL_USER")
            or ""
        )

    def _admin_password(self) -> str:
        return str(
            os.environ.get("PANEL_PASSWORD")
            or self.env.get("PANEL_PASSWORD")
            or os.environ.get("PENTA5_PANEL_PASSWORD")
            or ""
        )

    def _viewer_password(self) -> str:
        return str(
            os.environ.get("PANEL_VIEWER_PASSWORD")
            or self.env.get("PANEL_VIEWER_PASSWORD")
            or ""
        )

    def _basic_auth_header(self) -> str:
        user = self._admin_user()
        password = self._admin_password()
        if not user or not password:
            return ""
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        return f"Basic {token}"

    def login(self) -> bool:
        """保留 cookie 登录仅作 GET 兜底；admin 写操作不依赖此路径。"""
        password = self._admin_password() or self._viewer_password()
        if not password:
            log("PANEL_PASSWORD missing; /state /sync may fail if auth enabled")
            self._logged_in = False
            return False
        payload = json.dumps({"password": password}).encode("utf-8")
        req = urllib.request.Request(
            RUNNER_LOGIN,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with self.opener.open(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", "replace")
            data = json.loads(body) if body else {}
            self._logged_in = bool(data.get("ok", True))
            self._auth_mode = f"cookie:{data.get('role') or 'unknown'}"
            return self._logged_in
        except Exception as exc:
            log(f"login failed: {exc}")
            self._logged_in = False
            return False

    def fetch_json(self, url: str, method: str = "GET", body: Optional[bytes] = None, timeout: Optional[float] = None) -> Tuple[bool, Dict[str, Any], str]:
        headers = {"Accept": "application/json,text/plain,*/*", "Cache-Control": "no-cache"}
        if method == "POST" and body is not None:
            headers["Content-Type"] = "application/json"
        basic = self._basic_auth_header()
        if basic:
            headers["Authorization"] = basic
            self._auth_mode = "basic-admin"
        sep = "&" if "?" in url else "?"
        full = f"{url}{sep}_={int(time.time() * 1000)}"
        req = urllib.request.Request(full, data=body, headers=headers, method=method)
        wait = HTTP_TIMEOUT if timeout is None else float(timeout)
        try:
            with self.opener.open(req, timeout=wait) as resp:
                text = resp.read().decode(resp.headers.get_content_charset() or "utf-8", "replace")
            data = json.loads(text) if text else {}
            if not isinstance(data, dict):
                return False, {}, "non-object-json"
            return True, data, ""
        except urllib.error.HTTPError as exc:
            # Basic 未配置时才尝试 cookie；已带 Basic 仍 403 说明账号不是 admin，禁止误升级重启
            if exc.code in (401, 403) and not basic and not self._logged_in:
                if self.login():
                    return self.fetch_json(url, method=method, body=body, timeout=timeout)
            return False, {}, f"http-{exc.code}"
        except Exception as exc:
            return False, {}, str(exc)


def probe_http(url: str) -> Tuple[bool, Dict[str, Any], str]:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "Cache-Control": "no-cache"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace")
        data = json.loads(text) if text else {}
        if not isinstance(data, dict):
            return False, {}, "non-object-json"
        return True, data, ""
    except Exception as exc:
        return False, {}, str(exc)


def ensure_core_processes(state: Dict[str, Any]) -> Dict[str, Any]:
    status = pm2_status_map()
    report: Dict[str, Any] = {"pm2": status, "restarts": []}
    for app in CORE_APPS:
        st = status.get(app, "missing")
        if st != "online":
            ok = restart_app(app, state, f"pm2-status={st}")
            report["restarts"].append({"app": app, "reason": f"pm2-status={st}", "ok": ok})
    return report


def ensure_health_probes(state: Dict[str, Any], client: PanelClient) -> Dict[str, Any]:
    report: Dict[str, Any] = {"probes": {}, "restarts": []}

    ok_rds, rds, err_rds = probe_http(RDS_HEALTH)
    report["probes"]["rds"] = {"ok": ok_rds and bool(rds.get("ok")), "error": err_rds, "latestPeriod": rds.get("latestPeriod")}
    if not report["probes"]["rds"]["ok"]:
        app = APP_BY_PROBE["rds"]
        restarted = restart_app(app, state, f"rds-health-fail:{err_rds or rds.get('error')}")
        report["restarts"].append({"app": app, "reason": "rds-health", "ok": restarted})

    ok_proxy, proxy, err_proxy = probe_http(PROXY_HEALTH)
    report["probes"]["proxy"] = {"ok": ok_proxy and bool(proxy.get("ok")), "error": err_proxy, "cachePeriod": proxy.get("cachePeriod")}
    if not report["probes"]["proxy"]["ok"]:
        app = APP_BY_PROBE["proxy"]
        restarted = restart_app(app, state, f"proxy-health-fail:{err_proxy}")
        report["restarts"].append({"app": app, "reason": "proxy-health", "ok": restarted})

    # /health 免鉴权（轻量探针）；写操作与 /state 仍需 Basic admin 或 cookie。
    # 切勿在已配置 Basic 时回退到无鉴权 probe_http 误判：忙时 timeout 会被覆盖成 401，触发重启风暴。
    if not client._basic_auth_header() and not client._logged_in:
        client.login()
    ok_runner, runner, err_runner = client.fetch_json(RUNNER_HEALTH)
    if not ok_runner and not client._basic_auth_header():
        ok_runner, runner, err_runner = probe_http(RUNNER_HEALTH)
        if not ok_runner and client.login():
            ok_runner, runner, err_runner = client.fetch_json(RUNNER_HEALTH)
    summary = runner.get("summary") if isinstance(runner.get("summary"), dict) else {}
    audit = summary.get("modelAudit") if isinstance(summary.get("modelAudit"), dict) else {}
    storm = summary.get("stormMode") if isinstance(summary.get("stormMode"), dict) else {}
    syncing = bool(summary.get("syncing") or runner.get("syncing"))
    prediction_build_running = bool(summary.get("predictionBuildRunning") or runner.get("predictionBuildRunning"))
    report["probes"]["runner"] = {
        "ok": ok_runner and bool(runner.get("ok")),
        "error": err_runner,
        "historyCount": summary.get("historyCount"),
        "sharedHistorySource": summary.get("sharedHistorySource"),
        "latestPeriod": summary.get("latestPeriod"),
        "auditOk": audit.get("ok"),
        "integrityIssueCount": audit.get("integrityIssueCount"),
        "stabilityIssueCount": audit.get("stabilityIssueCount"),
        "modelVersion": audit.get("modelVersion") or summary.get("modelVersion"),
        "syncing": syncing,
        "predictionBuildRunning": prediction_build_running,
        "stormActive": bool(storm.get("active")),
        "tipReady": summary.get("tipReady"),
        "tipReadyIssueCount": summary.get("tipReadyIssueCount"),
    }
    if not report["probes"]["runner"]["ok"]:
        err_text = str(err_runner or "")
        auth_fail = err_text in {"http-401", "http-403"} or "401" in err_text or "403" in err_text or "Unauthorized" in err_text
        busy_fail = syncing or prediction_build_running or "timed out" in err_text.lower() or "timeout" in err_text.lower()
        if auth_fail:
            log(f"skip runner restart: auth error ({err_text}); check PANEL_USER/PANEL_PASSWORD in .env")
            report["restarts"].append({"app": "penta5-runner", "reason": "runner-health-skipped-auth", "ok": False})
        elif busy_fail:
            log(f"skip runner restart: busy/timeout during health fail ({err_text})")
            report["restarts"].append({"app": "penta5-runner", "reason": "runner-health-skipped-busy", "ok": False})
        else:
            app = APP_BY_PROBE["runner"]
            restarted = restart_app(app, state, f"runner-health-fail:{err_runner}")
            report["restarts"].append({"app": app, "reason": "runner-health", "ok": restarted})
    report["summary"] = summary
    report["modelAudit"] = audit
    report["stormMode"] = storm
    report["syncing"] = syncing
    report["predictionBuildRunning"] = bool(summary.get("predictionBuildRunning"))
    return report


def ensure_robot_health(state: Dict[str, Any], health_report: Dict[str, Any]) -> Dict[str, Any]:
    """Detect a robot that is process-online but no longer reading fresh state."""
    status_map = pm2_status_map()
    runner_probe = (health_report.get("probes") or {}).get("runner") or {}
    runner_period = str(runner_probe.get("latestPeriod") or "")
    runner_ready = bool(runner_probe.get("ok")) and not bool(runner_probe.get("syncing"))
    report: Dict[str, Any] = {"robots": {}, "restarts": []}

    for app in ROBOT_APPS:
        pm2_status = str(status_map.get(app) or "missing")
        health_file = ROBOT_HEALTH_FILES[app]
        payload = read_json_object(health_file)
        status = str(payload.get("status") or "")
        updated_at = str(payload.get("updated_at") or "")
        updated_epoch = parse_time(updated_at)
        if updated_epoch <= 0:
            try:
                updated_epoch = health_file.stat().st_mtime
            except Exception:
                updated_epoch = 0.0
        age_seconds = max(0.0, time.time() - updated_epoch) if updated_epoch > 0 else float("inf")
        last_seen_period = str(payload.get("last_seen_period") or "")
        period_lag_value = period_lag(runner_period, last_seen_period) if runner_period and last_seen_period else None
        active = pm2_status == "online"
        fresh_status = status in {"state-ok", "scheduled-wait"}
        fresh_age = age_seconds <= max(30, ROBOT_HEALTH_MAX_AGE_SEC)
        fresh_period = status == "scheduled-wait" or period_lag_value is None or period_lag_value <= max(0, ROBOT_PERIOD_LAG_LIMIT)
        ok = (not active) or (fresh_status and fresh_age and fresh_period)
        reason = ""
        if active and not fresh_status:
            reason = f"robot-status={status or 'missing'}"
        elif active and not fresh_age:
            reason = f"heartbeat-age={int(age_seconds) if age_seconds != float('inf') else 'missing'}s"
        elif active and not fresh_period:
            reason = f"period-lag={period_lag_value}"

        item = {
            "ok": ok,
            "pm2": pm2_status,
            "status": status or "missing",
            "lastSeenPeriod": last_seen_period,
            "runnerPeriod": runner_period,
            "periodLag": period_lag_value,
            "heartbeatAgeSeconds": None if age_seconds == float("inf") else round(age_seconds, 1),
            "error": str(payload.get("error") or "")[:300],
            "reason": reason,
        }
        report["robots"][app] = item

        # A stopped robot may be intentionally paused by storm/tip policy. Only
        # restart a process that claims to be online yet is stale, and only
        # while the shared runner is itself ready.
        if active and not ok and runner_ready:
            restarted = restart_app(app, state, f"robot-health-fail:{reason}")
            report["restarts"].append({"app": app, "reason": reason, "ok": restarted})
            item["restartRequested"] = restarted

    return report


def runner_pipeline_busy(health_report: Dict[str, Any], state_payload: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
    summary = health_report.get("summary") if isinstance(health_report.get("summary"), dict) else {}
    syncing = bool(health_report.get("syncing"))
    prediction_build_running = bool(health_report.get("predictionBuildRunning"))
    if isinstance(summary, dict):
        if summary.get("syncing") is not None:
            syncing = bool(summary.get("syncing"))
        if summary.get("predictionBuildRunning") is not None:
            prediction_build_running = bool(summary.get("predictionBuildRunning"))
    if isinstance(state_payload, dict):
        if state_payload.get("syncing") is not None:
            syncing = bool(state_payload.get("syncing"))
        if state_payload.get("predictionBuildRunning") is not None:
            prediction_build_running = bool(state_payload.get("predictionBuildRunning"))
    return {
        "syncing": syncing,
        "predictionBuildRunning": prediction_build_running,
        "busy": syncing or prediction_build_running,
    }


def integrity_repairable(issues: List[Any], integrity_count: int = 0) -> bool:
    """结构性降智才升级；observe-only / 纯低命中不触发进程重启。"""
    # 不再仅凭 integrityIssueCount>0 升级：/health 高峰时 issues 列表可能未带上。
    if not issues:
        return False
    saw_non_observe = False
    for issue in issues:
        if not isinstance(issue, dict):
            return True
        action = str(issue.get("action") or "")
        itype = str(issue.get("type") or "")
        if action == "observe-only":
            continue
        if itype == "watchdog-error":
            return True
        if action in {"regenerate-current-prediction", "clear-lock-and-regenerate", ""}:
            return True
        if itype in {"missing-extra", "missing-snapshot", "seal-invalid", "version-mismatch", "structure"}:
            return True
        if issue.get("key") or issue.get("message") or issue.get("type"):
            saw_non_observe = True
    return saw_non_observe


def is_transient_tip_structure_issue(issues: List[Any]) -> bool:
    """开奖后 ensure/seal 窗口的瞬时结构问题，不应直接重启。"""
    if not issues:
        return False
    for issue in issues:
        if not isinstance(issue, dict):
            return False
        blob = " ".join(
            str(issue.get(k) or "")
            for k in ("type", "key", "message", "label", "action")
        ).lower()
        if any(
            token in blob
            for token in (
                "incomplete",
                "seal",
                "missing-snapshot",
                "tipready",
                "tip-ready",
                "structure",
                "ensure",
                "regenerate",
            )
        ):
            continue
        if str(issue.get("action") or "") == "observe-only":
            continue
        return False
    return True


def is_transient_network_error(err: Any) -> bool:
    text = str(err or "").lower()
    tokens = (
        "timed out",
        "timeout",
        "connection refused",
        "connection reset",
        "remote end closed",
        "broken pipe",
        "temporarily unavailable",
        "errno 111",
        "errno 104",
    )
    return any(token in text for token in tokens)


def period_lag(local_period: Any, rds_period: Any) -> int:
    """同日 YYYYMMDDNNN 期号落后期数；无法比较时返回 0。"""
    local = str(local_period or "").strip()
    remote = str(rds_period or "").strip()
    if not local or not remote or len(local) < 11 or len(remote) < 11:
        return 0
    if local[:8] != remote[:8]:
        return 0
    try:
        return max(0, int(local[8:]) - int(remote[8:]))
    except Exception:
        return 0


def read_storm_flag_file() -> Dict[str, Any]:
    try:
        if STORM_MODE_FILE.exists():
            data = json.loads(STORM_MODE_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as exc:
        log(f"read storm flag failed: {exc}")
    return {}


def write_alert_log(event: str, detail: Dict[str, Any]) -> None:
    line = f"[{now_text()}] {event} {json.dumps(detail, ensure_ascii=False)}"
    try:
        ALERT_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with ALERT_LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:
        pass
    log(f"ALERT {event}: {json.dumps(detail, ensure_ascii=False)}")


def send_alert(state: Dict[str, Any], event: str, detail: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> bool:
    cooldown_key = f"lastAlertAt:{event}"
    if not cooldown_ok(state, cooldown_key, ALERT_COOLDOWN_SEC):
        return False
    write_alert_log(event, detail)
    mark_action(state, cooldown_key)
    webhook = ALERT_WEBHOOK_URL or str((env or {}).get("PENTA5_ALERT_WEBHOOK_URL") or "").strip()
    if not webhook:
        return True
    text = f"[Penta5] {event} | " + " | ".join(f"{k}={detail.get(k)}" for k in list(detail.keys())[:8])
    # 兼容钉钉 / 企微 / 飞书自定义机器人的常见字段
    payload = {
        "msgtype": "text",
        "msg_type": "text",
        "text": {"content": text},
        "content": {"text": text},
        "event": event,
        "detail": detail,
        "ts": now_iso(),
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            resp.read()
        log(f"webhook sent event={event}")
        return True
    except Exception as exc:
        log(f"webhook failed event={event}: {exc}")
        return False


def stop_robots(state: Dict[str, Any], reason: str) -> Dict[str, Any]:
    if not cooldown_ok(state, "lastRobotStopAt", ROBOT_ACTION_COOLDOWN_SEC):
        return {"ok": False, "skipped": True, "reason": "cooldown"}
    log(f"storm stop robots: {reason}")
    results = []
    for app in ROBOT_APPS:
        code, out, err = run_cmd(["pm2", "stop", app], timeout=60)
        results.append({"app": app, "ok": code == 0, "error": (err or out)[:200]})
    run_cmd(["pm2", "save"], timeout=30)
    mark_action(state, "lastRobotStopAt")
    state["robotsPausedByStorm"] = True
    return {"ok": all(item.get("ok") for item in results), "results": results, "reason": reason}


def resume_robots(state: Dict[str, Any], reason: str) -> Dict[str, Any]:
    if not STORM_RESUME_ROBOTS:
        return {"ok": False, "skipped": True, "reason": "auto-resume-disabled"}
    status = pm2_status_map()
    stopped_robots = [app for app in ROBOT_APPS if status.get(app) != "online"]
    if not stopped_robots and not state.get("robotsPausedByStorm"):
        return {"ok": False, "skipped": True, "reason": "robots-already-online"}
    if not cooldown_ok(state, "lastRobotResumeAt", ROBOT_ACTION_COOLDOWN_SEC):
        return {"ok": False, "skipped": True, "reason": "cooldown"}
    log(f"storm resume robots: {reason}")
    results = []
    for app in ROBOT_APPS:
        code, out, err = run_cmd(["pm2", "restart", app, "--update-env"], timeout=60)
        results.append({"app": app, "ok": code == 0, "error": (err or out)[:200]})
    run_cmd(["pm2", "save"], timeout=30)
    mark_action(state, "lastRobotResumeAt")
    state["robotsPausedByStorm"] = False
    return {"ok": all(item.get("ok") for item in results), "results": results, "reason": reason}


def handle_storm_mode(
    state: Dict[str, Any],
    health_report: Dict[str, Any],
    state_payload: Dict[str, Any],
    env: Dict[str, str],
) -> Dict[str, Any]:
    storm = {}
    if isinstance(health_report.get("stormMode"), dict) and health_report.get("stormMode"):
        storm = health_report.get("stormMode") or {}
    if isinstance(state_payload.get("stormMode"), dict) and state_payload.get("stormMode"):
        storm = state_payload.get("stormMode") or {}
    flag = read_storm_flag_file()
    if flag and not storm:
        storm = flag
    active = bool(storm.get("active"))
    prev_active = bool(state.get("stormActive"))
    result: Dict[str, Any] = {
        "active": active,
        "reason": storm.get("reason") or "",
        "autoTop6CurrentMiss": storm.get("autoTop6CurrentMiss"),
        "threeDuishuCurrentMiss": storm.get("threeDuishuCurrentMiss"),
        "robotsPaused": storm.get("robotsPaused"),
        "changed": prev_active != active,
        "actions": [],
    }
    if active and not prev_active:
        stop_result = stop_robots(state, str(storm.get("reason") or "storm-enter"))
        result["actions"].append({"type": "stop-robots", **stop_result})
        send_alert(
            state,
            "storm-enter",
            {
                "reason": storm.get("reason"),
                "autoTop6Miss": storm.get("autoTop6CurrentMiss"),
                "threeDuishuMiss": storm.get("threeDuishuCurrentMiss"),
                "period": (health_report.get("probes") or {}).get("runner", {}).get("latestPeriod"),
            },
            env,
        )
    elif (not active) and prev_active:
        resume_result = resume_robots(state, str(storm.get("reason") or "storm-exit"))
        result["actions"].append({"type": "resume-robots", **resume_result})
        send_alert(
            state,
            "storm-exit",
            {
                "reason": storm.get("reason"),
                "autoTop6Miss": storm.get("autoTop6CurrentMiss"),
                "threeDuishuMiss": storm.get("threeDuishuCurrentMiss"),
                "resumeRobots": STORM_RESUME_ROBOTS,
            },
            env,
        )
    elif active:
        # 保持停射：若机器人又被拉起则再停
        status = pm2_status_map()
        online_robots = [app for app in ROBOT_APPS if status.get(app) == "online"]
        if online_robots:
            stop_result = stop_robots(state, "storm-keep-paused")
            result["actions"].append({"type": "stop-robots-keepalive", **stop_result})
    state["stormActive"] = active
    state["lastStorm"] = result
    return result


def ensure_exclusion_model(state: Dict[str, Any], client: PanelClient, health_report: Dict[str, Any]) -> Dict[str, Any]:
    """L1 Exclusion Model：探活 + 期号对齐 + 失败时 refresh。"""
    report: Dict[str, Any] = {"ok": False, "actions": []}
    runner_period = str(((health_report.get("probes") or {}).get("runner") or {}).get("latestPeriod") or "")
    syncing = bool(health_report.get("syncing") or ((health_report.get("probes") or {}).get("runner") or {}).get("syncing"))
    prediction_build = bool(
        health_report.get("predictionBuildRunning")
        or ((health_report.get("probes") or {}).get("runner") or {}).get("predictionBuildRunning")
    )
    busy = syncing or prediction_build

    if not client._basic_auth_header() and not client._logged_in:
        client.login()
    ok, payload, err = client.fetch_json(RUNNER_EXCLUSION_MODEL)
    model = payload.get("model") if isinstance(payload.get("model"), dict) else {}
    report["error"] = err
    report["version"] = model.get("version")
    report["latestPeriod"] = model.get("latestPeriod")
    report["runnerPeriod"] = runner_period

    stable = (model.get("exclusions") or {}).get("stableMath") if isinstance(model.get("exclusions"), dict) else {}
    hewei = (model.get("exclusions") or {}).get("hewei") if isinstance(model.get("exclusions"), dict) else {}
    report["stableShapeCount"] = len((stable or {}).get("excludeShapes") or [])
    report["heweiTotal"] = (hewei or {}).get("total")

    lag = period_lag(runner_period, report["latestPeriod"])
    # 开奖窗同步中：允许落后 1 期，不当硬失败
    soft_mismatch = bool(runner_period and report["latestPeriod"] and runner_period != report["latestPeriod"])
    hard_mismatch = soft_mismatch and not (busy and lag <= 1)
    report["periodMismatch"] = soft_mismatch
    report["periodLag"] = lag
    report["ok"] = bool(
        ok
        and payload.get("ok")
        and str(model.get("version") or "").startswith("exclusion-model-v")
    )
    if soft_mismatch and busy and lag <= 1:
        report["ok"] = True
        state["exclusionNeedsCatchup"] = True
        report["actions"].append({"type": "exclusion-period-lag-soft", "lag": lag, "busy": True})

    probe_transient = (not ok) and is_transient_network_error(err)
    if probe_transient and busy:
        report["ok"] = True
        state["exclusionNeedsCatchup"] = True
        report["actions"].append({"type": "exclusion-probe-deferred-busy", "error": err})
        return report

    catchup = bool(state.get("exclusionNeedsCatchup")) and not busy and soft_mismatch
    needs_refresh = ((not report["ok"]) and not probe_transient) or hard_mismatch or catchup
    refresh_cooldown = 45 if catchup else EXCLUSION_REFRESH_COOLDOWN_SEC
    if needs_refresh and cooldown_ok(state, "lastExclusionModelRefreshAt", refresh_cooldown):
        reason = (
            "catchup-after-soft-lag" if catchup
            else ("period-mismatch" if hard_mismatch else f"probe-fail:{err or 'bad-payload'}")
        )
        log(f"L1 exclusion-model refresh: {reason}")
        ok_refresh, refresh_payload, err_refresh = client.fetch_json(
            f"{RUNNER_EXCLUSION_MODEL}?refresh=1",
            timeout=max(HTTP_TIMEOUT, 60),
        )
        mark_action(state, "lastExclusionModelRefreshAt")
        refresh_model = refresh_payload.get("model") if isinstance(refresh_payload.get("model"), dict) else {}
        report["actions"].append({
            "type": "exclusion-model-refresh",
            "reason": reason,
            "ok": bool(ok_refresh and refresh_payload.get("ok")),
            "error": err_refresh,
            "latestPeriod": refresh_model.get("latestPeriod"),
        })
        if ok_refresh and refresh_payload.get("ok"):
            report["ok"] = True
            report["latestPeriod"] = refresh_model.get("latestPeriod")
            report["version"] = refresh_model.get("version")
            report["periodMismatch"] = bool(
                runner_period and report["latestPeriod"] and runner_period != report["latestPeriod"]
            )
            if not report["periodMismatch"]:
                state["exclusionNeedsCatchup"] = False
        elif is_transient_network_error(err_refresh) and busy:
            report["ok"] = True
            report["actions"].append({"type": "exclusion-refresh-deferred-busy", "error": err_refresh})
    elif needs_refresh:
        report["actions"].append({"type": "exclusion-model-refresh-skipped", "reason": "cooldown"})
    elif report["ok"] and not soft_mismatch:
        state["exclusionNeedsCatchup"] = False

    if not report["ok"] and not (probe_transient and busy):
        send_alert(
            state,
            "exclusion-model-fail",
            {
                "error": report.get("error"),
                "version": report.get("version"),
                "latestPeriod": report.get("latestPeriod"),
                "runnerPeriod": runner_period,
            },
            None,
        )
    return report


def ensure_stable_math_filter(state: Dict[str, Any], client: PanelClient, health_report: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """更稳排除摘要探活；默认响应不得带 rules.details。"""
    report: Dict[str, Any] = {"ok": False, "actions": []}
    health_report = health_report or {}
    busy = bool(health_report.get("syncing") or health_report.get("predictionBuildRunning"))
    if not client._basic_auth_header() and not client._logged_in:
        client.login()
    ok, payload, err = client.fetch_json(RUNNER_STABLE_MATH)
    filt = payload.get("filter") if isinstance(payload.get("filter"), dict) else {}
    report["ok"] = bool(ok and payload.get("ok") and filt)
    report["error"] = err
    report["slim"] = filt.get("slim")
    report["hasRules"] = "rules" in filt
    report["latestPeriod"] = filt.get("latest_period")
    if (not ok) and is_transient_network_error(err) and busy:
        report["ok"] = True
        report["actions"].append({"type": "stable-math-deferred-busy", "error": err})
        return report
    if report["ok"] and report["hasRules"]:
        report["ok"] = False
        report["error"] = "stable-math-not-slim"
        if cooldown_ok(state, "lastStableMathRefreshAt", EXCLUSION_REFRESH_COOLDOWN_SEC):
            ok_refresh, refresh_payload, err_refresh = client.fetch_json(f"{RUNNER_STABLE_MATH}?refresh=1")
            mark_action(state, "lastStableMathRefreshAt")
            report["actions"].append({
                "type": "stable-math-refresh",
                "ok": bool(ok_refresh and refresh_payload.get("ok")),
                "error": err_refresh,
            })
    return report


def escalate_model_quality(state: Dict[str, Any], client: PanelClient, health_report: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    result: Dict[str, Any] = {"level": "ok", "actions": [], "statePayload": {}}
    audit = health_report.get("modelAudit") or {}
    integrity_count = int(audit.get("integrityIssueCount") or 0)
    audit_ok = bool(audit.get("ok")) if "ok" in audit else integrity_count == 0
    syncing = bool(health_report.get("syncing"))

    if not client._basic_auth_header() and not client._logged_in:
        client.login()
    ok_state, state_payload, err_state = client.fetch_json(RUNNER_STATE)
    watchdog = {}
    if ok_state:
        result["statePayload"] = state_payload
        watchdog = state_payload.get("modelWatchdog") or {}
        if not isinstance(watchdog, dict):
            watchdog = {}
        state_audit = state_payload.get("modelAudit")
        if isinstance(state_audit, dict) and state_audit:
            audit = state_audit
            integrity_count = int(audit.get("integrityIssueCount") or 0)
            audit_ok = bool(audit.get("ok")) if "ok" in audit else integrity_count == 0
        if isinstance(state_payload.get("stormMode"), dict):
            health_report["stormMode"] = state_payload.get("stormMode")
        if state_payload.get("syncing") is not None:
            syncing = bool(state_payload.get("syncing"))
    else:
        result["stateError"] = err_state
        log(f"state fetch failed: {err_state}")

    last_issues = watchdog.get("lastIssues") if isinstance(watchdog.get("lastIssues"), list) else []
    has_watchdog_error = any(
        isinstance(i, dict) and str(i.get("type") or "") == "watchdog-error"
        for i in last_issues
    )
    integrity_issues = audit.get("integrityIssues") if isinstance(audit.get("integrityIssues"), list) else []
    if ok_state and not integrity_issues:
        tip_ready_issues = state_payload.get("tipReadyIssues")
        if isinstance(tip_ready_issues, list) and tip_ready_issues:
            integrity_issues = tip_ready_issues
    pipeline = runner_pipeline_busy(health_report, state_payload if ok_state else None)
    syncing = bool(pipeline.get("syncing"))
    prediction_build_running = bool(pipeline.get("predictionBuildRunning"))
    pipeline_busy = bool(pipeline.get("busy"))

    tip_ready = None
    tip_ready_issues: List[Any] = []
    if ok_state:
        tip_ready = state_payload.get("tipReady")
        raw_tip_issues = state_payload.get("tipReadyIssues")
        if isinstance(raw_tip_issues, list):
            tip_ready_issues = raw_tip_issues
    summary = health_report.get("summary") if isinstance(health_report.get("summary"), dict) else {}
    if tip_ready is None and isinstance(summary, dict):
        tip_ready = summary.get("tipReady")
    rds_read_only = bool(
        (ok_state and (state_payload.get("rdsReadOnly") or (state_payload.get("rdsLag") or {}).get("readOnly")))
        or (isinstance(summary, dict) and summary.get("rdsReadOnly"))
    )
    transient_structure = (
        tip_ready is False
        and is_transient_tip_structure_issue(tip_ready_issues or integrity_issues)
    )
    warming = pipeline_busy or transient_structure or rds_read_only

    would_repair = (
        not audit_ok
        and integrity_count > 0
        and integrity_repairable(integrity_issues or last_issues, integrity_count)
    )
    has_integrity_repair = would_repair and not warming
    needs_repair = has_watchdog_error or (has_integrity_repair and not pipeline_busy)

    result["auditOk"] = audit_ok
    result["integrityIssueCount"] = integrity_count
    result["watchdogError"] = has_watchdog_error
    result["syncing"] = syncing
    result["predictionBuildRunning"] = prediction_build_running
    result["rdsReadOnly"] = rds_read_only
    result["transientStructure"] = transient_structure
    result["watchdog"] = {
        "lastCheckAt": watchdog.get("lastCheckAt"),
        "lastRepairAt": watchdog.get("lastRepairAt"),
        "lastTrigger": watchdog.get("lastTrigger"),
        "issueCount": len(last_issues),
        "actionCount": len(watchdog.get("lastActions") or []) if isinstance(watchdog.get("lastActions"), list) else 0,
        "totalRepairs": watchdog.get("totalRepairs"),
    }

    if not needs_repair:
        state["integrityFailStreak"] = 0
        state["watchdogErrorStreak"] = 0
        result["level"] = "ok"
        if (would_repair or transient_structure) and warming and not has_watchdog_error:
            result["level"] = "l1-deferred-busy"
            result["actions"].append({
                "type": "integrity-deferred-busy",
                "syncing": syncing,
                "predictionBuildRunning": prediction_build_running,
                "transientStructure": transient_structure,
                "rdsReadOnly": rds_read_only,
            })
            log(
                "skip integrity escalate: runner warming "
                f"syncing={syncing} predictionBuildRunning={prediction_build_running} "
                f"transientStructure={transient_structure} rdsReadOnly={rds_read_only}"
            )
        elif rds_read_only:
            result["level"] = "ok-rds-readonly"
            result["actions"].append({"type": "rds-readonly-observe", "rdsReadOnly": True})
        return result

    if has_watchdog_error:
        state["watchdogErrorStreak"] = int(state.get("watchdogErrorStreak") or 0) + 1
    else:
        state["watchdogErrorStreak"] = 0
    if integrity_count > 0 and not audit_ok:
        state["integrityFailStreak"] = int(state.get("integrityFailStreak") or 0) + 1
    else:
        state["integrityFailStreak"] = 0

    integrity_streak = int(state.get("integrityFailStreak") or 0)
    error_streak = int(state.get("watchdogErrorStreak") or 0)
    result["integrityFailStreak"] = integrity_streak
    result["watchdogErrorStreak"] = error_streak

    # L2: sync（触发进程内 sync-new-record 巡检）
    still_bad = True
    sync_auth_blocked = False
    if pipeline_busy and not has_watchdog_error:
        result["level"] = "l2-deferred-busy"
        result["actions"].append({
            "type": "sync-skipped-busy",
            "syncing": syncing,
            "predictionBuildRunning": prediction_build_running,
        })
        state["integrityFailStreak"] = 0
        result["integrityFailStreak"] = 0
        log(
            "L2 deferred: runner busy "
            f"syncing={syncing} predictionBuildRunning={prediction_build_running}"
        )
        return result
    if cooldown_ok(state, "lastSyncAt", SYNC_COOLDOWN_SEC):
        log(f"L2 POST /sync integrity={integrity_count} streak={integrity_streak} watchdogError={has_watchdog_error}")
        ok_sync, sync_payload, err_sync = client.fetch_json(
            RUNNER_SYNC,
            method="POST",
            body=b'{"trigger":"supervisor"}',
            timeout=SYNC_HTTP_TIMEOUT,
        )
        mark_action(state, "lastSyncAt")
        sync_skipped = bool(sync_payload.get("skipped"))
        sync_ok = bool(ok_sync and (sync_payload.get("ok") is not False or sync_skipped))
        sync_transient = (not sync_ok) and is_transient_network_error(err_sync)
        sync_auth_blocked = (not ok_sync) and str(err_sync) in {"http-401", "http-403"}
        result["actions"].append({
            "type": "sync",
            "ok": sync_ok,
            "skipped": sync_skipped,
            "transient": sync_transient,
            "error": err_sync,
            "payloadOk": sync_payload.get("ok"),
        })
        result["level"] = "l2-sync"
        if sync_transient:
            # 开奖窗连接被掐 / 超时：不当硬失败，不刷告警，不推进 L3
            result["level"] = "l2-sync-transient"
            state["integrityFailStreak"] = max(0, integrity_streak - 1)
            result["integrityFailStreak"] = state["integrityFailStreak"]
            log(f"L2 sync transient ({err_sync}); treat as busy window")
            return result
        if sync_ok:
            # 成功或已在同步中：降噪，不每次打 l2-sync 告警
            log(f"L2 sync ok skipped={sync_skipped}")
        else:
            send_alert(
                state,
                "l2-sync",
                {
                    "integrity": integrity_count,
                    "streak": integrity_streak,
                    "watchdogError": has_watchdog_error,
                    "ok": False,
                    "error": err_sync,
                },
                env,
            )
        if sync_auth_blocked:
            send_alert(state, "sync-auth-blocked", {"error": err_sync, "authMode": getattr(client, "_auth_mode", "")}, env)
            log(f"L2 sync auth blocked ({err_sync}); skip L3 restart to avoid restart storm")
            result["level"] = "l2-auth-blocked"
            state["integrityFailStreak"] = max(0, integrity_streak - 1)
            return result
        time.sleep(3)
        ok_h, runner, _ = client.fetch_json(RUNNER_HEALTH)
        summary = runner.get("summary") if isinstance(runner.get("summary"), dict) else {}
        audit2 = summary.get("modelAudit") if isinstance(summary.get("modelAudit"), dict) else {}
        still_bad = (not bool(audit2.get("ok", True))) or int(audit2.get("integrityIssueCount") or 0) > 0 or has_watchdog_error
        result["afterSyncAuditOk"] = audit2.get("ok")
        result["afterSyncIntegrity"] = audit2.get("integrityIssueCount")
        syncing = bool(summary.get("syncing"))
        if sync_skipped or syncing:
            still_bad = False
            result["level"] = "l2-sync-inflight"
    else:
        result["actions"].append({"type": "sync-skipped-cooldown"})
        result["level"] = "l2-sync-cooldown"

    escalate_restart = (
        integrity_streak >= INTEGRITY_FAIL_THRESHOLD
        or error_streak >= WATCHDOG_ERROR_THRESHOLD
    ) and still_bad

    if escalate_restart and (syncing or rds_read_only or transient_structure):
        result["actions"].append({
            "type": "restart-skipped-warming",
            "syncing": syncing,
            "rdsReadOnly": rds_read_only,
            "transientStructure": transient_structure,
        })
        result["level"] = "l3-deferred-warming"
        log(
            "L3 deferred: runner warming "
            f"syncing={syncing} rdsReadOnly={rds_read_only} transientStructure={transient_structure}"
        )
    elif escalate_restart:
        ok = restart_app("penta5-runner", state, f"l3-integrity-streak={integrity_streak};watchdog-error-streak={error_streak}")
        result["actions"].append({"type": "restart-runner", "ok": ok})
        result["level"] = "l3-restart"
        send_alert(
            state,
            "l3-restart",
            {"integrity": integrity_count, "streak": integrity_streak, "watchdogErrorStreak": error_streak, "ok": ok},
            env,
        )
        if ok:
            time.sleep(8)
            state["integrityFailStreak"] = max(0, integrity_streak - 1)
    elif needs_repair:
        result["level"] = result.get("level") or "l1-observe"
        log(
            f"model degrade observed integrity={integrity_count} streak={integrity_streak}/"
            f"{INTEGRITY_FAIL_THRESHOLD} watchdogErrorStreak={error_streak}/{WATCHDOG_ERROR_THRESHOLD}"
        )

    return result


def runner_in_warmup_window(state: Dict[str, Any]) -> bool:
    """runner 重启/换码后的宽限期，避免 tipReady 瞬时 false 误停机器人。"""
    if RUNNER_WARMUP_SEC <= 0:
        return False
    for key in ("lastRestartAt:penta5-runner", "lastRestartAt"):
        last = state.get(key)
        if not last:
            continue
        try:
            elapsed = time.time() - parse_time(str(last))
        except Exception:
            continue
        if 0 <= elapsed < RUNNER_WARMUP_SEC:
            return True
    return False


def tip_ready_issues_from_payload(
    health_report: Dict[str, Any],
    state_payload: Dict[str, Any],
) -> List[Any]:
    summary = health_report.get("summary") if isinstance(health_report.get("summary"), dict) else {}
    audit = health_report.get("modelAudit") if isinstance(health_report.get("modelAudit"), dict) else {}
    for source in (state_payload, summary, audit):
        if not isinstance(source, dict):
            continue
        issues = source.get("tipReadyIssues")
        if isinstance(issues, list) and issues:
            return issues
    return []


def robot_stop_deferred(
    health_report: Dict[str, Any],
    state_payload: Dict[str, Any],
    state: Dict[str, Any],
    issue_count: int,
) -> str:
    """重启窗口 / runner 不可达 / 同步忙 / 瞬时结构问题：暂缓停机器人。"""
    probes = health_report.get("probes") if isinstance(health_report.get("probes"), dict) else {}
    runner_probe = probes.get("runner") if isinstance(probes.get("runner"), dict) else {}
    runner_err = str(runner_probe.get("error") or "")
    runner_ok = bool(runner_probe.get("ok"))
    pm2 = pm2_status_map()
    runner_pm2 = str(pm2.get("penta5-runner") or "")
    pipeline = runner_pipeline_busy(health_report, state_payload)

    if runner_pm2 and runner_pm2 != "online":
        return f"runner-pm2-{runner_pm2}"
    if not runner_ok:
        lowered = runner_err.lower()
        if any(token in lowered for token in ("refused", "reset", "timed out", "timeout", "broken pipe")):
            return f"runner-probe-transient:{runner_err[:80] or 'unreachable'}"
        return f"runner-probe-fail:{runner_err[:80] or 'unknown'}"
    if pipeline.get("busy"):
        parts = []
        if pipeline.get("syncing"):
            parts.append("syncing")
        if pipeline.get("predictionBuildRunning"):
            parts.append("prediction-build")
        return "runner-pipeline-busy:" + "+".join(parts or ["busy"])
    if runner_in_warmup_window(state):
        return f"runner-warmup<{RUNNER_WARMUP_SEC}s"
    issues = tip_ready_issues_from_payload(health_report, state_payload)
    if issues and is_transient_tip_structure_issue(issues):
        return "transient-tip-structure"
    if issue_count == 0 and runner_in_warmup_window(state):
        return "tip-pending-warmup"
    return ""


def handle_tip_ready(
    state: Dict[str, Any],
    health_report: Dict[str, Any],
    state_payload: Dict[str, Any],
    env: Dict[str, str],
) -> Dict[str, Any]:
    summary = health_report.get("summary") if isinstance(health_report.get("summary"), dict) else {}
    tip_ready = summary.get("tipReady")
    if tip_ready is None and isinstance(state_payload, dict):
        tip_ready = state_payload.get("tipReady")
    if tip_ready is None:
        audit = health_report.get("modelAudit") or {}
        tip_ready = audit.get("tipReady")
        if tip_ready is None:
            tip_ready = audit.get("ok")
    tip_ready = bool(tip_ready)
    issue_count = Number_or(
        summary.get("tipReadyIssueCount"),
        (state_payload or {}).get("tipReadyIssueCount"),
        0,
    )
    prev = state.get("tipReady")
    # 首次未知时只记录，不因历史 None 误停机器人
    if prev is None:
        state["tipReady"] = tip_ready
        state["tipNotReadyStreak"] = 0 if tip_ready else 1
        return {"tipReady": tip_ready, "changed": False, "actions": [], "issueCount": issue_count}

    defer_reason = robot_stop_deferred(health_report, state_payload, state, issue_count) if not tip_ready else ""
    # 瞬时结构/同步忙：不改写 tipReady 状态，避免 tip-ready/tip-not-ready 风暴
    if (not tip_ready) and defer_reason:
        state["tipNotReadyStreak"] = 0
        log(f"defer tipReady=false: {defer_reason}")
        return {
            "tipReady": bool(prev),
            "observed": False,
            "changed": False,
            "deferred": defer_reason,
            "issueCount": issue_count,
            "actions": [{"type": "tip-deferred", "skipped": True, "reason": defer_reason}],
        }

    if tip_ready:
        state["tipNotReadyStreak"] = 0
    else:
        state["tipNotReadyStreak"] = int(state.get("tipNotReadyStreak") or 0) + 1

    confirmed_not_ready = (not tip_ready) and int(state.get("tipNotReadyStreak") or 0) >= max(1, TIP_NOT_READY_CONFIRM_TICKS)
    # 未确认前仍视为 ready，避免抖动
    if (not tip_ready) and not confirmed_not_ready:
        return {
            "tipReady": True,
            "observed": False,
            "changed": False,
            "confirming": True,
            "streak": int(state.get("tipNotReadyStreak") or 0),
            "need": max(1, TIP_NOT_READY_CONFIRM_TICKS),
            "issueCount": issue_count,
            "actions": [{"type": "tip-confirming", "streak": int(state.get("tipNotReadyStreak") or 0)}],
        }

    changed = bool(prev) != tip_ready
    result: Dict[str, Any] = {
        "tipReady": tip_ready,
        "changed": changed,
        "issueCount": issue_count,
        "actions": [],
    }
    if (not tip_ready) and prev:
        stop_result = stop_robots(state, f"tipReady=false issues={issue_count}")
        result["actions"].append({"type": "stop-robots-tip", **stop_result})
        send_alert(
            state,
            "tip-not-ready",
            {
                "issueCount": issue_count,
                "period": (health_report.get("probes") or {}).get("runner", {}).get("latestPeriod"),
                "confirmedStreak": int(state.get("tipNotReadyStreak") or 0),
            },
            env,
        )
    elif tip_ready and (not prev):
        resume_result = resume_robots(state, "tipReady=true")
        result["actions"].append({"type": "resume-robots-tip", **resume_result})
        send_alert(state, "tip-ready", {"issueCount": 0}, env)
    elif not tip_ready:
        status = pm2_status_map()
        online_robots = [app for app in ROBOT_APPS if status.get(app) == "online"]
        if online_robots and not bool((health_report.get("stormMode") or {}).get("active")):
            stop_result = stop_robots(state, "tipReady-keep-paused")
            result["actions"].append({"type": "stop-robots-tip-keepalive", **stop_result})
    state["tipReady"] = tip_ready
    state["lastTipReady"] = result
    return result


def Number_or(*values: Any) -> int:
    for value in values:
        try:
            if value is None:
                continue
            return int(value)
        except Exception:
            continue
    return 0


def run_once() -> int:
    env = load_dotenv(ENV_FILE)
    state = read_supervisor_state()
    client = PanelClient(env)

    process_report = ensure_core_processes(state)
    health_report = ensure_health_probes(state, client)
    exclusion_report = ensure_exclusion_model(state, client, health_report)
    stable_math_report = ensure_stable_math_filter(state, client, health_report)
    health_report["exclusionModel"] = exclusion_report
    health_report["stableMathFilter"] = stable_math_report
    # RDS 落后只告警，不重启：常见于实例 read_only / LOCK_WRITE
    try:
        probes = health_report.get("probes") or {}
        runner_period = (probes.get("runner") or {}).get("latestPeriod")
        rds_period = (probes.get("rds") or {}).get("latestPeriod")
        proxy_period = (probes.get("proxy") or {}).get("cachePeriod")
        lag = max(period_lag(runner_period, rds_period), period_lag(proxy_period, rds_period))
        health_report["rdsLagPeriods"] = lag
        if lag >= RDS_LAG_ALERT_PERIODS:
            send_alert(
                state,
                "rds-lag",
                {
                    "lagPeriods": lag,
                    "runnerPeriod": runner_period,
                    "proxyPeriod": proxy_period,
                    "rdsPeriod": rds_period,
                    "hint": "Aliyun RDS may be read_only/LOCK_WRITE; turn off instance read-only then catchup",
                },
                env,
            )
    except Exception as exc:
        log(f"rds lag check failed: {exc}")
    quality_report = escalate_model_quality(state, client, health_report, env)
    state_payload = quality_report.get("statePayload") if isinstance(quality_report.get("statePayload"), dict) else {}
    storm_report = handle_storm_mode(state, health_report, state_payload, env)
    tip_report = handle_tip_ready(state, health_report, state_payload, env)
    robot_report = ensure_robot_health(state, health_report)

    summary = {
        "ok": True,
        "ts": now_iso(),
        "process": {
            "pm2": process_report.get("pm2"),
            "restartCount": len(process_report.get("restarts") or []),
        },
        "probes": health_report.get("probes"),
        "exclusionModel": {
            "ok": exclusion_report.get("ok"),
            "version": exclusion_report.get("version"),
            "latestPeriod": exclusion_report.get("latestPeriod"),
            "runnerPeriod": exclusion_report.get("runnerPeriod"),
            "periodMismatch": exclusion_report.get("periodMismatch"),
            "periodLag": exclusion_report.get("periodLag"),
            "actions": exclusion_report.get("actions"),
        },
        "stableMathFilter": {
            "ok": stable_math_report.get("ok"),
            "slim": stable_math_report.get("slim"),
            "hasRules": stable_math_report.get("hasRules"),
            "latestPeriod": stable_math_report.get("latestPeriod"),
            "actions": stable_math_report.get("actions"),
        },
        "quality": {
            "level": quality_report.get("level"),
            "auditOk": quality_report.get("auditOk"),
            "integrityIssueCount": quality_report.get("integrityIssueCount"),
            "integrityFailStreak": quality_report.get("integrityFailStreak"),
            "watchdogError": quality_report.get("watchdogError"),
            "syncing": quality_report.get("syncing"),
            "watchdog": quality_report.get("watchdog"),
            "actions": quality_report.get("actions"),
        },
        "storm": {
            "active": storm_report.get("active"),
            "reason": storm_report.get("reason"),
            "changed": storm_report.get("changed"),
            "autoTop6CurrentMiss": storm_report.get("autoTop6CurrentMiss"),
            "threeDuishuCurrentMiss": storm_report.get("threeDuishuCurrentMiss"),
            "actions": storm_report.get("actions"),
        },
        "tip": {
            "ready": tip_report.get("tipReady"),
            "changed": tip_report.get("changed"),
            "issueCount": tip_report.get("issueCount"),
            "actions": tip_report.get("actions"),
        },
        "robots": {
            "items": robot_report.get("robots"),
            "restarts": robot_report.get("restarts"),
        },
    }

    probe_ok = all(bool((health_report.get("probes") or {}).get(k, {}).get("ok")) for k in ("runner", "rds", "proxy"))
    exclusion_ok = bool(exclusion_report.get("ok"))
    stable_math_ok = bool(stable_math_report.get("ok"))
    pm2_ok = all(str((process_report.get("pm2") or {}).get(app) or "") == "online" for app in CORE_APPS)
    robots_ok = all(bool(item.get("ok")) for item in (robot_report.get("robots") or {}).values())
    quality_hard_fail = quality_report.get("level") == "l3-restart" and not any(
        a.get("ok") for a in (quality_report.get("actions") or []) if a.get("type") == "restart-runner"
    )
    summary["ok"] = bool(probe_ok and exclusion_ok and stable_math_ok and pm2_ok and robots_ok and not quality_hard_fail)

    state["lastSummary"] = summary
    write_supervisor_state(state)
    log(json.dumps(summary, ensure_ascii=False, separators=(",", ":")))
    return 0 if summary["ok"] else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Penta5 Aliyun compute/model supervisor")
    parser.add_argument("--loop", action="store_true", help="keep supervising")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="loop interval seconds")
    parser.add_argument("--once", action="store_true", help="run one check and exit (default without --loop)")
    args = parser.parse_args(argv)
    if args.loop:
        log(f"supervisor loop start interval={max(30, args.interval)}s root={ROOT}")
        while True:
            try:
                run_once()
            except Exception as exc:
                log(f"supervisor tick error: {exc}")
            time.sleep(max(30, args.interval))
    return run_once()


if __name__ == "__main__":
    raise SystemExit(main())
