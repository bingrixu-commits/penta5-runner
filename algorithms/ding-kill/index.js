"use strict";
const path = require("path");
const { createSnapshotAlgorithm } = require("../_shared/create_snapshot_algorithm");
module.exports = createSnapshotAlgorithm({
  id: "ding-kill",
  label: "定位杀码框架",
  field: "dingKill",
  ensureKey: "ensurePredictionDingKillSnapshot",
  deps: ["history","prediction","position-kills","hewei","extra-kills","stable-math"],
  formulaPath: path.join(__dirname, "formula.js")
});
