"use strict";

const { asObject, defineAlgorithm } = require("../../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "hewei-qian-shi-ge-freq",
  label: "千十个合尾·频次",
  deps: ["history"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  selectPrediction: (prediction) => {
    const kills = asObject(asObject(prediction).independentHeweiKills);
    return kills.wanBaiShiFreq || null;
  },
  selectEvaluation: (prediction) => {
    const evaluation = asObject(asObject(prediction).evaluation);
    const independent = asObject(evaluation.independentHewei);
    return independent.wanBaiShiFreq || null;
  }
});

module.exports.bindHost = computeApi.bindHost;
module.exports.calculate = computeApi.calculate;
