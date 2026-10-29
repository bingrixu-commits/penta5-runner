#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
四定回删 · 分批扔页脚本

规则（不许脑补）：
1. URL 只读 four_throw_urls.json 三个格式槽，空着就拒绝 --live
2. 组数只认你给的码表实切（默认最多 350/页，末页可不足），不另算、不凑数
3. 金额只认 --amount，脚本不改
4. 发出格式：网页页原文「码,码,…各」+ 金额

示例：
  # 先预览第 1–11 页（不发；有第1页就带上）
  python3 four_throw_robot.py --codes-file remain.txt --amount 0.5 --page-from 1 --page-to 11

  # 往格式1 URL 真发（需先在 four_throw_urls.json 填网址）
  python3 four_throw_robot.py --codes-file remain.txt --amount 0.5 --page-from 1 --page-to 11 --slot 1 --live

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
PAGE_SIZE = 350


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
        "amount": str(data.get("amount") or "").strip(),
        "password": str(data.get("password") or "").strip(),
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
    pending_index: Optional[int] = None
    for ln in lines:
        if not ln:
            continue
        # 保留真实页号：#page=3 下一行才是该页原文（避免从第3页起发时被重编成1）
        m = re.fullmatch(r"#\s*page\s*=\s*(\d+)\s*", ln, flags=re.I)
        if m:
            pending_index = int(m.group(1))
            if pending_index < 1:
                die(f"非法页号标记：{ln}")
            continue
        if ln.startswith("#"):
            continue
        if not ln.endswith("各"):
            die(f"第 {len(pages)+1} 行未以「各」结尾，已停止：{ln[:40]}…")
        body = ln[:-1]
        codes = parse_codes(body)
        rebuilt = ",".join(codes) + "各"
        if rebuilt != ln:
            die(f"第 {len(pages)+1} 行原文与码表不一致，已停止（不脑补）")
        idx = pending_index if pending_index is not None else (len(pages) + 1)
        pending_index = None
        pages.append(
            {
                "index": idx,
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
    indexes = [int(p["index"]) for p in pages]
    max_idx = max(indexes) if indexes else 0
    if page_to > max_idx:
        die(f"页号最大到第 {max_idx} 页，没有第 {page_to} 页（不凑数）")
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
    # 按真实页号排序，保证失败提示顺序正确
    out.sort(key=lambda p: int(p["index"]))
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


def _dismiss_safe_overlays(driver: Any) -> None:
    """只关 Element-UI 弹窗的关闭图标，绝不点「确定」类按钮。"""
    close_xpaths = (
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' el-message-box__wrapper ')]"
        "//*[self::button or self::a or @role='button']"
        "[contains(concat(' ', normalize-space(@class), ' '), ' el-message-box__headerbtn ') "
        "or translate(normalize-space(@aria-label), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='close'"
        " or contains(@title, '关闭') or contains(@aria-label, '关闭')]",
    )
    try:
        from selenium.webdriver.common.by import By
    except Exception:
        return
    for _ in range(3):
        clicked = False
        for xp in close_xpaths:
            try:
                nodes = driver.find_elements(By.XPATH, xp)
            except Exception:
                nodes = []
            for node in nodes:
                try:
                    if node.is_displayed() and node.is_enabled():
                        node.click()
                        clicked = True
                        time.sleep(0.2)
                        break
                except Exception:
                    continue
            if clicked:
                break
        if not clicked:
            break


def _fill_password_if_needed(driver: Any, password: str, timeout: int) -> None:
    """一般都不要密码；只有真出现密码框才处理。有框无密码则停，不脑补。"""
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
    except Exception:
        return
    selectors = (
        "input[type='password']",
        "input[placeholder*='密码']",
        "input[placeholder*='口令']",
        "input[placeholder*='password' i]",
    )
    node = None
    deadline = time.time() + min(3, max(1, timeout))
    while time.time() < deadline and node is None:
        for css in selectors:
            try:
                cands = driver.find_elements(By.CSS_SELECTOR, css)
            except Exception:
                cands = []
            for cand in reversed(cands):
                try:
                    if cand.is_displayed() and cand.is_enabled():
                        node = cand
                        break
                except Exception:
                    continue
            if node is not None:
                break
        if node is None:
            time.sleep(0.2)
    if node is None:
        print("PASSWORD_SKIP: 无密码框，直接进聊天")
        return
    if not password:
        die("目标页出现密码框，但配置 password 为空（不脑补密码）。在 four_throw_urls.json 填 password 后再发。")
    try:
        node.click()
    except Exception:
        pass
    try:
        node.clear()
    except Exception:
        try:
            node.send_keys(Keys.CONTROL, "a")
            node.send_keys(Keys.BACKSPACE)
        except Exception:
            pass
    node.send_keys(password)
    try:
        node.send_keys(Keys.ENTER)
    except Exception:
        pass
    time.sleep(0.8)
    print("PASSWORD_OK: 已填配置里的密码")


