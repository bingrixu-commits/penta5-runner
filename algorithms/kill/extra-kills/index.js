"use strict";

const { asObject, defineAlgorithm } = require("../../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "extra-kills",
  label: "扩展排除",
  deps: ["history"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  selectPrediction: (prediction) => {
    const current = asObject(prediction);
    return current.extraKills || asObject(current.detail).extraKills || {};
  },
  selectEvaluation: (prediction) => asObject(asObject(prediction).evaluation).extraKills || null
});
module.exports.bindHost = computeApi.bindHost;
