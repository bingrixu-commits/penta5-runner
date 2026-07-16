#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
penta5_auto_shooter.py

Read Penta5 state data, generate the current practice/shooting text, and
optionally send it to a target chat page with Selenium.

Default mode is dry-run. Add --live to perform real browser input/clicks.

Examples:
  python penta5_auto_shooter.py
  python penta5_auto_shooter.py --stake 1
  python penta5_auto_shooter.py --state-url http://8.210.5.221:3001/state --stake 1
  python penta5_auto_shooter.py --url "https://example.com/chat" --live --stake 1
  python penta5_auto_shooter.py --url "https://example.com/chat" --live --watch --rounds 20 --stake 1
"""

from __future__ import annotations

import argparse
import base64
import csv
import itertools
import json
import logging
import math
import random
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


try:
    from selenium import webdriver
    from selenium.common.exceptions import (
        ElementClickInterceptedException,
        NoSuchElementException,
        StaleElementReferenceException,
        TimeoutException,
        WebDriverException,
    )
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.action_chains import ActionChains
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
except Exception:
    webdriver = None
    ElementClickInterceptedException = Exception
    NoSuchElementException = Exception
    StaleElementReferenceException = Exception
    TimeoutException = Exception
    WebDriverException = Exception
    Options = None
    Service = None
    ActionChains = None
    By = None
    Keys = None
    EC = None
    WebDriverWait = None


DEFAULT_STATE_FILE = Path("current_source") / "penta5-runner" / "data" / "state.json"
DEFAULT_STATE_URL = "http://8.210.5.221:3001/state"
DEFAULT_STATE_USER = "admin"
DEFAULT_STATE_PASSWORD_FILE = Path("penta5_data_password.txt")
DEFAULT_SHARED_DATA_SOURCE_FILE = Path("penta5_data_source.json")
DEFAULT_MEMORY_FILE = Path("penta5_auto_shooter_state.json")
DEFAULT_HISTORY_CSV = Path("penta5_auto_shooter_rounds.csv")
DEFAULT_BROWSER_PROFILE_DIR = Path("penta5_chrome_profile")
DEFAULT_ADSPOWER_KEY_FILE = Path("penta5_adspower_key.txt")
DEFAULT_ADSPOWER_API_URL = "http://127.0.0.1:50325"
DEFAULT_GUI_SETTINGS_FILE = Path("penta5_auto_shooter_settings.json")
DEFAULT_SCHEDULE_RANDOM_DELAY = "10-40"
DEFAULT_BASE_TEXT = "千1234567890百1234567890十1234567890个1234567890四定"

RAW_NUMBER_NAMES: Tuple[str, ...] = ("球一", "球二", "球三", "球四", "球五")
MODEL_POSITIONS: Tuple[Dict[str, Any], ...] = (
    {"key": "ball1", "index": 0, "raw_index": 0, "name": "球一", "alias": "千位", "state_key": "wan", "label": "球一（千位）"},
    {"key": "ball2", "index": 1, "raw_index": 1, "name": "球二", "alias": "百位", "state_key": "qian", "label": "球二（百位）"},
    {"key": "ball3", "index": 2, "raw_index": 2, "name": "球三", "alias": "十位", "state_key": "bai", "label": "球三（十位）"},
    {"key": "ball4", "index": 3, "raw_index": 3, "name": "球四", "alias": "个位", "state_key": "shi", "label": "球四（个位）"},
)
DISPLAY_ONLY_POSITIONS: Tuple[Dict[str, Any], ...] = (
    {"key": "ball5", "raw_index": 4, "name": "球五", "display_only": True},
)
POSITION_DEFS = MODEL_POSITIONS

DIGIT_ORDER: Tuple[str, ...] = tuple("1234567890")
POSITION_CANDIDATE_MODEL_LIMIT = 16
HEWEI_CANDIDATE_MODEL_LIMIT = 16

HEWEI_DEFS: Tuple[Dict[str, Any], ...] = (
    {"key": "wanQianBai", "label": "千百十合尾", "group_index": 0},
    {"key": "qianBaiShi", "label": "百十个合尾", "group_index": 1},
    {"key": "wanQianBaiShi", "label": "千百十个合尾", "group_index": 2},
    {"key": "unified", "label": "三个合尾统一", "group_index": None},
)

SHAPE_DEFS: Tuple[Dict[str, Any], ...] = (
    {"key": "shapeParity", "label": "单双形态", "pool": ("单", "双")},
    {"key": "shapeSize", "label": "大小形态", "pool": ("大", "小")},
    {"key": "shapeMixed", "label": "单双大小形态", "pool": ("大", "小", "单", "双")},
)

SELECTION_CHOICES: Tuple[str, ...] = (
    "top5",
    "panel-top5",
    "recommend2",
    "panel-top5-pure",
    "fixed",
    "fixed-shot",
    "manual-fixed",
    "panel-pure",
    "pure",
    "position-hewei",
    "positions",
    "all",
)
PANEL_TOP5_MODES = {"panel-top5", "panel", "web-top5", "auto5"}
RECOMMEND2_MODES = {"recommend2", "panel-recommend2", "recommend2-framework"}
PANEL_TOP5_PURE_MODES = {"panel-top5-pure", "panel-pure", "web-top5-pure", "auto5-pure", "pure"}
FIXED_SHOT_MODES = {"fixed", "fixed-shot", "manual-fixed"}

EXTRA_DEFS: Tuple[Dict[str, Any], ...] = (
    {"key": "sumAvoid", "label": "前四位和值避开", "kind": "sum", "mode": "avoid"},
    {"key": "threeDigitAvoid", "label": "对数共现避开", "kind": "threeDigits", "mode": "avoid"},
    {"key": "twoDigitAvoid", "label": "对数避开1组", "kind": "twoDigits", "mode": "avoid"},
    {"key": "twoDigitAvoid2", "label": "对数避开2组", "kind": "twoDigits", "mode": "avoid"},
    {"key": "twoDigitAvoid3", "label": "对数避开3组", "kind": "twoDigits", "mode": "avoid"},
    {"key": "twoDigitHit", "label": "两数共现", "kind": "twoDigits", "mode": "avoid"},
    {"key": "threeDigitHit", "label": "三数共现命中", "kind": "threeDigits", "mode": "hit"},
)

USER_AGENTS: Tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Edg/124.0.0.0 Safari/537.36",
)

SENSITIVE_KEYS = {
    "uid",
    "id",
    "user",
    "user_id",
    "userid",
    "account",
    "token",
    "session",
    "sid",
    "openid",
    "key",
    "password",
}


@dataclass
class ShotItem:
    kind: str
    key: str
    side: str
    name: str
    code: str
    rate: Optional[float] = None
    rate10: Optional[float] = None
    rate30: Optional[float] = None
    rate60: Optional[float] = None
    rate120: Optional[float] = None
    rate300: Optional[float] = None
    stable_score: Optional[float] = None
    stage_label: str = "观察期"
    shape_key: str = ""


@dataclass
class ShotBuildResult:
    message: str
    raw_shot: str
    data_key: str
    period: str
    target_period: str
    prediction_id: str
    generated_at: str
    selection: str
    selected_items: List[ShotItem]


@dataclass
class SendResult:
    sent: bool
    status: str
    detail: str = ""


@dataclass
class MartingaleSnapshot:
    enabled: bool
    level: int
    stake: str
    base_stake: float
    multiplier: float
    max_level: int


@dataclass
class AdsPowerProfile:
    user_id: str
    serial_number: str = ""
    name: str = ""

    @property
    def label(self) -> str:
        parts = []
        if self.name:
            parts.append(self.name)
        if self.serial_number:
            parts.append(f"序号{self.serial_number}")
        if self.user_id:
            parts.append(self.user_id)
        return " / ".join(parts) if parts else self.user_id


@dataclass
class SendGateDecision:
    allowed: bool
    reason: str
    day: str = ""
    current_miss: Optional[int] = None
    threshold: int = 0
    ledger_name: str = ""


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def safe_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def as_dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def first_value(*values: Any, default: str = "") -> str:
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            if value:
                text = safe_str(value[0])
                if text and text != "--":
                    return text
            continue
        text = safe_str(value)
        if text and text != "--":
            return text
    return default


def parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.replace("%", "").strip()
        number = float(value)
        if math.isfinite(number):
            return number
    except Exception:
        return None
    return None


def format_amount(value: float) -> str:
    if not math.isfinite(float(value)):
        return ""
    rounded = round(float(value), 8)
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.8f}".rstrip("0").rstrip(".")


def unique_texts(values: Iterable[Any]) -> List[str]:
    seen: Dict[str, bool] = {}
    out: List[str] = []
    for value in values:
        text = safe_str(value)
        if text and text != "--" and text not in seen:
            seen[text] = True
            out.append(text)
    return out


def unique_digits(values: Iterable[Any], limit: Optional[int] = None) -> List[str]:
    seen: Dict[str, bool] = {}
    out: List[str] = []

    def push(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            for item in value:
                push(item)
            return
        for digit in re.findall(r"\d", safe_str(value)):
            if digit not in seen:
                seen[digit] = True
                out.append(digit)
                if limit is not None and len(out) >= limit:
                    return

    for raw in values:
        push(raw)
        if limit is not None and len(out) >= limit:
            break
    return out


def mask_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        safe_pairs = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            safe_pairs.append((key, "***" if key.lower() in SENSITIVE_KEYS else value))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_pairs, doseq=True), parts.fragment))
    except Exception:
        return "[hidden-url]"


def normalize_state_url(url: str) -> str:
    text = safe_str(url)
    if not text:
        return ""
    try:
        parts = urlsplit(text)
        clean_path = parts.path.rstrip("/")
        if clean_path.endswith("/panel"):
            clean_path = clean_path[: -len("/panel")] + "/state"
            return urlunsplit((parts.scheme, parts.netloc, clean_path, parts.query, parts.fragment))
        if clean_path == "panel":
            return urlunsplit((parts.scheme, parts.netloc, "state", parts.query, parts.fragment))
    except Exception:
        pass
    return text


def normalize_adspower_api_url(api_url: str) -> str:
    text = safe_str(api_url, DEFAULT_ADSPOWER_API_URL).rstrip("/")
    if not text:
        return DEFAULT_ADSPOWER_API_URL
    try:
        parts = urlsplit(text)
        if not parts.scheme:
            text = "http://" + text
    except Exception:
        if not text.startswith(("http://", "https://")):
            text = "http://" + text
    return text.rstrip("/")


def adspower_headers(api_key: str) -> Dict[str, str]:
    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": random.choice(USER_AGENTS),
        "Cache-Control": "no-cache",
    }
    key = safe_str(api_key)
    if key:
        headers["Authorization"] = "Bearer " + key
    return headers


def adspower_request_json(
    api_url: str,
    path: str,
    api_key: str,
    timeout: int,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base = normalize_adspower_api_url(api_url)
    clean_path = "/" + path.lstrip("/")
    query = urlencode({key: value for key, value in (params or {}).items() if value is not None})
    url = f"{base}{clean_path}" + (f"?{query}" if query else "")
    request = urllib.request.Request(url, headers=adspower_headers(api_key))
    try:
        with urllib.request.urlopen(request, timeout=max(3, int(timeout))) as response:
            raw = response.read()
        payload = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"AdsPower API 请求失败 {mask_url(url)}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AdsPower API 返回不是 JSON {mask_url(url)}: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"AdsPower API 读取失败 {mask_url(url)}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"AdsPower API 返回格式异常 {mask_url(url)}")
    return payload


def discover_adspower_profile(api_url: str, api_key: str, timeout: int) -> AdsPowerProfile:
    payload = adspower_request_json(
        api_url,
        "/api/v1/user/list",
        api_key,
        timeout,
        {"page": 1, "page_size": 50},
    )
    if str(payload.get("code")) not in {"0", "None"} and payload.get("code") not in (0, None):
        raise RuntimeError(f"AdsPower 获取环境列表失败: {payload.get('msg') or payload}")
    data = as_dict(payload.get("data"))
    profiles = as_list(data.get("list"))
    if not profiles:
        raise RuntimeError("AdsPower 没有可用环境，请先在 AdsPower 里创建或导入环境")
    for raw_profile in profiles:
        profile = as_dict(raw_profile)
        user_id = safe_str(profile.get("user_id"))
        if user_id:
            return AdsPowerProfile(
                user_id=user_id,
                serial_number=safe_str(profile.get("serial_number")),
                name=safe_str(profile.get("name")),
            )
    raise RuntimeError("AdsPower 环境列表里没有 user_id，无法启动浏览器环境")


def atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True) if path.parent != Path("") else None
    tmp = path.with_name(f".{path.name}.{int(time.time() * 1000)}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


class StateReader:
    def __init__(
        self,
        state_file: Path,
        state_url: str,
        timeout: int,
        state_user: str = "",
        state_password: str = "",
        sync_before_read: bool = False,
    ):
        self.state_file = state_file
        self.state_url = normalize_state_url(state_url.strip())
        self.timeout = timeout
        self.state_user = state_user
        self.state_password = state_password
        self.sync_before_read = sync_before_read

    def read(self) -> Dict[str, Any]:
        if self.state_url:
            if self.sync_before_read:
                self._sync_url(self.state_url)
            return self._read_url(self.state_url)
        return self._read_file(self.state_file)

    def _read_file(self, path: Path) -> Dict[str, Any]:
        try:
            text = path.read_text(encoding="utf-8-sig")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("state JSON root must be an object")
            return data
        except FileNotFoundError as exc:
            raise RuntimeError(f"state file not found: {path}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"state file is not valid JSON: {path}: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"failed to read state file {path}: {exc}") from exc

    def _read_url(self, url: str) -> Dict[str, Any]:
        headers = {
            "Accept": "application/json,text/plain,*/*",
            "User-Agent": random.choice(USER_AGENTS),
            "Cache-Control": "no-cache",
        }
        if self.state_user or self.state_password:
            token = f"{self.state_user}:{self.state_password}".encode("utf-8")
            headers["Authorization"] = "Basic " + base64.b64encode(token).decode("ascii")
        request = urllib.request.Request(
            url,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
            text = raw.decode("utf-8-sig", errors="replace")
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("state URL JSON root must be an object")
            return data
        except urllib.error.URLError as exc:
            raise RuntimeError(f"failed to fetch state URL {mask_url(url)}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"state URL did not return valid JSON {mask_url(url)}: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"failed to read state URL {mask_url(url)}: {exc}") from exc

    def _sync_url(self, url: str) -> None:
        try:
            parts = urlsplit(url)
            clean_path = parts.path.rstrip("/")
            if clean_path.endswith("/state"):
                clean_path = clean_path[: -len("/state")] + "/sync"
            elif clean_path.endswith("/panel"):
                clean_path = clean_path[: -len("/panel")] + "/sync"
            else:
                clean_path = "/sync"
            sync_url = urlunsplit((parts.scheme, parts.netloc, clean_path, parts.query, parts.fragment))
            headers = {
                "Accept": "application/json,text/plain,*/*",
                "User-Agent": random.choice(USER_AGENTS),
                "Cache-Control": "no-cache",
            }
            if self.state_user or self.state_password:
                token = f"{self.state_user}:{self.state_password}".encode("utf-8")
                headers["Authorization"] = "Basic " + base64.b64encode(token).decode("ascii")
            request = urllib.request.Request(sync_url, headers=headers, method="POST")
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read()
        except Exception as exc:
            logging.warning("state sync before read failed: %s", exc)


def state_period(data: Dict[str, Any]) -> str:
    prediction = as_dict(data.get("currentPrediction"))
    latest = as_dict(data.get("latestRecord"))
    return first_value(prediction.get("basePeriod"), latest.get("period"), latest.get("uid"), default="--")


def state_local_day(data: Dict[str, Any]) -> str:
    prediction = as_dict(data.get("currentPrediction"))
    latest = as_dict(data.get("latestRecord"))
    candidates = [
        prediction.get("baseOpenTime"),
        prediction.get("generatedAtText"),
        latest.get("openTime"),
        latest.get("time"),
        latest.get("drawTime"),
        latest.get("createdAt"),
        data.get("lastSync"),
        data.get("updatedAt"),
    ]
    for value in candidates:
        text = safe_str(value)
        match = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", text)
        if match:
            year, month, day = match.groups()
            return f"{year}-{int(month):02d}-{int(day):02d}"
    period_digits = re.sub(r"\D+", "", state_period(data))
    if len(period_digits) >= 8:
        try:
            parsed = datetime.strptime(period_digits[:8], "%Y%m%d")
            return parsed.strftime("%Y-%m-%d")
        except Exception:
            pass
    return datetime.now().strftime("%Y-%m-%d")


def increment_period_text(period: str, rounds_per_day: int = 288) -> str:
    text = safe_str(period)
    digits = re.sub(r"\D+", "", text)
    if not digits:
        return text
    if len(digits) >= 11:
        date_part = digits[:8]
        seq_part = digits[8:]
        try:
            seq = int(seq_part)
            width = len(seq_part)
            if seq >= rounds_per_day:
                date_value = datetime.strptime(date_part, "%Y%m%d") + timedelta(days=1)
                return date_value.strftime("%Y%m%d") + str(1).zfill(width)
            return date_part + str(seq + 1).zfill(width)
        except Exception:
            pass
    try:
        return str(int(digits) + 1).zfill(len(digits))
    except Exception:
        return text


def state_target_period(data: Dict[str, Any]) -> str:
    prediction = as_dict(data.get("currentPrediction"))
    direct = first_value(
        prediction.get("targetPeriod"),
        prediction.get("orderPeriod"),
        prediction.get("nextPeriod"),
        prediction.get("resultPeriod"),
        default="",
    )
    if direct:
        return direct
    return increment_period_text(state_period(data))


def state_prediction_id(data: Dict[str, Any]) -> str:
    prediction = as_dict(data.get("currentPrediction"))
    return first_value(prediction.get("id"), prediction.get("baseUid"), default=state_period(data))


def state_render_key(data: Dict[str, Any]) -> str:
    latest = as_dict(data.get("latestRecord"))
    return ":".join(
        [
            safe_str(data.get("updatedAt")),
            safe_str(latest.get("period") or latest.get("uid")),
            state_prediction_id(data),
        ]
    )


def history_records(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in as_list(data.get("history")):
        item = as_dict(row)
        numbers = as_list(item.get("numbers"))
        if len(numbers) >= 4:
            rows.append(item)
    return rows


def evaluated_rows(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = [
        as_dict(row)
        for row in as_list(data.get("predictions"))
        if as_dict(row).get("evaluated") and isinstance(as_dict(row).get("evaluation"), dict)
    ]

    def sort_key(row: Dict[str, Any]) -> str:
        ev = as_dict(row.get("evaluation"))
        return safe_str(ev.get("evaluatedAt") or row.get("generatedAt") or row.get("id"))

    rows.sort(key=sort_key, reverse=True)
    return rows


def actual_numbers_from_evaluation(ev: Dict[str, Any]) -> List[str]:
    numbers = as_list(ev.get("actualNumbers"))
    if len(numbers) >= 4:
        return [safe_str(n) for n in numbers]
    numbers = as_list(ev.get("actualFirstFourDigits"))
    if len(numbers) >= 4:
        return [safe_str(n) for n in numbers]
    return []


def composite_hewei_group(ev: Dict[str, Any]) -> List[str]:
    group = as_list(ev.get("actualCompositeHeweiGroup"))
    if len(group) >= 3:
        return [safe_str(x) for x in group[:3]]
    nums = actual_numbers_from_evaluation(ev)
    if len(nums) < 4:
        return []
    try:
        values = [int(x) for x in nums[:4]]
        return [
            str((values[0] + values[1] + values[2]) % 10),
            str((values[1] + values[2] + values[3]) % 10),
            str((values[0] + values[1] + values[2] + values[3]) % 10),
        ]
    except Exception:
        return []


def rate_of(values: Iterable[Optional[bool]]) -> Optional[float]:
    checks = [value for value in values if isinstance(value, bool)]
    if not checks:
        return None
    return sum(1 for value in checks if value) * 100.0 / len(checks)


def position_success(row: Dict[str, Any], key: str, side: str) -> Optional[bool]:
    ev = as_dict(row.get("evaluation"))
    pos = position_def_by_key(key)
    if not pos:
        return None
    state_key = safe_str(pos.get("state_key"))
    independent = as_dict(as_dict(ev.get("independentPosition")).get(state_key))
    fallback = unique_digits(as_list(as_dict(ev.get("previousPositionKills")).get(state_key)), limit=2)
    if not independent and not fallback:
        return None
    actual_numbers = actual_numbers_from_evaluation(ev)
    if not pos or len(actual_numbers) <= int(pos["index"]):
        return None
    actual = safe_str(independent.get("actualDigit") or actual_numbers[int(pos["index"])])
    if not actual:
        return None
    if side == "dual":
        compares = fallback[:2]
        if len(compares) < 2:
            return None
        return actual not in compares
    compare = fallback[0] if side == "main" and fallback else None
    if side == "backup":
        compare = fallback[1] if len(fallback) > 1 else None
    if not compare:
        return None
    return safe_str(compare) != actual


def item_success(data: Dict[str, Any], item: ShotItem, row: Dict[str, Any]) -> Optional[bool]:
    ev = as_dict(row.get("evaluation"))
    if item.kind == "position":
        pos = position_def_by_key(item.key)
        actual = actual_numbers_from_evaluation(ev)
        if not pos or len(actual) <= int(pos["index"]):
            return None
        actual_digit = safe_str(actual[int(pos["index"])])
        state_key = safe_str(pos.get("state_key"))
        if uses_panel_side_history(item):
            if item.side != "recommend":
                return position_success(row, item.key, item.side)
            saved = as_dict(as_dict(row.get("positionKills")).get(state_key))
            fallback = as_list(as_dict(ev.get("previousPositionKills")).get(state_key))
            code = first_value(
                saved.get("recommendDigit"),
                saved.get("selectedDigit"),
                saved.get("main"),
                as_list(saved.get("killDigits")),
                fallback[0] if fallback else None,
                default="",
            )
            return None if not code else code != actual_digit
        candidate_digits = unique_digits([item.code])
        if candidate_digits:
            return actual_digit not in candidate_digits
        if item.side != "recommend":
            return position_success(row, item.key, item.side)
        saved = as_dict(as_dict(row.get("positionKills")).get(state_key))
        fallback = as_list(as_dict(ev.get("previousPositionKills")).get(state_key))
        code = first_value(
            saved.get("recommendDigit"),
            saved.get("selectedDigit"),
            saved.get("main"),
            as_list(saved.get("killDigits")),
            fallback[0] if fallback else None,
            default="",
        )
        return None if not code else code != actual_digit

    if item.kind == "hewei":
        group = composite_hewei_group(ev)
        if len(group) < 3:
            return None
        if uses_panel_side_history(item):
            digit = ""
            if item.key == "unified":
                detail = as_dict(as_dict(row.get("detail")).get("threeFourHeweiKill"))
                if item.side == "backup":
                    digit = first_value(row.get("threeFourHeweiBackupDigit"), detail.get("backupDigit"), default="")
                elif item.side == "main":
                    digit = first_value(detail.get("primaryDigit"), row.get("threeFourHeweiKillDigit"), default="")
                else:
                    digit = first_value(row.get("threeFourHeweiKillDigit"), detail.get("killDigit"), default="")
                return None if not digit else all(digit != safe_str(group[index]) for index in range(3))
            saved_all = as_dict(row.get("independentHeweiKills")) or as_dict(ev.get("previousIndependentHeweiKills"))
            saved = as_dict(saved_all.get(item.key))
            if item.side == "backup":
                digit = first_value(saved.get("backupDigit"), default="")
            elif item.side == "main":
                digit = first_value(saved.get("primaryDigit"), saved.get("killDigit"), default="")
            else:
                digit = first_value(saved.get("killDigit"), default="")
            hewei = next((entry for entry in HEWEI_DEFS if entry["key"] == item.key), None)
            if not hewei or hewei["group_index"] is None or not digit:
                return None
            return digit != safe_str(group[int(hewei["group_index"])])
        candidate_digits = unique_digits([item.code])
        if candidate_digits:
            if item.key == "unified":
                return all(safe_str(group[index]) not in candidate_digits for index in range(3))
            hewei = next((entry for entry in HEWEI_DEFS if entry["key"] == item.key), None)
            if not hewei or hewei["group_index"] is None:
                return None
            return safe_str(group[int(hewei["group_index"])]) not in candidate_digits
        digit = ""
        if item.key == "unified":
            detail = as_dict(as_dict(row.get("detail")).get("threeFourHeweiKill"))
            if item.side == "backup":
                digit = first_value(row.get("threeFourHeweiBackupDigit"), detail.get("backupDigit"))
            elif item.side == "main":
                digit = first_value(detail.get("primaryDigit"), row.get("threeFourHeweiKillDigit"))
            else:
                digit = first_value(row.get("threeFourHeweiKillDigit"), detail.get("killDigit"))
            return None if not digit else all(digit != safe_str(group[index]) for index in range(3))
        saved_all = as_dict(row.get("independentHeweiKills")) or as_dict(ev.get("previousIndependentHeweiKills"))
        saved = as_dict(saved_all.get(item.key))
        if item.side == "backup":
            digit = first_value(saved.get("backupDigit"))
        elif item.side == "main":
            digit = first_value(saved.get("primaryDigit"), saved.get("killDigit"))
        else:
            digit = first_value(saved.get("killDigit"))
        hewei = next((entry for entry in HEWEI_DEFS if entry["key"] == item.key), None)
        if not hewei or hewei["group_index"] is None or not digit:
            return None
        return digit != safe_str(group[int(hewei["group_index"])])

    if item.kind == "shape":
        actual = actual_numbers_from_evaluation(ev)
        if len(actual) < 4 or not item.code or item.code == "--":
            return None
        return not shape_matches({"numbers": actual}, item.key, item.code)

    if item.kind == "derivedReverseShape":
        actual = actual_numbers_from_evaluation(ev)
        if len(actual) < 4 or not item.code or item.code == "--":
            return None
        shape_key = item.shape_key or ("shapeSize" if item.key == "reverseSize" else "shapeParity")
        shape_code = force_three_with_one_shape(item.code, shape_key)
        return not shape_matches({"numbers": actual}, shape_key, shape_code)

    if item.kind == "extra":
        actual = actual_numbers_from_evaluation(ev)
        if len(actual) < 4 or not item.code or item.code == "--":
            return None
        extra = next((entry for entry in EXTRA_DEFS if entry["key"] == item.key), None)
        if not extra:
            return None
        return extra_success(extra, extra_matches({"numbers": actual}, extra, item.code))

    return None


def evaluated_row_for_result(data: Dict[str, Any], result: ShotBuildResult) -> Optional[Dict[str, Any]]:
    period = safe_str(result.period)
    prediction_id = safe_str(result.prediction_id)
    for row in evaluated_rows(data):
        row_period = first_value(row.get("basePeriod"), row.get("period"), row.get("uid"), default="")
        row_id = first_value(row.get("id"), row.get("baseUid"), default="")
        if period and row_period == period:
            return row
        if prediction_id and row_id == prediction_id:
            return row
    return None


def selected_item_successes(data: Dict[str, Any], result: ShotBuildResult) -> List[Tuple[ShotItem, Optional[bool]]]:
    row = evaluated_row_for_result(data, result)
    if row is None:
        return []
    return [(item, item_success(data, item, row)) for item in result.selected_items]


def selected_items_all_hit(data: Dict[str, Any], result: ShotBuildResult) -> bool:
    checks = selected_item_successes(data, result)
    if len(checks) < 5:
        return False
    return all(value is True for _, value in checks)


def uses_panel_side_history(item: ShotItem) -> bool:
    return item.side in {"recommend", "main", "backup"} and "[" not in safe_str(item.name)


def project_rate(data: Dict[str, Any], item: ShotItem, limit: int) -> Optional[float]:
    rows = evaluated_rows(data)[: max(1, limit)]
    return rate_of(item_success(data, item, row) for row in rows)


def backend_item_rate(data: Dict[str, Any], item: ShotItem) -> Optional[float]:
    prediction = as_dict(data.get("currentPrediction"))
    if item.kind == "position":
        pos = position_def_by_key(item.key)
        if not pos:
            return None
        entry = as_dict(as_dict(prediction.get("positionKills")).get(safe_str(pos.get("state_key"))))
        performance = as_dict(entry.get("performance"))
        return parse_float(performance.get("rate"))
    if item.kind == "hewei":
        if item.key == "unified":
            performance = as_dict(as_dict(as_dict(prediction.get("detail")).get("threeFourHeweiKill")).get("performance"))
            return parse_float(performance.get("rate"))
        entry = as_dict(as_dict(prediction.get("independentHeweiKills")).get(item.key))
        performance = as_dict(entry.get("performance"))
        return parse_float(performance.get("rate"))
    if item.kind == "extra":
        entry = as_dict(as_dict(prediction.get("extraKills")).get(item.key))
        performance = as_dict(entry.get("performance"))
        if parse_float(performance.get("rate")) is not None:
            return parse_float(performance.get("rate"))
        counter = as_dict(as_dict(data.get("counters")).get(item.key))
        total = parse_float(counter.get("total"))
        match = parse_float(counter.get("match"))
        if total and total > 0 and match is not None:
            return match * 100.0 / total
    return None


def displayed_item_rate(data: Dict[str, Any], item: ShotItem, limit: int) -> Optional[float]:
    direct = project_rate(data, item, limit)
    if direct is not None:
        return direct
    backend = backend_item_rate(data, item)
    if backend is not None:
        return backend
    if item.kind in {"shape", "extra"} and item.code and item.code != "--":
        rows = history_records(data)[: max(1, limit)]
        if not rows:
            return None
        if item.kind == "shape":
            return rate_of(not shape_matches(row, item.key, item.code) for row in rows)
        extra = next((entry for entry in EXTRA_DEFS if entry["key"] == item.key), None)
        if extra:
            return rate_of(extra_success(extra, extra_matches(row, extra, item.code)) for row in rows)
    return None


def stable_stage(evaluated_count: int) -> str:
    if evaluated_count >= 300:
        return "稳定期"
    if evaluated_count >= 120:
        return "小修期"
    return "观察期"


def score_for_stage(evaluated_count: int, r30: Optional[float], r60: Optional[float], r120: Optional[float], r300: Optional[float]) -> float:
    v30 = float(r30 if r30 is not None else 0.0)
    v60 = float(r60 if r60 is not None else v30)
    v120 = float(r120 if r120 is not None else v60)
    v300 = float(r300 if r300 is not None else v120)
    if evaluated_count >= 300:
        return v30 * 0.10 + v60 * 0.15 + v120 * 0.20 + v300 * 0.55
    if evaluated_count >= 120:
        return v30 * 0.20 + v60 * 0.20 + v120 * 0.40 + v300 * 0.20
    return v30 * 0.45 + v60 * 0.35 + v120 * 0.20


def with_item_scores(data: Dict[str, Any], item: ShotItem) -> ShotItem:
    evaluated_count = len(evaluated_rows(data))
    item.rate10 = displayed_item_rate(data, item, 10)
    item.rate30 = displayed_item_rate(data, item, 30)
    item.rate60 = displayed_item_rate(data, item, 60)
    item.rate120 = displayed_item_rate(data, item, 120)
    item.rate300 = displayed_item_rate(data, item, 300)
    item.stable_score = score_for_stage(evaluated_count, item.rate30, item.rate60, item.rate120, item.rate300)
    item.rate = item.stable_score
    item.stage_label = stable_stage(evaluated_count)
    return item


def property_matches(digit: Any, prop: str) -> bool:
    try:
        n = int(digit)
    except Exception:
        return False
    if prop == "大":
        return n >= 5
    if prop == "小":
        return n < 5
    if prop == "单":
        return n % 2 == 1
    if prop == "双":
        return n % 2 == 0
    return False


def pattern_of(record: Dict[str, Any], shape_key: str) -> str:
    nums = as_list(record.get("numbers"))[:4]
    if len(nums) < 4:
        return ""
    if shape_key == "shapeParity":
        return "".join("单" if int(n) % 2 else "双" for n in nums)
    if shape_key == "shapeSize":
        return "".join("大" if int(n) >= 5 else "小" for n in nums)
    return ""


def is_three_with_one_shape(code: str) -> bool:
    if not re.fullmatch(r"[大小单双]{4}", safe_str(code)):
        return False
    counts: Dict[str, int] = {}
    for ch in code:
        counts[ch] = counts.get(ch, 0) + 1
    values = sorted(counts.values())
    return len(values) == 2 and values[0] == 1 and values[1] == 3


def opposite_shape_token(token: str) -> str:
    return {"单": "双", "双": "单", "大": "小", "小": "大"}.get(token, "单")


def shape_candidates(shape: Dict[str, Any]) -> List[str]:
    pool = tuple(shape.get("pool") or ())
    all_values = ["".join(parts) for parts in itertools.product(pool, repeat=4)]
    filtered = [value for value in all_values if is_three_with_one_shape(value)]
    return filtered or all_values


def force_three_with_one_shape(code: str, shape_key: str) -> str:
    code = re.sub(r"\s+", "", safe_str(code))
    if is_three_with_one_shape(code):
        return code
    if not re.fullmatch(r"[大小单双]{4}", code):
        return code
    shape = next((item for item in SHAPE_DEFS if item["key"] == shape_key), None)
    pool = shape_candidates(shape) if shape else []
    if not pool:
        base = code[0] if code else "单"
        return base + base + base + opposite_shape_token(base)

    def distance(candidate: str) -> Tuple[int, int, str]:
        diff = sum(1 for index in range(4) if candidate[index] != code[index])
        first_penalty = 0 if candidate[0] == code[0] else 1
        return diff, first_penalty, candidate

    return sorted(pool, key=distance)[0]


def shape_matches(record: Dict[str, Any], shape_key: str, candidate: str) -> bool:
    candidate = safe_str(candidate)
    nums = as_list(record.get("numbers"))[:4]
    if len(nums) < 4 or len(candidate) < 4:
        return False
    if shape_key in {"shapeParity", "shapeSize"}:
        return pattern_of(record, shape_key) == candidate
    return all(property_matches(nums[index], candidate[index]) for index in range(4))


def stable_shape_index(history: List[Dict[str, Any]], shape: Dict[str, Any], size: int) -> int:
    if size <= 0:
        return 0
    latest = history[0] if history else {}
    raw_period = re.sub(r"\D+", "", safe_str(latest.get("period") or latest.get("uid") or "0"))
    period_seed = int(raw_period[-4:] or "0")
    number_seed = 0
    for index, number in enumerate(as_list(latest.get("numbers"))[:4]):
        try:
            number_seed += int(number) * (index + 5)
        except Exception:
            continue
    key_seed = sum(ord(ch) * (index + 1) for index, ch in enumerate(safe_str(shape.get("key"))))
    return abs(period_seed + number_seed + key_seed) % size


def shape_dynamic_pool_size(shape: Dict[str, Any]) -> int:
    if shape.get("key") == "shapeParity":
        return 2
    if shape.get("key") == "shapeSize":
        return 4
    return 6


def shape_candidate_set(history: List[Dict[str, Any]], shape: Dict[str, Any]) -> List[Dict[str, Any]]:
    long_rows = history[:1000]
    recent_rows = history[:30]
    pool = shape_candidates(shape)
    prior = 1.0 / len(pool) if pool else 1.0 / 16.0
    scored: List[Dict[str, Any]] = []
    for candidate in pool:
        long_hits = sum(1 for row in long_rows if shape_matches(row, safe_str(shape.get("key")), candidate))
        long_rate = (long_hits + prior * 16) / (len(long_rows) + 16) if long_rows else prior
        weighted_hits = 0.0
        weights = 0.0
        for index, row in enumerate(recent_rows):
            weight = math.pow(0.94, index)
            weights += weight
            if shape_matches(row, safe_str(shape.get("key")), candidate):
                weighted_hits += weight
        recent_rate = (weighted_hits + prior * 10) / (weights + 10)
        scored.append({"value": candidate, "score": long_rate * 0.70 + recent_rate * 0.30, "longHits": long_hits})
    scored.sort(key=lambda item: (item["score"], item["longHits"], safe_str(item["value"])))
    return scored


def choose_shape_set(history: List[Dict[str, Any]], shape: Dict[str, Any]) -> Dict[str, str]:
    scored = shape_candidate_set(history, shape)
    if not scored:
        return {"value": "--", "primary": "--", "backup": "--"}
    pool = scored[: min(shape_dynamic_pool_size(shape), len(scored))]
    selected = pool[stable_shape_index(history, shape, len(pool))] if pool else scored[0]
    primary = pool[0] if pool else selected
    backup = pool[(stable_shape_index(history, shape, len(pool)) + 1) % len(pool)] if len(pool) > 1 else primary
    if backup["value"] == selected["value"]:
        for candidate in pool:
            if candidate["value"] != selected["value"]:
                backup = candidate
                break
    return {
        "value": safe_str(selected.get("value"), "--"),
        "primary": safe_str(primary.get("value"), "--"),
        "backup": safe_str(backup.get("value"), "--"),
    }


def first_four_sum(record: Dict[str, Any]) -> Optional[int]:
    nums = as_list(record.get("numbers"))[:4]
    if len(nums) < 4:
        return None
    try:
        return sum(int(n) for n in nums)
    except Exception:
        return None


def digits_cooccur(record: Dict[str, Any], code: str) -> bool:
    nums = set(safe_str(n) for n in as_list(record.get("numbers"))[:4])
    digits = [ch for ch in safe_str(code) if ch.isdigit()]
    return bool(digits) and all(ch in nums for ch in digits)


def extra_matches(record: Dict[str, Any], extra: Dict[str, Any], code: str) -> bool:
    key = safe_str(extra.get("key"))
    if key == "sumAvoid":
        total = first_four_sum(record)
        return total is not None and safe_str(total) == safe_str(code)
    return digits_cooccur(record, code)


def extra_success(extra: Dict[str, Any], matched: bool) -> bool:
    return matched if safe_str(extra.get("mode"), "avoid") == "hit" else not matched


def choose_extra(history: List[Dict[str, Any]], extra: Dict[str, Any]) -> str:
    key = safe_str(extra.get("key"))
    if key == "sumAvoid":
        candidates = [str(value) for value in range(10, 27)]
    elif safe_str(extra.get("kind")) == "threeDigits":
        candidates = ["".join(map(str, combo)) for combo in itertools.combinations(range(10), 3)]
    else:
        candidates = ["".join(map(str, combo)) for combo in itertools.combinations(range(10), 2)]
    if not candidates:
        return "--"
    scored: List[Tuple[float, int, str]] = []
    long_rows = history[:1000]
    recent_rows = history[:30]
    for candidate in candidates:
        long_hits = sum(1 for row in long_rows if extra_matches(row, extra, candidate))
        recent_hits = 0.0
        recent_weight = 0.0
        for index, row in enumerate(recent_rows):
            weight = math.pow(0.94, index)
            recent_weight += weight
            if extra_matches(row, extra, candidate):
                recent_hits += weight
        long_rate = long_hits / len(long_rows) if long_rows else 0.0
        recent_rate = recent_hits / recent_weight if recent_weight else long_rate
        appear_rate = long_rate * 0.70 + recent_rate * 0.30
        if safe_str(extra.get("mode")) == "hit":
            score = appear_rate
            scored.append((-score, -long_hits, candidate))
        else:
            score = 1.0 - appear_rate
            scored.append((-score, long_hits, candidate))
    scored.sort(key=lambda row: (row[0], row[1], int(row[2]) if row[2].isdigit() else 0))
    return scored[0][2]


def extra_def_by_key(key: str) -> Optional[Dict[str, Any]]:
    return next((item for item in EXTRA_DEFS if item["key"] == key), None)


def ranked_two_digit_cooccur_codes(history: List[Dict[str, Any]], limit: int, weighted: bool = False) -> List[str]:
    extra = extra_def_by_key("twoDigitHit")
    if not extra:
        return []
    rows = history[: max(1, limit)]
    if not rows:
        return []
    scored: List[Tuple[float, float, str]] = []
    for pair in ("".join(combo) for combo in itertools.combinations(DIGIT_ORDER, 2)):
        hits = 0.0
        weight_total = 0.0
        for index, row in enumerate(rows):
            weight = math.pow(0.94, index) if weighted else 1.0
            weight_total += weight
            if extra_matches(row, extra, pair):
                hits += weight
        rate = hits / weight_total if weight_total else 0.0
        scored.append((-rate, -hits, pair))
    scored.sort(key=lambda row: (row[0], row[1], row[2]))
    return [row[2] for row in scored]


def choose_two_digit_cooccur_code(history: List[Dict[str, Any]], limit: int, weighted: bool = False) -> str:
    ranked = ranked_two_digit_cooccur_codes(history, limit, weighted)
    return ranked[0] if ranked else ""


def position_def_by_key(key: str) -> Optional[Dict[str, Any]]:
    clean = safe_str(key)
    return next((item for item in POSITION_DEFS if item["key"] == clean or item.get("state_key") == clean), None)


def hewei_def_by_key(key: str) -> Optional[Dict[str, Any]]:
    return next((item for item in HEWEI_DEFS if item["key"] == key), None)


def row_position_digit(row: Dict[str, Any], key: str) -> str:
    pos = position_def_by_key(key)
    if not pos:
        return ""
    index = int(pos["index"])
    ev = as_dict(row.get("evaluation"))
    numbers = actual_numbers_from_evaluation(ev) if ev else []
    if len(numbers) <= index:
        numbers = as_list(row.get("numbers"))
    if len(numbers) <= index:
        return ""
    digit = safe_str(numbers[index])
    return digit if re.fullmatch(r"\d", digit) else ""


def hewei_group_from_numbers(numbers: Sequence[Any]) -> List[str]:
    if len(numbers) < 4:
        return []
    try:
        values = [int(safe_str(x)) for x in numbers[:4]]
    except Exception:
        return []
    return [
        str((values[0] + values[1] + values[2]) % 10),
        str((values[1] + values[2] + values[3]) % 10),
        str((values[0] + values[1] + values[2] + values[3]) % 10),
    ]


def row_hewei_group(row: Dict[str, Any]) -> List[str]:
    ev = as_dict(row.get("evaluation"))
    group = composite_hewei_group(ev) if ev else []
    if group:
        return group
    return hewei_group_from_numbers(as_list(row.get("numbers")))


def row_hewei_digit(row: Dict[str, Any], key: str) -> str:
    hewei = hewei_def_by_key(key)
    if not hewei or hewei["group_index"] is None:
        return ""
    group = row_hewei_group(row)
    index = int(hewei["group_index"])
    if len(group) <= index:
        return ""
    digit = safe_str(group[index])
    return digit if re.fullmatch(r"\d", digit) else ""


def least_seen_position_digits(data: Dict[str, Any], key: str, limit: int, weighted: bool = False) -> List[str]:
    counts: Dict[str, float] = {digit: 0.0 for digit in DIGIT_ORDER}
    rows = evaluated_rows(data)
    if not rows:
        rows = history_records(data)
    for index, row in enumerate(rows[: max(1, limit)]):
        digit = row_position_digit(row, key)
        if digit not in counts:
            continue
        weight = math.pow(0.94, index) if weighted else 1.0
        counts[digit] += weight
    return sorted(DIGIT_ORDER, key=lambda digit: (counts[digit], DIGIT_ORDER.index(digit)))


def least_seen_hewei_digits(data: Dict[str, Any], key: str, limit: int, weighted: bool = False) -> List[str]:
    counts: Dict[str, float] = {digit: 0.0 for digit in DIGIT_ORDER}
    rows = evaluated_rows(data)
    if not rows:
        rows = history_records(data)
    for index, row in enumerate(rows[: max(1, limit)]):
        weight = math.pow(0.94, index) if weighted else 1.0
        if key == "unified":
            for digit in unique_digits(row_hewei_group(row)):
                if digit in counts:
                    counts[digit] += weight
            continue
        digit = row_hewei_digit(row, key)
        if digit in counts:
            counts[digit] += weight
    return sorted(DIGIT_ORDER, key=lambda digit: (counts[digit], DIGIT_ORDER.index(digit)))


def two_digit_code(values: Iterable[Any]) -> str:
    digits = unique_digits(values, limit=2)
    return "".join(digits) if len(digits) == 2 else ""


# 对数映射：0↔5,1↔6,2↔7,3↔8,4↔9（与 penta5_exclusion_system.DUISHU_BY_DIGIT 一致）
DUISHU_PAIR_BY_DIGIT = {
    "0": "05", "5": "05",
    "1": "16", "6": "16",
    "2": "27", "7": "27",
    "3": "38", "8": "38",
    "4": "49", "9": "49",
}


def split_three_digit_duishu(code: str) -> str:
    """三对数码（如 689）拆成连字符分隔对子（16-38-49），与后端 shot 字段一致。

    规则 4.5：三对数必须拆分对子，禁止输出 除对数689。
    """
    pairs: List[str] = []
    seen: set = set()
    for digit in unique_digits([code]):
        pair = DUISHU_PAIR_BY_DIGIT.get(digit)
        if not pair or pair in seen:
            continue
        seen.add(pair)
        pairs.append(pair)
    return "-".join(pairs)


def one_digit_code(values: Iterable[Any]) -> str:
    digits = unique_digits(values, limit=1)
    return digits[0] if digits else ""


def position_code_candidates(data: Dict[str, Any], key: str, entry: Dict[str, Any]) -> List[Tuple[str, str]]:
    seen: Dict[str, bool] = {}
    candidates: List[Tuple[str, str]] = []

    def add(name: str, values: Iterable[Any], digit_count: int) -> None:
        code = one_digit_code(values) if digit_count == 1 else two_digit_code(values)
        if not code or code in seen:
            return
        seen[code] = True
        candidates.append((name, code))

    backend_values = [
        entry.get("main"),
        entry.get("backup"),
        entry.get("primaryDigit"),
        entry.get("originalBackupDigit"),
        as_list(entry.get("killDigits")),
        entry.get("recommendDigit"),
        entry.get("selectedDigit"),
    ]
    algorithm_values = [
        entry.get("mainAlgorithmDigit"),
        entry.get("backupAlgorithmDigit"),
        entry.get("main"),
        entry.get("backup"),
    ]
    low30 = least_seen_position_digits(data, key, 30)
    low60 = least_seen_position_digits(data, key, 60)
    low120 = least_seen_position_digits(data, key, 120)
    low300 = least_seen_position_digits(data, key, 300)
    weighted30 = least_seen_position_digits(data, key, 30, weighted=True)
    weighted60 = least_seen_position_digits(data, key, 60, weighted=True)

    add("单排-数据主", backend_values, 1)
    add("单排-算法主", algorithm_values, 1)
    add("单排-30低频", low30, 1)
    add("单排-60低频", low60, 1)
    add("单排-120低频", low120, 1)
    add("单排-300低频", low300, 1)
    add("单排-30权重低频", weighted30, 1)
    add("单排-60权重低频", weighted60, 1)

    backend_digits = unique_digits(backend_values, limit=2)
    algorithm_digits = unique_digits(algorithm_values, limit=2)
    if len(backend_digits) >= 2:
        add("单排-数据备", [backend_digits[1]], 1)
    if len(algorithm_digits) >= 2:
        add("单排-算法备", [algorithm_digits[1]], 1)

    add("双排-数据组合", backend_values, 2)
    add("双排-算法组合", algorithm_values, 2)
    add("双排-30低频", low30, 2)
    add("双排-60低频", low60, 2)
    add("双排-120低频", low120, 2)
    add("双排-300低频", low300, 2)
    add("双排-30权重低频", weighted30, 2)
    add("双排-60权重低频", weighted60, 2)

    if backend_digits:
        add("双排-主配30低频", [backend_digits[0], low30], 2)
        add("双排-主配60低频", [backend_digits[0], low60], 2)
        add("双排-主配60权重", [backend_digits[0], weighted60], 2)
    if len(backend_digits) >= 2:
        add("双排-备配30低频", [backend_digits[1], low30], 2)
        add("双排-备配60低频", [backend_digits[1], low60], 2)

    return candidates[:POSITION_CANDIDATE_MODEL_LIMIT]


def hewei_code_candidates(data: Dict[str, Any], key: str, entry: Dict[str, Any], prediction: Dict[str, Any]) -> List[Tuple[str, str]]:
    seen: Dict[str, bool] = {}
    candidates: List[Tuple[str, str]] = []

    def add(name: str, values: Iterable[Any], digit_count: int) -> None:
        code = one_digit_code(values) if digit_count == 1 else two_digit_code(values)
        if not code or code in seen:
            return
        seen[code] = True
        candidates.append((name, code))

    if key == "unified":
        detail = as_dict(as_dict(prediction.get("detail")).get("threeFourHeweiKill"))
        backend_values = [
            prediction.get("threeFourHeweiKillDigit"),
            detail.get("killDigit"),
            detail.get("primaryDigit"),
            prediction.get("threeFourHeweiBackupDigit"),
            detail.get("backupDigit"),
        ]
    else:
        backend_values = [
            entry.get("killDigit"),
            entry.get("primaryDigit"),
            entry.get("backupDigit"),
        ]

    low30 = least_seen_hewei_digits(data, key, 30)
    low60 = least_seen_hewei_digits(data, key, 60)
    low120 = least_seen_hewei_digits(data, key, 120)
    low300 = least_seen_hewei_digits(data, key, 300)
    weighted30 = least_seen_hewei_digits(data, key, 30, weighted=True)
    weighted60 = least_seen_hewei_digits(data, key, 60, weighted=True)
    backend_digits = unique_digits(backend_values, limit=2)

    add("单排-数据主", backend_values, 1)
    if len(backend_digits) >= 2:
        add("单排-数据备", [backend_digits[1]], 1)
    add("单排-30低频", low30, 1)
    add("单排-60低频", low60, 1)
    add("单排-120低频", low120, 1)
    add("单排-300低频", low300, 1)
    add("单排-30权重低频", weighted30, 1)
    add("单排-60权重低频", weighted60, 1)

    add("双排-数据组合", backend_values, 2)
    add("双排-30低频", low30, 2)
    add("双排-60低频", low60, 2)
    add("双排-120低频", low120, 2)
    add("双排-300低频", low300, 2)
    add("双排-30权重低频", weighted30, 2)
    add("双排-60权重低频", weighted60, 2)

    if backend_digits:
        add("双排-主配30低频", [backend_digits[0], low30], 2)
        add("双排-主配60低频", [backend_digits[0], low60], 2)
        add("双排-主配60权重", [backend_digits[0], weighted60], 2)
    if len(backend_digits) >= 2:
        add("双排-备配30低频", [backend_digits[1], low30], 2)
        add("双排-备配60低频", [backend_digits[1], low60], 2)

    return candidates[:HEWEI_CANDIDATE_MODEL_LIMIT]


def current_items(data: Dict[str, Any]) -> List[ShotItem]:
    prediction = as_dict(data.get("currentPrediction"))
    if not prediction:
        raise RuntimeError("state has no currentPrediction")
    items: List[ShotItem] = []
    position_kills = as_dict(prediction.get("positionKills"))
    for pos in POSITION_DEFS:
        key = safe_str(pos["key"])
        state_key = safe_str(pos.get("state_key"))
        entry = as_dict(position_kills.get(state_key))
        for model_name, code in position_code_candidates(data, state_key, entry):
            digit_count = len(unique_digits([code]))
            side = "dual" if digit_count >= 2 else "recommend"
            label = "双排" if digit_count >= 2 else "单排"
            items.append(ShotItem("position", key, side, f"{pos['label']}{label}[{model_name}]", code))

    independent = as_dict(prediction.get("independentHeweiKills"))
    for hewei in HEWEI_DEFS:
        key = safe_str(hewei["key"])
        entry = as_dict(independent.get(key))
        for model_name, code in hewei_code_candidates(data, key, entry, prediction):
            digit_count = len(unique_digits([code]))
            side = "dual" if digit_count >= 2 else "recommend"
            label = "双排" if digit_count >= 2 else "单排"
            items.append(ShotItem("hewei", key, side, f"{hewei['label']}{label}[{model_name}]", code))

    history = history_records(data)
    for shape in SHAPE_DEFS:
        chosen = choose_shape_set(history, shape)
        code = chosen.get("value", "--")
        if code and code != "--":
            items.append(ShotItem("shape", safe_str(shape["key"]), "recommend", f"{shape['label']}推荐", code))

    extra_kills = as_dict(prediction.get("extraKills"))
    for extra in EXTRA_DEFS:
        saved = as_dict(extra_kills.get(safe_str(extra["key"])))
        code = first_value(saved.get("value"), saved.get("code"), default="")
        if not code:
            code = choose_extra(history, extra)
        if code and code != "--":
            items.append(ShotItem("extra", safe_str(extra["key"]), "recommend", safe_str(extra["label"]), code))

    return [with_item_scores(data, item) for item in items]


def panel_recommendation_items(data: Dict[str, Any]) -> List[ShotItem]:
    prediction = as_dict(data.get("currentPrediction"))
    if not prediction:
        raise RuntimeError("state has no currentPrediction")
    items: List[ShotItem] = []
    seen: Dict[str, bool] = {}

    def add_unique(item: ShotItem) -> None:
        code = safe_str(item.code)
        if not code or code == "--":
            return
        ident = f"{item.kind}:{item.key}:{code}"
        if ident in seen:
            return
        seen[ident] = True
        item.code = code
        items.append(item)

    position_kills = as_dict(prediction.get("positionKills"))
    for pos in POSITION_DEFS:
        key = safe_str(pos["key"])
        state_key = safe_str(pos.get("state_key"))
        label = safe_str(pos["label"])
        entry = as_dict(position_kills.get(state_key))
        kill_digits = as_list(entry.get("killDigits"))
        recommend = first_value(
            entry.get("recommendDigit"),
            entry.get("selectedDigit"),
            entry.get("main"),
            kill_digits[0] if kill_digits else None,
            default="",
        )
        main = first_value(
            entry.get("primaryDigit"),
            entry.get("mainAlgorithmDigit"),
            entry.get("mainDigit"),
            entry.get("main"),
            default="",
        )
        backup = first_value(
            entry.get("originalBackupDigit"),
            entry.get("backupAlgorithmDigit"),
            entry.get("backup"),
            entry.get("backupDigit"),
            kill_digits[1] if len(kill_digits) > 1 else None,
            default="",
        )
        add_unique(ShotItem("position", key, "recommend", f"{label}推荐", recommend))
        add_unique(ShotItem("position", key, "main", f"{label}主攻", main))
        add_unique(ShotItem("position", key, "backup", f"{label}备用", backup))

    independent = as_dict(prediction.get("independentHeweiKills"))
    for hewei in HEWEI_DEFS:
        key = safe_str(hewei["key"])
        label = safe_str(hewei["label"])
        if key == "unified":
            detail = as_dict(as_dict(prediction.get("detail")).get("threeFourHeweiKill"))
            recommend = first_value(prediction.get("threeFourHeweiKillDigit"), detail.get("killDigit"), default="")
            main = first_value(detail.get("primaryDigit"), recommend, default="")
            backup = first_value(prediction.get("threeFourHeweiBackupDigit"), detail.get("backupDigit"), default="")
        else:
            entry = as_dict(independent.get(key))
            recommend = first_value(entry.get("killDigit"), default="")
            main = first_value(entry.get("primaryDigit"), recommend, default="")
            backup = first_value(entry.get("backupDigit"), default="")
        add_unique(ShotItem("hewei", key, "recommend", f"{label}推荐", recommend))
        add_unique(ShotItem("hewei", key, "main", f"{label}主攻", main))
        add_unique(ShotItem("hewei", key, "backup", f"{label}备用", backup))

    return [with_item_scores(data, item) for item in items]


def backend_auto_top6_items(data: Dict[str, Any]) -> List[ShotItem]:
    prediction = as_dict(data.get("currentPrediction"))
    raw_items = as_list(prediction.get("autoTop6Items"))
    if not raw_items:
        auto_top = as_dict(prediction.get("autoTop6"))
        raw_items = as_list(auto_top.get("items"))
    items: List[ShotItem] = []
    def optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except Exception:
            return None

    for raw in raw_items:
        entry = as_dict(raw)
        kind = safe_str(entry.get("kind"))
        key = safe_str(entry.get("key"))
        code = safe_str(entry.get("code"))
        if not kind or not key or not code or code == "--":
            continue
        name = safe_str(entry.get("name"), "后端六项")
        if kind == "position":
            pos = position_def_by_key(key)
            if not pos:
                continue
            key = safe_str(pos["key"])
            name = re.sub(r"^(?:万位|千位|百位|十位|个位|球[一二三四])", "", name)
            name = safe_str(pos["label"]) + (name or "模型")
        item = ShotItem(
            kind=kind,
            key=key,
            side=safe_str(entry.get("side"), "recommend"),
            name=name,
            code=code,
            rate=optional_float(entry.get("rate")),
            rate30=optional_float(entry.get("rate30")),
            rate60=optional_float(entry.get("rate60")),
            rate120=optional_float(entry.get("rate120")),
            rate300=optional_float(entry.get("rate300")),
            stable_score=optional_float(entry.get("stableScore")),
            stage_label=safe_str(entry.get("stageLabel"), "后端快照"),
            shape_key=safe_str(entry.get("shapeKey")),
        )
        items.append(item)
    return items


def backend_recommend2_items(data: Dict[str, Any]) -> List[ShotItem]:
    prediction = as_dict(data.get("currentPrediction"))
    raw_items = as_list(prediction.get("recommend2Items"))
    if not raw_items:
        recommend2 = as_dict(prediction.get("recommend2"))
        raw_items = as_list(recommend2.get("items"))
    items: List[ShotItem] = []

    def optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            number = float(value)
            return number if math.isfinite(number) else None
        except Exception:
            return None

    for index, raw in enumerate(raw_items, 1):
        entry = as_dict(raw)
        kind = safe_str(entry.get("kind"))
        key = safe_str(entry.get("key"))
        code = safe_str(entry.get("code"))
        if not kind or not key or not code or code == "--":
            continue
        name = safe_str(entry.get("name"), f"推荐2模型{index}")
        if kind == "position":
            pos = position_def_by_key(key)
            if not pos:
                continue
            key = safe_str(pos["key"])
            name = re.sub(r"^(?:万位|千位|百位|十位|个位|球[一二三四])", "", name)
            name = safe_str(pos["label"]) + (name or "模型")
        item = ShotItem(
            kind=kind,
            key=key,
            side=safe_str(entry.get("side"), "recommend"),
            name=name,
            code=code,
            rate=optional_float(first_value(entry.get("stableScore"), entry.get("rate"), default=None)),
            rate30=optional_float(entry.get("rate30")),
            rate60=optional_float(entry.get("rate60")),
            rate120=optional_float(entry.get("rate120")),
            rate300=optional_float(entry.get("rate300")),
            stable_score=optional_float(first_value(entry.get("stableScore"), entry.get("rate"), default=None)),
            stage_label=safe_str(entry.get("stageLabel"), "推荐2快照"),
            shape_key=safe_str(entry.get("shapeKey")),
        )
        items.append(item)
    return items


def two_digit_cooccur_candidate_items(data: Dict[str, Any]) -> List[ShotItem]:
    extra = extra_def_by_key("twoDigitHit")
    if not extra:
        return []
    history = history_records(data)
    prediction = as_dict(data.get("currentPrediction"))
    saved = as_dict(as_dict(prediction.get("extraKills")).get("twoDigitHit"))
    candidates = (
        ("两数共现数据推荐", first_value(saved.get("value"), saved.get("code"), choose_extra(history, extra), default="")),
        ("两数共现近30权重", choose_two_digit_cooccur_code(history, 30, weighted=True)),
        ("两数共现120稳定", choose_two_digit_cooccur_code(history, 120, weighted=False)),
    )
    items: List[ShotItem] = []
    seen: Dict[str, bool] = {}
    candidate_rows: List[Tuple[str, str]] = list(candidates)
    for index, code in enumerate(ranked_two_digit_cooccur_codes(history, 300, weighted=True), 1):
        if len({two_digit_code([raw]) for _, raw in candidate_rows if two_digit_code([raw])}) >= 3:
            break
        candidate_rows.append((f"两数共现稳定备选{index}", code))

    for name, raw_code in candidate_rows:
        code = two_digit_code([raw_code])
        if not code or code in seen:
            continue
        seen[code] = True
        items.append(with_item_scores(data, ShotItem("extra", "twoDigitHit", "recommend", name, code)))
        if len(items) >= 3:
            break
    return items


def two_digit_cooccur_item(data: Dict[str, Any]) -> Optional[ShotItem]:
    items = two_digit_cooccur_candidate_items(data)
    if not items:
        return None
    items.sort(
        key=lambda item: (
            -(item.stable_score if item.stable_score is not None else -1.0),
            -(item.rate120 if item.rate120 is not None else -1.0),
            -(item.rate300 if item.rate300 is not None else -1.0),
            item.name,
        )
    )
    chosen = items[0]
    chosen.name = "两数共现"
    return chosen


def two_digit_avoid_stable_item(data: Dict[str, Any]) -> Optional[ShotItem]:
    avoid_keys = {"twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3"}
    items = [item for item in current_items(data) if item.kind == "extra" and item.key in avoid_keys]
    if not items:
        return None
    items.sort(
        key=lambda item: (
            -(item.stable_score if item.stable_score is not None else -1.0),
            -(item.rate300 if item.rate300 is not None else -1.0),
            -(item.rate120 if item.rate120 is not None else -1.0),
            -(item.rate60 if item.rate60 is not None else -1.0),
            item.name,
        )
    )
    chosen = items[0]
    chosen.name = "两数共现避开稳定"
    return chosen


def top_five_items(data: Dict[str, Any], items: List[ShotItem]) -> List[ShotItem]:
    scored = [item for item in items if item.rate is not None]

    def item_sort_key(item: ShotItem) -> Tuple[float, float, float, float, int, str]:
        return (
            -(item.stable_score if item.stable_score is not None else 0.0),
            -(item.rate300 if item.rate300 is not None else 0.0),
            -(item.rate60 if item.rate60 is not None else 0.0),
            -(item.rate120 if item.rate120 is not None else 0.0),
            len(unique_digits([item.code])),
            item.name,
        )

    chosen: List[ShotItem] = []
    used: Dict[str, bool] = {}

    def item_id(item: ShotItem) -> str:
        return f"{item.kind}:{item.key}:{item.side}:{item.code}"

    def best_per_kind_key(pool: List[ShotItem]) -> List[ShotItem]:
        best: Dict[Tuple[str, str], ShotItem] = {}
        for item in sorted(pool, key=item_sort_key):
            slot = (item.kind, item.key)
            if slot not in best:
                best[slot] = item
        return list(best.values())

    def add_many(pool: List[ShotItem], count: int) -> None:
        nonlocal chosen
        remaining = count
        for item in sorted(pool, key=item_sort_key):
            if len(chosen) >= 5 or remaining <= 0:
                return
            ident = item_id(item)
            if ident not in used:
                used[ident] = True
                chosen.append(item)
                remaining -= 1

    add_many(best_per_kind_key([item for item in scored if item.kind == "position"]), 3)
    add_many(best_per_kind_key([item for item in scored if item.kind == "hewei"]), 2)
    if len(chosen) < 5:
        add_many([item for item in scored if item.kind not in {"position", "hewei"}], 5 - len(chosen))
    if len(chosen) < 5:
        add_many([item for item in items if item.rate is None], 5 - len(chosen))
    return chosen[:5]


def panel_top_five_items(data: Dict[str, Any], include_supplements: bool = True) -> List[ShotItem]:
    if include_supplements:
        backend_items = backend_auto_top6_items(data)
        if backend_items:
            items = backend_items[:6]
            # 4.3：第6项必须是 twoDigitHit 两数共现（除对数），
            # 禁止 twoDigitAvoid/twoDigitAvoid2/twoDigitAvoid3 稳定项占第6位（不用避开组冒充共现项）。
            if len(items) >= 6 and safe_str(items[5].key) != "twoDigitHit":
                cooccur = two_digit_cooccur_item(data)
                if cooccur:
                    items[5] = cooccur
                else:
                    logging.warning(
                        "panel_top_five_items: 后端第6项为 %s（非 twoDigitHit 共现），且无可用共现项可替换",
                        items[5].key,
                    )
            return items

    items = panel_recommendation_items(data)
    scored = [item for item in items if item.rate is not None]

    def item_sort_key(item: ShotItem) -> Tuple[float, float, float, float, str]:
        return (
            -(item.stable_score if item.stable_score is not None else 0.0),
            -(item.rate300 if item.rate300 is not None else 0.0),
            -(item.rate60 if item.rate60 is not None else 0.0),
            -(item.rate120 if item.rate120 is not None else 0.0),
            item.name,
        )

    chosen: List[ShotItem] = []
    used: Dict[str, bool] = {}

    def item_id(item: ShotItem) -> str:
        return f"{item.kind}:{item.key}:{item.side}:{item.code}"

    def add_many(pool: List[ShotItem], count: int, max_per_same_model: int = 2) -> None:
        model_count: Dict[str, int] = {}
        remaining = count
        for item in sorted(pool, key=item_sort_key):
            if len(chosen) >= 5 or remaining <= 0:
                return
            ident = item_id(item)
            model_key = f"{item.kind}:{item.key}"
            current = model_count.get(model_key, 0)
            if ident in used or current >= max_per_same_model:
                continue
            used[ident] = True
            model_count[model_key] = current + 1
            chosen.append(item)
            remaining -= 1

    add_many([item for item in scored if item.kind == "position"], 3, 2)
    add_many([item for item in scored if item.kind == "hewei"], 2, 2)
    for item in chosen:
        item.rate = item.stable_score
    if include_supplements:
        cooccur = two_digit_cooccur_item(data)
        if cooccur:
            ident = item_id(cooccur)
            if ident not in used:
                used[ident] = True
                chosen.append(cooccur)
        else:
            logging.warning(
                "panel_top_five_items: two_digit_cooccur_item 返回 None，autoTop6 第6项共现缺失（后端 twoDigitHit 可能为空）"
            )
    return chosen


def select_items(data: Dict[str, Any], selection: str) -> List[ShotItem]:
    mode = selection.lower().strip()
    if mode in FIXED_SHOT_MODES:
        return []
    if mode in PANEL_TOP5_MODES:
        return panel_top_five_items(data, include_supplements=True)
    if mode in RECOMMEND2_MODES:
        return backend_recommend2_items(data)
    if mode in PANEL_TOP5_PURE_MODES:
        return panel_top_five_items(data, include_supplements=False)
    items = current_items(data)
    if mode == "top5":
        return top_five_items(data, items)
    if mode == "position-hewei":
        return [item for item in items if item.kind in {"position", "hewei"}]
    if mode == "positions":
        return [item for item in items if item.kind == "position"]
    if mode == "all":
        return items
    raise RuntimeError(f"unsupported selection mode: {selection}")


def selection_adds_reverse_shapes(selection: str) -> bool:
    return False


def selection_uses_fixed_shot(selection: str) -> bool:
    return selection.lower().strip() in FIXED_SHOT_MODES


def remove_codes_from_segment(text: str, start_label: str, end_labels: Sequence[str], codes: Sequence[str]) -> str:
    start = text.find(start_label)
    if start < 0:
        return text
    value_start = start + len(start_label)
    end = len(text)
    for label in end_labels:
        index = text.find(label, value_start)
        if 0 <= index < end:
            end = index
    raw_value = text[value_start:end]
    digits_only = re.sub(r"\D+", "", raw_value)
    # 段内若混入非数字字符，静默跳过会造成「数字漏过滤」。改为只保留数字并告警。
    if digits_only != raw_value:
        logging.warning(
            "remove_codes_from_segment: 段 %r 含非数字字符 %r，已归一化为 %r（避免漏过滤）",
            start_label,
            raw_value,
            digits_only,
        )
    if not digits_only:
        return text
    value = digits_only
    for code in codes:
        for digit in unique_digits([code]):
            value = value.replace(digit, "")
    return text[:value_start] + value + text[end:]


def normalize_digit_pool_segment(text: str, label: str, end_labels: Sequence[str]) -> str:
    start = text.find(label)
    if start < 0:
        return text
    value_start = start + len(label)
    end = len(text)
    for end_label in end_labels:
        index = text.find(end_label, value_start)
        if 0 <= index < end:
            end = index
    value = text[value_start:end]
    if not re.fullmatch(r"\d+", value):
        logging.warning("normalize_digit_pool_segment: 段 %r 值 %r 非纯数字，已跳过归一化", label, value)
        return text
    return text[:value_start] + "1234567890" + text[end:]


def remove_digit_pool_segment(text: str, label: str, end_labels: Sequence[str]) -> str:
    start = text.find(label)
    if start < 0:
        return text
    value_start = start + len(label)
    end = len(text)
    for end_label in end_labels:
        index = text.find(end_label, value_start)
        if 0 <= index < end:
            end = index
    value = text[value_start:end]
    if not re.fullmatch(r"\d+", value):
        logging.warning("remove_digit_pool_segment: 段 %r 值 %r 非纯数字，已跳过删除", label, value)
        return text
    return text[:start] + text[end:]


def selected_codes(items: Sequence[ShotItem], kind: str, key: str) -> List[str]:
    return [item.code for item in items if item.kind == kind and item.key == key and item.code and item.code != "--"]


def remove_from_digit_pool(codes: Sequence[str]) -> str:
    value = "1234567890"
    for code in codes:
        for digit in unique_digits([code]):
            value = value.replace(digit, "")
    return value


def reverse_shape_digit_group(data: Dict[str, Any], shape_key: str, shape_code: str) -> str:
    code = safe_str(shape_code)
    if not re.fullmatch(r"[大小单双]{4}", code):
        return ""
    rows = evaluated_rows(data)
    if not rows:
        rows = history_records(data)
    selected: List[str] = []

    for index, token in enumerate(code[:4]):
        if index >= len(POSITION_DEFS):
            break
        counts: Dict[str, float] = {digit: 0.0 for digit in DIGIT_ORDER if property_matches(digit, token)}
        if not counts:
            continue
        key = safe_str(POSITION_DEFS[index]["key"])
        for row_index, row in enumerate(rows[:120]):
            digit = row_position_digit(row, key)
            if digit in counts:
                counts[digit] += math.pow(0.94, row_index)
        for digit in sorted(counts, key=lambda value: (counts[value], DIGIT_ORDER.index(value))):
            if digit not in selected:
                selected.append(digit)
                break
        if len(selected) >= 2:
            return "".join(selected[:2])

    for token in code[:4]:
        for digit in DIGIT_ORDER:
            if property_matches(digit, token) and digit not in selected:
                selected.append(digit)
                if len(selected) >= 2:
                    return "".join(selected[:2])
    return "".join(selected[:2]) if len(selected) >= 2 else ""


def best_reverse_shape_item(data: Dict[str, Any], parity_code: str, size_code: str) -> Optional[ShotItem]:
    candidates: List[ShotItem] = []
    for key, label, code in (
        ("shapeParity", "单双反形态流", parity_code),
        ("shapeSize", "大小反形态流", size_code),
    ):
        if re.fullmatch(r"[大小单双]{4}", safe_str(code)):
            candidates.append(with_item_scores(data, ShotItem("shape", key, "auto-reverse", label, safe_str(code))))
    if not candidates:
        return None
    candidates.sort(
        key=lambda item: (
            -(item.stable_score if item.stable_score is not None else -1.0),
            -(item.rate300 if item.rate300 is not None else -1.0),
            -(item.rate120 if item.rate120 is not None else -1.0),
            item.name,
        )
    )
    return candidates[0]


def build_practice_text(
    data: Dict[str, Any],
    selected: Sequence[ShotItem],
    base_text: str,
    add_reverse_position_shapes: bool = True,
) -> str:
    text = re.sub(r"\s+", "", base_text or DEFAULT_BASE_TEXT)
    text = normalize_digit_pool_segment(text, "千百十合", ("百十个合", "千百十个合", "除"))
    text = normalize_digit_pool_segment(text, "百十个合", ("千百十个合", "除"))
    text = normalize_digit_pool_segment(text, "千百十个合", ("除",))
    text = remove_digit_pool_segment(text, "千百十合", ("百十个合", "千百十个合", "除"))
    text = remove_digit_pool_segment(text, "百十个合", ("千百十个合", "除"))
    text = remove_digit_pool_segment(text, "千百十个合", ("除",))
    position_map = (
        {"start": "千", "end": ("百",), "key": "wan"},
        {"start": "百", "end": ("十",), "key": "qian"},
        {"start": "十", "end": ("个",), "key": "bai"},
        {"start": "个", "end": ("四定", "千百十个合", "除"), "key": "shi"},
    )
    for cfg in position_map:
        text = remove_codes_from_segment(text, cfg["start"], cfg["end"], selected_codes(selected, "position", cfg["key"]))

    def ensure_hewei_segment(label: str, end_labels: Sequence[str], codes: Sequence[str]) -> str:
        nonlocal text
        unique_codes = unique_texts(codes)
        if not unique_codes:
            return text
        if label not in text:
            text += label + remove_from_digit_pool(unique_codes)
        text = remove_codes_from_segment(text, label, end_labels, unique_codes)
        return text

    unified_hewei = selected_codes(selected, "hewei", "unified")

    def independent_or_unified(codes: Sequence[str]) -> List[str]:
        own = unique_texts(codes)
        return own if own else unified_hewei

    ensure_hewei_segment("千百十合", ("百十个合", "千百十个合", "除"), independent_or_unified(selected_codes(selected, "hewei", "wanQianBai")))
    ensure_hewei_segment("百十个合", ("千百十个合", "除"), independent_or_unified(selected_codes(selected, "hewei", "qianBaiShi")))
    ensure_hewei_segment(
        "千百十个合",
        ("除",),
        independent_or_unified(unique_texts(selected_codes(selected, "hewei", "wanQianBaiShi") + selected_codes(selected, "hewei", "total"))),
    )

    text = re.sub(r"四定除值\d+", "四定", text)
    text = re.sub(r"四定\d+", "四定", text)
    text = re.sub(r"除对数\d{2,3}(?:-\d{2,3})*", "", text)
    text = re.sub(r"含对数\d{2,3}", "", text)
    text = re.sub(r"中对数\d{2,3}", "", text)
    text = re.sub(r"二数合\d+", "", text)
    text = re.sub(r"三数合\d+", "", text)
    text = re.sub(r"除和值\d+", "", text)
    text = re.sub(r"除值\d+", "", text)
    text = re.sub(r"除[大小单双]{4}", "", text)

    tokens: List[str] = []
    seen_tokens: Dict[str, bool] = {}

    def push_token(token: str) -> None:
        token = safe_str(token)
        if token and token not in seen_tokens:
            seen_tokens[token] = True
            tokens.append(token)

    def selected_position_digits(key: str) -> List[str]:
        digits: List[str] = []
        seen: Dict[str, bool] = {}
        for item in selected:
            if item.kind == "position" and item.key == key and item.code:
                digit = re.sub(r"\D+", "", item.code)[:1]
                if digit and digit not in seen:
                    seen[digit] = True
                    digits.append(digit)
        return digits

    def reverse_position_token(key: str, axis: str) -> str:
        values: List[str] = []
        for digit in selected_position_digits(key):
            try:
                number = int(digit)
            except Exception:
                continue
            if axis == "parity":
                token = "双" if number % 2 == 1 else "单"
            else:
                token = "小" if number >= 5 else "大"
            if token not in values:
                values.append(token)
        return values[0] if len(values) == 1 else ""

    def selected_shape_code(shape_key: str) -> str:
        for item in selected:
            if item.kind == "shape" and item.key == shape_key and re.fullmatch(r"[大小单双]{4}", item.code or ""):
                return force_three_with_one_shape(item.code, shape_key)
        return ""

    def base_shape_code(shape_key: str, fallback: str) -> str:
        selected_code = selected_shape_code(shape_key)
        if selected_code:
            return selected_code
        shape = next((entry for entry in SHAPE_DEFS if entry["key"] == shape_key), None)
        if shape:
            chosen = choose_shape_set(history_records(data), shape)
            code = safe_str(chosen.get("value"))
            if re.fullmatch(r"[大小单双]{4}", code):
                return force_three_with_one_shape(code, shape_key)
        return fallback

    def complete_locked_shape(shape_key: str, locked: Sequence[str], fallback: str) -> str:
        base = base_shape_code(shape_key, fallback)
        shape = next((entry for entry in SHAPE_DEFS if entry["key"] == shape_key), None)
        pool = shape_candidates(shape) if shape else []

        def matches_locked(candidate: str) -> bool:
            for index in range(4):
                if index < len(locked) and locked[index] and candidate[index] != locked[index]:
                    return False
            return True

        def distance(candidate: str, target: str) -> int:
            return sum(1 for index in range(4) if candidate[index] != target[index])

        matches = [candidate for candidate in pool if matches_locked(candidate)]
        if matches:
            return sorted(
                matches,
                key=lambda candidate: (
                    distance(candidate, base),
                    0 if candidate[0] == base[0] else 1,
                    candidate,
                ),
            )[0]
        out = list((base or fallback or "单单单双")[:4])
        while len(out) < 4:
            out.append("")
        fill = fallback or "单单单双"
        for index in range(4):
            if index < len(locked) and locked[index]:
                out[index] = locked[index]
            elif not re.fullmatch(r"[大小单双]", out[index] or ""):
                out[index] = fill[index] if index < len(fill) else "单"
        return "".join(out[:4])

    if add_reverse_position_shapes:
        parity_locked = ["", "", "", ""]
        size_locked = ["", "", "", ""]
        changed = False
        for index, pos in enumerate(POSITION_DEFS):
            parity_token = reverse_position_token(safe_str(pos["key"]), "parity")
            size_token = reverse_position_token(safe_str(pos["key"]), "size")
            if parity_token:
                parity_locked[index] = parity_token
                changed = True
            if size_token:
                size_locked[index] = size_token
                changed = True
        if changed:
            parity_code = complete_locked_shape("shapeParity", parity_locked, "双双双单")
            size_code = complete_locked_shape("shapeSize", size_locked, "大大大小")
            reverse_item = best_reverse_shape_item(data, parity_code, size_code)
            if reverse_item and re.fullmatch(r"[大小单双]{4}", reverse_item.code):
                push_token("除" + reverse_item.code)

    for item in selected:
        token = ""
        if item.kind == "extra" and item.key in {"sumAvoid", "sumRangeAvoid"}:
            token = "除值" + item.code
        elif item.kind == "extra" and item.key == "spanAvoid":
            token = "除跨度" + item.code
        elif item.kind == "extra" and item.key in {"repeatAvoid", "consecutiveAvoid"}:
            token = "除" + item.code
        elif item.kind == "extra" and item.key == "parityRatioAvoid":
            token = "除奇偶比" + item.code
        elif item.kind == "extra" and item.key == "sizeRatioAvoid":
            token = "除大小比" + item.code
        elif item.kind == "extra" and re.fullmatch(r"recentExactCodeAvoid(?:\d+|Max)", item.key):
            window_match = re.fullmatch(r"recentExactCodeAvoid(\d+|Max)", item.key)
            window_value = "最大" if window_match and window_match.group(1) == "Max" else (window_match.group(1) if window_match else item.code)
            token = "除旧码" + window_value
        elif item.kind == "extra" and item.key == "threeDigitAvoid":
            # 4.5：三对数必须拆分对子，禁止 除对数689
            token = "除对数" + split_three_digit_duishu(item.code)
        elif item.kind == "extra" and item.key in {"twoDigitAvoid", "twoDigitAvoid2", "twoDigitAvoid3", "twoDigitHit"}:
            token = "除对数" + item.code
        elif item.kind == "extra" and item.key == "threeDigitHit":
            token = "含对数" + item.code
        elif item.kind == "extra" and item.key == "anyTwoHeweiHit":
            token = "二数合" + item.code
        elif item.kind == "extra" and item.key == "anyThreeHeweiHit":
            token = "三数合" + item.code
        elif item.kind == "shape":
            shape_code = force_three_with_one_shape(item.code, item.key)
            token = "除" + shape_code
        elif item.kind == "derivedReverseShape":
            shape_key = item.shape_key or ("shapeSize" if item.key == "reverseSize" else "shapeParity")
            shape_code = force_three_with_one_shape(item.code, shape_key)
            token = "除" + shape_code
        elif item.kind not in {"position", "hewei"}:
            token = "除" + item.code
        push_token(token)

    generated = re.sub(r"\s+", "", text + "".join(tokens)).strip()
    return generated.rstrip("各") + "各"


def normalize_fixed_shot_text(fixed_shot: str) -> str:
    text = re.sub(r"\s+", "", fixed_shot or "")
    if not text:
        raise RuntimeError("fixed selection requires fixed shot text")
    text = re.sub(r"各(?:\d+(?:\.\d+)?|\.\d+)$", "各", text)
    return text if text.endswith("各") else text + "各"


def parse_shot_language_rules(rules_text: str) -> List[Tuple[str, str]]:
    rules: List[Tuple[str, str]] = []
    for raw_line in safe_str(rules_text).splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=>" in line:
            left, right = line.split("=>", 1)
        elif "=" in line:
            left, right = line.split("=", 1)
        else:
            continue
        if left:
            rules.append((left, right))
    return rules


def apply_shot_language_rules(text: str, rules_text: str, enabled: bool = True) -> str:
    output = safe_str(text)
    if not enabled:
        return output
    for left, right in parse_shot_language_rules(rules_text):
        output = output.replace(left, right)
    return output


def format_message(raw_shot: str, template: str, stake: str, result: ShotBuildResult) -> str:
    chosen_template = template.strip() if template.strip() else ("{shot}{stake}" if stake else "{shot}")
    values = {
        "shot": raw_shot,
        "stake": stake,
        "period": result.period,
        "base_period": result.period,
        "target_period": result.target_period,
        "order_period": result.target_period,
        "prediction_id": result.prediction_id,
        "generated_at": result.generated_at,
        "selection": result.selection,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M:%S"),
    }
    values.update({
        "模型": raw_shot,
        "金额": stake,
        "本期期号": result.period,
        "期号": result.period,
        "订单期号": result.target_period,
        "预测编号": result.prediction_id,
        "生成时间": result.generated_at,
        "推荐模式": result.selection,
        "日期": datetime.now().strftime("%Y-%m-%d"),
        "时间": datetime.now().strftime("%H:%M:%S"),
    })
    try:
        return chosen_template.format(**values)
    except Exception as exc:
        logging.warning("message template format failed, using raw shot: %s", exc)
        return raw_shot + stake


def build_shot(
    data: Dict[str, Any],
    selection: str,
    base_text: str,
    template: str,
    stake: str,
    fixed_shot: str = "",
    shot_language_rules: str = "",
    shot_language_enabled: bool = False,
) -> ShotBuildResult:
    selected = select_items(data, selection)
    if selection_uses_fixed_shot(selection):
        raw_shot = normalize_fixed_shot_text(fixed_shot)
    elif not selected:
        raise RuntimeError("no usable recommendation items found in state")
    else:
        raw_shot = build_practice_text(
            data,
            selected,
            base_text,
            add_reverse_position_shapes=selection_adds_reverse_shapes(selection),
        )
    raw_shot = apply_shot_language_rules(raw_shot, shot_language_rules, shot_language_enabled)
    result = ShotBuildResult(
        message="",
        raw_shot=raw_shot,
        data_key=state_render_key(data),
        period=state_period(data),
        target_period=state_target_period(data),
        prediction_id=state_prediction_id(data),
        generated_at=safe_str(as_dict(data.get("currentPrediction")).get("generatedAtText") or as_dict(data.get("currentPrediction")).get("generatedAt")),
        selection=selection,
        selected_items=selected,
    )
    result.message = format_message(raw_shot, template, stake, result)
    return result


class SeleniumShooter:
    CHAT_INPUT_SELECTORS: Tuple[str, ...] = (
        "textarea",
        "input[type='text']",
        "input:not([type])",
        "[contenteditable='true']",
        "div[contenteditable='true']",
        "[role='textbox']",
        "[data-placeholder]",
        ".chat-input",
        ".input-area",
        ".message-input",
        ".edit-area",
        ".ql-editor",
        ".el-textarea__inner",
    )
    CHAT_SEND_XPATHS: Tuple[str, ...] = (
        "//button[normalize-space()='发送']",
        "//button[contains(normalize-space(), '发送')]",
        "//img[contains(translate(@alt, 'SEND', 'send'), 'send')]",
        "//img[contains(translate(@src, 'SEND', 'send'), 'send')]",
        "//*[contains(@class, 'cursor-pointer') and (contains(translate(@alt, 'SEND', 'send'), 'send') or contains(translate(@src, 'SEND', 'send'), 'send'))]",
        "//*[self::button or self::a][contains(normalize-space(), '发送')]",
        "//*[normalize-space()='发送']",
        "//*[contains(normalize-space(), '发送') and string-length(normalize-space()) < 8]",
        "//*[self::button or self::a or self::div][contains(@class, 'send') and not(contains(@style, 'display: none'))]",
        "//*[self::button or self::a][contains(translate(normalize-space(), 'SEND', 'send'), 'send')]",
    )
    HIT_KEYS: Tuple[str, ...] = (
        "入住:",
        "入住：",
        "本次总派送",
        "本次总派奖",
        "中介费",
        "合房费",
        "房费:",
        "房费：",
        "命中",
        "中奖",
        "win",
        "hit",
    )
    ORDER_ACCEPT_KEYS: Tuple[str, ...] = (
        "[序号]",
        "【序号】",
        "[点单]",
        "【点单】",
        "[单数]",
        "【单数】",
        "[单额]",
        "【单额】",
        "[取消订餐]",
        "【取消订餐】",
        "取消订餐",
        "本期成功订单",
        "中型审核成功",
        "审核成功",
        "户型审核成功",
        "成功订单",
        "通过",
        "√√",
        "success",
    )
    FAIL_KEYS: Tuple[str, ...] = (
        "交易源样式错误",
        "样式错误",
        "格式错误",
        "格式不对",
        "错误",
        "失败",
        "未通过",
        "未入住",
        "未命中",
        "未中",
        "[总出] 0",
        "[总出]0",
        "【总出】 0",
        "【总出】0",
        "总出 0",
        "总出0",
        "拒绝",
        "lose",
        "miss",
        "failed",
        "fail",
    )

    def __init__(
        self,
        url: str,
        headless: bool,
        timeout: int,
        driver_path: str = "",
        profile_dir: Path = Path(""),
        adspower_api_url: str = "",
        adspower_user_id: str = "",
        adspower_api_key: str = "",
        target_password: str = "",
        result_mode: str = "normal",
        ephemeral_profile: bool = False,
    ):
        if webdriver is None or Options is None or WebDriverWait is None:
            raise RuntimeError("selenium is not installed; install selenium or run without --live")
        if not url:
            raise RuntimeError("--url is required when --live is used")
        self.url = url
        self.timeout = max(3, timeout)
        self.profile_dir = profile_dir
        self.headless = bool(headless)
        self.driver_path = safe_str(driver_path)
        self.ephemeral_profile = bool(ephemeral_profile)
        self._temp_profile_dirs: List[Path] = []
        self.adspower_api_url = safe_str(adspower_api_url)
        self.adspower_user_id = safe_str(adspower_user_id)
        self.adspower_api_key = safe_str(adspower_api_key)
        self.target_password = safe_str(target_password)
        self.result_mode = safe_str(result_mode, "normal").lower()
        if not self.adspower_user_id and (self.adspower_api_url or self.adspower_api_key):
            profile = discover_adspower_profile(self.adspower_api_url, self.adspower_api_key, self.timeout)
            self.adspower_user_id = profile.user_id
        if self.adspower_user_id:
            self.driver = self._start_adspower_driver()
        else:
            self.driver = self._start_driver(headless=self.headless, driver_path=self.driver_path)
        self.opened = False

    def period_tokens(self, expected_period: str) -> List[str]:
        digits = re.sub(r"\D+", "", safe_str(expected_period))
        if not digits:
            return []
        tokens = [digits]
        if len(digits) >= 3:
            suffix = digits[-3:]
            tokens.append(suffix)
            try:
                tokens.append(str(int(suffix)))
            except Exception:
                pass
        return unique_texts(tokens)

    def payout_scopes_for_period(self, text: str, expected_period: str) -> List[str]:
        tokens = self.period_tokens(expected_period)
        if not tokens:
            return []
        escaped = "|".join(re.escape(token) for token in sorted(tokens, key=len, reverse=True))
        header_tail = r"\s*[【\[][^】\]\n]{0,80}\|"
        pattern = re.compile(rf"(?<!\d)(?:{escaped})(?!\d){header_tail}")
        next_header = re.compile(rf"(?<!\d)\d{{1,14}}(?!\d){header_tail}")
        scopes: List[str] = []
        for match in pattern.finditer(text):
            start = match.start()
            next_match = next_header.search(text, match.end())
            end = next_match.start() if next_match else min(len(text), match.end() + 700)
            segment = text[start:end]
            if re.search(r"(?:\[总出\]|【总出】|总出|(?:^|[，,;；\s])出\s*[:：])", segment):
                scopes.append(segment)
        return scopes

    def order_period_from_delta(self, delta: str) -> str:
        patterns = (
            r"(?:\[序号\]|【序号】|序号)\s*\[?\s*([0-9]{6,14})\s*\]?",
            r"(?:\[编号\]|【编号】|编号)\s*\[?\s*([0-9]{3,14})\s*\]?",
        )
        for pattern in patterns:
            matches = list(re.finditer(pattern, delta))
            if matches:
                return matches[-1].group(1)
        return ""

    def payout_status_from_delta(self, delta: str, expected_period: str = "", strict_period: bool = False) -> Optional[str]:
        text = safe_str(delta)
        scopes = self.payout_scopes_for_period(text, expected_period) if expected_period else []
        if expected_period and strict_period and not scopes:
            return None
        search_texts = scopes if scopes else [text]
        patterns = (
            r"(?:\[总出\]|【总出】|总出)\s*[:：]?\s*(-?\d+(?:\.\d+)?)",
            r"(?:^|[，,;；\s])出\s*[:：]\s*(-?\d+(?:\.\d+)?)",
        )
        seen_zero = False
        for search_text in search_texts:
            for pattern in patterns:
                for match in re.finditer(pattern, search_text, flags=re.IGNORECASE):
                    value = parse_float(match.group(1))
                    if value is None:
                        continue
                    if value > 0:
                        return "WIN"
                    seen_zero = True
        if seen_zero:
            return "LOSE"
        return None

    def _adspower_start_url(self) -> str:
        api = normalize_adspower_api_url(self.adspower_api_url)
        query = urlencode({"user_id": self.adspower_user_id, "open_tabs": "0", "ip_tab": "0"})
        return f"{api}/api/v1/browser/start?{query}"

    def _start_adspower_driver(self):
        payload = adspower_request_json(
            self.adspower_api_url,
            "/api/v1/browser/start",
            self.adspower_api_key,
            self.timeout,
            {"user_id": self.adspower_user_id, "open_tabs": "0", "ip_tab": "0"},
        )

        if not isinstance(payload, dict):
            raise RuntimeError("AdsPower returned invalid JSON")
        if str(payload.get("code")) not in {"0", "None"} and payload.get("code") not in (0, None):
            raise RuntimeError(f"AdsPower start failed: {payload.get('msg') or payload}")

        data = as_dict(payload.get("data"))
        webdriver_path = safe_str(data.get("webdriver"))
        ws = as_dict(data.get("ws"))
        debugger_address = safe_str(ws.get("selenium"))
        if not webdriver_path or not debugger_address:
            raise RuntimeError(f"AdsPower response missing webdriver/ws.selenium: {payload}")

        options = Options()
        options.add_experimental_option("debuggerAddress", debugger_address)
        try:
            driver = webdriver.Chrome(service=Service(webdriver_path), options=options)
            driver.set_page_load_timeout(self.timeout + 8)
            return driver
        except WebDriverException as exc:
            raise RuntimeError("failed to attach Selenium to AdsPower browser") from exc

    def _profile_dir_for_driver(self) -> Path:
        if not self.profile_dir or not str(self.profile_dir).strip():
            return Path("")
        base = self.profile_dir.resolve()
        if not self.ephemeral_profile:
            base.mkdir(parents=True, exist_ok=True)
            return base
        base.parent.mkdir(parents=True, exist_ok=True)
        temp_dir = Path(tempfile.mkdtemp(prefix=f"{base.name}_", dir=str(base.parent)))
        self._temp_profile_dirs.append(temp_dir)
        return temp_dir

    def _start_driver(self, headless: bool, driver_path: str):
        options = Options()
        options.page_load_strategy = "eager"
        if headless:
            options.add_argument("--headless=new")
        profile_dir = self._profile_dir_for_driver()
        if profile_dir and str(profile_dir).strip():
            options.add_argument(f"--user-data-dir={profile_dir}")
        options.add_argument("--window-size=1365,960")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-sync")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--no-sandbox")
        options.add_argument("--lang=zh-CN,zh")
        options.add_argument(f"--user-agent={random.choice(USER_AGENTS)}")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        options.add_argument("--disable-blink-features=AutomationControlled")
        try:
            if driver_path:
                driver = webdriver.Chrome(service=Service(driver_path), options=options)
            else:
                driver = webdriver.Chrome(options=options)
            driver.set_page_load_timeout(self.timeout + 8)
            return driver
        except WebDriverException as exc:
            raise RuntimeError("Chrome WebDriver 启动失败，请检查 Chrome/chromedriver 是否可用") from exc

    def restart_driver(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass
        if self.adspower_user_id:
            self.driver = self._start_adspower_driver()
        else:
            self.driver = self._start_driver(headless=self.headless, driver_path=self.driver_path)
        self.opened = False

    def wait(self, condition: Callable[[Any], Any], timeout: Optional[int] = None) -> Any:
        return WebDriverWait(self.driver, timeout or self.timeout).until(condition)

    def open(self) -> None:
        if self.opened:
            return
        last_exc: Optional[Exception] = None
        for attempt in range(1, 4):
            try:
                logging.info("opening target page: %s", mask_url(self.url))
                self.driver.get(self.url)
                self.wait(EC.visibility_of_element_located((By.TAG_NAME, "body")), timeout=self.timeout)
                self.wait(lambda d: d.execute_script("return document.readyState") in {"interactive", "complete"}, timeout=self.timeout)
                self.ensure_target_login()
                self.dismiss_target_overlays()
                self.opened = True
                return
            except Exception as exc:
                last_exc = exc
                logging.warning("target page open attempt %s failed: %s", attempt, exc)
                if attempt < 3:
                    self.restart_driver()
        raise RuntimeError(f"failed to open target page: {last_exc}") from last_exc

    def ensure_target_login(self) -> None:
        if not self.target_password:
            return
        deadline = time.time() + min(8, self.timeout)
        while time.time() < deadline:
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
            password_node = self.find_target_password_input(timeout=1)
            if password_node is None:
                if self.submit_target_virtual_password():
                    time.sleep(1.0)
                    if self.find_target_password_input(timeout=1) is None and not self.target_virtual_password_visible():
                        return
                    time.sleep(0.4)
                    continue
                return
            try:
                password_node.click()
            except Exception:
                try:
                    self.driver.execute_script("arguments[0].focus();", password_node)
                except Exception:
                    pass
            try:
                password_node.clear()
            except Exception:
                try:
                    password_node.send_keys(Keys.CONTROL, "a")
                    password_node.send_keys(Keys.BACKSPACE)
                except Exception:
                    pass
            password_node.send_keys(self.target_password)
            button = self.find_target_login_button(timeout=2)
            clicked = False
            if button is not None:
                clicked = self.safe_click(button)
            if not clicked:
                try:
                    password_node.send_keys(Keys.ENTER)
                    clicked = True
                except Exception:
                    clicked = False
            if clicked:
                time.sleep(1.0)
                if self.find_target_password_input(timeout=1) is None:
                    return
            time.sleep(0.4)

    def target_virtual_password_visible(self) -> bool:
        text = self.page_text()
        digit_count = self.visible_target_digit_count()
        if digit_count >= 6:
            return True
        if any(token in text for token in ("请输入验证码", "安全验证")) and digit_count >= 4:
            return True
        return False

    def visible_target_digit_count(self) -> int:
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass
        try:
            return int(
                self.driver.execute_script(
                    """
                    const nodes = Array.from(document.querySelectorAll('button,a,div,span,p'));
                    const digits = new Set();
                    for (const node of nodes) {
                        const text = (node.textContent || '').trim();
                        if (!/^[0-9]$/.test(text)) continue;
                        const rect = node.getBoundingClientRect();
                        const style = window.getComputedStyle(node);
                        if (rect.width < 20 || rect.height < 20) continue;
                        if (style.visibility === 'hidden' || style.display === 'none' || style.pointerEvents === 'none') continue;
                        if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) continue;
                        digits.add(text);
                    }
                    return digits.size;
                    """
                )
            )
        except Exception:
            return 0

    def find_target_digit_button(self, digit: str, timeout: int = 1) -> Optional[Any]:
        clean_digit = re.sub(r"\D+", "", safe_str(digit))[:1]
        if not clean_digit:
            return None
        xpath = (
            "//*[normalize-space()=$digit and "
            "not(self::script) and not(self::style) and "
            "not(ancestor::*[contains(@style,'display: none')])]"
        )
        deadline = time.time() + max(0.2, timeout)
        while time.time() < deadline:
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
            try:
                nodes = self.driver.execute_script(
                    """
                    const digit = arguments[0];
                    const nodes = Array.from(document.querySelectorAll('button,a,div,span,p'));
                    return nodes.filter((node) => {
                        const text = (node.textContent || '').trim();
                        if (text !== digit) return false;
                        const rect = node.getBoundingClientRect();
                        const style = window.getComputedStyle(node);
                        return rect.width >= 20 && rect.height >= 20
                            && style.visibility !== 'hidden'
                            && style.display !== 'none'
                            && rect.bottom >= 0 && rect.right >= 0
                            && rect.top <= window.innerHeight && rect.left <= window.innerWidth;
                    }).sort((a, b) => {
                        const ar = a.getBoundingClientRect();
                        const br = b.getBoundingClientRect();
                        return (br.width * br.height) - (ar.width * ar.height);
                    });
                    """,
                    clean_digit,
                )
                for node in nodes:
                    if self._visible_enabled(node):
                        return node
            except Exception:
                pass
            try:
                nodes = self.driver.find_elements(By.XPATH, f"//*[normalize-space()='{clean_digit}']")
                for node in sorted(nodes, key=lambda item: item.size.get("width", 0) * item.size.get("height", 0), reverse=True):
                    if self._visible_enabled(node):
                        return node
            except Exception:
                pass
            time.sleep(0.15)
        return None

    def submit_target_virtual_password(self) -> bool:
        digits = re.sub(r"\D+", "", self.target_password)
        if not digits:
            return False
        if not self.target_virtual_password_visible():
            return False
        clicked = 0
        for digit in digits:
            if not self.click_target_digit_button(digit, timeout=2):
                logging.warning("target virtual password digit click failed: %s", digit)
                return False
            clicked += 1
            time.sleep(0.35)
        logging.info("target virtual password submitted: %s digits", clicked)
        return clicked == len(digits)

    def click_target_digit_button(self, digit: str, timeout: int = 2) -> bool:
        clean_digit = re.sub(r"\D+", "", safe_str(digit))[:1]
        if not clean_digit:
            return False
        deadline = time.time() + max(0.2, timeout)
        xpaths = (
            f"//button[normalize-space()='{clean_digit}']",
            f"//*[@role='button' and normalize-space()='{clean_digit}']",
            f"//*[contains(concat(' ', normalize-space(@class), ' '), ' key ') and normalize-space()='{clean_digit}']",
            f"//*[self::a or self::div][normalize-space()='{clean_digit}']",
        )
        script = """
            const digit = arguments[0];
            const nodes = Array.from(document.querySelectorAll('button,a,div,span,p'));
            const candidates = [];
            for (const node of nodes) {
                const text = (node.textContent || '').trim();
                if (text !== digit) continue;
                const rect = node.getBoundingClientRect();
                const style = window.getComputedStyle(node);
                if (rect.width < 20 || rect.height < 20) continue;
                if (style.visibility === 'hidden' || style.display === 'none' || style.pointerEvents === 'none') continue;
                if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) continue;
                candidates.push({node, rect, area: rect.width * rect.height, bottom: rect.bottom});
            }
            candidates.sort((a, b) => (b.area - a.area) || (b.bottom - a.bottom));
            if (!candidates.length) return false;
            const target = candidates[0].node;
            const rect = candidates[0].rect;
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
            target.scrollIntoView({block: 'center', inline: 'center'});
            target.focus && target.focus();
            const base = {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0, buttons: 1};
            try {
                target.dispatchEvent(new PointerEvent('pointerdown', Object.assign({pointerId: 1, pointerType: 'mouse', isPrimary: true}, base)));
            } catch (err) {
            }
            target.dispatchEvent(new MouseEvent('mousedown', base));
            try {
                target.dispatchEvent(new PointerEvent('pointerup', Object.assign({pointerId: 1, pointerType: 'mouse', isPrimary: true, buttons: 0}, base)));
            } catch (err) {
            }
            target.dispatchEvent(new MouseEvent('mouseup', Object.assign({}, base, {buttons: 0})));
            target.dispatchEvent(new MouseEvent('click', Object.assign({}, base, {buttons: 0})));
            if (typeof target.click === 'function') target.click();
            return true;
        """
        while time.time() < deadline:
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
            for xpath in xpaths:
                node = self._first_visible_by_xpath(xpath, timeout=0.4)
                if node is not None and self.safe_click(node):
                    return True
            try:
                if self.driver.execute_script(script, clean_digit):
                    time.sleep(0.1)
                    return True
            except Exception:
                pass
            node = self.find_target_digit_button(clean_digit, timeout=0.4)
            if node is not None and self.safe_click(node):
                return True
            time.sleep(0.1)
        return False

    def find_target_password_input(self, timeout: int = 1) -> Optional[Any]:
        selectors = (
            "input[type='password']",
            "input[placeholder*='密码']",
            "input[placeholder*='口令']",
            "input[placeholder*='password' i]",
        )
        for selector in selectors:
            node = self._first_visible_by_css(selector, timeout=timeout)
            if node is not None:
                return node
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass
        for selector in selectors:
            node = self._first_visible_by_css(selector, timeout=timeout)
            if node is not None:
                return node
        return None

    def find_target_login_button(self, timeout: int = 1) -> Optional[Any]:
        xpaths = (
            "//button[contains(normalize-space(), '登录')]",
            "//button[contains(normalize-space(), '登入')]",
            "//button[contains(normalize-space(), '进入')]",
            "//button[contains(normalize-space(), '确定')]",
            "//button[contains(normalize-space(), '确认')]",
            "//button[contains(normalize-space(), '提交')]",
            "//*[self::button or self::a or self::div][contains(@class, 'login')]",
            "//*[self::button or self::a or self::div][contains(@class, 'submit')]",
        )
        for xpath in xpaths:
            node = self._first_visible_by_xpath(xpath, timeout=timeout)
            if node is not None:
                return node
        return None

    def dismiss_target_overlays(self) -> None:
        xpaths = (
            "//*[contains(@class,'dialog') or contains(@class,'modal') or contains(@class,'overlay') or contains(@class,'bulletin')]//*[self::button or self::a or self::div or self::span][normalize-space()='关闭']",
            "//*[contains(@class,'dialog') or contains(@class,'modal') or contains(@class,'overlay') or contains(@class,'bulletin')]//*[self::button or self::a or self::div or self::span][contains(normalize-space(),'关闭')]",
            "//*[self::button or self::a][normalize-space()='关闭']",
        )
        for _ in range(3):
            clicked = False
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
            for xpath in xpaths:
                node = self._first_visible_by_xpath(xpath, timeout=0.5)
                if node is not None and self.safe_click(node):
                    clicked = True
                    time.sleep(0.3)
                    break
            if not clicked:
                return

    def page_text(self) -> str:
        try:
            self.driver.switch_to.default_content()
            body = self.wait(EC.presence_of_element_located((By.TAG_NAME, "body")), timeout=5)
            return body.text or ""
        except Exception:
            return ""

    def _visible_enabled(self, node: Any) -> bool:
        try:
            return bool(node.is_displayed() and node.is_enabled())
        except (StaleElementReferenceException, WebDriverException):
            return False
        except Exception:
            return False

    def _first_visible_by_css(self, selector: str, timeout: int) -> Optional[Any]:
        try:
            return WebDriverWait(self.driver, timeout).until(
                lambda d: next(
                    (
                        node
                        for node in reversed(d.find_elements(By.CSS_SELECTOR, selector))
                        if self._visible_enabled(node)
                    ),
                    None,
                )
            )
        except TimeoutException:
            return None
        except Exception:
            return None

    def _first_visible_by_xpath(self, xpath: str, timeout: int) -> Optional[Any]:
        try:
            return WebDriverWait(self.driver, timeout).until(
                lambda d: next(
                    (
                        node
                        for node in reversed(d.find_elements(By.XPATH, xpath))
                        if self._visible_enabled(node)
                    ),
                    None,
                )
            )
        except TimeoutException:
            return None
        except Exception:
            return None

    def find_chat_input(self) -> Optional[Any]:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
            for selector in self.CHAT_INPUT_SELECTORS:
                node = self._first_visible_by_css(selector, timeout=1)
                if node is not None:
                    return node
            frames = []
            try:
                frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe, frame")
            except Exception:
                frames = []
            for frame in frames:
                try:
                    self.driver.switch_to.default_content()
                    WebDriverWait(self.driver, 1).until(EC.frame_to_be_available_and_switch_to_it(frame))
                    for selector in self.CHAT_INPUT_SELECTORS:
                        node = self._first_visible_by_css(selector, timeout=1)
                        if node is not None:
                            logging.info("chat input found inside frame")
                            return node
                except Exception:
                    continue
            time.sleep(0.25)
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass
        return None

    def find_send_button(self) -> Optional[Any]:
        for xpath in self.CHAT_SEND_XPATHS:
            node = self._first_visible_by_xpath(xpath, timeout=1)
            if node is not None:
                return node
        try:
            self.driver.switch_to.default_content()
        except Exception:
            pass
        for xpath in self.CHAT_SEND_XPATHS:
            node = self._first_visible_by_xpath(xpath, timeout=1)
            if node is not None:
                return node
        return None

    def find_send_button_by_viewport(self) -> Optional[Any]:
        script = """
            const nodes = Array.from(document.querySelectorAll('button,a,div,span,img,svg'));
            const candidates = [];
            for (const node of nodes) {
                const text = (node.innerText || node.textContent || '').trim();
                const alt = (node.getAttribute('alt') || '').toLowerCase();
                const src = (node.getAttribute('src') || '').toLowerCase();
                const className = String(node.className || '').toLowerCase();
                const isSend = text === '发送' || text === 'SEND' || text.toLowerCase() === 'send'
                    || alt.includes('send') || src.includes('send') || className.includes('send');
                if (!isSend) continue;
                const style = window.getComputedStyle(node);
                if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') continue;
                const rect = node.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) continue;
                if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) continue;
                const clickable = node.closest('button,a,[role="button"],.btn,.button,.send,.cursor-pointer') || node;
                const clickableRect = clickable.getBoundingClientRect();
                candidates.push({
                    node: clickable,
                    bottom: clickableRect.bottom,
                    right: clickableRect.right,
                    area: clickableRect.width * clickableRect.height
                });
            }
            candidates.sort((a, b) => (b.bottom - a.bottom) || (b.right - a.right) || (b.area - a.area));
            return candidates.length ? candidates[0].node : null;
        """
        contexts = ["current", "default"]
        for context in contexts:
            try:
                if context == "default":
                    self.driver.switch_to.default_content()
                node = self.driver.execute_script(script)
                if node is not None and self._visible_enabled(node):
                    return node
            except Exception:
                continue
        return None

    def send_button_center(self) -> Optional[Dict[str, Any]]:
        script = """
            const nodes = Array.from(document.querySelectorAll('button,a,div,span,img,svg'));
            const candidates = [];
            for (const node of nodes) {
                const text = (node.innerText || node.textContent || '').trim();
                const alt = (node.getAttribute('alt') || '').toLowerCase();
                const src = (node.getAttribute('src') || '').toLowerCase();
                const className = String(node.className || '').toLowerCase();
                const isSend = text === '发送' || text === 'SEND' || text.toLowerCase() === 'send'
                    || alt.includes('send') || src.includes('send') || className.includes('send');
                if (!isSend) continue;
                const clickable = node.closest('button,a,[role="button"],.btn,.button,.send,.cursor-pointer') || node;
                const style = window.getComputedStyle(clickable);
                if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') continue;
                const rect = clickable.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) continue;
                if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) continue;
                candidates.push({
                    node: clickable,
                    rect,
                    bottom: rect.bottom,
                    right: rect.right,
                    area: rect.width * rect.height,
                    text
                });
            }
            candidates.sort((a, b) => (b.bottom - a.bottom) || (b.right - a.right) || (b.area - a.area));
            if (!candidates.length) return null;
            const best = candidates[0];
            let x = best.rect.left + best.rect.width / 2;
            let y = best.rect.top + best.rect.height / 2;
            try {
                let win = window;
                while (win && win.frameElement) {
                    const frameRect = win.frameElement.getBoundingClientRect();
                    x += frameRect.left;
                    y += frameRect.top;
                    win = win.parent;
                }
            } catch (err) {
            }
            return {
                x,
                y,
                text: best.text,
                tag: best.node.tagName,
                className: best.node.className || '',
                width: best.rect.width,
                height: best.rect.height
            };
        """
        for context in ("current", "default"):
            try:
                if context == "default":
                    self.driver.switch_to.default_content()
                info = self.driver.execute_script(script)
                if isinstance(info, dict) and parse_float(info.get("x")) is not None and parse_float(info.get("y")) is not None:
                    return info
            except Exception:
                continue
        return None

    def dispatch_send_button_dom_events(self) -> bool:
        script = """
            const nodes = Array.from(document.querySelectorAll('button,a,div,span,img,svg'));
            const candidates = [];
            for (const node of nodes) {
                const text = (node.innerText || node.textContent || '').trim();
                const alt = (node.getAttribute('alt') || '').toLowerCase();
                const src = (node.getAttribute('src') || '').toLowerCase();
                const className = String(node.className || '').toLowerCase();
                const isSend = text === '发送' || text === 'SEND' || text.toLowerCase() === 'send'
                    || alt.includes('send') || src.includes('send') || className.includes('send');
                if (!isSend) continue;
                const clickable = node.closest('button,a,[role="button"],.btn,.button,.send,.cursor-pointer') || node;
                const style = window.getComputedStyle(clickable);
                if (style.display === 'none' || style.visibility === 'hidden' || style.pointerEvents === 'none') continue;
                const rect = clickable.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) continue;
                if (rect.bottom < 0 || rect.right < 0 || rect.top > window.innerHeight || rect.left > window.innerWidth) continue;
                candidates.push({node: clickable, rect, bottom: rect.bottom, right: rect.right, area: rect.width * rect.height});
            }
            candidates.sort((a, b) => (b.bottom - a.bottom) || (b.right - a.right) || (b.area - a.area));
            if (!candidates.length) return false;
            const target = candidates[0].node;
            const rect = candidates[0].rect;
            const x = rect.left + rect.width / 2;
            const y = rect.top + rect.height / 2;
            target.scrollIntoView({block: 'center', inline: 'center'});
            target.focus && target.focus();
            const base = {bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0, buttons: 1};
            try {
                target.dispatchEvent(new PointerEvent('pointerdown', Object.assign({pointerId: 1, pointerType: 'mouse', isPrimary: true}, base)));
            } catch (err) {
            }
            target.dispatchEvent(new MouseEvent('mousedown', base));
            try {
                target.dispatchEvent(new PointerEvent('pointerup', Object.assign({pointerId: 1, pointerType: 'mouse', isPrimary: true, buttons: 0}, base)));
            } catch (err) {
            }
            target.dispatchEvent(new MouseEvent('mouseup', Object.assign({}, base, {buttons: 0})));
            target.dispatchEvent(new MouseEvent('click', Object.assign({}, base, {buttons: 0})));
            if (typeof target.click === 'function') target.click();
            return true;
        """
        for context in ("current", "default"):
            try:
                if context == "default":
                    self.driver.switch_to.default_content()
                if self.driver.execute_script(script):
                    return True
            except Exception:
                continue
        return False

    def cdp_click_send_button_center(self) -> bool:
        info = self.send_button_center()
        if not info:
            return False
        try:
            x = float(info["x"])
            y = float(info["y"])
            self.driver.execute_cdp_cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
            self.driver.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                {"type": "mousePressed", "x": x, "y": y, "button": "left", "buttons": 1, "clickCount": 1},
            )
            time.sleep(0.08)
            self.driver.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                {"type": "mouseReleased", "x": x, "y": y, "button": "left", "buttons": 0, "clickCount": 1},
            )
            return True
        except Exception as exc:
            logging.warning("cdp send button click failed: %s", exc)
            return False

    def safe_click(self, node: Any) -> bool:
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", node)
        except Exception:
            pass
        try:
            ActionChains(self.driver).move_to_element(node).pause(0.05).click(node).perform()
            return True
        except ElementClickInterceptedException:
            try:
                self.driver.execute_script("arguments[0].click();", node)
                return True
            except Exception as exc:
                logging.warning("javascript click failed: %s", exc)
                return False
        except Exception:
            try:
                node.click()
                return True
            except Exception as exc:
                logging.warning("click failed: %s", exc)
                return False

    def set_chat_input(self, node: Any, message: str) -> bool:
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center', inline:'center'});", node)
        except Exception:
            pass
        try:
            node.click()
        except Exception:
            try:
                self.driver.execute_script("arguments[0].focus();", node)
            except Exception:
                pass
        tag = ""
        contenteditable = False
        try:
            tag = safe_str(node.tag_name).lower()
            contenteditable = safe_str(node.get_attribute("contenteditable")).lower() == "true"
        except Exception:
            pass

        try:
            if tag in {"textarea", "input"}:
                try:
                    node.clear()
                except Exception:
                    node.send_keys(Keys.CONTROL, "a")
                    node.send_keys(Keys.BACKSPACE)
                node.send_keys(message)
            elif contenteditable:
                try:
                    node.send_keys(Keys.CONTROL, "a")
                    node.send_keys(Keys.BACKSPACE)
                    node.send_keys(message)
                    if self.input_still_has_message(node, message):
                        return True
                except Exception as exc:
                    logging.warning("contenteditable keyboard fill failed: %s", exc)
                script = """
                    const el = arguments[0];
                    const value = arguments[1];
                    el.focus();
                    if ('innerText' in el) el.innerText = value;
                    else el.textContent = value;
                    el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                """
                self.driver.execute_script(script, node, message)
            else:
                node.send_keys(Keys.CONTROL, "a")
                node.send_keys(Keys.BACKSPACE)
                node.send_keys(message)
            return True
        except Exception as exc:
            logging.warning("primary input fill failed: %s", exc)
            try:
                node.send_keys(message)
                return True
            except Exception as fallback_exc:
                logging.warning("fallback input fill failed: %s", fallback_exc)
                return False

    def input_text(self, node: Any) -> str:
        try:
            tag = safe_str(node.tag_name).lower()
            if tag in {"textarea", "input"}:
                return safe_str(node.get_attribute("value"))
            contenteditable = safe_str(node.get_attribute("contenteditable")).lower() == "true"
            if contenteditable:
                return safe_str(self.driver.execute_script("return arguments[0].innerText || arguments[0].textContent || '';", node))
            return safe_str(node.text)
        except (StaleElementReferenceException, WebDriverException):
            return ""
        except Exception:
            return ""

    def input_still_has_message(self, node: Any, message: str) -> bool:
        text = re.sub(r"\s+", "", self.input_text(node))
        expected = re.sub(r"\s+", "", safe_str(message))
        return bool(expected and expected in text)

    def wait_input_released(self, node: Any, message: str, timeout: float = 1.5) -> bool:
        deadline = time.time() + max(0.2, timeout)
        while time.time() < deadline:
            if not self.input_still_has_message(node, message):
                return True
            time.sleep(0.12)
        return not self.input_still_has_message(node, message)

    def normalized_message_text(self, value: str) -> str:
        return re.sub(r"\s+", "", safe_str(value))

    def page_delta_has_message(self, before_text: str, message: str) -> bool:
        expected = self.normalized_message_text(message)
        if not expected:
            return False
        current = self.page_text()
        delta = current[len(before_text) :] if before_text and len(current) >= len(before_text) else current
        compact_delta = self.normalized_message_text(delta)
        if expected in compact_delta:
            return True
        return len(expected) >= 24 and expected[:24] in compact_delta and expected[-12:] in compact_delta

    def wait_send_confirmed(self, node: Any, message: str, before_text: str, timeout: float = 8.0) -> bool:
        deadline = time.time() + max(1.0, timeout)
        while time.time() < deadline:
            if self.page_delta_has_message(before_text, message):
                return True
            if not self.input_still_has_message(node, message):
                return True
            time.sleep(0.2)
        return self.page_delta_has_message(before_text, message) or not self.input_still_has_message(node, message)

    def submit_filled_message(self, node: Any, message: str, before_text: str) -> bool:
        def click_once() -> bool:
            if self.cdp_click_send_button_center():
                return True
            button = self.find_send_button()
            if button is None:
                button = self.find_send_button_by_viewport()
            if button is not None and self.safe_click(button):
                return True
            if self.dispatch_send_button_dom_events():
                return True
            return False

        try:
            WebDriverWait(self.driver, 3).until(lambda _d: self.input_still_has_message(node, message))
        except TimeoutException:
            pass
        except Exception:
            pass

        current_node = node
        for attempt in range(1, 4):
            if click_once() and self.wait_send_confirmed(current_node, message, before_text, timeout=4.0):
                return True
            current_node = self.find_chat_input() or current_node
            if self.page_delta_has_message(before_text, message):
                return True
            if not self.input_still_has_message(current_node, message):
                if self.page_delta_has_message(before_text, message):
                    return True
                if not self.set_chat_input(current_node, message):
                    continue
            try:
                current_node.send_keys(Keys.ENTER)
                if self.wait_send_confirmed(current_node, message, before_text, timeout=3.0):
                    return True
            except Exception as exc:
                logging.warning("enter send attempt %s failed: %s", attempt, exc)
            try:
                current_node = self.find_chat_input() or current_node
                current_node.send_keys(Keys.CONTROL, Keys.ENTER)
                if self.wait_send_confirmed(current_node, message, before_text, timeout=3.0):
                    return True
            except Exception as exc:
                logging.warning("ctrl-enter send attempt %s failed: %s", attempt, exc)
        return False

    def result_delta_text(self, page_text: str, before_text: str, message: str) -> str:
        message = safe_str(message)
        if message:
            index = page_text.rfind(message)
            if index >= 0:
                return page_text[index + len(message) :]
            compact_page = re.sub(r"\s+", "", page_text)
            compact_message = re.sub(r"\s+", "", message)
            compact_index = compact_page.rfind(compact_message)
            if compact_index >= 0:
                return compact_page[compact_index + len(compact_message) :]
        if before_text and page_text.startswith(before_text):
            return page_text[len(before_text) :]
        if before_text:
            index = page_text.rfind(before_text)
            if index >= 0:
                return page_text[index + len(before_text) :]
        return page_text[-3000:]

    def wait_result(self, before_text: str, timeout: int, message: str = "", expected_period: str = "") -> str:
        if timeout <= 0:
            return "SENT"
        deadline = time.time() + timeout
        last_text = ""
        accepted = False
        payout_mode = self.result_mode == "payout"
        active_period = safe_str(expected_period)
        ignored_payout_fail_keys = ("[总出]", "【总出】", "总出")
        while time.time() < deadline:
            text = self.page_text()
            last_text = text
            delta = self.result_delta_text(text, before_text, message)
            delta_lower = delta.lower()
            order_period = self.order_period_from_delta(delta)
            if order_period:
                active_period = order_period
                accepted = True
            payout_status = self.payout_status_from_delta(delta, active_period, strict_period=payout_mode)
            if payout_status is not None:
                return payout_status
            if not payout_mode and any(key.lower() in delta_lower for key in self.HIT_KEYS):
                return "WIN"
            fail_keys = () if payout_mode else self.FAIL_KEYS
            if any(key.lower() in delta_lower for key in fail_keys):
                return "LOSE"
            if any(key.lower() in delta_lower for key in self.ORDER_ACCEPT_KEYS):
                accepted = True
            time.sleep(0.5)
        if accepted:
            return "ACCEPTED"
        tail = last_text[-240:].replace("\n", " | ")
        return "UNKNOWN:" + tail

    def send_message(self, message: str, result_timeout: int, expected_period: str = "") -> SendResult:
        self.open()
        self.dismiss_target_overlays()
        before = self.page_text()
        node = self.find_chat_input()
        if node is None:
            return SendResult(False, "INPUT_NOT_FOUND", "chat input not found")
        if not self.set_chat_input(node, message):
            return SendResult(False, "INPUT_FAILED", "failed to fill chat input")
        if not self.submit_filled_message(node, message, before):
            return SendResult(False, "SEND_NOT_TRIGGERED", "single send click did not produce a visible sent message")
        status = self.wait_result(before, result_timeout, message, expected_period)
        return SendResult(True, status)

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass
        for temp_dir in self._temp_profile_dirs:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
        self._temp_profile_dirs = []


def read_memory(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def martingale_enabled(args: argparse.Namespace) -> bool:
    return float(args.base_stake) > 0 and not safe_str(args.stake)


def clamp_level(level: Any, max_level: int) -> int:
    try:
        value = int(level)
    except Exception:
        value = 1
    return max(1, min(max(1, max_level), value))


def current_martingale_snapshot(args: argparse.Namespace, memory: Dict[str, Any]) -> MartingaleSnapshot:
    enabled = martingale_enabled(args)
    max_level = max(1, int(args.max_level))
    level = clamp_level(memory.get("martingale_level", 1), max_level)
    if not enabled:
        return MartingaleSnapshot(False, level, safe_str(args.stake), float(args.base_stake), float(args.multiplier), max_level)
    stake_value = float(args.base_stake) * (float(args.multiplier) ** (level - 1))
    return MartingaleSnapshot(True, level, format_amount(stake_value), float(args.base_stake), float(args.multiplier), max_level)


def outcome_from_status(status: str, unknown_action: str) -> str:
    text = safe_str(status).upper()
    if text.startswith("WIN"):
        return "win"
    if text.startswith("LOSE"):
        return "lose"
    if unknown_action in {"win", "lose", "reset", "keep"}:
        return unknown_action
    return "lose"


def update_martingale_after_send(
    args: argparse.Namespace,
    memory: Dict[str, Any],
    snapshot: MartingaleSnapshot,
    send_status: str,
) -> Dict[str, Any]:
    if not snapshot.enabled:
        return memory
    if send_status == "DRY_RUN" and not args.advance_dry_run:
        memory["martingale_level"] = snapshot.level
        memory["martingale_last_outcome"] = "dry-run"
        return memory
    if safe_str(getattr(args, "result_mode", "")).lower() == "payout":
        normalized_status = safe_str(send_status).upper()
        if not normalized_status.startswith(("WIN", "LOSE")):
            memory["martingale_level"] = snapshot.level
            memory["martingale_previous_level"] = snapshot.level
            memory["martingale_last_stake"] = snapshot.stake
            memory["martingale_last_outcome"] = "pending-payout"
            memory["martingale_max_level"] = snapshot.max_level
            memory["martingale_multiplier"] = snapshot.multiplier
            memory["martingale_base_stake"] = snapshot.base_stake
            return memory

    outcome = outcome_from_status(send_status, args.unknown_action)
    level = snapshot.level
    if outcome in {"win", "reset"}:
        next_level = 1
    elif outcome == "keep":
        next_level = level
    else:
        next_level = 1 if level >= snapshot.max_level else level + 1

    memory["martingale_level"] = next_level
    memory["martingale_previous_level"] = level
    memory["martingale_last_stake"] = snapshot.stake
    memory["martingale_last_outcome"] = outcome
    memory["martingale_max_level"] = snapshot.max_level
    memory["martingale_multiplier"] = snapshot.multiplier
    memory["martingale_base_stake"] = snapshot.base_stake
    return memory


def next_schedule_time(now: datetime, offset_minute: int, step_minutes: int) -> datetime:
    step = max(1, int(step_minutes))
    offset = int(offset_minute) % step
    cursor = now.replace(second=0, microsecond=0)
    if now.second > 0 or now.microsecond > 0:
        cursor += timedelta(minutes=1)
    for add_minutes in range(0, 24 * 60 + step + 1):
        candidate = cursor + timedelta(minutes=add_minutes)
        if candidate.minute % step == offset:
            return candidate
    return cursor + timedelta(minutes=step)


def parse_schedule_random_delay(value: Any) -> Tuple[int, int]:
    text = safe_str(value)
    if not text:
        return 0, 0
    numbers = [int(item) for item in re.findall(r"\d+", text)]
    if not numbers:
        return 0, 0
    low = numbers[0]
    high = numbers[1] if len(numbers) > 1 else numbers[0]
    if low > high:
        low, high = high, low
    return max(0, low), max(0, high)


def next_schedule_time_with_random_delay(
    now: datetime,
    offset_minute: int,
    step_minutes: int,
    random_delay: Any = DEFAULT_SCHEDULE_RANDOM_DELAY,
) -> Tuple[datetime, datetime, int]:
    step = max(1, int(step_minutes))
    base = next_schedule_time(now, offset_minute, step)
    low, high = parse_schedule_random_delay(random_delay)
    max_safe_delay = max(0, step * 60 - 15)
    high = min(high, max_safe_delay)
    low = min(low, high)
    delay = random.randint(low, high) if high > 0 else 0
    return base + timedelta(seconds=delay), base, delay


def parse_clock_minutes(value: Any) -> Optional[int]:
    text = safe_str(value)
    if not text:
        return None
    normalized = text.replace("：", ":")
    match = re.fullmatch(r"(\d{1,2})(?::?(\d{2}))?", normalized)
    if not match:
        raise ValueError(f"invalid time value: {text}")
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError(f"invalid time value: {text}")
    return hour * 60 + minute


def format_clock_minutes(value: Optional[int]) -> str:
    if value is None:
        return "--:--"
    minutes = int(value) % (24 * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def active_time_window_allows(args: argparse.Namespace, now: Optional[datetime] = None) -> Tuple[bool, str]:
    start = parse_clock_minutes(getattr(args, "active_start_time", ""))
    stop = parse_clock_minutes(getattr(args, "active_stop_time", ""))
    if start is None and stop is None:
        return True, "time window disabled"
    current = now or datetime.now()
    minute = current.hour * 60 + current.minute
    if start is not None and stop is not None:
        if start == stop:
            return True, f"time window 24h {format_clock_minutes(start)}-{format_clock_minutes(stop)}"
        if start < stop:
            allowed = start <= minute < stop
        else:
            allowed = minute >= start or minute < stop
        return allowed, (
            f"now={current.strftime('%H:%M')} window={format_clock_minutes(start)}-{format_clock_minutes(stop)}"
        )
    if start is not None:
        return minute >= start, f"now={current.strftime('%H:%M')} start>={format_clock_minutes(start)}"
    return minute < int(stop), f"now={current.strftime('%H:%M')} stop<{format_clock_minutes(stop)}"


def parse_non_negative_int(value: Any, default: int = 0) -> int:
    try:
        number = int(float(safe_str(value, str(default))))
    except Exception:
        return max(0, int(default))
    return max(0, number)


def daily_streak_stats(data: Dict[str, Any], ledger_name: str) -> Tuple[str, Dict[str, Any]]:
    day = state_local_day(data)
    ledger = as_dict(data.get("dailyStreakLedger"))
    group = as_dict(ledger.get(safe_str(ledger_name, "autoTop6")))
    stats = as_dict(group.get(day))
    if stats:
        return day, stats
    latest_day = ""
    latest_stats: Dict[str, Any] = {}
    for key, value in group.items():
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", safe_str(key)) and safe_str(key) > latest_day:
            latest_day = safe_str(key)
            latest_stats = as_dict(value)
    return day, latest_stats if latest_day == day else {}


def send_gate_decision(data: Dict[str, Any], args: argparse.Namespace) -> SendGateDecision:
    storm = as_dict(data.get("stormMode"))
    if storm.get("active") or storm.get("robotsPaused"):
        return SendGateDecision(
            False,
            "storm mode active: " + safe_str(storm.get("reason"), "robots paused"),
            day=state_local_day(data),
            current_miss=parse_non_negative_int(storm.get("autoTop6CurrentMiss"), 0),
            threshold=0,
            ledger_name="stormMode",
        )
    tip_ready = data.get("tipReady")
    if tip_ready is None:
        tip_ready = as_dict(data.get("modelAudit")).get("tipReady")
    if tip_ready is None:
        tip_ready = as_dict(data.get("currentPrediction")).get("tipReady")
    if tip_ready is False:
        return SendGateDecision(
            False,
            "tip structure not perfect: tipReady=false issues="
            + safe_str(data.get("tipReadyIssueCount"), "unknown"),
            day=state_local_day(data),
            current_miss=0,
            threshold=0,
            ledger_name="tipReady",
        )
    try:
        in_window, window_detail = active_time_window_allows(args)
    except ValueError as exc:
        return SendGateDecision(False, str(exc))
    if not in_window:
        return SendGateDecision(False, "outside active time: " + window_detail)

    threshold = parse_non_negative_int(getattr(args, "min_miss_streak", 0), 0)
    ledger_name = safe_str(getattr(args, "trigger_ledger", "autoTop6"), "autoTop6")
    if threshold <= 0:
        return SendGateDecision(True, "miss streak gate disabled", threshold=0, ledger_name=ledger_name)

    day, stats = daily_streak_stats(data, ledger_name)
    if not stats:
        return SendGateDecision(
            False,
            f"waiting for {ledger_name} daily ledger on {day}",
            day=day,
            current_miss=0,
            threshold=threshold,
            ledger_name=ledger_name,
        )
    current_miss = parse_non_negative_int(
        first_value(stats.get("currentMiss"), stats.get("current_miss"), stats.get("missStreak"), default="0"),
        0,
    )
    if "currentMiss" not in stats and "current_miss" not in stats and "missStreak" not in stats:
        logging.warning(
            "send_gate: 账本 %s 当日 %s 缺少 currentMiss/current_miss/missStreak 字段，按 0 处理（可能误放行/误等待）",
            ledger_name,
            day,
        )
    if current_miss < threshold:
        return SendGateDecision(
            False,
            f"{ledger_name} currentMiss={current_miss} < trigger={threshold}",
            day=day,
            current_miss=current_miss,
            threshold=threshold,
            ledger_name=ledger_name,
        )
    return SendGateDecision(
        True,
        f"{ledger_name} currentMiss={current_miss} >= trigger={threshold}",
        day=day,
        current_miss=current_miss,
        threshold=threshold,
        ledger_name=ledger_name,
    )


def wait_until_schedule(args: argparse.Namespace) -> None:
    target, base, delay = next_schedule_time_with_random_delay(
        datetime.now(),
        args.shoot_offset_minute,
        args.shoot_step_minutes,
        getattr(args, "schedule_random_delay", DEFAULT_SCHEDULE_RANDOM_DELAY),
    )
    while True:
        now = datetime.now()
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        if remaining > 20:
            logging.info(
                "next scheduled shot: %s base=%s random_delay=%ss",
                target.strftime("%H:%M:%S"),
                base.strftime("%H:%M:%S"),
                delay,
            )
        time.sleep(min(remaining, 20.0))


def append_history(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True) if path.parent != Path("") else None
    exists = path.exists()
    fieldnames = [
        "ts",
        "mode",
        "status",
        "period",
        "prediction_id",
        "data_key",
        "selection",
        "message",
        "items",
        "detail",
    ]
    with path.open("a", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow({key: row.get(key, "") for key in fieldnames})


def print_build(result: ShotBuildResult) -> None:
    print("period:", result.period)
    print("target_period:", result.target_period)
    print("prediction_id:", result.prediction_id)
    print("selection:", result.selection)
    print("items:")
    for index, item in enumerate(result.selected_items, 1):
        rate = "--" if item.stable_score is None else f"{item.stable_score:.2f}%"
        print(f"  {index}. {item.name} {item.code} {rate}")
    print("message:")
    print(result.message)


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return number


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Penta5 data-driven auto shooter")
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE_FILE, help="local Penta5 state JSON path")
    parser.add_argument("--state-url", default="", help="remote Penta5 /state URL; overrides --state-file")
    parser.add_argument("--state-user", default="", help="optional username for protected state URL")
    parser.add_argument("--state-password", default="", help="optional password for protected state URL")
    parser.add_argument("--sync-before-read", action="store_true", help="POST /sync before reading remote state")
    parser.add_argument("--url", default="", help="target chat URL for Selenium live mode")
    parser.add_argument("--live", action="store_true", help="really send with Selenium; default is dry-run preview")
    parser.add_argument("--dry-run", action="store_true", help="explicit dry-run flag; kept for clarity")
    parser.add_argument("--watch", action="store_true", help="keep watching state and send when a new prediction appears")
    parser.add_argument("--schedule", action="store_true", help="send only at scheduled minutes, default 03/08/13/18...")
    parser.add_argument("--shoot-offset-minute", type=int, default=3, help="minute offset for schedule; 3 means 03/08/13/18...")
    parser.add_argument("--shoot-step-minutes", type=int, default=5, help="minute step for schedule")
    parser.add_argument("--schedule-random-delay", default=DEFAULT_SCHEDULE_RANDOM_DELAY, help="random seconds after scheduled base time, e.g. 8-75; 0 disables")
    parser.add_argument("--active-start-time", default="", help="daily active window start time, for example 09:00; empty disables")
    parser.add_argument("--active-stop-time", default="", help="daily active window stop time, for example 23:30; empty disables")
    parser.add_argument("--min-miss-streak", type=int, default=0, help="only send when today's trigger ledger currentMiss reaches this value; 0 disables")
    parser.add_argument("--trigger-ledger", default="autoTop6", help="dailyStreakLedger key used by --min-miss-streak")
    parser.add_argument("--rounds", type=positive_int, default=1, help="number of new predictions to process")
    parser.add_argument("--check-interval", type=float, default=5.0, help="watch polling interval in seconds")
    parser.add_argument("--timeout", type=int, default=12, help="state/browser explicit wait timeout")
    parser.add_argument("--result-timeout", type=int, default=260, help="seconds to wait for hit/miss text after accepted order")
    parser.add_argument("--headless", action="store_true", help="run Chrome in headless mode")
    parser.add_argument("--fresh-browser-per-round", action="store_true", help="start Chrome only for one send round, then close it")
    parser.add_argument("--ephemeral-browser-profile", action="store_true", help="use a new temporary Chrome profile for each Selenium browser")
    parser.add_argument("--driver-path", default="", help="optional chromedriver path")
    parser.add_argument("--browser-profile-dir", type=Path, default=DEFAULT_BROWSER_PROFILE_DIR, help="persistent Chrome profile directory")
    parser.add_argument("--adspower-api-url", default="", help="AdsPower Local API URL, for example http://local.adspower.net:50325")
    parser.add_argument("--adspower-user-id", default="", help="AdsPower browser profile user_id")
    parser.add_argument("--adspower-api-key", default="", help="optional AdsPower Local API key")
    parser.add_argument("--target-password", default="", help="optional target page login password")
    parser.add_argument("--result-mode", choices=["normal", "payout"], default="normal", help="normal uses text hit keys; payout requires payout amount")
    parser.add_argument("--selection", choices=SELECTION_CHOICES, default="top5")
    parser.add_argument("--base-text", default=DEFAULT_BASE_TEXT, help="base practice text before data-driven removals")
    parser.add_argument("--fixed-shot", default="", help="fixed practice text used when --selection fixed")
    parser.add_argument("--shot-language-rules", default="", help="line based replacement rules: old=new or old=>new")
    parser.add_argument("--shot-language-enabled", action="store_true", help="apply --shot-language-rules before sending")
    parser.add_argument("--stake", default="", help="fixed amount appended after the trailing 各; overrides martingale")
    parser.add_argument("--base-stake", type=float, default=1.0, help="martingale base amount; set 0 to disable")
    parser.add_argument("--multiplier", type=float, default=2.0, help="martingale multiplier")
    parser.add_argument("--max-level", type=positive_int, default=4, help="reset after this martingale level")
    parser.add_argument("--unknown-action", choices=["lose", "win", "reset", "keep"], default="lose", help="how to update martingale when result is unknown")
    parser.add_argument("--advance-dry-run", action="store_true", help="advance martingale even in dry-run")
    parser.add_argument("--reset-martingale", action="store_true", help="reset martingale level to 1 before running")
    parser.add_argument("--message-template", default="", help="format template using {shot}, {stake}, {period}, {prediction_id}")
    parser.add_argument("--memory-file", type=Path, default=DEFAULT_MEMORY_FILE, help="duplicate-prevention state file")
    parser.add_argument("--history-csv", type=Path, default=DEFAULT_HISTORY_CSV, help="send history CSV")
    parser.add_argument("--resend", action="store_true", help="allow sending the same data key again")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args(argv)


def run_once(
    reader: StateReader,
    args: argparse.Namespace,
    shooter: Optional[SeleniumShooter],
    memory: Dict[str, Any],
) -> Tuple[bool, Optional[ShotBuildResult]]:
    data = reader.read()
    snapshot = current_martingale_snapshot(args, memory)
    result = build_shot(
        data,
        args.selection,
        args.base_text,
        args.message_template,
        snapshot.stake,
        args.fixed_shot,
        getattr(args, "shot_language_rules", ""),
        bool(getattr(args, "shot_language_enabled", False)),
    )
    last_key = safe_str(memory.get("last_sent_key"))
    if not args.resend and last_key == result.data_key:
        logging.info("skip duplicate prediction: %s", result.data_key)
        return False, result

    gate = send_gate_decision(data, args)
    if not gate.allowed:
        logging.info("skip send gate: %s", gate.reason)
        return False, result
    if gate.threshold > 0:
        logging.info("send gate passed: %s", gate.reason)

    if not args.live:
        print_build(result)
        send_result = SendResult(True, "DRY_RUN")
        mode = "dry-run"
    else:
        owned_shooter: Optional[SeleniumShooter] = None
        if shooter is None:
            owned_shooter = SeleniumShooter(
                url=args.url,
                headless=args.headless,
                timeout=args.timeout,
                driver_path=args.driver_path,
                profile_dir=args.browser_profile_dir,
                adspower_api_url=args.adspower_api_url,
                adspower_user_id=args.adspower_user_id,
                adspower_api_key=args.adspower_api_key,
                target_password=args.target_password,
                result_mode=args.result_mode,
                ephemeral_profile=args.ephemeral_browser_profile,
            )
            shooter = owned_shooter
        try:
            logging.info("sending period %s target=%s message length=%s", result.period, result.target_period, len(result.message))
            send_result = shooter.send_message(result.message, args.result_timeout, result.target_period)
            mode = "live"
            if not send_result.sent:
                raise RuntimeError(f"send failed: {send_result.status} {send_result.detail}")
        finally:
            if owned_shooter is not None:
                owned_shooter.close()

    update_martingale_after_send(args, memory, snapshot, send_result.status)
    memory.update(
        {
            "last_sent_key": result.data_key,
            "last_period": result.period,
            "last_prediction_id": result.prediction_id,
            "last_message": result.message,
            "last_status": send_result.status,
            "last_martingale_level": snapshot.level,
            "last_martingale_stake": snapshot.stake,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
    )
    atomic_write_json(args.memory_file, memory)
    append_history(
        args.history_csv,
        {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode,
            "status": send_result.status,
            "period": result.period,
            "prediction_id": result.prediction_id,
            "data_key": result.data_key,
            "selection": result.selection,
            "message": result.message,
            "items": json.dumps([asdict(item) for item in result.selected_items], ensure_ascii=False),
            "detail": json.dumps(
                {
                    "send_detail": send_result.detail,
                    "target_period": result.target_period,
                    "martingale": asdict(snapshot),
                    "next_level": memory.get("martingale_level"),
                    "last_outcome": memory.get("martingale_last_outcome"),
                },
                ensure_ascii=False,
            ),
        },
    )
    logging.info(
        "%s complete: status=%s period=%s stake=%s level=%s next_level=%s",
        mode,
        send_result.status,
        result.period,
        snapshot.stake,
        snapshot.level,
        memory.get("martingale_level"),
    )
    return True, result


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    reader = StateReader(args.state_file, args.state_url, args.timeout, args.state_user, args.state_password, args.sync_before_read)
    memory = read_memory(args.memory_file)
    if args.reset_martingale:
        memory["martingale_level"] = 1
        memory["martingale_last_outcome"] = "manual-reset"
        atomic_write_json(args.memory_file, memory)
    shooter: Optional[SeleniumShooter] = None

    if args.live and not args.fresh_browser_per_round:
        shooter = SeleniumShooter(
            url=args.url,
            headless=args.headless,
            timeout=args.timeout,
            driver_path=args.driver_path,
            profile_dir=args.browser_profile_dir,
            adspower_api_url=args.adspower_api_url,
            adspower_user_id=args.adspower_user_id,
            adspower_api_key=args.adspower_api_key,
            target_password=args.target_password,
            result_mode=args.result_mode,
            ephemeral_profile=args.ephemeral_browser_profile,
        )
    else:
        if args.live:
            logging.info("live fresh-browser mode; browser will open only for each send round")
        else:
            logging.info("dry-run mode; add --live to send with Selenium")

    processed = 0
    try:
        while processed < args.rounds:
            try:
                if args.schedule:
                    wait_until_schedule(args)
                did_process, _ = run_once(reader, args, shooter, memory)
                if did_process:
                    processed += 1
                if not args.watch:
                    break
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                logging.error("round failed: %s", exc)
                if not args.watch:
                    return 1
            if args.watch and processed < args.rounds:
                time.sleep(max(0.5, float(args.check_interval)))
        return 0
    finally:
        if shooter is not None:
            shooter.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logging.warning("interrupted")
        raise SystemExit(130)
