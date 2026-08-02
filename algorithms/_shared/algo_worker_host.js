"use strict";

/**
 * Host-side dispatcher for isolated algorithm processes.
 *
 * Prefer long-lived pool via algo_worker_dispatcher (Unix socket + warm forks).
 * Fall back to spawnSync(--once) / batch-parallel when pool is unavailable.
 */

const fs = require("fs");
const path = require("path");
const { spawnSync, fork } = require("child_process");

const ENTRY = path.join(__dirname, "algo_worker_entry.js");
const BATCH = path.join(__dirname, "algo_worker_batch.js");
const DISPATCHER = path.join(__dirname, "algo_worker_dispatcher.js");
const CLIENT = path.join(__dirname, "algo_worker_client.js");

const P1_PROCESS_ALGO_IDS = Object.freeze([
  "stable-math",
  "hewei",
  "position-kills"
]);

const P2_L0_PROCESS_ALGO_IDS = Object.freeze([
  "stable-math",
  "hewei",
  "position-kills",
  "codex-stable",
  "extra-kills",
  "four-pos-exclude"
]);

const P2_L1_PROCESS_ALGO_IDS = Object.freeze([
  "auto-top6",
  "recommend2",
  "kill-framework",
  "three-duishu"
]);

const P2_PROCESS_ALGO_IDS = Object.freeze(P2_L0_PROCESS_ALGO_IDS.concat(P2_L1_PROCESS_ALGO_IDS));

const L1_WAVE1 = Object.freeze([
  "auto-top6",
  "recommend2",
  "kill-framework",
  "three-duishu"
]);
const L1_WAVE2 = Object.freeze([]);
const L1_WAVE3 = Object.freeze([]);

let dispatcherChild = null;
let dispatcherReady = false;
let dispatcherStarting = null;

function envFlagEnabled(name, defaultValue) {
  const raw = process.env[name];
  if (raw === undefined || raw === null || String(raw).trim() === "") return !!defaultValue;
  const text = String(raw).trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(text)) return true;
  if (["0", "false", "no", "off"].includes(text)) return false;
  return !!defaultValue;
}

function isProcessModeEnabled() {
  return envFlagEnabled("PENTA5_ALGO_PROCESS_MODE", false);
}