def _probe_chat_ui(driver: Any) -> Dict[str, Any]:
    """探测快选输入框与按钮。「发送」常在输入后才出现。"""
    pack = driver.execute_script(
        """
        const visible = (el) => {
          if (!el) return false;
          const st = window.getComputedStyle(el);
          const r = el.getBoundingClientRect();
          return st.display !== 'none' && st.visibility !== 'hidden'
            && st.pointerEvents !== 'none'
            && r.width >= 8 && r.height >= 8
            && r.bottom >= 0 && r.right >= 0
            && r.top <= (window.innerHeight || 1) && r.left <= (window.innerWidth || 1);
        };
        const isPassword = (el) => {
          const type = (el.getAttribute('type') || '').toLowerCase();
          const ph = (el.getAttribute('placeholder') || '');
          return type === 'password' || /密码|口令|password/i.test(ph);
        };
        const isEditable = (el) => {
          if (!el || el.disabled || el.readOnly || el.getAttribute('aria-disabled') === 'true') return false;
          if (isPassword(el)) return false;
          const tag = (el.tagName || '').toLowerCase();
          const type = (el.getAttribute('type') || '').toLowerCase();
          if (tag === 'textarea') return true;
          if (tag === 'input') return !['hidden','button','submit','checkbox','radio','file','image','reset','password'].includes(type);
          if (el.isContentEditable || ['true','plaintext-only'].includes((el.getAttribute('contenteditable')||'').toLowerCase())) return true;
          const role = (el.getAttribute('role') || '').toLowerCase();
          return role === 'textbox' || role === 'combobox';
        };
        const textOf = (el) => (el.innerText || el.textContent || el.value || '').replace(/\\s+/g, '');
        const sendExact = Array.from(document.querySelectorAll('button,a,div,span'))
          .filter((el) => visible(el) && textOf(el) === '发送');
        const kuaiExact = Array.from(document.querySelectorAll('button,a,div,span'))
          .filter((el) => visible(el) && textOf(el) === '快选');
        // 输入后才会出「发送」；未输入时可能只有「快选」
        const sendBtn = sendExact.length
          ? sendExact[sendExact.length - 1]
          : (kuaiExact.length ? kuaiExact[kuaiExact.length - 1] : null);
        const inputs = Array.from(document.querySelectorAll(
          "textarea, input[type='text'], input:not([type]), [contenteditable='true'], [role='textbox'], .el-textarea__inner, .chat-input, .message-input"
        )).filter((el) => visible(el) && isEditable(el));
        let best = null, bestScore = -1e18;
        for (const el of inputs) {
          const ph = String(el.getAttribute('placeholder') || '');
          if (/快译|快选/.test(ph)) { best = el; bestScore = 1e12; break; }
        }
        const scoreInput = (el, send) => {
          const ir = el.getBoundingClientRect();
          let score = ir.top + ir.left * 0.01;
          const ph = String(el.getAttribute('placeholder') || '');
          if (/快译|快选/.test(ph)) score += 500000;
          if ((el.tagName || '').toLowerCase() === 'textarea' && !ph) score -= 200000;
          if (send) {
            const sr = send.getBoundingClientRect();
            const dy = Math.abs((ir.top + ir.height/2) - (sr.top + sr.height/2));
            if (dy < 100) score += 100000 - dy * 10;
            if (ir.right <= sr.left + 8) score += 50000;
          }
          score += Math.min(ir.width, 800) * 0.2 + Math.min(ir.height, 120) * 2;
          return score;
        };
        if (!best) {
          for (const el of inputs) {
            const s = scoreInput(el, sendBtn);
            if (s > bestScore) { bestScore = s; best = el; }
          }
        }
        return {
          input: best,
          send: sendBtn,
          score: bestScore,
          mode: sendExact.length ? 'send' : (kuaiExact.length ? 'kuai' : 'none'),
          sendCount: sendExact.length,
          kuaiCount: kuaiExact.length
        };
        """
    )
    return pack if isinstance(pack, dict) else {}


