"use strict";
/** Primary formula — local engine only. */
const engine = require("./engine");
function bindRuntime(runtime) { engine.bindRuntime(runtime); }
function ensure(prediction, history) {
  return engine.ensurePredictionFourDingFrameworkSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return engine.buildFourDingFrameworkSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionFourDingFrameworkSnapshot: ensure,
  buildFourDingFrameworkSnapshot: build,
  fourDingFrameworkSnapshotIsComplete: engine.fourDingFrameworkSnapshotIsComplete,
  FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION: engine.FOUR_DING_FRAMEWORK_SNAPSHOT_VERSION,
  FOUR_DING_FRAMEWORK_ITEM_COUNT: engine.FOUR_DING_FRAMEWORK_ITEM_COUNT
};
