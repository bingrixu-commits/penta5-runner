"use strict";

const { asObject, defineAlgorithm } = require("../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "claude-dual-pos-hewei",
  label: "Claude二定+合尾",
  deps: ["history"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  selectPrediction: (prediction) => {
    const current = asObject(prediction);
    return current.claudeDualPosHewei || asObject(current.detail).claudeDualPosHewei || null;
  }
});

module.exports.bindHost = computeApi.bindHost;
module.exports.buildSnapshot = computeApi.buildSnapshot;
module.exports.snapshotIsComplete = computeApi.snapshotIsComplete;
module.exports.evaluateSnapshot = computeApi.evaluateSnapshot;
module.exports.VERSION = computeApi.VERSION;
module.exports.LABEL = computeApi.LABEL;
module.exports.KIND = computeApi.KIND;
module.exports.POSITION_LIST = computeApi.POSITION_LIST;
