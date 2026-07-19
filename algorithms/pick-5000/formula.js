"use strict";
const items = require("../_shared/framework_items");
function bindRuntime(runtime) { items.bindRuntime(runtime); }
function ensure(prediction, history) {
  return items.ensurePredictionPick5000Snapshot(prediction, history);
}
function build(prediction, history, options) {
  return items.buildPick5000Snapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionPick5000Snapshot: ensure,
  buildPick5000Snapshot: build
};
