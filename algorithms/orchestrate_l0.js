"use strict";

/**
 * L0 orchestrator: runs independent algorithm compute modules in order.
 * Only shared history (+ modelPerformance) is input; modules must not read
 * sibling algorithm-projects state.json.
 *
 * When PENTA5_ALGO_PROCESS_MODE=1, all L0 algos run in isolated OS processes.
 */

const positionKillsAlgo = require("./position-kills");
const codexStableAlgo = require("./codex-stable");
const heweiAlgo = require("./hewei");
const stableMathAlgo = require("./stable-math");
const extraKillsAlgo = require("./extra-kills");
const fourPosExcludeAlgo = require("./four-pos-exclude");
const flexDualPosHeweiAlgo = require("./flex-dual-pos-hewei");
const dualPosTotalHeweiAlgo = require("./dual-pos-total-hewei");
const algoWorkerHost = require("./_shared/algo_worker_host");

const L0_ALGORITHMS = Object.freeze([
  positionKillsAlgo,
  codexStableAlgo,
  heweiAlgo,
  stableMathAlgo,
  extraKillsAlgo,
  fourPosExcludeAlgo,
  flexDualPosHeweiAlgo,
  dualPosTotalHeweiAlgo
]);

let runnerHost = null;

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function bindRunnerHost(host) {
  runnerHost = host && typeof host === "object" ? host : null;
  const next = runnerHost || {};
  L0_ALGORITHMS.forEach((algo) => {
    if (typeof algo.bindHost === "function") algo.bindHost(next);
  });
}

function hostState() {
  if (runnerHost && typeof runnerHost.getState === "function") {
    return runnerHost.getState() || {};
  }
  return {};
}

function readLocksFromHost() {
  if (runnerHost && typeof runnerHost.getStable120Locks === "function") {
    return asObject(runnerHost.getStable120Locks());
  }
  return asObject(hostState().stable120Locks);
}

function writeLocksToHost(locks) {
  if (runnerHost && typeof runnerHost.setStable120Locks === "function") {
    runnerHost.setStable120Locks(asObject(locks));
    return;
  }
  const st = hostState();
  if (st) st.stable120Locks = asObject(locks);
}

function readModelSelectionLocks() {
  return asObject(hostState().modelSelectionLocks);
}

function writeModelSelectionLocks(locks) {
  const st = hostState();
  if (st) st.modelSelectionLocks = asObject(locks);
}

function readCachedFilterFromHost() {
  if (runnerHost && typeof runnerHost.getCachedFilter === "function") {
    const fromHost = runnerHost.getCachedFilter();
    if (fromHost && typeof fromHost === "object") return fromHost;
  }
  if (runnerHost && typeof runnerHost.readCachedFilter === "function") {
    const fromDisk = runnerHost.readCachedFilter();
    if (fromDisk && typeof fromDisk === "object") return fromDisk;
  }
  return null;
}

