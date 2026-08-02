"use strict";

const { asObject, defineAlgorithm } = require("../../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "hewei-qian-shi-ge-stable",
  label: "千十个合尾·稳定",
  deps: ["history"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  selectPrediction: (prediction) => {
    const kills = asObject(asObject(prediction).independentHeweiKills);
    return kills.wanBaiShi || null;
  },
  selectEvaluation: (prediction) => {
    const evaluation = asObject(asObject(prediction).evaluation);
    const independent = asObject(evaluation.independentHewei);
    return independent.wanBaiShi || null;
  }
});

module.exports.bindHost = computeApi.bindHost;
module.exports.calculate = computeApi.calculate;
