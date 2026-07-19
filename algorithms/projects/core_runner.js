"use strict";

const { asObject, defineProject } = require("./project_helpers");

module.exports = defineProject({
  id: "core-runner",
  label: "核心预测编排",
  selectPrediction: (prediction) => {
    const current = asObject(prediction);
    return {
      modelVersion: current.modelVersion || "",
      baseUid: current.baseUid || "",
      basePeriod: current.basePeriod || "",
      targetPeriod: current.targetPeriod || "",
      tipReady: current.tipReady === true,
      allSnapshotsSealed: current.allSnapshotsSealed === true
    };
  }
});
