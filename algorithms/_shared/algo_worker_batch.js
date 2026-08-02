"use strict";

/**
 * Parallel batch launcher: one parent process fans out N --once workers.
 * Algorithms still do not share memory with each other (separate OS processes).
 *
 * stdin JSON: { algoIds, history, modelPerformance, predictionSlice, runtime, ... }
 * stdout JSON: { ok, results: { [algoId]: result }, elapsedMs }
 */

const { spawn } = require("child_process");
const path = require("path");

const ENTRY = path.join(__dirname, "algo_worker_entry.js");

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(chunks.join("")));
    process.stdin.on("error", reject);
  });
}

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

function runOne(algoId, baseRequest) {
  const timeout = defaultTimeoutMs(algoId);
  const request = Object.assign({}, baseRequest, {
    type: "compute",
    algoId,
    requestId: (baseRequest && baseRequest.requestIdPrefix || "batch") + ":" + algoId + ":" + Date.now()
  });
  return new Promise((resolve) => {
    const started = Date.now();
    const child = spawn(process.execPath, [ENTRY, "--once", "--algo=" + algoId], {
      env: Object.assign({}, process.env, { PENTA5_RDS_PRIMARY_WRITE: "0" }),
      stdio: ["pipe", "pipe", "pipe"]
    });
    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (payload) => {
      if (settled) return;
      settled = true;
      resolve(Object.assign({ algoId, elapsedMs: Date.now() - started }, payload));
    };
    const timer = setTimeout(() => {
      try { child.kill("SIGKILL"); } catch (_) {}
      finish({ ok: false, error: "worker-timeout", workerMode: "once" });
    }, timeout);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => { stdout += chunk; });
    child.stderr.on("data", (chunk) => { stderr += chunk; });
    child.on("error", (err) => {
      clearTimeout(timer);
      finish({ ok: false, error: err && err.message ? err.message : String(err), workerMode: "once" });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (settled) return;
      try {
        const parsed = JSON.parse(String(stdout || "").trim() || "{}");
        finish(Object.assign({}, parsed, {
          ok: parsed.ok !== false,
          workerPid: parsed.pid || null,
          workerMode: parsed.workerMode || "once",
          stderr: stderr ? String(stderr).slice(0, 400) : undefined
        }));
      } catch (err) {
        finish({
          ok: false,
          error: "invalid-worker-json: " + (err && err.message ? err.message : String(err)),
          exitCode: code,
          stderr: String(stderr || "").slice(0, 400),
          workerMode: "once"
        });
      }
    });
    child.stdin.end(JSON.stringify(request));
  });
}

async function main() {
  const started = Date.now();
  const raw = await readStdin();
  let request;
  try {
    request = raw ? JSON.parse(raw) : {};
  } catch (err) {
    process.stdout.write(JSON.stringify({
      ok: false,
      error: "invalid-json: " + (err && err.message ? err.message : String(err))
    }));
    process.exitCode = 2;
    return;
  }
  const algoIds = Array.isArray(request.algoIds)
    ? request.algoIds.map(String).filter(Boolean)
    : [];
  if (!algoIds.length) {
    process.stdout.write(JSON.stringify({ ok: false, error: "algoIds required" }));
    process.exitCode = 2;
    return;
  }
  const baseRequest = {
    history: request.history,
    modelPerformance: asObject(request.modelPerformance),
    predictionSlice: request.predictionSlice,
    runtime: asObject(request.runtime),
    requestIdPrefix: request.requestId || ("batch-" + Date.now())
  };
  const settled = await Promise.all(algoIds.map((algoId) => runOne(algoId, baseRequest)));
  const results = {};
  let allOk = true;
  settled.forEach((item) => {
    results[item.algoId] = item;
    if (!item || item.ok === false) allOk = false;
  });
  process.stdout.write(JSON.stringify({
    ok: allOk,
    results,
    elapsedMs: Date.now() - started,
    workerMode: "batch-parallel",
    pid: process.pid
  }));
  if (!allOk) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((err) => {
    process.stdout.write(JSON.stringify({
      ok: false,
      error: err && err.message ? err.message : String(err)
    }));
    process.exit(1);
  });
}

module.exports = {
  ENTRY,
  runOne
};
