"use strict";

/**
 * Isolated algorithm worker entry.
 * - --once --algo=<id> : read one JSON request from stdin, write one JSON response, exit
 * - long-lived IPC mode via process.on("message") when forked without --once
 *
 * Workers must not write RDS and must not read sibling algorithm-projects state files.
 */

const ALGO_LOADERS = Object.freeze({
  "position-kills": () => require("../position-kills"),
  "codex-stable": () => require("../codex-stable"),
  hewei: () => require("../hewei"),
  "stable-math": () => require("../stable-math"),
  "extra-kills": () => require("../extra-kills"),
  "four-pos-exclude": () => require("../four-pos-exclude"),
  "auto-top6": () => require("../auto-top6"),
  recommend2: () => require("../recommend2"),
  "kill-framework": () => require("../kill-framework"),
  "three-duishu": () => require("../three-duishu")
});

function readStdin() {
  return new Promise((resolve, reject) => {
    const chunks = [];
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => chunks.push(chunk));
    process.stdin.on("end", () => resolve(chunks.join("")));
    process.stdin.on("error", reject);
  });
}

function writeResult(payload) {
  process.stdout.write(JSON.stringify(payload));
}

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function cloneJson(value, fallback) {
  try {
    return JSON.parse(JSON.stringify(value === undefined ? fallback : value));
  } catch (_) {
    return fallback;
  }
}

function bindSupportingFormulas(runtime, locksRef, modelLocksRef, stateShim) {
  const rt = asObject(runtime);
  const stableMathFilter = rt.stableMathFilter && typeof rt.stableMathFilter === "object"
    ? rt.stableMathFilter
    : null;
  try {
    const heweiFormula = require("../hewei/formula");
    if (typeof heweiFormula.bindRuntime === "function") {
      heweiFormula.bindRuntime({
        getStable120Locks: () => locksRef.locks,
        setStable120Locks: (value) => {
          locksRef.locks = value && typeof value === "object" ? value : {};
        },
        getStableMathFilter: () => stableMathFilter,
        readCachedStableMathFilter: () => stableMathFilter
      });
    }
  } catch (_) {}
  try {
    const extraFormula = require("../extra-kills/formula");
    if (typeof extraFormula.bindRuntime === "function") {
      extraFormula.bindRuntime({
        getState: () => stateShim,
        getStableMathExcludeDigitSet: () => {
          try {
            return require("../hewei/formula").getStableMathExcludeDigitSet();
          } catch (_) {
            return new Set();
          }
        }
      });
    }
  } catch (_) {}
  void modelLocksRef;
}

function buildStateShim(runtime, history, locksRef, modelLocksRef, duishuRef) {
  const rt = asObject(runtime);
  const stableMathFilter = rt.stableMathFilter && typeof rt.stableMathFilter === "object"
    ? rt.stableMathFilter
    : null;
  const historyRows = Array.isArray(history) ? history : (Array.isArray(rt.historyMirror) ? rt.historyMirror : []);
  const latestRecord = rt.latestRecord && typeof rt.latestRecord === "object"
    ? rt.latestRecord
    : (historyRows[0] || null);
  const predictionsMirror = Array.isArray(rt.predictionsMirror) ? rt.predictionsMirror : [];

  return {
    get history() { return historyRows; },
    get latestRecord() { return latestRecord; },
    get predictions() { return predictionsMirror; },
    get stableMathFilter() { return stableMathFilter; },
    get stable120Locks() { return locksRef.locks; },
    set stable120Locks(value) { locksRef.locks = value && typeof value === "object" ? value : {}; },
    get modelSelectionLocks() { return modelLocksRef.locks; },
    set modelSelectionLocks(value) { modelLocksRef.locks = value && typeof value === "object" ? value : {}; },
    get threeDuishuRecommendationCache() { return duishuRef.cache; },
    set threeDuishuRecommendationCache(value) { duishuRef.cache = value && typeof value === "object" ? value : null; }
  };
}

function collectPredictionPatch(prediction, field) {
  if (!prediction || typeof prediction !== "object" || !field) return {};
  const patch = {};
  if (prediction[field] !== undefined) patch[field] = prediction[field];
  const itemsKey = field + "Items";
  const repairKey = field + "RepairReport";
  if (prediction[itemsKey] !== undefined) patch[itemsKey] = prediction[itemsKey];
  if (prediction[repairKey] !== undefined) patch[repairKey] = prediction[repairKey];
  if (prediction.recommendationMeta && typeof prediction.recommendationMeta === "object") {
    patch.recommendationMeta = prediction.recommendationMeta;
  }
  return patch;
}

function bindAlgoRuntime(algo, runtime, locksRef, modelLocksRef, stateShim) {
  const rt = asObject(runtime);
  const stableMathFilter = rt.stableMathFilter && typeof rt.stableMathFilter === "object"
    ? rt.stableMathFilter
    : null;

  if (typeof algo.bindHost === "function") {
    algo.bindHost({
      getStable120Locks: () => locksRef.locks,
      setStable120Locks: (value) => {
        locksRef.locks = value && typeof value === "object" ? value : {};
      },
      getCachedFilter: () => stableMathFilter,
      setCachedFilter: () => {},
      readCachedFilter: () => stableMathFilter,
      getStableMathFilter: () => stableMathFilter,
      readCachedStableMathFilter: () => stableMathFilter,
      getState: () => stateShim,
      invalidatePanelStateCache: () => {}
    });
  }

  // Local formulas bind their own engine via bindRuntime (no hall).
  try {
    const formulaPath = algo && algo.hasLocalFormula ? null : null;
    void formulaPath;
  } catch (_) {}

  bindSupportingFormulas(runtime, locksRef, modelLocksRef, stateShim);
}

