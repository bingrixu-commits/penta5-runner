"use strict";
const path = require("path");
const { createSnapshotAlgorithm } = require("../_shared/create_snapshot_algorithm");
module.exports = createSnapshotAlgorithm({
  id: "three-ding-two",
  label: "三定二",
  field: "threeDingTwo",
  ensureKey: "ensurePredictionThreeDingTwoSnapshot",
  deps: ["history","prediction","position-kills","hewei","extra-kills","stable-math"],
  formulaPath: path.join(__dirname, "formula.js")
});
