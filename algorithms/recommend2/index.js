"use strict";
const path = require("path");
const { createSnapshotAlgorithm } = require("../_shared/create_snapshot_algorithm");
module.exports = createSnapshotAlgorithm({
  id: "recommend2",
  label: "推荐2",
  field: "recommend2",
  ensureKey: "ensurePredictionRecommend2Snapshot",
  deps: ["history","prediction","position-kills","hewei","extra-kills","stable-math"],
  formulaPath: path.join(__dirname, "formula.js")
});
