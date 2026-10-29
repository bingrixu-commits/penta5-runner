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
const MAX_RESULT_AGE_MS = 10 * 60 * 1000;
let cacheWriteSequence = 0;

function canonicalPeriod(value) {
  if (typeof value !== "string" || !/^[0-9]{11}$/.test(value)) return null;
  const day = `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6, 8)}`;
  const timestamp = Date.parse(`${day}T00:00:00Z`);
  const sequence = Number(value.slice(8));
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 10) !== day
    || sequence < 1 || sequence > 288) return null;
  return { value, day, timestamp, sequence };
}

function nextPeriod(value) {
  const parsed = canonicalPeriod(value);
  if (!parsed) return "";
  if (parsed.sequence < 288) return `${value.slice(0, 8)}${String(parsed.sequence + 1).padStart(3, "0")}`;
  return `${new Date(parsed.timestamp + 86400000).toISOString().slice(0, 10).replace(/-/g, "")}001`;
}

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
  let temporary = "";
  try {
    const current = readCachedSidecar(file);
    // A slow response for an older draw must not overwrite a newer target's cache.
    if (isValidSidecarResult(current) && (
      current.targetPeriod > payload.targetPeriod
      || (current.targetPeriod === payload.targetPeriod && Date.parse(current.generatedAt) > Date.parse(payload.generatedAt))
    )) return false;
    fs.mkdirSync(path.dirname(file), { recursive: true });
    temporary = `${file}.${process.pid}.${++cacheWriteSequence}.tmp`;
    fs.writeFileSync(temporary, JSON.stringify(payload), "utf8");
    fs.renameSync(temporary, file);
    return true;
  } catch (_) {
    if (temporary) { try { fs.unlinkSync(temporary); } catch (_) {} }
    return false;
  }
}

