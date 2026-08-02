"use strict";
/** Primary formula — local engine only. */
const engine = require("./engine");
function bindRuntime(runtime) { engine.bindRuntime(runtime); }
function ensure(prediction, history) {
  return engine.ensurePredictionRecommend2Snapshot(prediction, history);
}
function build(prediction, history, options) {
  return engine.buildRecommend2Snapshot(prediction, history, options);
}
module.exports = {
  bindRuntime,
  ensure,
  build,
  ensurePredictionRecommend2Snapshot: ensure,
  buildRecommend2Snapshot: build
};
