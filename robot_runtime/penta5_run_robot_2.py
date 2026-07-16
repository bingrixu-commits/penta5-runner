#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import penta5_auto_shooter as core


ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / "penta5_auto_shooter_settings_2.json"


def read_json(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
    except Exception:
        return {}
    return {}


def read_secret(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""


def is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def add_flag(argv: List[str], flag: str, enabled: Any) -> None:
    if is_true(enabled):
        argv.append(flag)


def add_value(argv: List[str], flag: str, value: Any, default: str = "") -> None:
    text = str(value if value is not None else default).strip()
    if text:
        argv.extend([flag, text])


def build_argv() -> List[str]:
    cfg = read_json(SETTINGS_FILE)
    shared = read_json(ROOT / core.DEFAULT_SHARED_DATA_SOURCE_FILE)
    state_password = read_secret(ROOT / core.DEFAULT_STATE_PASSWORD_FILE)
    adspower_key = read_secret(ROOT / core.DEFAULT_ADSPOWER_KEY_FILE)

    state_url = shared.get("state_url") or cfg.get("state_url") or core.DEFAULT_STATE_URL
    state_user = shared.get("state_user") or cfg.get("state_user") or core.DEFAULT_STATE_USER

    argv: List[str] = []
    add_value(argv, "--state-file", cfg.get("state_file"), str(core.DEFAULT_STATE_FILE))
    add_value(argv, "--state-url", state_url)
    add_value(argv, "--state-user", state_user)
    add_value(argv, "--state-password", state_password)
    add_flag(argv, "--sync-before-read", cfg.get("sync_before_read", True))

    add_value(argv, "--url", cfg.get("url"))
    argv.append("--live")
    add_flag(argv, "--watch", cfg.get("watch", True))
    add_flag(argv, "--schedule", cfg.get("schedule", True))
    add_value(argv, "--rounds", cfg.get("rounds"), "288")
    add_value(argv, "--check-interval", cfg.get("check_interval"), "5")
    add_value(argv, "--timeout", cfg.get("timeout"), "12")
    add_value(argv, "--result-timeout", cfg.get("result_timeout"), "260")
    add_value(argv, "--shoot-offset-minute", cfg.get("offset"), "3")
    add_value(argv, "--shoot-step-minutes", cfg.get("step"), "5")
    add_value(argv, "--schedule-random-delay", cfg.get("random_delay"), core.DEFAULT_SCHEDULE_RANDOM_DELAY)
    add_value(argv, "--active-start-time", cfg.get("active_start_time"))
    add_value(argv, "--active-stop-time", cfg.get("active_stop_time"))
    add_value(argv, "--min-miss-streak", cfg.get("min_miss_streak"), "0")
    add_value(argv, "--trigger-ledger", cfg.get("trigger_ledger"), "autoTop6")
    if not is_true(cfg.get("show_browser", True)):
        argv.append("--headless")
    if is_true(cfg.get("fresh_browser_per_round", True)):
        argv.append("--fresh-browser-per-round")
    if is_true(cfg.get("ephemeral_browser_profile", True)):
        argv.append("--ephemeral-browser-profile")

    add_value(argv, "--browser-profile-dir", cfg.get("browser_profile_dir"), "penta5_chrome_profile_2")
    add_value(argv, "--adspower-api-url", cfg.get("adspower_api_url"), core.DEFAULT_ADSPOWER_API_URL)
    add_value(argv, "--adspower-user-id", cfg.get("adspower_user_id"))
    add_value(argv, "--adspower-api-key", adspower_key)
    add_value(argv, "--target-password", cfg.get("target_password"))
    add_value(argv, "--result-mode", cfg.get("result_mode"), "payout")

    add_value(argv, "--selection", cfg.get("selection"), "panel-top5")
    add_value(argv, "--fixed-shot", cfg.get("fixed_shot"))
    add_value(argv, "--base-stake", cfg.get("base_stake"), "0.2")
    add_value(argv, "--multiplier", cfg.get("multiplier"), "2")
    add_value(argv, "--max-level", cfg.get("max_level"), "4")
    add_value(argv, "--message-template", cfg.get("message_template"), "{shot}{stake}")
    add_value(argv, "--memory-file", cfg.get("memory_file"), "penta5_auto_shooter_state_2.json")
    add_value(argv, "--history-csv", cfg.get("history_file"), "penta5_auto_shooter_rounds_2.csv")
    add_flag(argv, "--resend", cfg.get("resend", False))
    add_flag(argv, "--reset-martingale", cfg.get("reset_martingale", False))
    add_value(argv, "--log-level", "INFO")
    return argv


def main() -> int:
    return core.main(build_argv())


if __name__ == "__main__":
    raise SystemExit(main())
