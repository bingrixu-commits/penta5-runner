#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四定回删 · 分批扔页脚本

规则（不许脑补）：
1. URL 只读 four_throw_urls.json 三个格式槽，空着就拒绝 --live
2. 组数只认你给的码表实切（500/页），不另算、不凑数
3. 金额只认 --amount，脚本不改
4. 发出格式：网页页原文「码,码,…各」+ 金额

示例：
  # 先预览第 10–11 页（不发）
  python3 four_throw_robot.py --codes-file remain.txt --amount 0.5 --page-from 10 --page-to 11

  # 往格式1 URL 真发（需先在 four_throw_urls.json 填网址）
  python3 four_throw_robot.py --codes-file remain.txt --amount 0.5 --page-from 10 --page-to 11 --slot 1 --live

  # 直接用网页复制出来的整页文本（每行一页，须以「各」结尾）
  python3 four_throw_robot.py --page-text-file pages.txt --amount 0.5 --slot 2 --live
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent
DEFAULT_URLS = ROOT / "four_throw_urls.json"
PAGE_SIZE = 500


def die(msg: str, code: int = 2) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def load_urls(path: Path) -> Dict[str, Any]:
    if not path.exists():
        die(f"找不到网址配置：{path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        die(f"网址配置无法解析：{exc}")
    if not isinstance(data, dict):
        die("网址配置必须是 JSON 对象")
    urls = data.get("urls") if isinstance(data.get("urls"), dict) else {}
    return {
        "active": str(data.get("active") or "1"),
        "urls": {
            "1": str(urls.get("1") or "").strip(),
            "2": str(urls.get("2") or "").strip(),
            "3": str(urls.get("3") or "").strip(),
        },
        "labels": data.get("labels") if isinstance(data.get("labels"), dict) else {},
    }


def pick_url(cfg: Dict[str, Any], slot: str) -> Tuple[str, str]:
    slot = str(slot or cfg.get("active") or "1").strip()
    if slot not in {"1", "2", "3"}:
        die("槽位只能是 1 / 2 / 3")
    url = str((cfg.get("urls") or {}).get(slot) or "").strip()
    return slot, url


def parse_codes(text: str) -> List[str]:
    raw = str(text or "")
    raw = raw.replace("各", " ").replace("\r", "\n")
    parts = re.split(r"[\s,，;；]+", raw)
    codes: List[str] = []
    seen = set()
    for part in parts:
        token = part.strip()
        if not token:
            continue
        if not re.fullmatch(r"\d{4}", token):
            die(f"发现非四位码，已停止（不脑补）：{token!r}")
        if token in seen:
            continue
        seen.add(token)
        codes.append(token)
    return codes


def pages_from_codes(codes: Sequence[str], page_size: int = PAGE_SIZE) -> List[Dict[str, Any]]:
    if page_size <= 0:
        die("page-size 必须 > 0")
    pages: List[Dict[str, Any]] = []
    for i in range(0, len(codes), page_size):
        chunk = list(codes[i : i + page_size])
        text = ",".join(chunk) + "各"
        pages.append(
            {
                "index": len(pages) + 1,
                "count": len(chunk),
                "codes": chunk,
                "attackText": text,
            }
        )
    return pages


def pages_from_text_file(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        die(f"找不到页文本文件：{path}")
    lines = [ln.strip() for ln in path.read_text(encoding="utf-8-sig").splitlines()]
    pages: List[Dict[str, Any]] = []
    for ln in lines:
        if not ln or ln.startswith("#"):
            continue
        if not ln.endswith("各"):
            die(f"第 {len(pages)+1} 行未以「各」结尾，已停止：{ln[:40]}…")
        body = ln[:-1]
        codes = parse_codes(body)
        rebuilt = ",".join(codes) + "各"
        if rebuilt != ln:
            die(f"第 {len(pages)+1} 行原文与码表不一致，已停止（不脑补）")
        pages.append(
            {
                "index": len(pages) + 1,
                "count": len(codes),
                "codes": codes,
                "attackText": ln,
            }
        )
    if not pages:
        die("页文本文件里没有有效页")
    return pages


def select_pages(pages: Sequence[Dict[str, Any]], page_from: int, page_to: int) -> List[Dict[str, Any]]:
    if page_from < 1 or page_to < 1 or page_from > page_to:
        die("页号非法：要求 1 ≤ from ≤ to")
    if page_to > len(pages):
        die(f"一共只有 {len(pages)} 页，没有第 {page_to} 页（不凑数）")
    out = []
    for page in pages:
        idx = int(page["index"])
        if page_from <= idx <= page_to:
            if int(page["count"]) != len(page["codes"]):
                die(f"第 {idx} 页组数不一致：显示 {page['count']} / 实切 {len(page['codes'])}")
            if page["attackText"] != ",".join(page["codes"]) + "各":
                die(f"第 {idx} 页原文与码表不一致，已停止")
            out.append(page)
    if not out:
        die("选定页范围内没有页")
    return out


def build_payload(page: Dict[str, Any], amount: str) -> str:
    amount = str(amount or "").strip()
    if not amount:
        die("请用 --amount 填金额（脚本不脑补金额）")
    if not re.fullmatch(r"\d+(?:\.\d+)?", amount):
        die("金额只能是数字，例如 1 或 0.2")
    text = str(page["attackText"])
    if not text.endswith("各"):
        die(f"第 {page['index']} 页不以「各」结尾")
    return text + amount


def selenium_send(url: str, message: str, headless: bool, timeout: int) -> None:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
    except Exception as exc:
        die(f"selenium 不可用：{exc}")

    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1280,900")
    driver = webdriver.Chrome(options=opts)
    wait = WebDriverWait(driver, max(3, timeout))
    try:
        driver.get(url)
        time.sleep(1.2)
        # 聊天输入：textarea / contenteditable
        input_selectors = [
            "textarea",
            "input[type='text']",
            "[contenteditable='true']",
            "[role='textbox']",
            ".el-textarea__inner",
        ]
        node = None
        for css in input_selectors:
            try:
                cands = driver.find_elements(By.CSS_SELECTOR, css)
            except Exception:
                cands = []
            for cand in cands:
                try:
                    if cand.is_displayed() and cand.is_enabled():
                        node = cand
                        break
                except Exception:
                    continue
            if node is not None:
                break
        if node is None:
            die("目标页找不到聊天输入框（textarea）")

        tag = (node.tag_name or "").lower()
        driver.execute_script(
            """
            const el = arguments[0];
            const value = arguments[1];
            el.focus();
            if (el.isContentEditable) {
              el.textContent = value;
              el.dispatchEvent(new InputEvent('input', {bubbles:true, data:value, inputType:'insertText'}));
            } else {
              const proto = el.tagName === 'TEXTAREA'
                ? HTMLTextAreaElement.prototype
                : HTMLInputElement.prototype;
              const setter = Object.getOwnPropertyDescriptor(proto, 'value');
              if (setter && setter.set) setter.set.call(el, value);
              else el.value = value;
              el.dispatchEvent(new Event('input', {bubbles:true}));
              el.dispatchEvent(new Event('change', {bubbles:true}));
            }
            """,
            node,
            message,
        )
        time.sleep(0.25)

        # 发送按钮：发送 / 纸飞机近似
        send_xpaths = [
            "//button[normalize-space()='发送']",
            "//button[contains(normalize-space(), '发送')]",
            "//*[self::button or self::a][normalize-space()='发送']",
            "//*[contains(@class,'send') and (self::button or self::a or self::div)]",
        ]
        sent = False
        for xp in send_xpaths:
            try:
                btns = driver.find_elements(By.XPATH, xp)
            except Exception:
                btns = []
            for btn in btns:
                try:
                    if btn.is_displayed() and btn.is_enabled():
                        btn.click()
                        sent = True
                        break
                except Exception:
                    continue
            if sent:
                break
        if not sent:
            try:
                node.send_keys(Keys.ENTER)
                sent = True
            except Exception:
                pass
        if not sent:
            die("找不到「发送」按钮，也未能回车发送")
        time.sleep(1.0)
        print("SEND_OK")
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="四定回删剩码分批扔页（照实数，不脑补）")
    p.add_argument("--urls-file", default=str(DEFAULT_URLS), help="三个网址格式配置，默认 four_throw_urls.json")
    p.add_argument("--slot", default="", help="用哪个网址槽：1/2/3；默认读配置 active")
    p.add_argument("--amount", required=True, help="金额（你填，脚本不改）")
    p.add_argument("--page-from", type=int, default=10, help="从页（默认 10）")
    p.add_argument("--page-to", type=int, default=11, help="到页（默认 11）")
    p.add_argument("--page-size", type=int, default=PAGE_SIZE, help="每页组数，默认 500")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--codes-file", help="剩码文件（空格/逗号分隔四位码）")
    src.add_argument("--page-text-file", help="网页整页原文，每行一页，须以各结尾")
    p.add_argument("--live", action="store_true", help="真打开 URL 粘贴并点发送；默认只预览")
    p.add_argument("--headless", action="store_true", help="无头浏览器（仅 --live）")
    p.add_argument("--timeout", type=int, default=20, help="浏览器超时秒数")
    p.add_argument("--sleep", type=float, default=1.5, help="页与页间隔秒数（仅 --live）")
    p.add_argument("--dry-write", default="", help="把将发送的每页文本写入该文件（预览用）")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    cfg = load_urls(Path(args.urls_file))
    slot, url = pick_url(cfg, args.slot)

    if args.codes_file:
        codes = parse_codes(Path(args.codes_file).read_text(encoding="utf-8-sig"))
        if not codes:
            die("codes-file 里没有四位码")
        all_pages = pages_from_codes(codes, args.page_size)
    else:
        all_pages = pages_from_text_file(Path(args.page_text_file))

    chosen = select_pages(all_pages, args.page_from, args.page_to)
    payloads: List[Tuple[Dict[str, Any], str]] = []
    for page in chosen:
        payloads.append((page, build_payload(page, args.amount)))

    print(f"slot={slot} url={url or '（空）'}")
    print(f"total_pages={len(all_pages)} throw={args.page_from}-{args.page_to} amount={args.amount}")
    for page, payload in payloads:
        print(f"page={page['index']} count={page['count']} chars={len(payload)}")

    if args.dry_write:
        out = Path(args.dry_write)
        out.write_text("\n".join(p for _, p in payloads) + "\n", encoding="utf-8")
        print(f"wrote={out}")

    if not args.live:
        print("DRY_RUN: 未发送。确认组数无误后加 --live；URL 空着会拒绝 live。")
        for page, payload in payloads:
            print("---")
            print(f"# 第{page['index']}页 {page['count']}组")
            print(payload[:180] + ("…" if len(payload) > 180 else ""))
        return 0

    if not url:
        die(f"格式{slot} URL 为空：先编辑 {args.urls_file} 填网址，或面板里填三个格式后再跑 --live")

    for i, (page, payload) in enumerate(payloads):
        print(f"LIVE page={page['index']} count={page['count']} -> slot{slot}")
        selenium_send(url, payload, headless=bool(args.headless), timeout=int(args.timeout))
        if i + 1 < len(payloads) and args.sleep > 0:
            time.sleep(float(args.sleep))
    print("ALL_DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
