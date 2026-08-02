"use strict";
/** Primary formula — local engine only. */
const engine = require("./engine");
function bindRuntime(runtime) { engine.bindRuntime(runtime); }
function ensure(prediction, history) {
  return engine.ensurePredictionThreeDingOneSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return engine.buildThreeDingOneSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionThreeDingOneSnapshot: ensure,
  buildThreeDingOneSnapshot: build
};