function isPoolEnabled() {
  if (!isProcessModeEnabled()) return false;
  return envFlagEnabled("PENTA5_ALGO_WORKER_POOL", true);
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function slimHistoryRow(row) {
  if (!row || typeof row !== "object") return row;
  const out = {};
  Object.keys(row).forEach((key) => {
    if (key === "rawJson") return;
    out[key] = row[key];
  });
  return out;
}

function slimHistory(history, limit) {
  const rows = Array.isArray(history) ? history : [];
  const max = Math.max(50, Math.min(5000, Number(limit || process.env.PENTA5_ALGO_WORKER_HISTORY_LIMIT || 1200) || 1200));
  return rows.slice(0, max).map(slimHistoryRow);
}

function defaultTimeoutMs(algoId) {
  if (algoId === "stable-math") {
    return Math.max(5000, Number(process.env.PENTA5_ALGO_WORKER_STABLE_TIMEOUT_MS || 30000) || 30000);
  }
  if (algoId === "extra-kills") {
    return Math.max(5000, Number(process.env.PENTA5_ALGO_WORKER_HEAVY_TIMEOUT_MS || 45000) || 45000);
  }
  return Math.max(3000, Number(process.env.PENTA5_ALGO_WORKER_TIMEOUT_MS || 25000) || 25000);
}

function dispatchSockPath() {
  return String(process.env.PENTA5_ALGO_DISPATCH_SOCK || path.join(__dirname, "..", "..", "data", "algo-dispatch.sock"));
}

function buildRuntime(runtime) {
  const rt = asObject(runtime);
  return {
    stable120Locks: asObject(rt.stable120Locks),
    modelSelectionLocks: asObject(rt.modelSelectionLocks),
    stableMathFilter: rt.stableMathFilter && typeof rt.stableMathFilter === "object"
      ? rt.stableMathFilter
      : null,
    historyMirror: Array.isArray(rt.historyMirror) ? rt.historyMirror : undefined,
    predictionsMirror: Array.isArray(rt.predictionsMirror) ? rt.predictionsMirror : undefined,
    latestRecord: rt.latestRecord && typeof rt.latestRecord === "object" ? slimHistoryRow(rt.latestRecord) : undefined,
    threeDuishuRecommendationCache: rt.threeDuishuRecommendationCache && typeof rt.threeDuishuRecommendationCache === "object"
      ? rt.threeDuishuRecommendationCache
      : null
  };
}

function buildRequest(algoId, ctx, extras) {
  const context = asObject(ctx);
  const extra = asObject(extras);
  return {
    type: "compute",
    requestId: extra.requestId || (algoId + ":" + Date.now() + ":" + Math.random().toString(16).slice(2, 8)),
    algoId,
    history: slimHistory(context.history),
    modelPerformance: asObject(context.modelPerformance),
    predictionSlice: context.prediction && typeof context.prediction === "object" ? context.prediction : null,
    runtime: buildRuntime(extra.runtime)
  };
}

function startDispatcher() {
  if (dispatcherChild && dispatcherChild.connected && !dispatcherChild.killed) {
    return Promise.resolve(dispatcherReady);
  }
  if (dispatcherStarting) return dispatcherStarting;

  dispatcherStarting = new Promise((resolve) => {
    const sock = dispatchSockPath();
    try {
      if (fs.existsSync(sock)) fs.unlinkSync(sock);
    } catch (_) {}
    fs.mkdirSync(path.dirname(sock), { recursive: true });

    const child = fork(DISPATCHER, [], {
      env: Object.assign({}, process.env, {
        PENTA5_RDS_PRIMARY_WRITE: "0",
        PENTA5_ALGO_DISPATCH_SOCK: sock
      }),
      stdio: ["ignore", "ignore", "pipe", "ipc"]
    });
    dispatcherChild = child;
    dispatcherReady = false;

    const timer = setTimeout(() => {
      dispatcherStarting = null;
      resolve(false);
    }, 30000);

    child.stderr.on("data", (buf) => {
      const text = String(buf || "").trim();
      if (text) console.error("[algo-dispatch]", text.slice(0, 400));
    });
    child.on("message", (msg) => {
      const body = asObject(msg);
      if (body.type === "ready") {
        dispatcherReady = true;
        clearTimeout(timer);
        dispatcherStarting = null;
        resolve(true);
      }
    });
    child.on("exit", () => {
      dispatcherChild = null;
      dispatcherReady = false;
      dispatcherStarting = null;
    });
  });
  return dispatcherStarting;
}

function ensureDispatcherSync() {
  if (!isPoolEnabled()) return false;
  if (dispatcherReady && dispatcherChild && !dispatcherChild.killed && fs.existsSync(dispatchSockPath())) {
    return true;
  }
  // Best-effort sync start: fork and poll sock readiness.
  const sock = dispatchSockPath();
  if (!(dispatcherChild && !dispatcherChild.killed)) {
    try {
      if (fs.existsSync(sock)) fs.unlinkSync(sock);
    } catch (_) {}
    fs.mkdirSync(path.dirname(sock), { recursive: true });
    dispatcherChild = fork(DISPATCHER, [], {
      env: Object.assign({}, process.env, {
        PENTA5_RDS_PRIMARY_WRITE: "0",
        PENTA5_ALGO_DISPATCH_SOCK: sock
      }),
      stdio: ["ignore", "ignore", "pipe", "ipc"]
    });
    dispatcherChild.stderr.on("data", (buf) => {
      const text = String(buf || "").trim();
      if (text) console.error("[algo-dispatch]", text.slice(0, 400));
    });
    dispatcherChild.on("message", (msg) => {
      if (asObject(msg).type === "ready") dispatcherReady = true;
    });
    dispatcherChild.on("exit", () => {
      dispatcherChild = null;
      dispatcherReady = false;
    });
  }
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    if (fs.existsSync(sock) && (dispatcherReady || true)) {
      // sock exists => listen() completed; warmup may still be running.
      if (fs.existsSync(sock)) {
        dispatcherReady = true;
        return true;
      }
    }
    spawnSync(process.execPath, ["-e", ""], { timeout: 50 });
  }
  return fs.existsSync(sock);
}

