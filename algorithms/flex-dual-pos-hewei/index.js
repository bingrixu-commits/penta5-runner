"use strict";

const { asObject, defineAlgorithm } = require("../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "flex-dual-pos-hewei",
  label: "灵活二位排除(千百/百十合尾)",
  deps: ["history"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  selectPrediction: (prediction) => {
    const current = asObject(prediction);
    return current.flexDualPosHewei || asObject(current.detail).flexDualPosHewei || null;
  }
});

module.exports.bindHost = computeApi.bindHost;
module.exports.buildSnapshot = computeApi.buildSnapshot;
module.exports.snapshotIsComplete = computeApi.snapshotIsComplete;
module.exports.VERSION = computeApi.VERSION;
module.exports.LABEL = computeApi.LABEL;
module.exports.KIND = computeApi.KIND;
module.exports.POSITION_LIST = computeApi.POSITION_LIST;
