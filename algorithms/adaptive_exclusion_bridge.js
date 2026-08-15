"use strict";

/**
 * 只读 sidecar 桥：异步调用 adaptive_decision_engine.sidecar_cli。
 * 禁止 spawnSync。非 JSON stdout 不得 ok:true。
 * 不回写 positionKills / hewei / extraKills。
 */

const fs = require("fs");
const path = require("path");
const { spawn } = require("child_process");

const ROOT = path.join(__dirname, "..");
const DEFAULT_OUT = path.join(ROOT, "data", "adaptive_exclusion_sidecar.json");

function readCachedSidecar(outPath) {
  const file = outPath || DEFAULT_OUT;
  try {
    if (!fs.existsSync(file)) return null;
    const parsed = JSON.parse(fs.readFileSync(file, "utf8"));
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (_) {
    return null;
  }
}

function writeCachedSidecar(payload, outPath) {
  const file = outPath || DEFAULT_OUT;
  try {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(payload), "utf8");
  } catch (_) {}
}

function isValidSidecarResult(result) {
  return !!(
    result
    && typeof result === "object"
    && result.ok === true
    && result.readOnly === true
    && result.appliesToTip === false
    && result.decision
    && typeof result.decision === "object"
    && typeof result.decision.action === "string"
  );
}

function buildSidecarPayload(history, liveKills, options) {
  const opts = options || {};
  const list = Array.isArray(history) ? history : [];
  return {
    newest_first: true,
    replay_window: Number(opts.replayWindow || 80),
    targetPeriod: opts.targetPeriod ? String(opts.targetPeriod) : "",
    history: list.map((row) => ({
      period: row && (row.period != null ? row.period : row.uid) != null
        ? String(row.period != null ? row.period : row.uid)
        : "",
      numbers: Array.isArray(row && row.numbers) ? row.numbers.slice(0, 4) : []
    })),
    liveKills: liveKills && typeof liveKills === "object" ? liveKills : {}
  };
}

function runAdaptiveExclusionAsync(history, liveKills, options) {
  const opts = options || {};
  const outPath = opts.outPath || DEFAULT_OUT;
  const timeout = Number(opts.timeoutMs || 15000);
  const python = process.env.PENTA5_PYTHON || process.env.PYTHON || "python3";
  const payload = buildSidecarPayload(history, liveKills, opts);

  return new Promise((resolve) => {
    if (!payload.history.length) {
      resolve({ ok: false, error: "empty-history", result: readCachedSidecar(outPath) });
      return;
    }
    try {
      fs.mkdirSync(path.dirname(outPath), { recursive: true });
    } catch (_) {}

    let settled = false;
    let stdout = "";
    let stderr = "";
    const child = spawn(python, ["-m", "adaptive_decision_engine.sidecar_cli", "--out", outPath], {
      cwd: ROOT,
      env: process.env,
      stdio: ["pipe", "pipe", "pipe"]
    });

    const finish = (outcome) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(outcome);
    };

    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch (_) {}
      finish({ ok: false, error: "timeout", result: readCachedSidecar(outPath) });
    }, timeout);

    child.stdout.on("data", (chunk) => { stdout += String(chunk); });
    child.stderr.on("data", (chunk) => { stderr += String(chunk); });
    child.on("error", (err) => {
      finish({
        ok: false,
        error: err && err.message ? err.message : String(err),
        result: readCachedSidecar(outPath)
      });
    });
    child.on("close", (code) => {
      let parsed = null;
      try {
        parsed = JSON.parse(String(stdout || "").trim() || "null");
      } catch (_) {
        parsed = null;
      }
      if (!parsed) parsed = readCachedSidecar(outPath);
      if (!isValidSidecarResult(parsed)) {
        finish({
          ok: false,
          error: (stderr || stdout || `invalid-json-or-exit:${code}`).slice(0, 500),
          result: parsed && typeof parsed === "object" ? parsed : null
        });
        return;
      }
      writeCachedSidecar(parsed, outPath);
      finish({ ok: true, result: parsed, outPath });
    });

    try {
      child.stdin.write(JSON.stringify(payload));
      child.stdin.end();
    } catch (err) {
      try { child.kill("SIGKILL"); } catch (_) {}
      finish({
        ok: false,
        error: err && err.message ? err.message : String(err),
        result: readCachedSidecar(outPath)
      });
    }
  });
}

module.exports = {
  DEFAULT_OUT,
  readCachedSidecar,
  writeCachedSidecar,
  isValidSidecarResult,
  buildSidecarPayload,
  runAdaptiveExclusionAsync
};
