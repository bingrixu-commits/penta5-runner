"use strict";
/** Primary formula — local engine only. */
const engine = require("./engine");
function bindRuntime(runtime) { engine.bindRuntime(runtime); }
function ensure(prediction, history) {
  return engine.ensurePredictionBrotherNumberFrameworkSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return engine.buildBrotherNumberFrameworkSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionBrotherNumberFrameworkSnapshot: ensure,
  buildBrotherNumberFrameworkSnapshot: build,
  brotherNumberFrameworkSnapshotIsComplete: engine.brotherNumberFrameworkSnapshotIsComplete,
  BROTHER_NUMBER_FRAMEWORK_SNAPSHOT_VERSION: engine.BROTHER_NUMBER_FRAMEWORK_SNAPSHOT_VERSION,
  BROTHER_NUMBER_FRAMEWORK_ITEM_COUNT: engine.BROTHER_NUMBER_FRAMEWORK_ITEM_COUNT
};