def _find_chat_input(driver: Any) -> Any:
    """只找输入框；「发送」键要等输入后才会出来。"""
    pack = _probe_chat_ui(driver)
    node = pack.get("input")
    if not node:
        raise RuntimeError("目标页找不到快选输入框（已排除密码框）")
    return node


def _wait_send_button_after_input(driver: Any, timeout: float = 6.0) -> Any:
    """输入内容后轮询「发送」出现（本站：输入才出发送键）。"""
    deadline = time.time() + max(1.0, float(timeout))
    last_mode = ""
    while time.time() < deadline:
        pack = _probe_chat_ui(driver)
        mode = str(pack.get("mode") or "")
        send_btn = pack.get("send")
        if mode == "send" and send_btn is not None:
            if last_mode != "send":
                print("CHAT_SEND: 输入后已出现「发送」键", flush=True)
            return send_btn
        if mode != last_mode:
            if mode == "kuai":
                print("CHAT_WAIT_SEND: 仍是「快选」，继续等输入后出现「发送」…", flush=True)
            else:
                print("CHAT_WAIT_SEND: 等待「发送」键出现…", flush=True)
            last_mode = mode
        time.sleep(0.2)
    # 超时仍无「发送」时，若有「快选」也能点（兜底）
    pack = _probe_chat_ui(driver)
    if pack.get("send") is not None:
        print(
            "CHAT_SEND_FALLBACK: 超时未出「发送」，暂用按钮模式="
            + str(pack.get("mode") or "?"),
            flush=True,
        )
        return pack.get("send")
    raise RuntimeError("输入后仍未出现「发送」键")


def _find_chat_input_and_send(driver: Any) -> Tuple[Any, Any]:
    """兼容旧调用：先定位输入框；发送键可能此时还没有。"""
    node = _find_chat_input(driver)
    pack = _probe_chat_ui(driver)
    return node, pack.get("send")


def _read_input_text(driver: Any, node: Any) -> str:
    try:
        return str(
            driver.execute_script(
                """
                const el = arguments[0];
                if (!el) return '';
                if (el.isContentEditable) return String(el.innerText || el.textContent || '');
                return String(el.value || '');
                """,
                node,
            )
            or ""
        )
    except Exception:
        return ""


def _set_input_value(driver: Any, node: Any, message: str) -> None:
    driver.execute_script(
        """
        const el = arguments[0];
        const value = arguments[1];
        el.focus();
        el.dispatchEvent(new Event('focus', {bubbles:true}));
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
          // 本站「发送」键靠输入事件才渲染出来
          el.dispatchEvent(new Event('input', {bubbles:true}));
          el.dispatchEvent(new InputEvent('input', {bubbles:true, data:value, inputType:'insertText'}));
          el.dispatchEvent(new KeyboardEvent('keydown', {bubbles:true, key:'a'}));
          el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true, key:'a'}));
          el.dispatchEvent(new Event('change', {bubbles:true}));
        }
        """,
        node,
        message,
    )
    got = _read_input_text(driver, node).replace("\r", "").replace("\n", "")
    expect = str(message or "").replace("\r", "").replace("\n", "")
    if not got or (expect and expect[:80] not in got and got[:80] not in expect):
        raise RuntimeError("聊天框写入失败（读回内容与待发页不一致）")


def _click_send(driver: Any, node: Any, send_btn: Any) -> None:
    from selenium.webdriver.common.keys import Keys

    sent = False
    if send_btn is not None:
        try:
            send_btn.click()
            sent = True
        except Exception:
            try:
                driver.execute_script("arguments[0].click();", send_btn)
                sent = True
            except Exception:
                sent = False
    if not sent:
        try:
            node.send_keys(Keys.ENTER)
            sent = True
        except Exception:
            pass
    if not sent:
        raise RuntimeError("找到聊天框但点「发送」/回车都失败")


