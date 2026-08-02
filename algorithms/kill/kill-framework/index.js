"use strict";
const path = require("path");
const { createSnapshotAlgorithm } = require("../../_shared/create_snapshot_algorithm");
module.exports = createSnapshotAlgorithm({
  id: "kill-framework",
  label: "杀码框架",
  field: "killFramework",
  ensureKey: "ensurePredictionKillFrameworkSnapshot",
  deps: ["history","prediction","position-kills","hewei","extra-kills","stable-math"],
  formulaPath: path.join(__dirname, "formula.js")
});
