"use strict";
const path = require("path");
const { createSnapshotAlgorithm } = require("../_shared/create_snapshot_algorithm");
module.exports = createSnapshotAlgorithm({
  id: "side-stable",
  label: "侧稳框架",
  field: "sideStable",
  ensureKey: "ensurePredictionSideStableSnapshot",
  deps: ["history","prediction","position-kills","hewei","extra-kills","stable-math"],
  formulaPath: path.join(__dirname, "formula.js")
});
