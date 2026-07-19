"use strict";
const items = require("../_shared/framework_items");
function bindRuntime(runtime) { items.bindRuntime(runtime); }
function ensure(prediction, history) {
  return items.ensurePredictionRecommend2Snapshot(prediction, history);
}
function build(prediction, history, options) {
  return items.buildRecommend2Snapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionRecommend2Snapshot: ensure,
  buildRecommend2Snapshot: build
};