function dispatchRequestSync(payload, timeoutMs, options) {
  const opts = options && typeof options === "object" ? options : {};
  if (opts.noStart) {
    if (!fs.existsSync(dispatchSockPath())) {
      return { ok: false, error: "dispatch-sock-missing" };
    }
  } else if (!ensureDispatcherSync()) {
    return { ok: false, error: "dispatcher-unavailable" };
  }
  const started = Date.now();
  const child = spawnSync(process.execPath, [CLIENT], {
    input: JSON.stringify(payload),
    encoding: "utf8",
    timeout: Math.max(1000, Number(timeoutMs || 120000) || 120000),
    maxBuffer: 128 * 1024 * 1024,
    env: Object.assign({}, process.env, {
      PENTA5_RDS_PRIMARY_WRITE: "0",
      PENTA5_ALGO_DISPATCH_SOCK: dispatchSockPath()
    })
  });
  if (child.error) {
    return {
      ok: false,
      error: child.error.message || String(child.error),
      elapsedMs: Date.now() - started,
      workerMode: "ipc-pool"
    };
  }
  try {
    const text = String(child.stdout || "").trim();
    if (!text) {
      return {
        ok: false,
        error: "dispatch-client-empty-stdout",
        stderr: String(child.stderr || "").slice(0, 400),
        exitCode: child.status,
        elapsedMs: Date.now() - started,
        workerMode: "ipc-pool"
      };
    }
    const parsed = JSON.parse(text);
    return Object.assign({}, parsed, {
      ok: parsed.ok === true || (parsed.ok !== false && !!(parsed.results || parsed.payload || parsed.type === "pong" || parsed.type === "warmup-done")),
      elapsedMs: parsed.elapsedMs != null ? parsed.elapsedMs : (Date.now() - started),
      workerMode: parsed.workerMode || "ipc-pool"
    });
  } catch (err) {
    return {
      ok: false,
      error: "invalid-client-json: " + (err && err.message ? err.message : String(err)),
      stderr: String(child.stderr || "").slice(0, 400),
      elapsedMs: Date.now() - started,
      workerMode: "ipc-pool"
    };
  }
}

function computeOnceSync(algoId, ctx, extras) {
  const request = buildRequest(algoId, ctx, extras);
  const timeout = defaultTimeoutMs(algoId);
  const started = Date.now();
  const child = spawnSync(process.execPath, [ENTRY, "--once", "--algo=" + algoId], {
    input: JSON.stringify(request),
    encoding: "utf8",
    timeout,
    maxBuffer: 96 * 1024 * 1024,
    env: Object.assign({}, process.env, { PENTA5_RDS_PRIMARY_WRITE: "0" })
  });

  if (child.error) {
    return {
      ok: false,
      algoId,
      requestId: request.requestId,
      error: child.error.message || String(child.error),
      elapsedMs: Date.now() - started,
      workerMode: "once"
    };
  }
  if (child.status !== 0 && !child.stdout) {
    return {
      ok: false,
      algoId,
      requestId: request.requestId,
      error: "worker-exit-" + String(child.status) + ":" + String(child.stderr || "").slice(0, 400),
      elapsedMs: Date.now() - started,
      workerMode: "once"
    };
  }

  let parsed;
  try {
    parsed = JSON.parse(String(child.stdout || "").trim() || "{}");
  } catch (err) {
    return {
      ok: false,
      algoId,
      requestId: request.requestId,
      error: "invalid-worker-json: " + (err && err.message ? err.message : String(err)),
      stderr: String(child.stderr || "").slice(0, 400),
      elapsedMs: Date.now() - started,
      workerMode: "once"
    };
  }

  return Object.assign({}, parsed, {
    ok: parsed.ok !== false,
    algoId,
    requestId: request.requestId,
    elapsedMs: Date.now() - started,
    workerPid: parsed.pid || null,
    workerMode: parsed.workerMode || "once"
  });
}

