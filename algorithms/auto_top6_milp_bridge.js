"use strict";

/**
 * autoTop6 cuOpt 桥：T4 HTTP（优先）→ 可选本机 Python → 调用方 JS 穷举回退。
 * 禁止 spawnSync。非 JSON 不得 ok:true。
 * T4 关机时连接失败应在 timeout 内返回，不得拖死面板。
 */

const { spawn } = require("child_process");
const path = require("path");

const RUN_PY = path.join(__dirname, "auto_top6_milp.py");

function slimItem(item) {
  return {
    id: [item && item.kind, item && item.key, item && item.side || "recommend", String(item && item.code)].join(":"),
    kind: item && item.kind,
    key: item && item.key,
    side: (item && item.side) || "recommend",
    code: item && item.code,
    stableScore: Number(item && item.stableScore || 0),
    rate300: Number(item && item.rate300 || 0),
    rate60: Number(item && item.rate60 || 0)
  };
}

function cuoptUrl() {
  const raw = String(process.env.PENTA5_CUOPT_URL || "").trim().replace(/\/+$/, "");
  return raw || "";
}

function useLocalPython() {
  return String(process.env.PENTA5_CUOPT_LOCAL || "0") === "1";
}

function timeoutMs(opts) {
  const fromOpts = Number(opts && opts.timeoutMs);
  const fromEnv = Number(process.env.PENTA5_CUOPT_TIMEOUT_MS || 1200);
  const n = Number.isFinite(fromOpts) && fromOpts > 0 ? fromOpts : fromEnv;
  return Math.max(200, Math.min(8000, n || 1200));
}

function buildPayload(o) {
  return {
    positionPool: (Array.isArray(o.positionPool) ? o.positionPool : []).map(slimItem),
    heweiPool: (Array.isArray(o.heweiPool) ? o.heweiPool : []).map(slimItem),
    positionCount: Number(o.positionCount || 3) || 3,
    heweiCount: Number(o.heweiCount || 2) || 2,
    maxPerModel: Number(o.maxPerModel || 2) || 2,
    timeLimit: Number(o.timeLimit || 5) || 5,
    cuoptOnly: true
  };
}

function acceptCuoptJson(parsed) {
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  if (parsed.ok !== true || parsed.solver !== "cuopt") return null;
  return parsed;
}

async function selectViaHttp(payload, timeout) {
  const base = cuoptUrl();
  const url = base + "/autotop6";
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeout);
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
      signal: ac.signal
    });
    const raw = await res.text();
    let parsed = null;
    try {
      parsed = JSON.parse(String(raw || "").trim() || "null");
    } catch (_) {
      return { ok: false, error: "non-json" };
    }
    const okBody = acceptCuoptJson(parsed);
    if (!okBody) {
      return {
        ok: false,
        error: (parsed && (parsed.error || (parsed.meta && parsed.meta.reason))) || ("http-" + res.status),
        result: parsed
      };
    }
    return { ok: true, result: okBody };
  } catch (err) {
    const name = err && err.name ? String(err.name) : "";
    const msg = err && err.message ? String(err.message) : String(err);
    if (name === "AbortError") return { ok: false, error: "timeout" };
    return { ok: false, error: msg.slice(0, 200) };
  } finally {
    clearTimeout(timer);
  }
}

function selectViaPython(payload, timeout) {
  const python = process.env.PENTA5_PYTHON || process.env.PYTHON || "python3";
  return new Promise((resolve) => {
    let settled = false;
    let stdout = "";
    let stderr = "";
    let child;
    try {
      child = spawn(python, [RUN_PY], {
        cwd: __dirname,
        env: process.env,
        stdio: ["pipe", "pipe", "pipe"]
      });
    } catch (err) {
      resolve({ ok: false, error: err && err.message ? err.message : String(err) });
      return;
    }

    const finish = (out) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(out);
    };

    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch (_) {}
      finish({ ok: false, error: "timeout" });
    }, timeout);

    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.on("error", (err) => {
      finish({ ok: false, error: err && err.message ? err.message : String(err) });
    });
    child.on("close", () => {
      const raw = String(stdout || "").trim();
      if (!raw) {
        finish({ ok: false, error: (stderr || "empty-stdout").slice(0, 500) });
        return;
      }
      let parsed = null;
      try {
        parsed = JSON.parse(raw);
      } catch (_) {
        finish({ ok: false, error: "non-json", detail: raw.slice(0, 200) });
        return;
      }
      const okBody = acceptCuoptJson(parsed);
      if (!okBody) {
        finish({
          ok: false,
          error: parsed.error || (parsed.meta && parsed.meta.reason) || "cuopt-not-used",
          result: parsed
        });
        return;
      }
      finish({ ok: true, result: okBody });
    });

    try {
      child.stdin.write(JSON.stringify(payload));
      child.stdin.end();
    } catch (err) {
      try { child.kill("SIGKILL"); } catch (_) {}
      finish({ ok: false, error: err && err.message ? err.message : String(err) });
    }
  });
}

async function selectFormalPositionHeweiAsync(opts) {
  const o = opts || {};
  const payload = buildPayload(o);
  const timeout = timeoutMs(o);
  if (cuoptUrl()) {
    const httpOut = await selectViaHttp(payload, timeout);
    if (httpOut && httpOut.ok) return httpOut;
    if (!useLocalPython()) return httpOut || { ok: false, error: "cuopt-http-failed" };
  } else if (!useLocalPython()) {
    return { ok: false, error: "cuopt-url-unset" };
  }
  return selectViaPython(payload, timeout);
}

module.exports = {
  selectFormalPositionHeweiAsync,
  slimItem,
  cuoptUrl
};
