"use strict";
const items = require("../_shared/framework_items");
function bindRuntime(runtime) { items.bindRuntime(runtime); }
function ensure(prediction, history) {
  return items.ensurePredictionThreeDingTwoSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return items.buildThreeDingTwoSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionThreeDingTwoSnapshot: ensure,
  buildThreeDingTwoSnapshot: build
};
