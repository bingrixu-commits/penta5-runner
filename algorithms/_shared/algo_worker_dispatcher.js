"use strict";

/**
 * Long-lived dispatcher: owns per-algorithm fork workers and serves
 * length-delimited JSON over a Unix socket.
 *
 * Protocol (one request / one response per connection):
 *   client -> { type:"compute"|"computeMany", algoId?, algoIds?, history, ... }
 *   server -> { ok, results?, ... } then close
 */

const fs = require("fs");
const net = require("net");
const path = require("path");
const { fork } = require("child_process");

const ENTRY = path.join(__dirname, "algo_worker_entry.js");

const ALL_ALGOS = Object.freeze([
  "stable-math",
  "hewei",
  "position-kills",
  "codex-stable",
  "extra-kills",
  "four-pos-exclude",
  "flex-dual-pos-hewei",
  "dual-pos-total-hewei",
  "auto-top6",
  "recommend2",
  "kill-framework",
  "three-duishu",
  "four-ding-framework",
  "brother-number-framework"
]);


function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
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

const pool = new Map();

function ensureForkWorker(algoId) {
  let entry = pool.get(algoId);
  if (entry && entry.child && entry.child.connected && !entry.child.killed) return entry;
  const child = fork(ENTRY, ["--algo=" + algoId], {
    env: Object.assign({}, process.env, { PENTA5_RDS_PRIMARY_WRITE: "0" }),
    stdio: ["ignore", "ignore", "pipe", "ipc"]
  });
  entry = { child, algoId, ready: false, pending: new Map() };
  child.stderr.on("data", (buf) => {
    const text = String(buf || "").trim();
    if (text) console.error("[algo-dispatch worker:" + algoId + "]", text.slice(0, 300));
  });
  child.on("message", (msg) => {
    const body = asObject(msg);
    if (body.type === "ready") {
      entry.ready = true;
      if (entry.readyWaiters) {
        entry.readyWaiters.forEach((fn) => fn());
        entry.readyWaiters = [];
      }
      return;
    }
    const requestId = body.requestId;
    if (!requestId || !entry.pending.has(requestId)) return;
    const waiter = entry.pending.get(requestId);
    entry.pending.delete(requestId);
    clearTimeout(waiter.timer);
    const pid = Number(body.pid || (entry.child && entry.child.pid) || 0) || null;
    waiter.resolve(Object.assign({}, body, {
      pid,
      workerPid: pid,
      workerMode: "ipc-pool"
    }));
  });
  child.on("exit", (code, signal) => {
    pool.delete(algoId);
    entry.pending.forEach((waiter) => {
      clearTimeout(waiter.timer);
      waiter.resolve({
        ok: false,
        algoId,
        error: "worker-exited:" + String(code) + ":" + String(signal || ""),
        workerMode: "ipc-pool"
      });
    });
    entry.pending.clear();
  });
  pool.set(algoId, entry);
  return entry;
}