function isValidSidecarResult(result, expected) {
  const context = expected || {};
  if (!(
    result
    && typeof result === "object"
    && !Array.isArray(result)
    && result.ok === true
    && result.stale !== true
    && result.readOnly === true
    && result.appliesToTip === false
    && result.sidecar === "adaptive-exclusion"
    && canonicalPeriod(result.basePeriod)
    && canonicalPeriod(result.targetPeriod)
    && nextPeriod(result.basePeriod) === result.targetPeriod
    && result.decision
    && typeof result.decision === "object"
    && ["EXCLUDE", "SKIP", "HALT"].includes(result.decision.action)
    && ["NORMAL", "OBSERVATION", "FROZEN"].includes(result.engineState)
    && result.decision.engine_state === result.engineState
    && result.decision.period_hint === result.targetPeriod
    && Number.isFinite(result.decision.confidence)
    && result.decision.confidence >= 0 && result.decision.confidence <= 1
    && Number.isInteger(result.historyCount) && result.historyCount >= 12 && result.historyCount <= 160
  )) return false;
  if (context.basePeriod != null && String(context.basePeriod) !== result.basePeriod) return false;
  if (context.targetPeriod != null && String(context.targetPeriod) !== result.targetPeriod) return false;
  const timestampPattern = /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})$/;
  const generatedMs = typeof result.generatedAt === "string" && timestampPattern.test(result.generatedAt)
    ? Date.parse(result.generatedAt) : NaN;
  const now = Number.isFinite(context.nowMs) ? context.nowMs : Date.now();
  if (!Number.isFinite(generatedMs) || generatedMs > now + 1000 || now - generatedMs > MAX_RESULT_AGE_MS) return false;
  if (Number.isFinite(context.minGeneratedMs) && generatedMs < context.minGeneratedMs - 1000) return false;
  const excluded = result.decision.excluded;
  if (!excluded || typeof excluded !== "object" || Array.isArray(excluded)) return false;
  if (Object.keys(excluded).some((key) => !["1", "2", "3", "4"].includes(key))) return false;
  const positions = ["1", "2", "3", "4"];
  const validPositions = positions.every((position) => {
    const digits = excluded[position];
    return Array.isArray(digits) && digits.length <= 2
      && new Set(digits).size === digits.length
      && digits.every((digit) => Number.isInteger(digit) && digit >= 0 && digit <= 9)
      && (result.decision.action === "EXCLUDE" || digits.length === 0);
  });
  // Blend votes can legitimately produce no exclusion for some positions.
  // EXCLUDE means at least one real exclusion across the four positions; it
  // never authorizes filling empty slots or changing the engine's decision.
  return validPositions && (result.decision.action !== "EXCLUDE"
    || positions.some((position) => excluded[position].length > 0));
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
  const timeoutValue = Number(opts.timeoutMs || 15000);
  const timeout = Number.isFinite(timeoutValue) ? Math.max(1, Math.min(120000, timeoutValue)) : 15000;
  const python = process.env.PENTA5_PYTHON || process.env.PYTHON || "python3";
  const payload = buildSidecarPayload(history, liveKills, opts);
  const expected = {
    basePeriod: payload.history.length ? payload.history[0].period : "",
    targetPeriod: payload.targetPeriod
  };
  const failure = (error) => {
    const cached = readCachedSidecar(outPath);
    // Preserve diagnostic fallback only; callers cannot mistake it for a refresh.
    return {
      ok: false, error, source: cached ? "cache" : "none", cached: !!cached, stale: true,
      result: cached && typeof cached === "object"
        ? { ...cached, ok: false, stale: true, source: "cache", error }
        : null
    };
  };

  return new Promise((resolve) => {
    if (!payload.history.length) {
      resolve(failure("empty-history"));
      return;
    }
    try {
      fs.mkdirSync(path.dirname(outPath), { recursive: true });
    } catch (_) {}

    let settled = false;
    let stdout = "";
    let stderr = "";
    let timer = null;
    let child;
    const startedMs = Date.now();
    try {
      // Only the bridge writes validated JSON to the cache, never the subprocess.
      child = spawn(python, ["-m", "adaptive_decision_engine.sidecar_cli"], {
        cwd: ROOT,
        env: process.env,
        stdio: ["pipe", "pipe", "pipe"]
      });
    } catch (err) {
      resolve(failure(err && err.message ? err.message : String(err)));
      return;
    }

    const finish = (outcome) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(outcome);
    };

    timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch (_) {}
      finish(failure("timeout"));
    }, timeout);

    if (typeof child.stdout.setEncoding === "function") child.stdout.setEncoding("utf8");
    if (typeof child.stderr.setEncoding === "function") child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += String(chunk);
      if (stdout.length > 1024 * 1024) {
        try { child.kill("SIGKILL"); } catch (_) {}
        finish(failure("stdout-too-large"));
      }
    });
    child.stderr.on("data", (chunk) => { stderr = (stderr + String(chunk)).slice(0, 2000); });
    child.on("error", (err) => {
      finish(failure(err && err.message ? err.message : String(err)));
    });
    child.on("close", (code) => {
      if (settled) return;
      let parsed = null;
      try {
        parsed = JSON.parse(String(stdout || "").trim() || "null");
      } catch (_) {
        parsed = null;
      }
      if (code !== 0 || !isValidSidecarResult(parsed, { ...expected, minGeneratedMs: startedMs })) {
        const detail = parsed && typeof parsed.error === "string" ? parsed.error : (stderr || stdout || "invalid-json");
        finish(failure(`invalid-sidecar-or-exit:${code}:${detail}`.slice(0, 500)));
        return;
      }
      parsed = { ...parsed, source: "fresh", stale: false };
      const cacheSaved = writeCachedSidecar(parsed, outPath);
      finish({ ok: true, result: parsed, outPath, source: "fresh", stale: false, cached: false, cacheSaved });
    });

    child.stdin.on("error", (err) => {
      try { child.kill("SIGKILL"); } catch (_) {}
      finish(failure(err && err.message ? err.message : String(err)));
    });

    try {
      child.stdin.write(JSON.stringify(payload));
      child.stdin.end();
    } catch (err) {
      try { child.kill("SIGKILL"); } catch (_) {}
      finish(failure(err && err.message ? err.message : String(err)));
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
