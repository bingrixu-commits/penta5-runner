"use strict";
const path = require("path");
const { createSnapshotAlgorithm } = require("../../_shared/create_snapshot_algorithm");
module.exports = createSnapshotAlgorithm({
  id: "brother-number-framework",
  label: "取二兄弟框架",
  field: "brotherNumberFramework",
  ensureKey: "ensurePredictionBrotherNumberFrameworkSnapshot",
  deps: ["history", "prediction", "position-kills", "hewei", "extra-kills"],
  formulaPath: path.join(__dirname, "formula.js")
});