function computeSync(algoId, ctx, extras) {
  if (isPoolEnabled()) {
    const request = buildRequest(algoId, ctx, extras);
    const dispatched = dispatchRequestSync(Object.assign({}, request, { type: "compute" }), defaultTimeoutMs(algoId) + 5000);
    if (dispatched && dispatched.ok !== false) {
      const one = (dispatched.results && dispatched.results[algoId]) || dispatched;
      return Object.assign({}, one, {
        ok: one.ok !== false,
        algoId,
        workerMode: one.workerMode || "ipc-pool",
        workerPid: one.workerPid || one.pid || null,
        elapsedMs: one.elapsedMs != null ? one.elapsedMs : dispatched.elapsedMs
      });
    }
    // fall through to once
  }
  return computeOnceSync(algoId, ctx, extras);
}

function computeManyOnceBatch(algoIds, ctx, extras) {
  const ids = (Array.isArray(algoIds) ? algoIds : []).map(String).filter(Boolean);
  if (!ids.length) {
    return { ok: true, results: {}, elapsedMs: 0, workerMode: "batch-parallel" };
  }
  if (ids.length === 1) {
    const one = computeOnceSync(ids[0], ctx, extras);
    return {
      ok: !!one.ok,
      results: { [ids[0]]: one },
      elapsedMs: one.elapsedMs || 0,
      workerMode: "once"
    };
  }

  const request = buildRequest("batch", ctx, extras);
  request.algoIds = ids;
  const timeout = Math.max(
    30000,
    Number(process.env.PENTA5_ALGO_WORKER_BATCH_TIMEOUT_MS || 120000) || 120000,
    Math.max.apply(null, ids.map(defaultTimeoutMs)) + 5000
  );
  const started = Date.now();
  const child = spawnSync(process.execPath, [BATCH], {
    input: JSON.stringify(request),
    encoding: "utf8",
    timeout,
    maxBuffer: 128 * 1024 * 1024,
    env: Object.assign({}, process.env, { PENTA5_RDS_PRIMARY_WRITE: "0" })
  });

  if (child.error) {
    return {
      ok: false,
      results: {},
      error: child.error.message || String(child.error),
      elapsedMs: Date.now() - started,
      workerMode: "batch-parallel"
    };
  }

  let parsed;
  try {
    parsed = JSON.parse(String(child.stdout || "").trim() || "{}");
  } catch (err) {
    return {
      ok: false,
      results: {},
      error: "invalid-batch-json: " + (err && err.message ? err.message : String(err)),
      stderr: String(child.stderr || "").slice(0, 400),
      elapsedMs: Date.now() - started,
      workerMode: "batch-parallel"
    };
  }

  return Object.assign({ results: {} }, parsed, {
    ok: parsed.ok !== false,
    elapsedMs: parsed.elapsedMs != null ? parsed.elapsedMs : (Date.now() - started),
    workerMode: parsed.workerMode || "batch-parallel"
  });
}