def _clear_chat_input(driver: Any, node: Any) -> None:
    """清空输入框：发完后发送键会消失，清框给下一页「再输入再出键」。"""
    try:
        driver.execute_script(
            """
            const el = arguments[0];
            if (!el) return;
            el.focus();
            if (el.isContentEditable) {
              el.textContent = '';
              el.dispatchEvent(new InputEvent('input', {bubbles:true, data:'', inputType:'deleteContent'}));
            } else {
              const proto = el.tagName === 'TEXTAREA'
                ? HTMLTextAreaElement.prototype
                : HTMLInputElement.prototype;
              const setter = Object.getOwnPropertyDescriptor(proto, 'value');
              if (setter && setter.set) setter.set.call(el, '');
              else el.value = '';
              el.dispatchEvent(new Event('input', {bubbles:true}));
              el.dispatchEvent(new InputEvent('input', {bubbles:true, data:'', inputType:'deleteContent'}));
              el.dispatchEvent(new KeyboardEvent('keydown', {bubbles:true, key:'Backspace'}));
              el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true, key:'Backspace'}));
              el.dispatchEvent(new Event('change', {bubbles:true}));
            }
            """,
            node,
        )
    except Exception:
        pass


def _payload_markers(message: str) -> Dict[str, str]:
    text = str(message or "").replace("\r", "").replace("\n", "").strip()
    if "各" not in text:
        raise RuntimeError("待发文本缺少「各」，拒绝发送")
    body, amount = text.rsplit("各", 1)
    codes = [c for c in body.split(",") if c]
    if not codes or not all(re.fullmatch(r"\d{4}", c) for c in codes):
        raise RuntimeError("待发文本码表非法，拒绝发送")
    if not re.fullmatch(r"\d+(?:\.\d+)?", amount or ""):
        raise RuntimeError("待发文本金额非法，拒绝发送")
    return {
        "first": codes[0],
        "last": codes[-1],
        "count": str(len(codes)),
        "amount": amount,
        "head": text[:96],
        "tail": text[-96:],
        "seal": codes[-1] + "各" + amount,
    }


def _validate_page_payload(page: Dict[str, Any], message: str) -> None:
    """发送前硬核验：页组数、码表、拼接文本必须一致，禁止滥竽充数。"""
    codes = [str(c) for c in (page.get("codes") or [])]
    count = int(page.get("count") or 0)
    if not codes or count != len(codes):
        raise RuntimeError(f"第{page.get('index')}页组数不一致 count={count} codes={len(codes)}")
    expect = ",".join(codes) + "各"
    attack = str(page.get("attackText") or "")
    if attack and attack != expect:
        raise RuntimeError(f"第{page.get('index')}页原文与码表不一致")
    markers = _payload_markers(message)
    if int(markers["count"]) != len(codes):
        raise RuntimeError(f"第{page.get('index')}页待发组数被截断/篡改")
    if not message.startswith(codes[0] + ",") and message != codes[0] + "各" + markers["amount"]:
        if not message.startswith(codes[0]):
            raise RuntimeError(f"第{page.get('index')}页待发开头码不对")
    if markers["seal"] not in message:
        raise RuntimeError(f"第{page.get('index')}页待发结尾「码各+金额」不对")
    if message != expect + markers["amount"]:
        raise RuntimeError(f"第{page.get('index')}页待发全文与码表+金额不一致")


def _page_toast_error(driver: Any) -> str:
    """只看明显失败提示，不扫整页码文。"""
    try:
        err = driver.execute_script(
            """
            const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim();
            const nodes = Array.from(document.querySelectorAll(
              '.el-message--error, .el-notification__content, .error, [class*="error"]'
            ));
            for (const el of nodes) {
              const t = norm(el.innerText || el.textContent || '');
              if (t && /失败|错误|超长|限制|频繁|不行|无法|拒绝/.test(t)) return t.slice(0, 120);
            }
            return '';
            """
        )
        return str(err or "").strip()
    except Exception:
        return ""


