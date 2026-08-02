"use strict";
/** Primary formula — local engine only. */
const engine = require("./engine");
function bindRuntime(runtime) { engine.bindRuntime(runtime); }
function ensure(prediction, history) {
  return engine.ensurePredictionThreeDingTwoSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return engine.buildThreeDingTwoSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionThreeDingTwoSnapshot: ensure,
  buildThreeDingTwoSnapshot: build
};
