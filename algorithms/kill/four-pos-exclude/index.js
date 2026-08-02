"use strict";

const { asObject, defineAlgorithm } = require("../../_shared/define_algorithm");
const computeApi = require("./compute");

module.exports = defineAlgorithm({
  id: "four-pos-exclude",
  label: "四位各排1码",
  deps: ["history"],
  bindHost: computeApi.bindHost,
  compute: computeApi.compute,
  selectPrediction: (prediction) => {
    const current = asObject(prediction);
    return current.fourPosExclude || asObject(current.detail).fourPosExclude || null;
  },
  selectEvaluation: (prediction) => {
    const evaluation = asObject(asObject(prediction).evaluation);
    return evaluation.fourPosExclude || null;
  }
});

module.exports.bindHost = computeApi.bindHost;
module.exports.buildSnapshot = computeApi.buildSnapshot;
module.exports.snapshotIsComplete = computeApi.snapshotIsComplete;
module.exports.positionsComplete = computeApi.positionsComplete;
module.exports.shapesComplete = computeApi.shapesComplete;
module.exports.sealSnapshot = computeApi.sealSnapshot;
module.exports.evaluateSnapshot = computeApi.evaluateSnapshot;
module.exports.evaluatePositions = computeApi.evaluatePositions;
module.exports.evaluateShapes = computeApi.evaluateShapes;
module.exports.VERSION = computeApi.VERSION;
module.exports.LABEL = computeApi.LABEL;
module.exports.POSITION_LIST = computeApi.POSITION_LIST;
module.exports.SHAPE_COUNT = computeApi.SHAPE_COUNT;
