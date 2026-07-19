"use strict";

/**
 * Shared algorithm module contract for isolated compute packages.
 * Modules must not read sibling algorithm-projects state files.
 */

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function defineAlgorithm(definition) {
  const value = asObject(definition);
  if (!value.id || !value.label) {
    throw new Error("invalid algorithm definition: id/label required");
  }
  if (typeof value.compute !== "function") {
    throw new Error("invalid algorithm definition: compute required for " + value.id);
  }
  return {
    id: String(value.id),
    label: String(value.label),
    deps: Object.freeze(Array.isArray(value.deps) ? value.deps.map(String) : ["history"]),
    compute: value.compute,
    computeAsync: typeof value.computeAsync === "function" ? value.computeAsync : null,
    bindHost: typeof value.bindHost === "function" ? value.bindHost : null,
    selectPrediction: typeof value.selectPrediction === "function"
      ? value.selectPrediction
      : ((prediction) => asObject(prediction)),
    selectEvaluation: typeof value.selectEvaluation === "function"
      ? value.selectEvaluation
      : (() => null)
  };
}

module.exports = {
  asObject,
  defineAlgorithm
};
