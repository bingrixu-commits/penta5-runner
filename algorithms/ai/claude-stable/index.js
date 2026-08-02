"use strict";
const path = require("path");
const { createSnapshotAlgorithm } = require("../../_shared/create_snapshot_algorithm");
module.exports = createSnapshotAlgorithm({
  id: "claude-stable",
  label: "Claude稳杀(定位+合尾)",
  field: "claudeStable",
  ensureKey: "ensurePredictionClaudeStableSnapshot",
  deps: ["history", "prediction", "position-kills", "hewei", "extra-kills", "auto-top6", "recommend2", "kill-framework", "compact-attack", "three-ding-two", "three-ding-one", "three-duishu"],
  formulaPath: path.join(__dirname, "formula.js")
});
