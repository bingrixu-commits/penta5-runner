"use strict";
const items = require("../_shared/framework_items");
function bindRuntime(runtime) { items.bindRuntime(runtime); }
function ensure(prediction, history) {
  return items.ensurePredictionKillFrameworkSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return items.buildKillFrameworkSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionKillFrameworkSnapshot: ensure,
  buildKillFrameworkSnapshot: build
};
