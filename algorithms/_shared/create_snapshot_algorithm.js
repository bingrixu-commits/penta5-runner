"use strict";

/**
 * Factory for L1 snapshot algorithms.
 * Prefers local ./formula.js ensure/build when present; otherwise host ensure* (legacy).
 */

const path = require("path");
const { asObject, defineAlgorithm } = require("./define_algorithm");

function createSnapshotAlgorithm(options) {
  const opts = options && typeof options === "object" ? options : {};
  const id = String(opts.id || "");
  const label = String(opts.label || id);
  const field = String(opts.field || "");
  const ensureKey = String(opts.ensureKey || "");
  const deps = Array.isArray(opts.deps) ? opts.deps.slice() : ["history", "prediction"];
  const formulaPath = opts.formulaPath ? String(opts.formulaPath) : null;
  let host = null;
  let localFormula = null;

  if (formulaPath) {
    try {
      localFormula = require(formulaPath);
    } catch (_) {
      localFormula = null;
    }
  }

  function bindHost(nextHost) {
    host = nextHost && typeof nextHost === "object" ? nextHost : null;
    if (localFormula && typeof localFormula.bindRuntime === "function" && host) {
      localFormula.bindRuntime(host);
    }
  }

  function compute(ctx) {
    const context = ctx && typeof ctx === "object" ? ctx : {};
    const prediction = context.prediction;
    if (!prediction || typeof prediction !== "object") {
      throw new Error(id + " compute requires ctx.prediction");
    }

    if (localFormula && typeof localFormula.ensure === "function") {
      const ensured = localFormula.ensure(prediction, context.history, context);
      if (ensured && typeof ensured.then === "function") {
        // 同步编排不能 await；回退 runner 上的同步 ensure*，避免整段 L1 掉进 legacy-ensure-fallback。
        // 异步主路径请走 computeAsync（cuOpt / MILP）。
        if (host && typeof host[ensureKey] === "function") {
          host[ensureKey](prediction, context.history, context);
        } else {
          throw new Error(id + " ensure is async; call computeAsync");
        }
      }
    } else if (localFormula && typeof localFormula[ensureKey] === "function") {
      localFormula[ensureKey](prediction, context.history, context);
    } else if (host && typeof host[ensureKey] === "function") {
      host[ensureKey](prediction, context.history, context);
    } else {
      throw new Error(id + " has no local formula.ensure and host." + ensureKey + " missing");
    }

    return {
      payload: prediction[field] || null,
      meta: {
        algorithmId: id,
        field,
        hasPayload: !!(prediction[field] && typeof prediction[field] === "object"),
        formula: localFormula ? (formulaPath || "local") : "host:" + ensureKey
      }
    };
  }

  async function computeAsync(ctx) {
    const context = ctx && typeof ctx === "object" ? ctx : {};
    const prediction = context.prediction;
    if (!prediction || typeof prediction !== "object") {
      throw new Error(id + " compute requires ctx.prediction");
    }

    if (localFormula && typeof localFormula.ensure === "function") {
      await localFormula.ensure(prediction, context.history, context);
    } else if (localFormula && typeof localFormula[ensureKey] === "function") {
      await localFormula[ensureKey](prediction, context.history, context);
    } else if (host && typeof host[ensureKey] === "function") {
      await host[ensureKey](prediction, context.history, context);
    } else {
      throw new Error(id + " has no local formula.ensure and host." + ensureKey + " missing");
    }

    return {
      payload: prediction[field] || null,
      meta: {
        algorithmId: id,
        field,
        hasPayload: !!(prediction[field] && typeof prediction[field] === "object"),
        formula: localFormula ? (formulaPath || "local") : "host:" + ensureKey
      }
    };
  }

  const algo = defineAlgorithm({
    id,
    label,
    deps,
    bindHost,
    compute,
    computeAsync,
    selectPrediction: (prediction) => asObject(prediction)[field] || null,
    selectEvaluation: (prediction) => asObject(asObject(prediction).evaluation)[field] || null
  });
  algo.bindHost = bindHost;
  algo.field = field;
  algo.ensureKey = ensureKey;
  algo.hasLocalFormula = !!localFormula;
  algo.computeAsync = computeAsync;
  return algo;
}

module.exports = {
  createSnapshotAlgorithm
};
