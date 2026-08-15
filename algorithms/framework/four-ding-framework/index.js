"use strict";
const path = require("path");
const { createSnapshotAlgorithm } = require("../../_shared/create_snapshot_algorithm");
module.exports = createSnapshotAlgorithm({
  id: "four-ding-framework",
  label: "四定框架",
  field: "fourDingFramework",
  ensureKey: "ensurePredictionFourDingFrameworkSnapshot",
  deps: ["history", "prediction", "extra-kills", "stable-math", "position-kills", "hewei"],
  formulaPath: path.join(__dirname, "formula.js")
});
