"use strict";
const items = require("../_shared/framework_items");
function bindRuntime(runtime) { items.bindRuntime(runtime); }
function ensure(prediction, history) {
  return items.ensurePredictionCompactAttackSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return items.buildCompactAttackSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionCompactAttackSnapshot: ensure,
  buildCompactAttackSnapshot: build
};
