"use strict";

const { asObject, defineAlgorithm } = require("../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "dual-pos-total-hewei",
  label: "二定+千百十个合尾",
  deps: ["history"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  selectPrediction: (prediction) => {
    const current = asObject(prediction);
    return current.dualPosTotalHewei || asObject(current.detail).dualPosTotalHewei || null;
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