def _confirm_send_landed(
    driver: Any,
    node: Any,
    send_btn: Any,
    message: str,
    before: Dict[str, Any] | None = None,
    wait_s: float = 2.5,
) -> None:
    """本站规律：点发送后输入框清空、「发送」键消失 = 成功。不扫页面找码文。"""
    del before  # 兼容旧调用，不再用全文 digest
    expect_head = str(message or "").replace("\r", "").replace("\n", "")[:48]
    deadline = time.time() + max(0.8, float(wait_s))
    while time.time() < deadline:
        err = _page_toast_error(driver)
        if err:
            raise RuntimeError("页面报错：" + err)
        try:
            node = _find_chat_input(driver)
        except Exception:
            node = None
        box = _read_input_text(driver, node).replace("\r", "").replace("\n", "").strip() if node is not None else ""
        pack = _probe_chat_ui(driver)
        mode = str(pack.get("mode") or "")
        # 成功：框已空，或「发送」已消失（回到快选/无键）
        if not box and mode != "send":
            print("CHAT_SENT_OK: 框已清空且发送键已消失", flush=True)
            return
        if not box and expect_head and expect_head not in box:
            print("CHAT_SENT_OK: 输入框已清空", flush=True)
            return
        if mode != "send" and expect_head and expect_head not in box:
            print("CHAT_SENT_OK: 发送键已消失", flush=True)
            return
        time.sleep(0.15)
    # 超时仍有内容且发送键还在 → 才当失败
    box = ""
    try:
        node = _find_chat_input(driver)
        box = _read_input_text(driver, node).replace("\r", "").replace("\n", "").strip()
    except Exception:
        pass
    mode = str((_probe_chat_ui(driver) or {}).get("mode") or "")
    if not box and mode != "send":
        print("CHAT_SENT_OK: 超时前已清空", flush=True)
        return
    raise RuntimeError(
        f"点发送后未清空（框长={len(box)} mode={mode or 'none'}），当失败"
    )


def _mark_fail(
    failed: List[int],
    page_index: int,
    i: int,
    total: int,
    reason: str,
    round_name: str = "1",
) -> None:
    reason = str(reason or "unknown").replace("\n", " ").strip() or "unknown"
    if page_index > 0 and page_index not in failed:
        failed.append(page_index)
    tag = "RETRY_FAIL" if str(round_name) == "2" else "SEND_FAIL"
    print(
        f"{tag} page={page_index} ({i + 1}/{total}) round={round_name} reason={reason}",
        flush=True,
    )
    if str(round_name) == "2":
        print(f"PAGE_FAIL_TIP 第{page_index}页继续转发仍失败：{reason}", flush=True)
    else:
        print(f"PAGE_FAIL_TIP 第{page_index}页转发失败：{reason}", flush=True)
        print(f"ALERT_PAGE 预警：第{page_index}页没转成功，请点「继续发失败页」再发", flush=True)