function runCompute(algoId, request) {
  const loader = ALGO_LOADERS[algoId];
  if (typeof loader !== "function") {
    throw new Error("unsupported algoId: " + algoId);
  }
  const algo = loader();
  const req = asObject(request);
  const runtime = asObject(req.runtime);
  const history = Array.isArray(req.history) ? req.history : [];
  const locksRef = { locks: cloneJson(runtime.stable120Locks, {}) };
  const modelLocksRef = { locks: cloneJson(runtime.modelSelectionLocks, {}) };
  const duishuRef = { cache: cloneJson(runtime.threeDuishuRecommendationCache, null) };
  const stateShim = buildStateShim(runtime, history, locksRef, modelLocksRef, duishuRef);
  bindAlgoRuntime(algo, runtime, locksRef, modelLocksRef, stateShim);

  // Bind only this algo's formula/engine (no full framework_items hall).
  try {
    const formula = require(require("path").join(
      __dirname,
      "..",
      algoId,
      "formula.js"
    ));
    if (formula && typeof formula.bindRuntime === "function") {
      formula.bindRuntime({
        getState: () => stateShim,
        invalidatePanelStateCache: () => {}
      });
    }
  } catch (_) {}

  const prediction = req.predictionSlice && typeof req.predictionSlice === "object"
    ? cloneJson(req.predictionSlice, {})
    : {};

  const ctx = {
    history,
    modelPerformance: asObject(req.modelPerformance),
    prediction,
    getStableMathFilter: () => runtime.stableMathFilter || null,
    readCachedStableMathFilter: () => runtime.stableMathFilter || null,
    getState: () => stateShim,
    lockApi: {
      getStable120Locks: () => locksRef.locks,
      setStable120Locks: (value) => {
        locksRef.locks = value && typeof value === "object" ? value : {};
      }
    }
  };

  const result = algo.compute(ctx);
  const field = algo.field || null;
  return {
    type: "result",
    ok: true,
    algoId,
    requestId: req.requestId || null,
    field,
    payload: result && result.payload !== undefined ? result.payload : (field ? prediction[field] : null),
    meta: result && result.meta ? result.meta : null,
    predictionPatch: collectPredictionPatch(prediction, field),
    runtimeOut: {
      stable120Locks: locksRef.locks,
      modelSelectionLocks: modelLocksRef.locks,
      threeDuishuRecommendationCache: duishuRef.cache
    }
  };
}

async function mainOnce(algoId) {
  const raw = await readStdin();
  let request;
  try {
    request = raw ? JSON.parse(raw) : {};
  } catch (err) {
    writeResult({
      type: "result",
      ok: false,
      algoId,
      error: "invalid-json: " + (err && err.message ? err.message : String(err))
    });
    process.exitCode = 2;
    return;
  }

  try {
    const out = runCompute(algoId, request);
    out.pid = process.pid;
    out.workerMode = "once";
    writeResult(out);
  } catch (err) {
    writeResult({
      type: "result",
      ok: false,
      algoId,
      requestId: request && request.requestId || null,
      pid: process.pid,
      workerMode: "once",
      error: err && err.message ? err.message : String(err),
      stack: err && err.stack ? String(err.stack).slice(0, 800) : null
    });
    process.exitCode = 1;
  }
}

function parseArgs(argv) {
  const out = { once: false, algo: "" };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = String(argv[i] || "");
    if (arg === "--once") out.once = true;
    else if (arg.startsWith("--algo=")) out.algo = arg.slice("--algo=".length).trim();
    else if (arg === "--algo") out.algo = String(argv[++i] || "").trim();
  }
  return out;
}

async function main() {
  const args = parseArgs(process.argv);
  const algoId = args.algo;
  if (!algoId) {
    writeResult({ type: "result", ok: false, error: "missing --algo" });
    process.exitCode = 2;
    return;
  }
  if (!ALGO_LOADERS[algoId]) {
    writeResult({ type: "result", ok: false, algoId, error: "unsupported algoId" });
    process.exitCode = 2;
    return;
  }
  if (args.once) {
    await mainOnce(algoId);
    return;
  }
  process.on("message", (msg) => {
    const request = asObject(msg);
    try {
      const out = runCompute(algoId, request);
      out.pid = process.pid;
      out.workerMode = "ipc";
      if (typeof process.send === "function") process.send(out);
    } catch (err) {
      if (typeof process.send === "function") {
        process.send({
          type: "result",
          ok: false,
          algoId,
          requestId: request.requestId || null,
          pid: process.pid,
          workerMode: "ipc",
          error: err && err.message ? err.message : String(err)
        });
      }
    }
  });
  if (typeof process.send === "function") {
    process.send({ type: "ready", algoId, pid: process.pid });
  }
}

if (require.main === module) {
  main().catch((err) => {
    writeResult({
      type: "result",
      ok: false,
      error: err && err.message ? err.message : String(err)
    });
    process.exit(1);
  });
}

module.exports = {
  ALGO_LOADERS,
  runCompute
};
