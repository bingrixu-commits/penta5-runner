"use strict";

const { asObject, defineAlgorithm } = require("../../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "codex-stable",
  label: "Codex稳定位杀",
  deps: ["history", "position-kills"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  selectPrediction: (prediction) => {
    const current = asObject(prediction);
    return current.codexStable || asObject(current.detail).codexStable || null;
  },
  selectEvaluation: () => null
});

module.exports.field = "codexStable";
module.exports.bindHost = computeApi.bindHost;
module.exports.compute = computeApi.compute;
