#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Sequence

import penta5_auto_shooter as core


ROOT = Path(__file__).resolve().parent
ALI_STATE_URL = "http://127.0.0.1:3001/panel"


ROBOT_CONFIGS: Dict[str, Dict[str, str]] = {
    "1": {
        "settings": "penta5_auto_shooter_settings.json",
        "memory": "penta5_auto_shooter_state_ali_1.json",
        "history": "penta5_auto_shooter_rounds_ali_1.csv",
        "profile": "penta5_chrome_profile_ali_1",
        "default_result_mode": "normal",
        "default_base_stake": "0.4",
    },
    "2": {
        "settings": "penta5_auto_shooter_settings_2.json",
        "memory": "penta5_auto_shooter_state_ali_2.json",
        "history": "penta5_auto_shooter_rounds_ali_2.csv",
        "profile": "penta5_chrome_profile_ali_2",
        "default_result_mode": "payout",
        "default_base_stake": "0.2",
    },
}


def safe_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text if text else default


def read_json(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
    except Exception:
        return {}
    return {}


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


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
    text = safe_text(value, default)
    if text:
        argv.extend([flag, text])


def robot_paths(robot: str) -> Dict[str, Path]:
    cfg = ROBOT_CONFIGS[robot]
    state_dir = ROOT / "robot_state"
    return {
        "settings": ROOT / cfg["settings"],
        "memory": state_dir / cfg["memory"],
        "history": state_dir / cfg["history"],
        "profile": ROOT / "robot_profiles" / cfg["profile"],
    }


def secret_from_file(path_text: str) -> str:
    path = Path(path_text)
    if not path.is_absolute():
        path = ROOT / path
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""


def target_password_for(robot: str, cfg: Dict[str, Any]) -> str:
    env_key = f"PENTA5_ROBOT{robot}_TARGET_PASSWORD"
    env_value = os.environ.get(env_key, "").strip()
    if env_value:
        return env_value
    file_value = safe_text(cfg.get("target_password_file"))
    if file_value:
        return secret_from_file(file_value)
    return safe_text(cfg.get("target_password"))


def build_argv(robot: str, cfg: Dict[str, Any], paths: Dict[str, Path]) -> List[str]:
    defaults = ROBOT_CONFIGS[robot]
    argv: List[str] = []

    add_value(argv, "--state-url", cfg.get("ali_state_url"), ALI_STATE_URL)
    add_flag(argv, "--sync-before-read", cfg.get("sync_before_read", True))

    add_value(argv, "--url", cfg.get("url"))
    argv.append("--live")
    argv.append("--headless")
    if is_true(cfg.get("ali_fresh_browser_per_round", True)):
        argv.append("--fresh-browser-per-round")
    if is_true(cfg.get("ali_ephemeral_browser_profile", True)):
        argv.append("--ephemeral-browser-profile")
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

    add_value(argv, "--browser-profile-dir", str(paths["profile"]))
    if is_true(cfg.get("ali_use_adspower", False)):
        add_value(argv, "--adspower-api-url", cfg.get("adspower_api_url"))
        add_value(argv, "--adspower-user-id", cfg.get("adspower_user_id"))
        add_value(argv, "--adspower-api-key", secret_from_file(safe_text(cfg.get("adspower_key_file"))))

    add_value(argv, "--target-password", target_password_for(robot, cfg))
    add_value(argv, "--result-mode", cfg.get("result_mode"), defaults["default_result_mode"])
    add_value(argv, "--selection", cfg.get("selection"), "panel-top5")
    add_value(argv, "--fixed-shot", cfg.get("fixed_shot"))
    add_value(argv, "--base-stake", cfg.get("base_stake"), defaults["default_base_stake"])
    add_value(argv, "--multiplier", cfg.get("multiplier"), "2")
    add_value(argv, "--max-level", cfg.get("max_level"), "4")
    add_value(argv, "--message-template", cfg.get("message_template"), "{shot}{stake}")
    add_value(argv, "--memory-file", str(paths["memory"]))
    add_value(argv, "--history-csv", str(paths["history"]))
    add_flag(argv, "--resend", cfg.get("resend", False))
    add_flag(argv, "--reset-martingale", cfg.get("reset_martingale", False))
    add_value(argv, "--log-level", cfg.get("log_level"), "INFO")
    return argv


def initialize_memory(robot: str, cfg: Dict[str, Any], paths: Dict[str, Path]) -> None:
    reader = core.StateReader(
        state_file=Path(""),
        state_url=safe_text(cfg.get("ali_state_url"), ALI_STATE_URL),
        timeout=int(float(safe_text(cfg.get("timeout"), "12"))),
        sync_before_read=is_true(cfg.get("sync_before_read", True)),
    )
    data = reader.read()
    memory = read_json(paths["memory"])
    memory.update(
        {
            "last_sent_key": core.state_render_key(data),
            "last_period": core.state_period(data),
            "last_prediction_id": core.state_prediction_id(data),
            "last_status": "ALI_INIT_SKIP_CURRENT",
            "martingale_level": int(memory.get("martingale_level") or 1),
            "updated_at": core.datetime.now().isoformat(timespec="seconds"),
        }
    )
    write_json(paths["memory"], memory)
    print(
        f"robot{robot} initialized: period={memory['last_period']} "
        f"prediction={memory['last_prediction_id']}"
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Penta5 auto shooter on Ali backend.")
    parser.add_argument("--robot", choices=sorted(ROBOT_CONFIGS), required=True)
    parser.add_argument("--init-memory-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    paths = robot_paths(args.robot)
    cfg = read_json(paths["settings"])
    if not cfg:
        raise SystemExit(f"settings file not found or invalid: {paths['settings']}")
    if args.init_memory_only:
        initialize_memory(args.robot, cfg, paths)
        return 0
    return core.main(build_argv(args.robot, cfg, paths))


if __name__ == "__main__":
    raise SystemExit(main())
