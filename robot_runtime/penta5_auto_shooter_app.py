#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tkinter desktop launcher for penta5_auto_shooter.py.
"""

from __future__ import annotations

import queue
import json
import os
import random
import socket
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from types import SimpleNamespace
from typing import Optional
from urllib.parse import urlsplit

import penta5_auto_shooter as core


def read_secret_file(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    return ""


def write_secret_file(path: Path, value: str) -> None:
    try:
        text = value.strip()
        if not text:
            return
        path.write_text(text, encoding="utf-8")
    except Exception:
        return


def read_default_adspower_key() -> str:
    return read_secret_file(core.DEFAULT_ADSPOWER_KEY_FILE)


def read_default_state_password() -> str:
    return read_secret_file(core.DEFAULT_STATE_PASSWORD_FILE)


def gui_settings_file() -> Path:
    value = os.environ.get("PENTA5_GUI_SETTINGS_FILE", "").strip()
    return Path(value) if value else core.DEFAULT_GUI_SETTINGS_FILE


def robot_window_title() -> str:
    return os.environ.get("PENTA5_ROBOT_TITLE", "").strip() or "Penta5 系统推荐五自动射击"


def load_gui_settings() -> dict:
    try:
        path = gui_settings_file()
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
    except Exception:
        return {}
    return {}


def load_shared_data_source_settings() -> dict:
    try:
        path = core.DEFAULT_SHARED_DATA_SOURCE_FILE
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(data, dict):
                return data
    except Exception:
        return {}
    return {}


def save_shared_data_source_settings(state_url: str, state_user: str) -> None:
    payload = {
        "state_url": state_url.strip() or core.DEFAULT_STATE_URL,
        "state_user": state_user.strip() or core.DEFAULT_STATE_USER,
    }
    core.DEFAULT_SHARED_DATA_SOURCE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class AutoShooterApp:
    def __init__(self, master: tk.Tk):
        self.master = master
        self.master.title(robot_window_title())
        self.master.geometry("1040x760")
        self.settings = load_gui_settings()
        self.shared_data_source = load_shared_data_source_settings()
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.running = False
        self.celebration_window: Optional[tk.Toplevel] = None

        self.url_var = tk.StringVar(value=self._setting("url", ""))
        self.target_password_var = tk.StringVar(value=self._setting("target_password", ""))
        self.state_file_var = tk.StringVar(value=self._setting("state_file", str(core.DEFAULT_STATE_FILE)))
        self.state_url_var = tk.StringVar(value=self._data_source_setting("state_url", core.DEFAULT_STATE_URL))
        self.state_user_var = tk.StringVar(value=self._data_source_setting("state_user", core.DEFAULT_STATE_USER))
        self.state_password_var = tk.StringVar(value=read_default_state_password())
        self.base_stake_var = tk.StringVar(value=self._setting("base_stake", "1"))
        self.multiplier_var = tk.StringVar(value=self._setting("multiplier", "2"))
        self.max_level_var = tk.StringVar(value=self._setting("max_level", "4"))
        self.rounds_var = tk.StringVar(value=self._setting("rounds", "288"))
        self.timeout_var = tk.StringVar(value=self._setting("timeout", "12"))
        self.result_timeout_var = tk.StringVar(value=self._setting("result_timeout", "260"))
        self.check_interval_var = tk.StringVar(value=self._setting("check_interval", "5"))
        self.offset_var = tk.StringVar(value=self._setting("offset", "3"))
        self.step_var = tk.StringVar(value=self._setting("step", "5"))
        self.random_delay_var = tk.StringVar(value=self._setting("random_delay", core.DEFAULT_SCHEDULE_RANDOM_DELAY))
        self.active_start_time_var = tk.StringVar(value=self._setting("active_start_time", ""))
        self.active_stop_time_var = tk.StringVar(value=self._setting("active_stop_time", ""))
        self.min_miss_streak_var = tk.StringVar(value=self._setting("min_miss_streak", "0"))
        self.trigger_ledger_var = tk.StringVar(value=self._setting("trigger_ledger", "autoTop6"))
        self.memory_file_var = tk.StringVar(value=self._setting("memory_file", str(core.DEFAULT_MEMORY_FILE)))
        self.history_file_var = tk.StringVar(value=self._setting("history_file", str(core.DEFAULT_HISTORY_CSV)))
        self.browser_profile_dir_var = tk.StringVar(value=self._setting("browser_profile_dir", str(core.DEFAULT_BROWSER_PROFILE_DIR)))
        self.adspower_api_url_var = tk.StringVar(value=self._setting("adspower_api_url", core.DEFAULT_ADSPOWER_API_URL))
        self.adspower_user_id_var = tk.StringVar(value=self._setting("adspower_user_id", ""))
        self.adspower_api_key_var = tk.StringVar(value=read_default_adspower_key())
        self.message_template_var = tk.StringVar(value=self._setting("message_template", "{shot}{stake}"))
        self.selection_var = tk.StringVar(value=self._setting("selection", "top5"))
        self.fixed_shot_initial = self._setting("fixed_shot", "")
        self.fixed_shot_text: Optional[tk.Text] = None
        self.shot_language_rules_initial = self._setting("shot_language_rules", "")
        self.shot_language_rules_text: Optional[tk.Text] = None
        self.shot_language_enabled_var = tk.BooleanVar(value=self._setting_bool("shot_language_enabled", False))
        self.result_mode_var = tk.StringVar(value=self._setting("result_mode", "normal"))

        self.live_var = tk.BooleanVar(value=self._setting_bool("live", False))
        self.show_browser_var = tk.BooleanVar(value=self._setting_bool("show_browser", True))
        self.schedule_var = tk.BooleanVar(value=self._setting_bool("schedule", True))
        self.watch_var = tk.BooleanVar(value=self._setting_bool("watch", True))
        self.resend_var = tk.BooleanVar(value=self._setting_bool("resend", False))
        self.reset_martingale_var = tk.BooleanVar(value=self._setting_bool("reset_martingale", False))
        self.auto_start_var = tk.BooleanVar(value=self._setting_bool("auto_start", False))
        self.sync_before_read_var = tk.BooleanVar(value=self._setting_bool("sync_before_read", True))
        self.hide_url_var = tk.BooleanVar(value=True)

        self._build_ui()
        self._pump_logs()
        self.master.after(250, self._load_adspower_profile_async)
        self.master.after(350, self._ensure_data_service_async)
        if self.auto_start_var.get():
            self.master.after(1200, self.start)
        self.master.protocol("WM_DELETE_WINDOW", self.on_close)

    def _setting(self, key: str, default: str) -> str:
        value = self.settings.get(key, default)
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    def _data_source_setting(self, key: str, default: str) -> str:
        value = self.shared_data_source.get(key)
        if value is None or str(value).strip() == "":
            value = self.settings.get(key, default)
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default

    def _setting_bool(self, key: str, default: bool) -> bool:
        value = self.settings.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    def _fixed_shot_value(self) -> str:
        if self.fixed_shot_text is not None:
            return self.fixed_shot_text.get("1.0", tk.END).strip()
        return self.fixed_shot_initial.strip()

    def _shot_language_rules_value(self) -> str:
        if self.shot_language_rules_text is not None:
            return self.shot_language_rules_text.get("1.0", tk.END).strip()
        return self.shot_language_rules_initial.strip()

    def _build_ui(self) -> None:
        container = tk.Frame(self.master)
        container.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left = tk.Frame(container)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = tk.Frame(container, width=380)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(10, 0))
        right.pack_propagate(False)

        def add_entry(label: str, var: tk.StringVar, show: str = "") -> None:
            row = tk.Frame(left)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, width=18, anchor="w").pack(side=tk.LEFT)
            tk.Entry(row, textvariable=var, width=74, show=show).pack(side=tk.LEFT, fill=tk.X, expand=True)

        def add_combo(label: str, var: tk.StringVar, values: tuple[str, ...]) -> None:
            row = tk.Frame(left)
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=label, width=18, anchor="w").pack(side=tk.LEFT)
            ttk.Combobox(row, textvariable=var, values=values, width=72).pack(side=tk.LEFT, fill=tk.X, expand=True)

        add_entry("射击场网址", self.url_var)
        add_entry("网页登录密码", self.target_password_var, show="*")
        add_entry("本地数据文件", self.state_file_var)
        add_entry("数据网 /state", self.state_url_var)
        add_entry("数据网账号", self.state_user_var)
        add_entry("数据网密码", self.state_password_var, show="*")
        add_entry("消息模板", self.message_template_var)
        add_combo("推荐模式", self.selection_var, core.SELECTION_CHOICES)
        fixed_row = tk.Frame(left)
        fixed_row.pack(fill=tk.X, pady=2)
        tk.Label(fixed_row, text="固定模型文本(fixed)", width=18, anchor="w").pack(side=tk.LEFT, anchor="n")
        self.fixed_shot_text = tk.Text(fixed_row, height=3, width=74, wrap=tk.WORD)
        self.fixed_shot_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if self.fixed_shot_initial:
            self.fixed_shot_text.insert("1.0", self.fixed_shot_initial)
        language_row = tk.Frame(left)
        language_row.pack(fill=tk.X, pady=2)
        tk.Label(language_row, text="射击语言转换", width=18, anchor="w").pack(side=tk.LEFT, anchor="n")
        language_box = tk.Frame(language_row)
        language_box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Checkbutton(language_box, text="启用自定义转换规则", variable=self.shot_language_enabled_var).pack(anchor="w")
        self.shot_language_rules_text = tk.Text(language_box, height=3, width=74, wrap=tk.WORD)
        self.shot_language_rules_text.pack(fill=tk.X, expand=True)
        if self.shot_language_rules_initial:
            self.shot_language_rules_text.insert("1.0", self.shot_language_rules_initial)
        tk.Label(
            language_box,
            text="每行一条：旧词=新词 或 旧词=>新词；先生成标准模型，再按顺序替换。",
            fg="#475569",
            anchor="w",
            justify=tk.LEFT,
        ).pack(fill=tk.X)
        add_entry("结果模式", self.result_mode_var)

        grid = tk.Frame(left)
        grid.pack(fill=tk.X, pady=(8, 2))
        fields = [
            ("基础数值", self.base_stake_var),
            ("倍投倍数", self.multiplier_var),
            ("回转层数", self.max_level_var),
            ("发射轮数", self.rounds_var),
            ("超时秒数", self.timeout_var),
            ("结果等待", self.result_timeout_var),
            ("检查间隔", self.check_interval_var),
            ("起始分钟", self.offset_var),
            ("间隔分钟", self.step_var),
            ("随机秒", self.random_delay_var),
            ("启动时间", self.active_start_time_var),
            ("停止时间", self.active_stop_time_var),
            ("连败启动", self.min_miss_streak_var),
            ("触发账本", self.trigger_ledger_var),
        ]
        for index, (label, var) in enumerate(fields):
            row = index // 3
            col = index % 3
            box = tk.Frame(grid)
            box.grid(row=row, column=col, sticky="ew", padx=(0 if col == 0 else 8, 0), pady=3)
            tk.Label(box, text=label, width=9, anchor="w").pack(side=tk.LEFT)
            tk.Entry(box, textvariable=var, width=12).pack(side=tk.LEFT, fill=tk.X, expand=True)
            grid.columnconfigure(col, weight=1)

        add_entry("状态记忆文件", self.memory_file_var)
        add_entry("发射记录 CSV", self.history_file_var)
        add_entry("浏览器用户目录", self.browser_profile_dir_var)
        add_entry("AdsPower API", self.adspower_api_url_var)
        add_entry("AdsPower环境ID", self.adspower_user_id_var)
        add_entry("AdsPower Key", self.adspower_api_key_var, show="*")

        opts = tk.Frame(left)
        opts.pack(fill=tk.X, pady=8)
        tk.Checkbutton(opts, text="真实发送", variable=self.live_var).pack(side=tk.LEFT)
        tk.Checkbutton(opts, text="显示浏览器", variable=self.show_browser_var).pack(side=tk.LEFT, padx=(10, 0))
        tk.Checkbutton(opts, text="周期随机定时", variable=self.schedule_var).pack(side=tk.LEFT, padx=(10, 0))
        tk.Checkbutton(opts, text="持续监听", variable=self.watch_var).pack(side=tk.LEFT, padx=(10, 0))
        tk.Checkbutton(opts, text="允许重复本期", variable=self.resend_var).pack(side=tk.LEFT, padx=(10, 0))
        tk.Checkbutton(opts, text="启动时回到第一层", variable=self.reset_martingale_var).pack(side=tk.LEFT, padx=(10, 0))
        tk.Checkbutton(opts, text="打开后自动启动", variable=self.auto_start_var).pack(side=tk.LEFT, padx=(10, 0))
        tk.Checkbutton(opts, text="读取前同步数据", variable=self.sync_before_read_var).pack(side=tk.LEFT, padx=(10, 0))

        btns = tk.Frame(left)
        btns.pack(fill=tk.X, pady=8)
        self.preview_btn = tk.Button(btns, text="预览本期文本", command=self.preview_once, bg="#2563eb", fg="white")
        self.preview_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.start_btn = tk.Button(btns, text="启动", command=self.start, bg="#16a34a", fg="white")
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = tk.Button(btns, text="停止", command=self.stop, bg="#dc2626", fg="white", state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT)

        note = (
            "默认：系统推荐五；按 03、08、13、18... 周期，到点后随机延迟秒数再发；倍投为 1 -> 2 -> 4 -> 8 -> 回到 1。"
            "【户型审核成功】只算攻击成功；出现【入住/本次总派送/中介费】才算命中。"
            "未勾选“真实发送”时只预览/模拟。"
        )
        tk.Label(left, text=note, fg="#475569", anchor="w", justify=tk.LEFT, wraplength=620).pack(fill=tk.X, pady=(4, 8))

        tk.Label(right, text="运行日志", anchor="w").pack(fill=tk.X)
        self.log_box = scrolledtext.ScrolledText(right, wrap=tk.WORD, height=42, state=tk.DISABLED)
        self.log_box.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

    def _append_log(self, text: str) -> None:
        self.log_queue.put(text)

    def _show_full_hit_celebration(self, period: str, duration_ms: int = 9000) -> None:
        try:
            if self.celebration_window is not None and self.celebration_window.winfo_exists():
                self.celebration_window.destroy()
        except Exception:
            pass

        window = tk.Toplevel(self.master)
        self.celebration_window = window
        window.title("推荐项全中")
        window.configure(bg="#111827")
        window.resizable(False, False)
        window.attributes("-topmost", True)

        width = 560
        height = 360
        self.master.update_idletasks()
        root_x = self.master.winfo_rootx()
        root_y = self.master.winfo_rooty()
        root_w = max(1, self.master.winfo_width())
        root_h = max(1, self.master.winfo_height())
        x = root_x + max(0, (root_w - width) // 2)
        y = root_y + max(0, (root_h - height) // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")

        canvas = tk.Canvas(window, width=width, height=height, bg="#111827", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        canvas.create_text(width // 2, 54, text="推荐项全中", fill="#fef3c7", font=("Microsoft YaHei UI", 28, "bold"))
        canvas.create_text(width // 2, 160, text="中", fill="#ef4444", font=("Microsoft YaHei UI", 96, "bold"))
        canvas.create_text(width // 2, 250, text=f"期号 {period}", fill="#bfdbfe", font=("Microsoft YaHei UI", 18, "bold"))
        canvas.create_text(width // 2, 302, text="全部命中", fill="#fde68a", font=("Microsoft YaHei UI", 22, "bold"))

        particles = []
        colors = ("#facc15", "#fb7185", "#38bdf8", "#34d399", "#f97316", "#e879f9", "#ffffff")
        start_time = time.time()

        def spawn_firework(cx: int, cy: int) -> None:
            for _ in range(54):
                angle = random.random() * 6.283185307179586
                speed = random.uniform(2.0, 7.0)
                particles.append(
                    {
                        "x": float(cx),
                        "y": float(cy),
                        "vx": speed * core.math.cos(angle),
                        "vy": speed * core.math.sin(angle),
                        "life": random.randint(24, 42),
                        "color": random.choice(colors),
                        "size": random.uniform(2.0, 4.8),
                    }
                )

        for _ in range(5):
            spawn_firework(random.randint(90, width - 90), random.randint(80, 245))

        def animate(frame: int = 0) -> None:
            try:
                if not window.winfo_exists():
                    return
                elapsed_ms = int((time.time() - start_time) * 1000)
                canvas.delete("particle")
                if frame % 9 == 0 and elapsed_ms < duration_ms - 900:
                    spawn_firework(random.randint(70, width - 70), random.randint(65, 255))
                alive = []
                for item in particles:
                    item["x"] += item["vx"]
                    item["y"] += item["vy"]
                    item["vy"] += 0.08
                    item["life"] -= 1
                    if item["life"] <= 0:
                        continue
                    alive.append(item)
                    size = float(item["size"])
                    canvas.create_oval(
                        item["x"] - size,
                        item["y"] - size,
                        item["x"] + size,
                        item["y"] + size,
                        fill=item["color"],
                        outline="",
                        tags="particle",
                    )
                particles[:] = alive
                canvas.tag_raise("all")
                if elapsed_ms >= duration_ms:
                    window.destroy()
                    return
                window.after(70, lambda: animate(frame + 1))
            except Exception:
                try:
                    window.destroy()
                except Exception:
                    pass

        animate()

    def _poll_full_hit(self, reader: core.StateReader, result: core.ShotBuildResult, timeout: float = 12.0) -> bool:
        deadline = time.time() + max(1.0, timeout)
        last_detail = ""
        while time.time() < deadline and not self.stop_event.is_set():
            try:
                data = reader.read()
                checks = core.selected_item_successes(data, result)
                if checks:
                    summary = []
                    for item, value in checks:
                        mark = "中" if value is True else ("未中" if value is False else "未知")
                        summary.append(f"{item.name}{item.code}:{mark}")
                    last_detail = "；".join(summary)
                    if len(checks) >= 5 and all(value is True for _, value in checks):
                        self._append_log("推荐项全中: " + last_detail)
                        return True
                    self._append_log("推荐项未全中: " + last_detail)
                    return False
            except Exception as exc:
                last_detail = str(exc)
            time.sleep(1.0)
        if last_detail:
            self._append_log("推荐项全中检查未确认: " + last_detail)
        return False

    def _pump_logs(self) -> None:
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_box.config(state=tk.NORMAL)
                self.log_box.insert(tk.END, msg + "\n")
                self.log_box.see(tk.END)
                self.log_box.config(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.master.after(120, self._pump_logs)

    def _set_running(self, value: bool) -> None:
        self.running = value
        self.start_btn.config(state=tk.DISABLED if value else tk.NORMAL)
        self.preview_btn.config(state=tk.DISABLED if value else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if value else tk.DISABLED)

    def _load_adspower_profile_async(self) -> None:
        if self.adspower_user_id_var.get().strip():
            return

        def worker() -> None:
            try:
                profile = core.discover_adspower_profile(
                    self.adspower_api_url_var.get().strip(),
                    self.adspower_api_key_var.get().strip(),
                    8,
                )
            except Exception as exc:
                self._append_log(f"AdsPower 环境未自动读取: {exc}")
                return

            def apply_profile() -> None:
                if not self.adspower_user_id_var.get().strip():
                    self.adspower_user_id_var.set(profile.user_id)
                    self._append_log("已自动填入 AdsPower 环境: " + profile.label)
                    self._save_settings()

            self.master.after(0, apply_profile)

        threading.Thread(target=worker, daemon=True).start()

    def _is_local_host(self, host: str) -> bool:
        host = host.strip().lower()
        if host in {"localhost", "127.0.0.1", "::1"}:
            return True
        try:
            local_names = {socket.gethostname(), socket.getfqdn()}
            local_ips = {"127.0.0.1"}
            for name in list(local_names):
                try:
                    local_ips.update(socket.gethostbyname_ex(name)[2])
                except Exception:
                    continue
            try:
                local_ips.update(socket.gethostbyname_ex("")[2])
            except Exception:
                pass
            return host in {item.lower() for item in local_names} or host in local_ips
        except Exception:
            return False

    def _can_connect(self, host: str, port: int, timeout: float = 1.0) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except Exception:
            return False

    def _find_node_exe(self) -> Optional[Path]:
        candidates = [
            Path(r"C:\Users\Administrator\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"),
            Path(r"C:\Users\Administrator\AppData\Local\OpenAI\Codex\runtimes\cua_node\1b23c930bdf84ed6\bin\node.exe"),
            Path(r"C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\Lib\site-packages\playwright\driver\node.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        for folder in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(folder) / ("node.exe" if os.name == "nt" else "node")
            if candidate.exists():
                return candidate
        return None

    def _find_python_exe(self) -> Optional[Path]:
        candidates = [
            Path(r"C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe"),
            Path(r"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe"),
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        for folder in os.environ.get("PATH", "").split(os.pathsep):
            candidate = Path(folder) / ("python.exe" if os.name == "nt" else "python")
            if candidate.exists():
                return candidate
        return None

    def _start_remote_source_proxy(self) -> bool:
        if self._can_connect("127.0.0.1", 3000, timeout=0.8):
            return True
        python_exe = self._find_python_exe()
        script = Path("current_source") / "penta5-runner" / "remote_state_proxy.py"
        if python_exe is None or not script.exists():
            self._append_log("3000 数据代理未启动，且未找到 Python 或 remote_state_proxy.py")
            return False
        env = os.environ.copy()
        env["LOCAL_PROXY_HOST"] = "127.0.0.1"
        env["LOCAL_PROXY_PORT"] = "3000"
        env["REMOTE_STATE_URL"] = "http://8.210.5.221:3001/state"
        env["REMOTE_HEALTH_URL"] = "http://8.210.5.221:3001/health"
        env["REMOTE_PANEL_USER"] = self.state_user_var.get().strip() or core.DEFAULT_STATE_USER
        env["REMOTE_PANEL_PASSWORD"] = self.state_password_var.get().strip()
        env["REMOTE_REQUEST_TIMEOUT"] = "12"
        env["REMOTE_PROXY_CACHE_FILE"] = str((Path("current_source") / "penta5-runner" / "data" / "proxy_latest_cache.json").resolve())
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(
            [str(python_exe), str(script.resolve())],
            cwd=str(script.parent.resolve()),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            if self._can_connect("127.0.0.1", 3000, timeout=0.8):
                self._append_log("已自动启动 3000 数据代理")
                return True
            time.sleep(0.5)
        self._append_log("3000 数据代理启动后仍未连通")
        return False

    def _trigger_data_sync(self, state_url: str) -> None:
        try:
            parts = urlsplit(state_url)
            base = f"{parts.scheme}://{parts.netloc}"
            sync_url = base + "/sync"
            import base64
            import urllib.request

            token = base64.b64encode(
                f"{self.state_user_var.get().strip() or core.DEFAULT_STATE_USER}:{self.state_password_var.get().strip()}".encode("utf-8")
            ).decode("ascii")
            request = urllib.request.Request(
                sync_url,
                headers={"Authorization": "Basic " + token, "Accept": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                response.read(512)
            self._append_log("已触发数据后台同步")
        except Exception as exc:
            self._append_log(f"触发数据同步失败: {exc}")

    def _ensure_data_service_async(self) -> None:
        def worker() -> None:
            try:
                state_url = core.normalize_state_url(self.state_url_var.get().strip() or core.DEFAULT_STATE_URL)
                parts = urlsplit(state_url)
                host = parts.hostname or ""
                port = parts.port or (443 if parts.scheme == "https" else 80)
                if port != 3001 or not self._is_local_host(host):
                    return
                self._start_remote_source_proxy()
                if self._can_connect(host, port, timeout=0.8):
                    self._append_log("数据后台已在线: " + state_url)
                    self._trigger_data_sync(state_url)
                    return
                node_exe = self._find_node_exe()
                script = Path("current_source") / "penta5-runner" / "start_penta5_compat.js"
                if node_exe is None or not script.exists():
                    self._append_log("数据后台未启动，且未找到 Node 或 start_penta5_compat.js")
                    return
                env = os.environ.copy()
                env["PORT"] = "3001"
                env["PANEL_USER"] = self.state_user_var.get().strip() or core.DEFAULT_STATE_USER
                env["PANEL_PASSWORD"] = self.state_password_var.get().strip()
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                subprocess.Popen(
                    [str(node_exe), str(script.resolve())],
                    cwd=str(script.parent.resolve()),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                deadline = time.time() + 8
                while time.time() < deadline:
                    if self._can_connect(host, port, timeout=0.8):
                        self._append_log("已自动启动数据后台: " + state_url)
                        self._trigger_data_sync(state_url)
                        return
                    time.sleep(0.5)
                self._append_log("数据后台启动后仍未连通: " + state_url)
            except Exception as exc:
                self._append_log(f"自动启动数据后台失败: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    def _settings_payload(self) -> dict:
        return {
            "url": self.url_var.get().strip(),
            "target_password": self.target_password_var.get().strip(),
            "state_file": self.state_file_var.get().strip(),
            "state_url": self.state_url_var.get().strip() or core.DEFAULT_STATE_URL,
            "state_user": self.state_user_var.get().strip() or core.DEFAULT_STATE_USER,
            "base_stake": self.base_stake_var.get().strip(),
            "multiplier": self.multiplier_var.get().strip(),
            "max_level": self.max_level_var.get().strip(),
            "rounds": self.rounds_var.get().strip(),
            "timeout": self.timeout_var.get().strip(),
            "result_timeout": self.result_timeout_var.get().strip(),
            "check_interval": self.check_interval_var.get().strip(),
            "offset": self.offset_var.get().strip(),
            "step": self.step_var.get().strip(),
            "random_delay": self.random_delay_var.get().strip() or core.DEFAULT_SCHEDULE_RANDOM_DELAY,
            "active_start_time": self.active_start_time_var.get().strip(),
            "active_stop_time": self.active_stop_time_var.get().strip(),
            "min_miss_streak": self.min_miss_streak_var.get().strip() or "0",
            "trigger_ledger": self.trigger_ledger_var.get().strip() or "autoTop6",
            "memory_file": self.memory_file_var.get().strip(),
            "history_file": self.history_file_var.get().strip(),
            "browser_profile_dir": self.browser_profile_dir_var.get().strip(),
            "adspower_api_url": self.adspower_api_url_var.get().strip() or core.DEFAULT_ADSPOWER_API_URL,
            "adspower_user_id": self.adspower_user_id_var.get().strip(),
            "message_template": self.message_template_var.get(),
            "selection": self.selection_var.get().strip() or "top5",
            "fixed_shot": self._fixed_shot_value(),
            "shot_language_enabled": bool(self.shot_language_enabled_var.get()),
            "shot_language_rules": self._shot_language_rules_value(),
            "result_mode": self.result_mode_var.get().strip() or "normal",
            "live": bool(self.live_var.get()),
            "show_browser": bool(self.show_browser_var.get()),
            "schedule": bool(self.schedule_var.get()),
            "watch": bool(self.watch_var.get()),
            "resend": bool(self.resend_var.get()),
            "reset_martingale": bool(self.reset_martingale_var.get()),
            "auto_start": bool(self.auto_start_var.get()),
            "sync_before_read": bool(self.sync_before_read_var.get()),
        }

    def _save_settings(self) -> None:
        try:
            payload = self._settings_payload()
            gui_settings_file().write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            write_secret_file(core.DEFAULT_STATE_PASSWORD_FILE, self.state_password_var.get())
            write_secret_file(core.DEFAULT_ADSPOWER_KEY_FILE, self.adspower_api_key_var.get())
            save_shared_data_source_settings(self.state_url_var.get(), self.state_user_var.get())
        except Exception as exc:
            self._append_log(f"保存本地配置失败: {exc}")

    def on_close(self) -> None:
        self._save_settings()
        self.stop_event.set()
        self.master.destroy()

    def _args(self) -> SimpleNamespace:
        try:
            base_stake = float(self.base_stake_var.get())
            multiplier = float(self.multiplier_var.get())
            max_level = max(1, int(self.max_level_var.get()))
            rounds = max(1, int(self.rounds_var.get()))
            timeout = max(3, int(self.timeout_var.get()))
            result_timeout = max(0, int(self.result_timeout_var.get()))
            check_interval = max(0.5, float(self.check_interval_var.get()))
            offset = int(self.offset_var.get())
            step = max(1, int(self.step_var.get()))
            random_delay = self.random_delay_var.get().strip() or core.DEFAULT_SCHEDULE_RANDOM_DELAY
            core.parse_schedule_random_delay(random_delay)
            core.parse_clock_minutes(self.active_start_time_var.get().strip()) if self.active_start_time_var.get().strip() else None
            core.parse_clock_minutes(self.active_stop_time_var.get().strip()) if self.active_stop_time_var.get().strip() else None
            min_miss_streak = max(0, int(float(self.min_miss_streak_var.get().strip() or "0")))
        except ValueError as exc:
            raise ValueError(f"数字参数格式错误: {exc}") from exc

        return SimpleNamespace(
            state_file=Path(self.state_file_var.get().strip() or core.DEFAULT_STATE_FILE),
            state_url=self.state_url_var.get().strip(),
            state_user=self.state_user_var.get().strip(),
            state_password=self.state_password_var.get(),
            sync_before_read=bool(self.sync_before_read_var.get()),
            url=self.url_var.get().strip(),
            target_password=self.target_password_var.get().strip(),
            result_mode=self.result_mode_var.get().strip() or "normal",
            live=bool(self.live_var.get()),
            watch=bool(self.watch_var.get()),
            schedule=bool(self.schedule_var.get()),
            shoot_offset_minute=offset,
            shoot_step_minutes=step,
            schedule_random_delay=random_delay,
            active_start_time=self.active_start_time_var.get().strip(),
            active_stop_time=self.active_stop_time_var.get().strip(),
            min_miss_streak=min_miss_streak,
            trigger_ledger=self.trigger_ledger_var.get().strip() or "autoTop6",
            rounds=rounds,
            check_interval=check_interval,
            timeout=timeout,
            result_timeout=result_timeout,
            headless=not bool(self.show_browser_var.get()),
            driver_path="",
            browser_profile_dir=Path(self.browser_profile_dir_var.get().strip() or core.DEFAULT_BROWSER_PROFILE_DIR),
            adspower_api_url=self.adspower_api_url_var.get().strip(),
            adspower_user_id=self.adspower_user_id_var.get().strip(),
            adspower_api_key=self.adspower_api_key_var.get().strip(),
            selection=self.selection_var.get().strip() or "top5",
            base_text=core.DEFAULT_BASE_TEXT,
            fixed_shot=self._fixed_shot_value(),
            shot_language_rules=self._shot_language_rules_value(),
            shot_language_enabled=bool(self.shot_language_enabled_var.get()),
            stake="",
            base_stake=base_stake,
            multiplier=multiplier,
            max_level=max_level,
            unknown_action="lose",
            advance_dry_run=False,
            reset_martingale=bool(self.reset_martingale_var.get()),
            message_template=self.message_template_var.get(),
            memory_file=Path(self.memory_file_var.get().strip() or core.DEFAULT_MEMORY_FILE),
            history_csv=Path(self.history_file_var.get().strip() or core.DEFAULT_HISTORY_CSV),
            resend=bool(self.resend_var.get()),
        )

    def preview_once(self) -> None:
        try:
            self._save_settings()
            args = self._args()
            memory = core.read_memory(args.memory_file)
            reader = core.StateReader(args.state_file, args.state_url, args.timeout, args.state_user, args.state_password, args.sync_before_read)
            data = reader.read()
            snapshot = core.current_martingale_snapshot(args, memory)
            result = core.build_shot(
                data,
                args.selection,
                args.base_text,
                args.message_template,
                snapshot.stake,
                args.fixed_shot,
                args.shot_language_rules,
                args.shot_language_enabled,
            )
            self._append_log(f"预览期号: {result.period}")
            self._append_log(f"订单期号: {result.target_period}")
            self._append_log(f"当前层级: {snapshot.level}  本次数值: {snapshot.stake}")
            for index, item in enumerate(result.selected_items, 1):
                rate = "--" if item.stable_score is None else f"{item.stable_score:.2f}%"
                self._append_log(f"{index}. {item.name} {item.code} 综合 {rate}")
            self._append_log("发送文本:")
            self._append_log(result.message)
        except Exception as exc:
            messagebox.showerror("预览失败", str(exc))

    def _wait_schedule(self, args: SimpleNamespace) -> bool:
        target, base, delay = core.next_schedule_time_with_random_delay(
            core.datetime.now(),
            args.shoot_offset_minute,
            args.shoot_step_minutes,
            args.schedule_random_delay,
        )
        if delay > 0:
            self._append_log(f"下一次随机发射: {target.strftime('%H:%M:%S')}（基础 {base.strftime('%H:%M:%S')} +{delay}秒）")
        else:
            self._append_log(f"下一次定时发射: {target.strftime('%H:%M:%S')}")
        while not self.stop_event.is_set():
            remaining = (target - core.datetime.now()).total_seconds()
            if remaining <= 0:
                return True
            time.sleep(min(1.0, max(0.1, remaining)))
        return False

    def start(self) -> None:
        if self.running:
            return
        try:
            self._save_settings()
            args = self._args()
        except Exception as exc:
            messagebox.showerror("参数错误", str(exc))
            return
        if args.live and not args.url:
            messagebox.showerror("缺少网址", "真实发送需要填写射击场网址")
            return
        if core.selection_uses_fixed_shot(args.selection) and not args.fixed_shot.strip():
            messagebox.showerror("缺少固定模型", "推荐模式选择 fixed 时，需要填写固定模型文本")
            return
        self.stop_event.clear()
        self.log_box.config(state=tk.NORMAL)
        self.log_box.delete("1.0", tk.END)
        self.log_box.config(state=tk.DISABLED)
        self._set_running(True)

        def worker() -> None:
            shooter = None
            try:
                mode_label = "固定模型" if core.selection_uses_fixed_shot(args.selection) else "系统推荐五"
                self._append_log("启动参数: " + mode_label + " / 四层回转 / " + ("真实发送" if args.live else "模拟预览"))
                if args.url:
                    shown_url = core.mask_url(args.url) if self.hide_url_var.get() else args.url
                    self._append_log("射击场网址: " + shown_url)
                if args.state_url:
                    self._append_log("数据读取地址: " + core.mask_url(core.normalize_state_url(args.state_url)))
                memory = core.read_memory(args.memory_file)
                if args.reset_martingale:
                    memory["martingale_level"] = 1
                    memory["martingale_last_outcome"] = "manual-reset"
                    core.atomic_write_json(args.memory_file, memory)
                    self._append_log("倍投层级已回到第一层")
                reader = core.StateReader(args.state_file, args.state_url, args.timeout, args.state_user, args.state_password, args.sync_before_read)
                if args.live:
                    if not args.adspower_user_id and (args.adspower_api_url or args.adspower_api_key):
                        profile = core.discover_adspower_profile(
                            args.adspower_api_url,
                            args.adspower_api_key,
                            args.timeout,
                        )
                        args.adspower_user_id = profile.user_id
                        self.master.after(0, lambda value=profile.user_id: self.adspower_user_id_var.set(value))
                        self._append_log("自动选择 AdsPower 环境: " + profile.label)
                    shooter = core.SeleniumShooter(
                        args.url,
                        args.headless,
                        args.timeout,
                        args.driver_path,
                        args.browser_profile_dir,
                        args.adspower_api_url,
                        args.adspower_user_id,
                        args.adspower_api_key,
                        args.target_password,
                        args.result_mode,
                    )
                    shooter.open()
                    if args.adspower_user_id:
                        self._append_log("AdsPower 浏览器环境已启动并接管。")
                    else:
                        self._append_log("自动浏览器已打开；如果页面白屏或未登录，请先在该窗口登录/刷新。")
                processed = 0
                while not self.stop_event.is_set() and processed < args.rounds:
                    if args.schedule and not self._wait_schedule(args):
                        break
                    try:
                        did_process, result = core.run_once(reader, args, shooter, memory)
                        if did_process:
                            processed += 1
                            self._append_log(f"完成第 {processed} 次: {result.period if result else '--'}")
                            self._append_log(f"下次倍投层级: {memory.get('martingale_level', 1)}")
                            if result is not None and self._poll_full_hit(reader, result):
                                self.master.after(0, lambda period=result.period: self._show_full_hit_celebration(period))
                        elif result is not None:
                            self._append_log(f"本期已处理，跳过: {result.period}")
                    except Exception as exc:
                        self._append_log(f"本轮失败: {exc}")
                        if not args.watch:
                            break
                    if not args.watch:
                        break
                    if not args.schedule:
                        end = time.time() + args.check_interval
                        while time.time() < end and not self.stop_event.is_set():
                            time.sleep(0.2)
                self._append_log("已停止")
            except Exception as exc:
                self._append_log(f"错误: {exc}")
            finally:
                if shooter is not None:
                    shooter.close()
                self.master.after(0, lambda: self._set_running(False))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._append_log("已请求停止")


def main() -> None:
    root = tk.Tk()
    AutoShooterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