function computeManySync(algoIds, ctx, extras) {
  const ids = (Array.isArray(algoIds) ? algoIds : []).map(String).filter(Boolean);
  if (!ids.length) {
    return { ok: true, results: {}, elapsedMs: 0, workerMode: "ipc-pool" };
  }

  if (isPoolEnabled()) {
    const request = buildRequest(ids[0], ctx, extras);
    delete request.algoId;
    const timeout = Math.max(
      30000,
      Number(process.env.PENTA5_ALGO_WORKER_BATCH_TIMEOUT_MS || 120000) || 120000,
      Math.max.apply(null, ids.map(defaultTimeoutMs)) + 10000
    );
    const dispatched = dispatchRequestSync(Object.assign({}, request, {
      type: "computeMany",
      algoIds: ids
    }), timeout);
    if (dispatched && dispatched.results && Object.keys(dispatched.results).length) {
      return Object.assign({}, dispatched, {
        ok: dispatched.ok !== false,
        workerMode: "ipc-pool"
      });
    }
  }
  return computeManyOnceBatch(ids, ctx, extras);
}

async function warmupPool() {
  if (!isPoolEnabled()) return { ok: false, skipped: true, reason: "pool-disabled" };
  const ready = await startDispatcher();
  if (!ready && !fs.existsSync(dispatchSockPath())) {
    return { ok: false, error: "dispatcher-start-failed" };
  }
  const ping = dispatchRequestSync({ type: "warmup", algoIds: P2_PROCESS_ALGO_IDS.slice() }, 120000);
  return ping;
}

function shutdownPool() {
  if (dispatcherChild && !dispatcherChild.killed) {
    try { dispatcherChild.kill("SIGTERM"); } catch (_) {}
  }
  dispatcherChild = null;
  dispatcherReady = false;
  dispatcherStarting = null;
  try {
    const sock = dispatchSockPath();
    if (fs.existsSync(sock)) fs.unlinkSync(sock);
  } catch (_) {}
}

let poolStatusCache = null;
let poolStatusCacheAt = 0;

function poolStatus(options) {
  const opts = options && typeof options === "object" ? options : {};
  const base = {
    enabled: isPoolEnabled(),
    processMode: isProcessModeEnabled(),
    ready: dispatcherReady,
    sock: dispatchSockPath(),
    sockExists: fs.existsSync(dispatchSockPath()),
    dispatcherPid: dispatcherChild && dispatcherChild.pid || null,
    algoCount: P2_PROCESS_ALGO_IDS.length
  };
  if (!base.enabled || !base.sockExists) {
    return Object.assign(base, { poolReady: 0, poolSize: 0, live: false });
  }
  const ttl = Math.max(500, Number(process.env.PENTA5_ALGO_POOL_STATUS_TTL_MS || 3000) || 3000);
  if (!opts.force && poolStatusCache && (Date.now() - poolStatusCacheAt) < ttl) {
    return Object.assign({}, base, poolStatusCache);
  }
  const ping = dispatchRequestSync(
    { type: "ping" },
    Math.max(1000, Number(process.env.PENTA5_ALGO_POOL_PING_TIMEOUT_MS || 3000) || 3000),
    { noStart: true }
  );
  const live = !!(ping && ping.ok && ping.type === "pong");
  const cached = {
    live,
    poolReady: live ? Number(ping.ready || 0) : 0,
    poolSize: live ? Number(ping.poolSize || 0) : 0,
    dispatcherPid: live ? (ping.pid || base.dispatcherPid) : base.dispatcherPid,
    pingError: live ? null : String((ping && ping.error) || "ping-failed")
  };
  poolStatusCache = cached;
  poolStatusCacheAt = Date.now();
  return Object.assign({}, base, cached);
}

module.exports = {
  ENTRY,
  BATCH,
  DISPATCHER,
  CLIENT,
  P1_PROCESS_ALGO_IDS,
  P2_L0_PROCESS_ALGO_IDS,
  P2_L1_PROCESS_ALGO_IDS,
  P2_PROCESS_ALGO_IDS,
  L1_WAVE1,
  L1_WAVE2,
  L1_WAVE3,
  isProcessModeEnabled,
  isPoolEnabled,
  slimHistory,
  buildRequest,
  computeSync,
  computeManySync,
  warmupPool,
  startDispatcher,
  shutdownPool,
  poolStatus
};