function waitReady(entry, timeoutMs) {
  if (entry.ready) return Promise.resolve(true);
  return new Promise((resolve) => {
    const timer = setTimeout(() => resolve(false), timeoutMs);
    if (!entry.readyWaiters) entry.readyWaiters = [];
    entry.readyWaiters.push(() => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

async function computeOne(algoId, baseRequest) {
  const request = Object.assign({}, baseRequest, {
    type: "compute",
    algoId,
    requestId: (baseRequest.requestIdPrefix || "dispatch") + ":" + algoId + ":" + Date.now() + ":" + Math.random().toString(16).slice(2, 6)
  });
  const entry = ensureForkWorker(algoId);
  const ready = await waitReady(entry, 15000);
  if (!ready && !entry.ready) {
    return {
      ok: false,
      algoId,
      requestId: request.requestId,
      error: "worker-not-ready",
      workerMode: "ipc-pool"
    };
  }
  const timeout = defaultTimeoutMs(algoId);
  const started = Date.now();
  return new Promise((resolve) => {
    const timer = setTimeout(() => {
      entry.pending.delete(request.requestId);
      resolve({
        ok: false,
        algoId,
        requestId: request.requestId,
        error: "worker-timeout",
        elapsedMs: Date.now() - started,
        workerMode: "ipc-pool",
        workerPid: entry.child && entry.child.pid || null
      });
    }, timeout);
    entry.pending.set(request.requestId, {
      timer,
      resolve: (body) => resolve(Object.assign({ elapsedMs: Date.now() - started }, body))
    });
    try {
      entry.child.send(request);
    } catch (err) {
      clearTimeout(timer);
      entry.pending.delete(request.requestId);
      resolve({
        ok: false,
        algoId,
        requestId: request.requestId,
        error: err && err.message ? err.message : String(err),
        elapsedMs: Date.now() - started,
        workerMode: "ipc-pool"
      });
    }
  });
}

async function handleRequest(req) {
  const body = asObject(req);
  const started = Date.now();
  if (body.type === "ping") {
    return {
      ok: true,
      type: "pong",
      pid: process.pid,
      poolSize: pool.size,
      ready: Array.from(pool.values()).filter((e) => e.ready).length
    };
  }
  if (body.type === "warmup") {
    const ids = Array.isArray(body.algoIds) && body.algoIds.length ? body.algoIds.map(String) : ALL_ALGOS.slice();
    await Promise.all(ids.map(async (algoId) => {
      const entry = ensureForkWorker(algoId);
      await waitReady(entry, 20000);
    }));
    return {
      ok: true,
      type: "warmup-done",
      pid: process.pid,
      poolSize: pool.size,
      ready: Array.from(pool.values()).filter((e) => e.ready).length,
      elapsedMs: Date.now() - started
    };
  }

  const baseRequest = {
    history: body.history,
    modelPerformance: asObject(body.modelPerformance),
    predictionSlice: body.predictionSlice,
    runtime: asObject(body.runtime),
    requestIdPrefix: body.requestId || ("dispatch-" + Date.now())
  };

  const algoIds = Array.isArray(body.algoIds) ? body.algoIds.map(String).filter(Boolean) : [];
  const singleAlgo = String(body.algoId || "");
  const isSingle = body.type === "compute" || (singleAlgo && !algoIds.length);
  if (isSingle) {
    if (!singleAlgo || singleAlgo === "batch") {
      return { ok: false, error: "algoId required for compute", elapsedMs: Date.now() - started };
    }
    const one = await computeOne(singleAlgo, baseRequest);
    return Object.assign({}, one, {
      ok: one.ok !== false,
      results: { [singleAlgo]: one },
      elapsedMs: Date.now() - started,
      dispatcherPid: process.pid,
      workerMode: "ipc-pool"
    });
  }

  if (!algoIds.length) {
    return { ok: false, error: "algoId/algoIds required", elapsedMs: Date.now() - started };
  }
  const settled = await Promise.all(algoIds.map((algoId) => computeOne(algoId, baseRequest)));
  const results = {};
  let allOk = true;
  settled.forEach((item) => {
    results[item.algoId] = item;
    if (!item || item.ok === false) allOk = false;
  });
  return {
    ok: allOk,
    results,
    elapsedMs: Date.now() - started,
    workerMode: "ipc-pool",
    dispatcherPid: process.pid
  };
}

function readSockPath() {
  return String(process.env.PENTA5_ALGO_DISPATCH_SOCK || path.join(__dirname, "..", "..", "data", "algo-dispatch.sock"));
}

function main() {
  const sockPath = readSockPath();
  try {
    if (fs.existsSync(sockPath)) fs.unlinkSync(sockPath);
  } catch (_) {}
  fs.mkdirSync(path.dirname(sockPath), { recursive: true });

  // allowHalfOpen: client may FIN after request; we still need to write the response.
  const server = net.createServer({ allowHalfOpen: true }, (socket) => {
    const chunks = [];
    let handled = false;
    const respond = (obj) => {
      if (handled || socket.destroyed) return;
      handled = true;
      const msg = JSON.stringify(obj || { ok: false, error: "empty-handler-result" });
      socket.write(msg, () => {
        try { socket.end(); } catch (_) {}
      });
    };
    socket.on("data", (buf) => chunks.push(buf));
    socket.on("end", () => {
      let req;
      try {
        req = JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
      } catch (err) {
        respond({ ok: false, error: "invalid-json: " + (err && err.message) });
        return;
      }
      handleRequest(req).then((out) => respond(out)).catch((err) => {
        respond({ ok: false, error: err && err.message ? err.message : String(err) });
      });
    });
    socket.on("error", () => {
      try { socket.destroy(); } catch (_) {}
    });
  });

  server.listen(sockPath, () => {
    try { fs.chmodSync(sockPath, 0o600); } catch (_) {}
    if (typeof process.send === "function") {
      process.send({ type: "ready", sockPath, pid: process.pid });
    }
    console.error("[algo-dispatch] listening", sockPath, "pid", process.pid);
    // Eager warmup of all algorithm workers.
    handleRequest({ type: "warmup", algoIds: ALL_ALGOS.slice() }).then((out) => {
      console.error("[algo-dispatch] warmup", out && out.ready, "/", out && out.poolSize, "in", out && out.elapsedMs, "ms");
    }).catch((err) => {
      console.error("[algo-dispatch] warmup failed", err && err.message ? err.message : err);
    });
  });

  const shutdown = () => {
    try { server.close(); } catch (_) {}
    pool.forEach((entry) => {
      try {
        if (entry.child && !entry.child.killed) entry.child.kill("SIGTERM");
      } catch (_) {}
    });
    pool.clear();
    try { if (fs.existsSync(sockPath)) fs.unlinkSync(sockPath); } catch (_) {}
    process.exit(0);
  };
  process.on("SIGINT", shutdown);
  process.on("SIGTERM", shutdown);
}

if (require.main === module) {
  main();
}

module.exports = {
  ALL_ALGOS,
  handleRequest,
  ensureForkWorker
};
