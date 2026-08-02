"use strict";

const { asObject, defineAlgorithm } = require("../../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "stable-math",
  label: "稳定数学过滤",
  deps: ["history"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  computeAsync: computeApi.computeAsync,
  selectPrediction: (prediction) => {
    const current = asObject(prediction);
    return current.stableMathFilter || asObject(current.detail).stableMathFilter || null;
  },
  selectEvaluation: () => null
});

module.exports.bindHost = computeApi.bindHost;
module.exports.computeAsync = computeApi.computeAsync;
