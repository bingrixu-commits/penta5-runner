"use strict";

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function defineProject(definition) {
  const value = asObject(definition);
  if (!value.id || !value.label || typeof value.selectPrediction !== "function") {
    throw new Error("invalid algorithm project definition");
  }
  return Object.freeze({
    id: String(value.id),
    label: String(value.label),
    selectPrediction: value.selectPrediction,
    selectEvaluation: typeof value.selectEvaluation === "function" ? value.selectEvaluation : (() => null)
  });
}

function snapshotProject(id, label, field) {
  return defineProject({
    id,
    label,
    selectPrediction: (prediction) => asObject(prediction)[field] || null,
    selectEvaluation: (prediction) => asObject(asObject(prediction).evaluation)[field] || null
  });
}

module.exports = {
  asObject,
  defineProject,
  snapshotProject
};
