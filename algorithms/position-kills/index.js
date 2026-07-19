"use strict";

const { asObject, defineAlgorithm } = require("../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "position-kills",
  label: "定位排除",
  deps: ["history"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  selectPrediction: (prediction) => {
    const current = asObject(prediction);
    return current.positionKills || asObject(current.detail).positionKills || {};
  },
  selectEvaluation: (prediction) => {
    const evaluation = asObject(asObject(prediction).evaluation);
    return evaluation.independentPosition || evaluation.positionKills || null;
  }
});

module.exports.computeRawEntries = computeApi.computeRawEntries;
module.exports.bindHost = computeApi.bindHost;
