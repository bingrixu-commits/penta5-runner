"use strict";
const path = require("path");
const { createSnapshotAlgorithm } = require("../_shared/create_snapshot_algorithm");
module.exports = createSnapshotAlgorithm({
  id: "compact-attack",
  label: "紧凑攻击",
  field: "compactAttack",
  ensureKey: "ensurePredictionCompactAttackSnapshot",
  deps: ["history","prediction","position-kills","hewei","extra-kills","stable-math"],
  formulaPath: path.join(__dirname, "formula.js")
});
