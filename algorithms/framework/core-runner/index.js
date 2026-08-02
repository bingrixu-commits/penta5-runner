"use strict";

/**
 * Core tip orchestration projection (not a formula algorithm).
 * Mirrors tip readiness / seal status into its own algorithm-projects state.
 */

const { asObject, defineProject } = require("../../projects/project_helpers");

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
      allSnapshotsSealed: current.allSnapshotsSealed === true,
      independenceMode: asObject(current.recommendationMeta).independenceMode || "",
      hasL0Meta: !!(asObject(current.detail).l0Meta),
      hasL1Meta: !!(asObject(current.detail).l1Meta)
    };
  }
});
