"use strict";
const path = require("path");
const { createSnapshotAlgorithm } = require("../../_shared/create_snapshot_algorithm");
module.exports = createSnapshotAlgorithm({
  id: "three-ding-one",
  label: "三定一",
  field: "threeDingOne",
  ensureKey: "ensurePredictionThreeDingOneSnapshot",
  deps: ["history","prediction","position-kills","hewei","extra-kills","stable-math"],
  formulaPath: path.join(__dirname, "formula.js")
});
