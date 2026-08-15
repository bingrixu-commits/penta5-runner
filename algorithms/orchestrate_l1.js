"use strict";

/**
 * L1 orchestrator: runs framework snapshot algorithms after L0 base outputs
 * are already on the prediction. Modules never read sibling state.json.
 *
 * Process mode: wave1 independent frameworks (three-ding retired).
 */

const autoTop6 = require("./auto-top6");
const recommend2 = require("./recommend2");
const killFramework = require("./kill-framework");
const threeDuishu = require("./three-duishu");
const fourDingFramework = require("./four-ding-framework");
const brotherNumberFramework = require("./brother-number-framework");
const algoWorkerHost = require("./_shared/algo_worker_host");

const L1_ALGORITHMS = Object.freeze([
  autoTop6,
  recommend2,
  killFramework,
  threeDuishu,
  fourDingFramework,
  brotherNumberFramework
]);


let runnerHost = null;

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function bindRunnerHost(host) {
  runnerHost = host && typeof host === "object" ? host : null;
  const bound = runnerHost || {};
  L1_ALGORITHMS.forEach((algo) => {
    if (typeof algo.bindHost === "function") algo.bindHost(bound);
  });
}

function hostState() {
  if (runnerHost && typeof runnerHost.getState === "function") {
    return runnerHost.getState() || {};
  }
  return {};
}

function processMeta(result, fallbackAlgoId) {
  const base = result && result.meta && typeof result.meta === "object" ? result.meta : {};
  return Object.assign({}, base, {
    algorithmId: base.algorithmId || fallbackAlgoId,
    processIsolated: true,
    workerMode: result && result.workerMode || "once",
    workerPid: result && (result.workerPid || result.pid) || null,
    elapsedMs: result && result.elapsedMs || null,
    error: result && result.ok === false ? String(result.error || "worker-failed") : null
  });
}

function applyPredictionPatch(prediction, patch) {
  const body = asObject(patch);
  Object.keys(body).forEach((key) => {
    if (key === "recommendationMeta") return;
    prediction[key] = body[key];
  });
  if (body.recommendationMeta && typeof body.recommendationMeta === "object") {
    prediction.recommendationMeta = Object.assign(
      {},
      asObject(prediction.recommendationMeta),
      body.recommendationMeta
    );
  }
}

function buildRuntimeForL1(history) {
  const st = hostState();
  const slimHistory = algoWorkerHost.slimHistory(history);
  const preds = Array.isArray(st.predictions) ? st.predictions : [];
  const predictionsMirror = preds
    .filter((item) => item && item.evaluated && item.evaluation)
    .map(() => ({ evaluated: true, evaluation: { ok: true } }));
  return {
    stable120Locks: asObject(st.stable120Locks),
    modelSelectionLocks: asObject(st.modelSelectionLocks),
    stableMathFilter: st.stableMathFilter && typeof st.stableMathFilter === "object"
      ? st.stableMathFilter
      : null,
    historyMirror: slimHistory,
    latestRecord: slimHistory[0] || asObject(st.latestRecord),
    predictionsMirror,
    threeDuishuRecommendationCache: st.threeDuishuRecommendationCache && typeof st.threeDuishuRecommendationCache === "object"
      ? st.threeDuishuRecommendationCache
      : null
  };
}

function mergeRuntimeOut(runtime, result) {
  const out = result && result.runtimeOut ? result.runtimeOut : null;
  if (!out) return runtime;
  const st = hostState();
  if (out.stable120Locks) {
    runtime.stable120Locks = asObject(out.stable120Locks);
    if (st) st.stable120Locks = runtime.stable120Locks;
  }
  if (out.modelSelectionLocks) {
    runtime.modelSelectionLocks = asObject(out.modelSelectionLocks);
    if (st) st.modelSelectionLocks = runtime.modelSelectionLocks;
  }
  if (out.threeDuishuRecommendationCache) {
    runtime.threeDuishuRecommendationCache = out.threeDuishuRecommendationCache;
    if (st) st.threeDuishuRecommendationCache = out.threeDuishuRecommendationCache;
  }
  return runtime;
}