def _send_pages_once(
    driver: Any,
    items: Sequence[Tuple[Dict[str, Any], str]],
    slot: str,
    sleep_s: float,
    round_name: str,
) -> Tuple[List[int], List[int]]:
    """在已打开的浏览器里发一轮；返回 (失败页号, 已处理页号)。
    本站循环：空框无发送键 → 输入后发送键出现 → 点发送后键消失 → 下一页再输入。
    """
    failed: List[int] = []
    touched: List[int] = []
    for i, (page, message) in enumerate(items):
        page_index = int(page.get("index") or 0)
        if page_index:
            touched.append(page_index)
        print(
            f"LIVE page={page_index} count={page.get('count')} -> slot{slot} round={round_name}",
            flush=True,
        )
        node = None
        try:
            _validate_page_payload(page, message)
            _dismiss_safe_overlays(driver)
            node = _find_chat_input(driver)
            if i == 0 and str(round_name) == "1":
                print(
                    "CHAT_TARGET: 快选输入框就绪｜规律=输入出「发送」/发完键消失/再输入再出",
                    flush=True,
                )
            # 上一页发完键会消失；先清空，避免旧内容挡下一页触发
            _clear_chat_input(driver, node)
            time.sleep(0.12)
            _set_input_value(driver, node, message)
            time.sleep(0.25)
            wrote = _read_input_text(driver, node).replace("\r", "").replace("\n", "")
            expect = str(message).replace("\r", "").replace("\n", "")
            if wrote != expect:
                raise RuntimeError(
                    f"输入框读回被截断/篡改 wrote={len(wrote)} expect={len(expect)}"
                )
            # 输入后等「发送」键出来
            send_btn = _wait_send_button_after_input(driver, timeout=8.0)
            _click_send(driver, node, send_btn)
            # 本站：发完框会清空、发送键消失 —— 以此判成功，不扫页面码文
            _confirm_send_landed(driver, node, send_btn, message)
            # 再清一次，给下一页铺路
            try:
                node = _find_chat_input(driver)
                _clear_chat_input(driver, node)
            except Exception:
                pass
            if str(round_name) == "2":
                print(f"RETRY_OK page={page_index} ({i + 1}/{len(items)})", flush=True)
                print(f"SEND_OK page={page_index} ({i + 1}/{len(items)})", flush=True)
                print(f"PAGE_OK_TIP 第{page_index}页继续转发成功", flush=True)
            else:
                print(f"SEND_OK page={page_index} ({i + 1}/{len(items)})", flush=True)
                print(f"PAGE_OK_TIP 第{page_index}页转发成功", flush=True)
        except Exception as exc:
            if node is not None:
                try:
                    _clear_chat_input(driver, node)
                except Exception:
                    pass
            _mark_fail(failed, page_index, i, len(items), str(exc), round_name=round_name)
            # 出错立刻退出本轮：后面页不硬撑，方便你手动「继续发失败页」
            print(
                f"BATCH_EXIT_ON_FAIL page={page_index}｜出错自动退出，后面页留给手动继续发",
                flush=True,
            )
            for j in range(i + 1, len(items)):
                rest = items[j]
                rest_idx = int(rest[0].get("index") or 0)
                if rest_idx and rest_idx not in touched:
                    touched.append(rest_idx)
                _mark_fail(
                    failed,
                    rest_idx,
                    j,
                    len(items),
                    f"前页第{page_index}页出错已自动退出，待继续发",
                    round_name=round_name,
                )
            break
        if i + 1 < len(items) and sleep_s > 0:
            time.sleep(float(sleep_s))
    return failed, touched


