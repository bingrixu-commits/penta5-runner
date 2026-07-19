"use strict";
const items = require("../_shared/framework_items");
function bindRuntime(runtime) { items.bindRuntime(runtime); }
function ensure(prediction, history) {
  return items.ensurePredictionColdFrameworkSnapshot(prediction, history);
}
function build(prediction, history, options) {
  return items.buildColdFrameworkSnapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionColdFrameworkSnapshot: ensure,
  buildColdFrameworkSnapshot: build
};
