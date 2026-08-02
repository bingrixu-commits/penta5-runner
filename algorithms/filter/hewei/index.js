"use strict";

const { asObject, defineAlgorithm } = require("../../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "hewei",
  label: "合尾排除",
  deps: ["history"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  selectPrediction: (prediction) => {
    const current = asObject(prediction);
    const detail = asObject(current.detail);
    return {
      totalHeweiKill: current.totalHeweiKill,
      totalHeweiBackupDigit: current.totalHeweiBackupDigit,
      threeFourHeweiKillDigit: current.threeFourHeweiKillDigit,
      threeFourHeweiBackupDigit: current.threeFourHeweiBackupDigit,
      independentHeweiKills: current.independentHeweiKills || {},
      detail: {
        totalHeweiKill: detail.totalHeweiKill || null,
        threeFourHeweiKill: detail.threeFourHeweiKill || null,
        independentHeweiKills: detail.independentHeweiKills || current.independentHeweiKills || {}
      }
    };
  },
  selectEvaluation: (prediction) => {
    const evaluation = asObject(asObject(prediction).evaluation);
    return {
      totalHewei: evaluation.totalHewei || null,
      independentHewei: evaluation.independentHewei || null
    };
  }
});

module.exports.bindHost = computeApi.bindHost;