function buildPredictionsMirror() {
  const preds = Array.isArray(hostState().predictions) ? hostState().predictions : [];
  return preds
    .filter((item) => item && item.evaluated && item.evaluation)
    .map(() => ({ evaluated: true, evaluation: { ok: true } }));
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

function computeL0InProcess(ctx) {
  const context = ctx && typeof ctx === "object" ? ctx : {};
  const heweiResult = heweiAlgo.compute(context);
  const positionResult = positionKillsAlgo.compute(context);
  const codexResult = codexStableAlgo.compute(Object.assign({}, context, {
    prediction: { positionKills: positionResult.payload },
    positionKills: positionResult.payload
  }));
  const codexPayload = asObject(codexResult.payload);
  const positionKills = codexPayload.positionKills || positionResult.payload;
  const stableResult = stableMathAlgo.compute(context);
  const extraResult = extraKillsAlgo.compute(Object.assign({}, context, {
    getState: context.getState,
    getStableMathExcludeDigitSet: context.getStableMathExcludeDigitSet
  }));
  const fourPosResult = fourPosExcludeAlgo.compute(context);
  const flexDualResult = flexDualPosHeweiAlgo.compute(context);
  const dualPosTotalResult = dualPosTotalHeweiAlgo.compute(context);
  return {
    hewei: heweiResult.payload,
    positionKills,
    codexStable: codexPayload.codexStable || null,
    stableMathFilter: stableResult.payload,
    extraKills: extraResult.payload,
    fourPosExclude: fourPosResult.payload,
    flexDualPosHewei: flexDualResult.payload,
    dualPosTotalHewei: dualPosTotalResult.payload,
    meta: {
      hewei: heweiResult.meta,
      positionKills: positionResult.meta,
      codexStable: codexResult.meta,
      stableMath: stableResult.meta,
      extraKills: extraResult.meta,
      fourPosExclude: fourPosResult.meta,
      flexDualPosHewei: flexDualResult.meta,
      dualPosTotalHewei: dualPosTotalResult.meta
    },
    processMode: false
  };
}

function computeL0Process(ctx) {
  const context = ctx && typeof ctx === "object" ? ctx : {};
  let locks = readLocksFromHost();
  let modelLocks = readModelSelectionLocks();
  const cachedFilter = readCachedFilterFromHost();
  const history = Array.isArray(context.history) ? context.history : [];
  const slimHistory = algoWorkerHost.slimHistory(history);
  const predictionsMirror = buildPredictionsMirror();

  const stableResult = algoWorkerHost.computeSync("stable-math", context, {
    runtime: {
      stable120Locks: locks,
      modelSelectionLocks: modelLocks,
      stableMathFilter: cachedFilter,
      historyMirror: slimHistory,
      latestRecord: slimHistory[0] || null,
      predictionsMirror
    }
  });
  if (!stableResult.ok) {
    throw new Error("stable-math worker failed: " + String(stableResult.error || "unknown"));
  }
  const stableMathFilter = stableResult.payload || cachedFilter;
  if (stableMathFilter && runnerHost && typeof runnerHost.setCachedFilter === "function") {
    runnerHost.setCachedFilter(stableMathFilter);
  }
  if (stableResult.runtimeOut && stableResult.runtimeOut.stable120Locks) {
    locks = asObject(stableResult.runtimeOut.stable120Locks);
  }

  const heweiResult = algoWorkerHost.computeSync("hewei", context, {
    runtime: {
      stable120Locks: locks,
      modelSelectionLocks: modelLocks,
      stableMathFilter,
      historyMirror: slimHistory,
      latestRecord: slimHistory[0] || null,
      predictionsMirror
    }
  });
  if (!heweiResult.ok) {
    throw new Error("hewei worker failed: " + String(heweiResult.error || "unknown"));
  }
  if (heweiResult.runtimeOut && heweiResult.runtimeOut.stable120Locks) {
    locks = asObject(heweiResult.runtimeOut.stable120Locks);
  }

  const positionResult = algoWorkerHost.computeSync("position-kills", context, {
    runtime: {
      stable120Locks: locks,
      modelSelectionLocks: modelLocks,
      stableMathFilter,
      historyMirror: slimHistory,
      latestRecord: slimHistory[0] || null,
      predictionsMirror
    }
  });
  if (!positionResult.ok) {
    throw new Error("position-kills worker failed: " + String(positionResult.error || "unknown"));
  }
  if (positionResult.runtimeOut && positionResult.runtimeOut.stable120Locks) {
    locks = asObject(positionResult.runtimeOut.stable120Locks);
  }
  writeLocksToHost(locks);

  const codexResult = algoWorkerHost.computeSync("codex-stable", Object.assign({}, context, {
    prediction: { positionKills: positionResult.payload },
    positionKills: positionResult.payload
  }), {
    runtime: {
      stable120Locks: locks,
      modelSelectionLocks: modelLocks,
      stableMathFilter,
      historyMirror: slimHistory,
      latestRecord: slimHistory[0] || null,
      predictionsMirror
    }
  });
  if (!codexResult.ok) {
    throw new Error("codex-stable worker failed: " + String(codexResult.error || "unknown"));
  }
  const codexPayload = asObject(codexResult.payload);
  const positionKills = codexPayload.positionKills || positionResult.payload;

  const extraResult = algoWorkerHost.computeSync("extra-kills", context, {
    runtime: {
      stable120Locks: locks,
      modelSelectionLocks: modelLocks,
      stableMathFilter,
      historyMirror: slimHistory,
      latestRecord: slimHistory[0] || null,
      predictionsMirror
    }
  });
  if (!extraResult.ok) {
    throw new Error("extra-kills worker failed: " + String(extraResult.error || "unknown"));
  }
  if (extraResult.runtimeOut && extraResult.runtimeOut.modelSelectionLocks) {
    modelLocks = asObject(extraResult.runtimeOut.modelSelectionLocks);
    writeModelSelectionLocks(modelLocks);
  }
  if (extraResult.runtimeOut && extraResult.runtimeOut.stable120Locks) {
    locks = asObject(extraResult.runtimeOut.stable120Locks);
    writeLocksToHost(locks);
  }

  const fourPosResult = algoWorkerHost.computeSync("four-pos-exclude", context, {
    runtime: {
      stable120Locks: locks,
      modelSelectionLocks: modelLocks,
      stableMathFilter,
      historyMirror: slimHistory,
      latestRecord: slimHistory[0] || null,
      predictionsMirror
    }
  });
  if (!fourPosResult.ok) {
    throw new Error("four-pos-exclude worker failed: " + String(fourPosResult.error || "unknown"));
  }

  const flexDualResult = algoWorkerHost.computeSync("flex-dual-pos-hewei", context, {
    runtime: {
      stable120Locks: locks,
      modelSelectionLocks: modelLocks,
      stableMathFilter,
      historyMirror: slimHistory,
      latestRecord: slimHistory[0] || null,
      predictionsMirror
    }
  });
  if (!flexDualResult.ok) {
    throw new Error("flex-dual-pos-hewei worker failed: " + String(flexDualResult.error || "unknown"));
  }

  const dualPosTotalResult = algoWorkerHost.computeSync("dual-pos-total-hewei", context, {
    runtime: {
      stable120Locks: locks,
      modelSelectionLocks: modelLocks,
      stableMathFilter,
      historyMirror: slimHistory,
      latestRecord: slimHistory[0] || null,
      predictionsMirror
    }
  });
  if (!dualPosTotalResult.ok) {
    throw new Error("dual-pos-total-hewei worker failed: " + String(dualPosTotalResult.error || "unknown"));
  }

  return {
    hewei: heweiResult.payload,
    positionKills,
    codexStable: codexPayload.codexStable || null,
    stableMathFilter,
    extraKills: extraResult.payload,
    fourPosExclude: fourPosResult.payload,
    flexDualPosHewei: flexDualResult.payload,
    dualPosTotalHewei: dualPosTotalResult.payload,
    meta: {
      hewei: processMeta(heweiResult, "hewei"),
      positionKills: processMeta(positionResult, "position-kills"),
      codexStable: processMeta(codexResult, "codex-stable"),
      stableMath: processMeta(stableResult, "stable-math"),
      extraKills: processMeta(extraResult, "extra-kills"),
      fourPosExclude: processMeta(fourPosResult, "four-pos-exclude"),
      flexDualPosHewei: processMeta(flexDualResult, "flex-dual-pos-hewei"),
      dualPosTotalHewei: processMeta(dualPosTotalResult, "dual-pos-total-hewei")
    },
    processMode: true,
    processAlgoIds: algoWorkerHost.P2_L0_PROCESS_ALGO_IDS.slice()
  };
}

function computeL0(ctx) {
  if (algoWorkerHost.isProcessModeEnabled()) {
    return computeL0Process(ctx);
  }
  return computeL0InProcess(ctx);
}

async function noopYield() {}

/**
 * In-process L0 with optional yield between steps so the runner HTTP loop
 * (panel /state, backdelete /history) can breathe during tip build.
 * Process-mode falls back to sync computeL0 (already isolated).
 */
async function computeL0Async(ctx, yieldFn) {
  if (algoWorkerHost.isProcessModeEnabled()) {
    return computeL0(ctx);
  }
  const y = typeof yieldFn === "function" ? yieldFn : noopYield;
  const context = ctx && typeof ctx === "object" ? ctx : {};
  const heweiResult = heweiAlgo.compute(context);
  await y();
  const positionResult = positionKillsAlgo.compute(context);
  await y();
  const codexResult = codexStableAlgo.compute(Object.assign({}, context, {
    prediction: { positionKills: positionResult.payload },
    positionKills: positionResult.payload
  }));
  await y();
  const codexPayload = asObject(codexResult.payload);
  const positionKills = codexPayload.positionKills || positionResult.payload;
  const stableResult = stableMathAlgo.compute(context);
  await y();
  const extraResult = extraKillsAlgo.compute(Object.assign({}, context, {
    getState: context.getState,
    getStableMathExcludeDigitSet: context.getStableMathExcludeDigitSet
  }));
  await y();
  const fourPosResult = fourPosExcludeAlgo.compute(context);
  await y();
  const flexDualResult = flexDualPosHeweiAlgo.compute(context);
  await y();
  const dualPosTotalResult = dualPosTotalHeweiAlgo.compute(context);
  return {
    hewei: heweiResult.payload,
    positionKills,
    codexStable: codexPayload.codexStable || null,
    stableMathFilter: stableResult.payload,
    extraKills: extraResult.payload,
    fourPosExclude: fourPosResult.payload,
    flexDualPosHewei: flexDualResult.payload,
    dualPosTotalHewei: dualPosTotalResult.payload,
    meta: {
      hewei: heweiResult.meta,
      positionKills: positionResult.meta,
      codexStable: codexResult.meta,
      stableMath: stableResult.meta,
      extraKills: extraResult.meta,
      fourPosExclude: fourPosResult.meta,
      flexDualPosHewei: flexDualResult.meta,
      dualPosTotalHewei: dualPosTotalResult.meta
    },
    processMode: false,
    yielded: true
  };
}

module.exports = {
  L0_ALGORITHMS,
  bindRunnerHost,
  computeL0,
  computeL0Async,
  positionKillsAlgo,
  codexStableAlgo,
  heweiAlgo,
  stableMathAlgo,
  extraKillsAlgo,
  fourPosExcludeAlgo,
  flexDualPosHeweiAlgo,
  dualPosTotalHeweiAlgo
};