function runWave(algoIds, prediction, history, runtime, meta) {
  const batch = algoWorkerHost.computeManySync(algoIds, { history, prediction }, { runtime });
  if (!batch || batch.ok === false && (!batch.results || !Object.keys(batch.results).length)) {
    throw new Error("L1 batch failed: " + String(batch && batch.error || "unknown"));
  }
  const results = asObject(batch.results);
  algoIds.forEach((algoId) => {
    const result = results[algoId];
    if (!result || result.ok === false) {
      throw new Error(algoId + " worker failed: " + String(result && result.error || "unknown"));
    }
    applyPredictionPatch(prediction, result.predictionPatch || { [result.field]: result.payload });
    meta[algoId] = processMeta(result, algoId);
    mergeRuntimeOut(runtime, result);
  });
  return batch;
}

function computeL1InProcess(ctx) {
  const context = ctx && typeof ctx === "object" ? ctx : {};
  if (!context.prediction || typeof context.prediction !== "object") {
    throw new Error("computeL1 requires ctx.prediction");
  }
  const meta = {};
  L1_ALGORITHMS.forEach((algo) => {
    if (algo.id === "three-duishu" && typeof context.recomputeThreeDuishuRecommendation === "function") {
      context.recomputeThreeDuishuRecommendation();
    }
    const result = algo.compute(context);
    meta[algo.id] = result.meta || null;
    if (algo.id === "three-duishu" && typeof context.ensureDuishuJointPush === "function") {
      context.ensureDuishuJointPush();
    }
  });
  return {
    prediction: context.prediction,
    meta,
    processMode: false
  };
}

function computeL1Process(ctx) {
  const context = ctx && typeof ctx === "object" ? ctx : {};
  const prediction = context.prediction;
  if (!prediction || typeof prediction !== "object") {
    throw new Error("computeL1 requires ctx.prediction");
  }
  const history = Array.isArray(context.history) ? context.history : [];
  let runtime = buildRuntimeForL1(history);
  const meta = {};

  runWave(algoWorkerHost.L1_WAVE1, prediction, history, runtime, meta);
  runWave(algoWorkerHost.L1_WAVE2, prediction, history, runtime, meta);
  runWave(algoWorkerHost.L1_WAVE3, prediction, history, runtime, meta);

  return {
    prediction,
    meta,
    processMode: true,
    processAlgoIds: algoWorkerHost.P2_L1_PROCESS_ALGO_IDS.slice()
  };
}

function computeL1(ctx) {
  if (algoWorkerHost.isProcessModeEnabled()) {
    return computeL1Process(ctx);
  }
  return computeL1InProcess(ctx);
}

async function noopYield() {}

/**
 * In-process L1 with optional yield between each framework algo.
 * Keeps panel/backdelete responsive while tip frameworks rebuild.
 */
async function computeL1Async(ctx, yieldFn) {
  if (algoWorkerHost.isProcessModeEnabled()) {
    return computeL1(ctx);
  }
  const y = typeof yieldFn === "function" ? yieldFn : noopYield;
  const context = ctx && typeof ctx === "object" ? ctx : {};
  if (!context.prediction || typeof context.prediction !== "object") {
    throw new Error("computeL1 requires ctx.prediction");
  }
  const meta = {};
  for (let i = 0; i < L1_ALGORITHMS.length; i += 1) {
    const algo = L1_ALGORITHMS[i];
    if (algo.id === "three-duishu" && typeof context.recomputeThreeDuishuRecommendation === "function") {
      context.recomputeThreeDuishuRecommendation();
    }
    const runner = typeof algo.computeAsync === "function" ? algo.computeAsync : null;
    const result = runner ? await runner(context) : algo.compute(context);
    meta[algo.id] = result.meta || null;
    if (algo.id === "three-duishu" && typeof context.ensureDuishuJointPush === "function") {
      context.ensureDuishuJointPush();
    }
    await y();
  }
  return {
    prediction: context.prediction,
    meta,
    processMode: false,
    yielded: true
  };
}

module.exports = {
  L1_ALGORITHMS,
  bindRunnerHost,
  computeL1,
  computeL1Async,
  autoTop6,
  recommend2,
  killFramework,
  threeDuishu,
  fourDingFramework,
  brotherNumberFramework
};
