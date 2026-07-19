"use strict";
const items = require("../_shared/framework_items");
function bindRuntime(runtime) { items.bindRuntime(runtime); }
function ensure(prediction, history) {
  return items.ensurePredictionThreeDingOneSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return items.buildThreeDingOneSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionThreeDingOneSnapshot: ensure,
  buildThreeDingOneSnapshot: build
};