def selenium_send_batch(
    url: str,
    items: Sequence[Tuple[Dict[str, Any], str]],
    headless: bool,
    timeout: int,
    password: str = "",
    sleep_s: float = 1.0,
    slot: str = "1",
) -> List[int]:
    """同一浏览器连发多页：只跑一轮。
    任一页出错立刻退出浏览器；失败/未发页标出，由面板「继续发失败页」人工再发。
    分页最多 350 组/页（人民币金额照配置，不脑补）。
    """
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
    except Exception as exc:
        die(f"selenium 不可用：{exc}")

    if not items:
        die("没有可发送的页")

    all_indexes = [int(p.get("index") or 0) for p, _ in items]
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--remote-allow-origins=*")
    final_failed: List[int] = []
    touched: set = set()
    driver = None
    try:
        driver = webdriver.Chrome(options=opts)
        driver.set_page_load_timeout(max(30, int(timeout) * 2))
        driver.get(url)
        time.sleep(0.8)
        _dismiss_safe_overlays(driver)
        _fill_password_if_needed(driver, str(password or "").strip(), timeout)
        _dismiss_safe_overlays(driver)

        print("ROUND_START round=1 total=" + str(len(items)), flush=True)
        first_failed, touched1 = _send_pages_once(driver, items, slot, sleep_s, "1")
        touched.update(touched1)
        final_failed = first_failed[:]
        if first_failed:
            fail_csv = ",".join(str(x) for x in first_failed)
            print(f"ALERT_FAIL_PAGES={fail_csv}", flush=True)
            print(
                "ALERT_TIP 第"
                + str(first_failed[0])
                + "页出错已自动退出｜没转成功：第"
                + "、".join(str(x) for x in first_failed)
                + "页｜请点「继续发失败页」再发",
                flush=True,
            )
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        reason = str(exc).replace("\n", " ").strip() or type(exc).__name__
        if reason.isdigit():
            reason = "browser_abort"
        print(f"BATCH_ABORT reason={reason}", flush=True)
        for j, idx in enumerate(all_indexes):
            if not idx or idx in touched or idx in final_failed:
                continue
            _mark_fail(
                final_failed,
                idx,
                j,
                len(items),
                "任务中断未发到：" + reason,
                round_name="1",
            )
        if isinstance(exc, SystemExit) and final_failed:
            pass
        elif isinstance(exc, SystemExit):
            raise
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
    for j, idx in enumerate(all_indexes):
        if idx and idx not in touched and idx not in final_failed:
            _mark_fail(final_failed, idx, j, len(items), "未产生发送结果", round_name="1")
    return final_failed


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="四定回删剩码分批扔页（照实数，不脑补）")
    p.add_argument("--urls-file", default=str(DEFAULT_URLS), help="三个网址格式配置，默认 four_throw_urls.json")
    p.add_argument("--slot", default="", help="用哪个网址槽：1/2/3；默认读配置 active")
    p.add_argument("--amount", default="", help="金额；空则读 four_throw_urls.json 的 amount（都不脑补）")
    p.add_argument("--password", default="", help="目标页密码；空则读配置 password；一般都不要，有密码框才用")
    p.add_argument("--page-from", type=int, default=1, help="从页（默认 1，有第1页就发）")
    p.add_argument("--page-to", type=int, default=9999, help="到页（默认很大，实际按文件里有的页）")
    p.add_argument("--page-size", type=int, default=PAGE_SIZE, help="每页组数，默认 350（末页可更少）")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--codes-file", help="剩码文件（空格/逗号分隔四位码）")
    src.add_argument("--page-text-file", help="网页整页原文，每行一页，须以各结尾")
    p.add_argument("--live", action="store_true", help="真打开 URL 粘贴并点发送；默认只预览")
    p.add_argument("--headless", action="store_true", help="无头浏览器（仅 --live）")
    p.add_argument("--timeout", type=int, default=20, help="浏览器超时秒数")
    p.add_argument("--sleep", type=float, default=1.0, help="页与页间隔秒数（仅 --live，默认 1.0）")
    p.add_argument("--dry-write", default="", help="把将发送的每页文本写入该文件（预览用）")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import os
    log_path = str(os.environ.get("FOUR_THROW_JOB_LOG") or "").strip()
    if log_path:
        try:
            class _Tee:
                def __init__(self, stream, path: str) -> None:
                    self._stream = stream
                    self._fp = open(path, "a", encoding="utf-8", buffering=1)
                def write(self, data: str) -> int:
                    try:
                        self._stream.write(data)
                        self._stream.flush()
                    except Exception:
                        pass
                    try:
                        self._fp.write(data)
                        self._fp.flush()
                    except Exception:
                        pass
                    return len(data or "")
                def flush(self) -> None:
                    try: self._stream.flush()
                    except Exception: pass
                    try: self._fp.flush()
                    except Exception: pass
            sys.stdout = _Tee(sys.stdout, log_path)  # type: ignore[assignment]
            sys.stderr = _Tee(sys.stderr, log_path)  # type: ignore[assignment]
        except Exception:
            pass
    args = parse_args(argv)
    cfg = load_urls(Path(args.urls_file))
    slot, url = pick_url(cfg, args.slot)
    amount = str(args.amount or "").strip() or str(cfg.get("amount") or "").strip()
    password = str(args.password or "").strip() or str(cfg.get("password") or "").strip()
    if not amount:
        die("金额为空：在 four_throw_urls.json 填 \"amount\"，或加 --amount 0.5（脚本不脑补金额）")
    # 覆盖 args 供后续统一使用
    args.amount = amount

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
    print(f"total_pages={len(all_pages)} throw={args.page_from}-{args.page_to} amount={args.amount} password={'（已配）' if password else '（空·一般不要）'}")
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

    failed = selenium_send_batch(
        url,
        payloads,
        headless=bool(args.headless),
        timeout=int(args.timeout),
        password=password,
        sleep_s=float(args.sleep),
        slot=slot,
    )
    ok_n = len(payloads) - len(failed)
    if failed:
        print(
            "FAILED_PAGES=" + ",".join(str(x) for x in failed)
            + f" ok={ok_n} fail={len(failed)}",
            flush=True,
        )
        print(
            "ALERT_TIP 第一次失败页：第"
            + "、".join(str(x) for x in failed)
            + "页｜请点「继续发失败页」再发",
            flush=True,
        )
        print("PARTIAL_DONE", flush=True)
        return 1
    print("FAILED_PAGES=", flush=True)
    print("ALL_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
